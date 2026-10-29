"""Publicacao do estoque Full ML (inerte): travas, contrato, transacao, reconciliacao, auditoria.

Sem banco: engine, sessao e auditoria sao dubles. A migration 029 e o
publicador sao executados contra PostgreSQL real descartavel pelo script de
validacao do gate (`.artefacts/deliveries/ml-full/publicacao-contrato/teste/`),
nao aqui.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipelines import publish_ml_full_stock_daily as pub
from pipelines import sync_ml_full_stock_daily as fonte_mod
from pipelines.tests.test_sync_ml_full_stock_daily import cenario

#: as funcoes REAIS da auditoria (o fixture `ambiente` troca por dubles)
AUDIT_START, AUDIT_FINISH = pub.audit_start, pub.audit_finish
ESCRITA = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|MERGE)\b", re.I)

REF = date(2026, 10, 20)
RAW = "run-raw-barbours-1"
PUB = "0123456789abcdef0123456789abcdef"
MIG = Path(__file__).resolve().parents[2] / "apps" / "api" / "alembic" / "versions" / "029_create_fact_ml_full_stock_daily.py"
#: arquivo aprovado do `-Identificar`, relativo ao cwd (o fixture `ambiente` faz chdir para tmp_path)
ARQ_DESTINO = "identidade_aprovada.json"
#: impressao digital do Neon como o helper grava (tipos do JSON: str, bool, null)
IDENT = {"modo": "real", "current_database": "neondb", "server_version_num": "160004", "em_recuperacao": False,
         "system_identifier": "7350000000000000001", "neon_tenant_id": "tenant-a", "neon_timeline_id": "timeline-a",
         "alembic_version_num": "029"}
CONTRATO = {"marcas": ["barbours"], "raw_runs": {"barbours": RAW}, "publish_run_id": PUB,
            "destino_esperado": ARQ_DESTINO}
ARGV_APPLY = ["--apply", "--ref-date", str(REF), "--marca", "barbours", "--raw-run", f"barbours={RAW}",
              "--publish-run-id", PUB, "--destino-esperado", ARQ_DESTINO]


def gravar_destino(caminho, alvo=None, fonte=None):
    alvo = dict(IDENT) if alvo is None else alvo
    fonte = dict(IDENT, current_database="datamart", em_recuperacao=True) if fonte is None else fonte
    Path(caminho).write_text(json.dumps({"subcomando": "identificar", "ok": True,
                                         "identidade": {"alvo_neon": alvo, "fonte_datamart": fonte}}),
                             encoding="utf-8")


def registro(run_id=RAW, marcas="barbours", status="completo", desfecho=None,
             inicio=datetime(2026, 10, 20, 10, 20, tzinfo=timezone.utc)):
    desfecho = desfecho or {m: "PUBLICADA" for m in marcas.split(",")}
    return {"run_id": run_id, "started_at": inicio, "brands_requested": marcas, "status": status,
            "outcome_by_brand": desfecho}


def fonte_marca(marca="barbours", run_id=RAW):
    s, st, p, v = cenario()
    troca = lambda xs: [dict(x, brand=marca, run_id=run_id) for x in xs]
    return {"snapshot": troca(s), "status": troca(st), "ponte": troca(p),
            "vendas": {(marca,) + k[1:]: q for k, q in v.items()}}


def fonte_barbours(run_id=RAW, runs=None):
    f = fonte_marca("barbours", run_id)
    f["runs"] = {RAW: registro()} if runs is None else runs
    return f


def fonte_barbours_e_kokeshi():
    b, k = fonte_marca("barbours"), fonte_marca("kokeshi")
    f = {t: b[t] + k[t] for t in ("snapshot", "status", "ponte")}
    f["vendas"] = {**b["vendas"], **k["vendas"]}
    f["runs"] = {RAW: registro(marcas="barbours,kokeshi")}
    return f


class Res:
    def __init__(self, linhas=None, escalar=None, mapa=None):
        self.linhas, self.escalar, self.mapa = linhas or [], escalar, mapa

    def scalar(self):
        return self.escalar

    def fetchone(self):
        return self.linhas[0] if self.linhas else None

    def fetchall(self):
        return list(self.linhas)

    def mappings(self):
        return self

    def one(self):
        return self.mapa


class Cursor:
    """Cursor DBAPI da MESMA conexao da transacao: responde as queries da impressao digital."""

    def __init__(self, conn):
        self.conn, self.ultimo = conn, None
        self.connection = SimpleNamespace(info=SimpleNamespace(host=conn.host))

    def execute(self, q, p=None):
        self.conn.sqls.append((q, p))
        i = self.conn.ident
        if q in self.conn.negar:
            raise RuntimeError("permission denied")
        if "INSERT INTO audit.source_sync_run" in q:
            self.ultimo = (77,)
            return
        if "UPDATE audit.source_sync_run" in q:
            self.rowcount = 1
            return
        respostas = (
            ("current_database()", i["current_database"]),
            ("current_setting('server_version_num')", i["server_version_num"]),
            ("pg_is_in_recovery()", i["em_recuperacao"]),
            ("has_function_privilege", self.conn.pode_control),
            ("pg_control_system()", None if i["system_identifier"] is None else int(i["system_identifier"])),
            ("neon.tenant_id", i["neon_tenant_id"]),
            ("neon.timeline_id", i["neon_timeline_id"]),
            ("to_regclass('public.alembic_version')", self.conn.tem_alembic),
            ("FROM public.alembic_version", i["alembic_version_num"]),
        )
        self.ultimo = next(((v,) for chave, v in respostas if chave in q), None)

    def fetchone(self):
        return self.ultimo


class Conn:
    def __init__(self, lock=True, destino=None, em_uso=False, ident=None, host="ep-x.sa-east-1.aws.neon.tech"):
        self.lock, self.destino, self.em_uso, self.sqls = lock, destino, em_uso, []
        self.ident, self.host = dict(IDENT) if ident is None else ident, host
        self.pode_control, self.tem_alembic, self.negar = True, True, set()
        self.connection = self
        self.commits = self.rollbacks = self.closes = 0

    def execute(self, sql, params=None):
        s = str(sql)
        self.sqls.append((s, params))
        if "pg_try_advisory_xact_lock" in s:
            return Res(escalar=self.lock)
        if "source_run_id = :publish_run_id" in s:
            return Res(escalar=self.em_uso)
        if "COUNT(*)" in s:
            return Res(mapa=self.destino)
        return Res()

    @contextmanager
    def cursor(self):
        yield Cursor(self)

    # conexao DBAPI crua da auditoria (`_audit_conn`)
    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class Engine:
    """`begin()` como o do SQLAlchemy: rollback na excecao do corpo, COMMIT na saida limpa."""

    def __init__(self, conn, falha_commit=None):
        self.conn, self.falha_commit = conn, falha_commit
        self.begins = self.commits = self.rollbacks = 0

    @contextmanager
    def begin(self):
        self.begins += 1
        try:
            yield self.conn
        except BaseException:
            self.rollbacks += 1
            raise
        if self.falha_commit:
            raise self.falha_commit
        self.commits += 1


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    estado = {"audit": [], "inseridos": [], "marcas_inseridas": set(), "leituras": []}
    monkeypatch.chdir(tmp_path)
    gravar_destino(tmp_path / ARQ_DESTINO)

    def ler(ref, runs=()):
        estado["leituras"].append((ref, runs))
        return fonte_barbours()

    monkeypatch.setattr(pub, "_ler_fonte", ler)
    monkeypatch.setattr(pub, "tabelas_destino_existem", lambda: True)
    monkeypatch.setattr(pub, "publish_run_id_em_uso", lambda rid: False)
    monkeypatch.setattr(pub, "audit_start", lambda n, esp=None: estado["audit"].append(("start", n)) or 77)
    monkeypatch.setattr(pub, "audit_finish", lambda sid, st, **kw: estado["audit"].append((st, kw)))

    def inserir(cur, sql, linhas, page_size):
        estado["inseridos"].append((sql.split()[2], len(linhas)))
        estado["marcas_inseridas"] |= {l[1] for l in linhas}      # brand e' a 2a coluna nas 3 fatos

    monkeypatch.setattr(pub, "execute_values", inserir)
    monkeypatch.setenv(pub.FLAG_AMBIENTE, "true")
    return estado


def _conn_ok(fonte=None):
    pacote = pub.montar(fonte or fonte_barbours(), REF, "r")
    return Conn(destino={"n": pacote["esperado"]["inventarios"], "soma": pacote["esperado"]["soma_disponivel"]})


def _sem_neon(monkeypatch, motivo="tocou o Neon"):
    monkeypatch.setattr(pub, "local_engine", lambda: pytest.fail(motivo))


def _main_json(capsys, argv):
    codigo = pub.main(argv)
    out = capsys.readouterr().out
    return codigo, json.loads(out)          # stdout tem de ser SO' o JSON


# ---------------------------------------------------------------------------
# Travas antigas (continuam obrigatorias)
# ---------------------------------------------------------------------------
def test_sem_apply_e_dry_run_e_nao_abre_o_neon(ambiente, monkeypatch):
    _sem_neon(monkeypatch, "abriu o Neon sem --apply")
    r = pub.run(apply=False, ref_date=REF)
    assert r["applied"] is False and r["modo"] == "dry_run" and r["grava"] is False
    assert r["linhagem_raw"] == {"barbours": [RAW]}
    assert ambiente["audit"] == []


def test_apply_sem_flag_recusa_sem_escrever(ambiente, monkeypatch):
    monkeypatch.delenv(pub.FLAG_AMBIENTE)
    _sem_neon(monkeypatch, "escreveu sem flag")
    with pytest.raises(pub.PublicacaoRecusada, match="desligada"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert ambiente["audit"] == []


@pytest.mark.parametrize("valor", ["false", "1", "TRUE ", "yes", ""])
def test_so_o_texto_true_liga(monkeypatch, valor):
    monkeypatch.setenv(pub.FLAG_AMBIENTE, valor)
    assert pub.flag_ligada() is (valor.strip().lower() == "true")


def test_apply_sem_fonte_recusa(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): None)
    with pytest.raises(pub.PublicacaoRecusada, match="fonte_inexistente"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)


def test_apply_sem_migration_recusa(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "tabelas_destino_existem", lambda: False)
    with pytest.raises(pub.PublicacaoRecusada, match="029"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert ambiente["audit"] == []


# ---------------------------------------------------------------------------
# Contrato novo do --apply (T-0019)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("falta,erro", [
    ("marcas", "--marca"), ("raw_runs", "--raw-run"), ("publish_run_id", "--publish-run-id")])
def test_apply_sem_marca_raw_run_ou_publish_id_recusa_sem_ler_nem_escrever(ambiente, monkeypatch, falta, erro):
    _sem_neon(monkeypatch)
    kw = dict(CONTRATO, **{falta: None})
    if falta == "marcas":
        kw["raw_runs"] = None
    with pytest.raises(pub.PublicacaoRecusada, match=erro):
        pub.run(apply=True, ref_date=REF, **kw)
    assert ambiente["leituras"] == [] and ambiente["audit"] == []


@pytest.mark.parametrize("kw,erro", [
    ({"marcas": ["gocase"], "raw_runs": {"gocase": RAW}}, "allowlist"),
    ({"marcas": ["barbours", "barbours"]}, "repetida"),
    ({"raw_runs": {"barbours": "run com espaco"}}, "raw_run_invalido"),
    ({"raw_runs": {"barbours": RAW, "kokeshi": RAW}}, "raw_run_sem_par"),
    ({"publish_run_id": PUB.upper()}, "publish_run_id_invalido"),
    ({"publish_run_id": PUB[:-1]}, "publish_run_id_invalido"),
    ({"publish_run_id": "0123456789abcdef-0123456789abcde"}, "publish_run_id_invalido"),
])
def test_contrato_mal_formado_recusa(ambiente, monkeypatch, kw, erro):
    _sem_neon(monkeypatch)
    with pytest.raises(pub.PublicacaoRecusada, match=erro):
        pub.run(apply=True, ref_date=REF, **dict(CONTRATO, **kw))
    assert ambiente["leituras"] == []


def test_marca_com_fotografia_nao_pedida_e_ignorada_e_nunca_gravada(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte_barbours_e_kokeshi())
    conn = _conn_ok(fonte_marca("barbours"))
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    r = pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert r["marcas_publicadas"] == ["barbours"] and r["marcas_ignoradas"] == ["kokeshi"]
    assert set(r["por_marca"]) == {"barbours"}
    deletes = [p for s, p in conn.sqls if s.startswith("DELETE")]
    assert len(deletes) == 3 and all(p["marcas"] == ["barbours"] for p in deletes)
    assert ambiente["marcas_inseridas"] == {"barbours"}
    assert all(s[1] == "barbours" for s in pub.montar(pub.filtrar_fonte(
        fonte_barbours_e_kokeshi(), ["barbours"])[0], REF, PUB)["inventario"])


def test_marca_pedida_sem_fotografia_recusa(ambiente, monkeypatch):
    _sem_neon(monkeypatch)
    with pytest.raises(pub.PublicacaoRecusada, match="marca_pedida_sem_fotografia"):
        pub.run(apply=True, ref_date=REF, marcas=["barbours", "kokeshi"],
                raw_runs={"barbours": RAW, "kokeshi": RAW}, publish_run_id=PUB, destino_esperado=ARQ_DESTINO)
    assert ambiente["audit"] == []


def test_leitura_pede_o_registro_dos_runs_informados(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_conn_ok()))
    pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert ambiente["leituras"] == [(REF, (RAW,))]


def _com_ponte_de_outro_run(f):
    f["ponte"][0] = dict(f["ponte"][0], run_id="run-novo")
    return f


def _com_status_de_outro_run(f):
    f["status"][0] = dict(f["status"][0], run_id="run-novo")
    return f


@pytest.mark.parametrize("fonte,erro", [
    (lambda: fonte_barbours(run_id="run-novo", runs={RAW: registro(), "run-novo": registro("run-novo")}),
     "linhagem_divergente"),                                                   # RAW republicado com outro run
    (lambda: _com_ponte_de_outro_run(fonte_barbours()), "linhagem_divergente"),   # mais de um run (ponte)
    (lambda: _com_status_de_outro_run(fonte_barbours()), "linhagem_divergente"),  # mais de um run (status)
    (lambda: fonte_barbours(runs={}), "run_raw_nao_registrado"),
    (lambda: fonte_barbours(runs=None) | {"runs": None}, "runs_raw_inexistente"),
    (lambda: fonte_barbours(runs={RAW: registro(status="running")}), "run_raw_nao_terminado"),
    (lambda: fonte_barbours(runs={RAW: registro(desfecho={"barbours": "INCOMPLETA_NAO_PUBLICADA"})}),
     "run_raw_nao_publicado"),
    (lambda: fonte_barbours(runs={RAW: registro(desfecho={"barbours": "JA_PUBLICADA_HOJE"})}),
     "run_raw_nao_publicado"),
    (lambda: fonte_barbours(runs={RAW: registro(desfecho={"kokeshi": "PUBLICADA"}, marcas="barbours")}),
     "run_raw_nao_publicado"),
    (lambda: fonte_barbours(runs={RAW: registro(marcas="kokeshi")}), "run_raw_de_outra_marca"),
    # 21/10 03:00 UTC = 21/10 00:00 em Sao Paulo: run do dia seguinte.
    (lambda: fonte_barbours(runs={RAW: registro(inicio=datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc))}),
     "run_raw_de_outra_data"),
])
def test_linhagem_raw_divergente_recusa_antes_de_qualquer_escrita(ambiente, monkeypatch, fonte, erro):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte())
    _sem_neon(monkeypatch, "tocou o Neon com linhagem divergente")
    with pytest.raises(pub.PublicacaoRecusada, match=erro):
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert ambiente["audit"] == [] and ambiente["inseridos"] == []


def test_run_parcial_com_a_marca_publicada_e_aceito_e_fuso_e_sao_paulo(ambiente, monkeypatch):
    # 21/10 02:59 UTC = 20/10 23:59 em Sao Paulo: o ref_date do airflow e' 20/10.
    reg = registro(status="parcial", marcas="barbours,kokeshi",
                   desfecho={"barbours": "PUBLICADA", "kokeshi": "INCOMPLETA_NAO_PUBLICADA"},
                   inicio=datetime(2026, 10, 21, 2, 59, tzinfo=timezone.utc))
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte_barbours(runs={RAW: reg}))
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_conn_ok()))
    assert pub.run(apply=True, ref_date=REF, **CONTRATO)["applied"] is True


def test_outcome_em_texto_json_tambem_e_lido(ambiente, monkeypatch):
    reg = registro(desfecho=None)
    reg["outcome_by_brand"] = '{"barbours": "PUBLICADA"}'
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte_barbours(runs={RAW: reg}))
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_conn_ok()))
    assert pub.run(apply=True, ref_date=REF, **CONTRATO)["applied"] is True


def test_dry_run_com_raw_run_confere_a_mesma_linhagem(ambiente, monkeypatch):
    _sem_neon(monkeypatch)
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte_barbours(run_id="run-novo"))
    with pytest.raises(pub.PublicacaoRecusada, match="linhagem_divergente"):
        pub.run(apply=False, ref_date=REF, marcas=["barbours"], raw_runs={"barbours": RAW})


def test_publish_run_id_vira_source_run_id(ambiente, monkeypatch):
    conn = _conn_ok()
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    capturado = []
    original = pub.montar
    monkeypatch.setattr(pub, "montar", lambda f, d, rid: capturado.append(rid) or original(f, d, rid))
    r = pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert capturado == [PUB] and r["source_run_id"] == PUB and r["run_id"] == PUB


def test_publish_run_id_reutilizado_recusa_na_pre_checagem(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "publish_run_id_em_uso", lambda rid: rid == PUB)
    _sem_neon(monkeypatch)
    with pytest.raises(pub.PublicacaoRecusada, match="publish_run_id_reutilizado"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert ambiente["audit"] == []


def test_publish_run_id_reutilizado_sob_o_lock_recusa_e_reverte(ambiente, monkeypatch):
    conn = Conn(em_uso=True)
    eng = Engine(conn)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    with pytest.raises(pub.PublicacaoRecusada, match="publish_run_id_reutilizado") as e:
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert e.value.estado_transacao == pub.ESTADO_REVERTIDA
    assert not any(s.startswith("DELETE") for s, _ in conn.sqls) and ambiente["inseridos"] == []
    assert "pg_try_advisory_xact_lock" in conn.sqls[0][0]
    assert any("source_run_id = :publish_run_id" in s for s, _ in conn.sqls)
    assert eng.rollbacks == 1 and eng.commits == 0 and ambiente["audit"][-1][0] == "failed"


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
def test_publicacao_lock_primeiro_delete_por_marca_e_reconcilia(ambiente, monkeypatch):
    conn = _conn_ok()
    eng = Engine(conn)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    r = pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert r["applied"] is True and r["marcas_publicadas"] == ["barbours"]
    assert r["estado_transacao"] == "commit_confirmado" and r["auditoria_ok"] is True
    assert r["modo"] == "apply" and r["grava"] is True
    assert "pg_try_advisory_xact_lock" in conn.sqls[0][0]
    deletes = [p for s, p in conn.sqls if s.startswith("DELETE")]
    assert len(deletes) == 3 and all(p["marcas"] == ["barbours"] for p in deletes)
    tabelas = [t for t, _ in ambiente["inseridos"]]
    assert tabelas == [pub.FACT_INVENTARIO, pub.FACT_STATUS, pub.FACT_MARCA]
    assert ambiente["audit"][0] == ("start", 5) and ambiente["audit"][-1][0] == "success"
    assert eng.begins == 1 and eng.commits == 1


def test_reconciliacao_divergente_aborta_e_audita_falha(ambiente, monkeypatch):
    conn = Conn(destino={"n": 999, "soma": 0})
    eng = Engine(conn)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    with pytest.raises(pub.ReconciliacaoFalhou) as e:
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert e.value.estado_transacao == pub.ESTADO_REVERTIDA and eng.commits == 0
    assert ambiente["audit"][-1][0] == "failed" and ambiente["audit"][-1][1]["rows_loaded"] == 0


def test_lock_ocupado_nao_escreve_e_audita_falha(ambiente, monkeypatch):
    conn = Conn(lock=False)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.ConcurrentRunError):
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert not any(s.startswith("DELETE") for s, _ in conn.sqls)
    assert ambiente["inseridos"] == [] and ambiente["audit"][-1][0] == "failed"


def test_montar_status_so_de_inventario_lido_e_estoque_uma_vez():
    pac = pub.montar(fonte_barbours(), REF, "r")
    assert {s[3] for s in pac["status"]} == {"IA"}
    inv = {r[3]: r for r in pac["inventario"]}
    assert inv["IA"][5] == 56                          # disponivel: uma vez, nao 3x
    assert inv["IC"][4] == "NAO_ENCONTRADO" and inv["IC"][5] is None
    marca = pac["marca"][0]
    assert marca[pub.COLS_MARCA.index("unidades_28d_item_varios_inventarios")] == 9
    assert pac["esperado"]["soma_disponivel"] == 56 + 0 + 30 + 500


# ---------------------------------------------------------------------------
# Estado da transacao, exit code e stdout (CLI)
# ---------------------------------------------------------------------------
def test_cli_apply_ok_exit_0_rotulos_de_apply(ambiente, monkeypatch, capsys):
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_conn_ok()))
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 0 and j["applied"] is True and j["grava"] is True and j["modo"] == "apply"
    assert j["estado_transacao"] == "commit_confirmado" and j["source_run_id"] == PUB
    assert "dry_run" not in json.dumps(j)


def test_falha_de_audit_finish_pos_commit_e_commit_confirmado_exit_5(ambiente, monkeypatch, capsys):
    eng = Engine(_conn_ok())
    monkeypatch.setattr(pub, "local_engine", lambda: eng)

    def audit_quebrado(sid, st, **kw):
        raise RuntimeError('permission denied for table source_sync_run (host "ep-x.neon.tech")')

    monkeypatch.setattr(pub, "audit_finish", audit_quebrado)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 5 and eng.commits == 1
    assert j["estado_transacao"] == "commit_confirmado" and j["applied"] is True
    assert j["auditoria_ok"] is False and j["erro_auditoria"].startswith("RuntimeError: permission denied")
    assert "neon.tech" not in json.dumps(j)


def test_excecao_pre_commit_e_revertida_comprovada_exit_1(ambiente, monkeypatch, capsys):
    eng = Engine(Conn(destino={"n": 999, "soma": 0}))
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 1 and eng.rollbacks == 1 and eng.commits == 0
    assert j["estado_transacao"] == "revertida_comprovada" and j["applied"] is False
    assert j["erro_classe"] == "ReconciliacaoFalhou" and j["auditoria_ok"] is True
    assert j["publish_run_id"] == PUB


def test_falha_no_proprio_commit_e_indeterminado(ambiente, monkeypatch, capsys):
    eng = Engine(_conn_ok(), falha_commit=ConnectionError("server closed the connection unexpectedly"))
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 1 and j["estado_transacao"] == "indeterminado" and j["applied"] is None
    assert "--diagnosticar" in j["proximo_passo"]
    assert ambiente["audit"][-1][0] == "failed" and ambiente["audit"][-1][1]["rows_loaded"] is None
    assert ambiente["audit"][-1][1]["error_message"].startswith("estado_transacao=indeterminado")


def test_falha_da_auditoria_de_falha_nao_mascara_o_erro_original(ambiente, monkeypatch, capsys):
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(Conn(lock=False)))
    monkeypatch.setattr(pub, "audit_finish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit fora")))
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 1 and j["erro_classe"] == "ConcurrentRunError"
    assert j["estado_transacao"] == "revertida_comprovada" and j["auditoria_ok"] is False


@pytest.mark.parametrize("argv", [
    ["--apply"],
    ["--apply", "--marca", "barbours", "--publish-run-id", PUB],
    ["--apply", "--marca", "barbours", "--raw-run", f"barbours={RAW}"],
    ["--apply", "--marca", "barbours", "--raw-run", "barbours", "--publish-run-id", PUB],
    ARGV_APPLY + ["--raw-run", f"barbours={RAW}"],
    ["--ref-date", "20-10-2026"],
    ["--diagnosticar", "--ref-date", str(REF)],
    ["--diagnosticar", "--publish-run-id", PUB, "--ref-date", str(REF), "--apply"],
])
def test_cli_recusa_e_json_exit_2_sem_tocar_o_neon(ambiente, monkeypatch, capsys, argv):
    _sem_neon(monkeypatch)
    codigo, j = _main_json(capsys, argv)
    assert codigo == 2 and j["resultado"] == "recusada" and j["estado_transacao"] == "nao_iniciada"
    assert ambiente["audit"] == []


def test_cli_dry_run_padrao_continua_exit_0(ambiente, monkeypatch, capsys):
    _sem_neon(monkeypatch)
    codigo, j = _main_json(capsys, ["--ref-date", str(REF)])
    assert codigo == 0 and j["modo"] == "dry_run" and j["applied"] is False


def test_cli_erro_de_conexao_sai_em_json_sanitizado(ambiente, monkeypatch, capsys):
    msg = ('connection to server at "ep-cool-1.sa-east-1.aws.neon.tech" (10.1.2.3), port 5432 failed: '
           'FATAL:  password authentication failed for user "neon_pub"\n'
           "postgresql://neon_pub:segredo@ep-cool-1.aws.neon.tech/db")

    class OperationalError(Exception):
        pass

    def quebra(ref, runs=()):
        raise OperationalError(msg)

    monkeypatch.setattr(pub, "_ler_fonte", quebra)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    texto = json.dumps(j)
    assert codigo == 1 and j["erro_classe"] == "OperationalError" and j["estado_transacao"] == "nao_iniciada"
    for vazado in ("neon.tech", "10.1.2.3", "neon_pub", "segredo", "postgresql://"):
        assert vazado not in texto, vazado
    assert "password authentication failed" in j["motivo"]


@pytest.mark.parametrize("bruto,proibido", [
    ("host=db.interno.local port=5432 user=pub password=x dbname=y", ["db.interno", "pub", "=x"]),
    ('connection to server at "1.2.3.4", port 5432 failed', ["1.2.3.4", "5432"]),
    ("could not translate host name \"ep-a.neon.tech\" to address", ["ep-a"]),
    ("postgresql+psycopg2://u:p@h:5432/d", ["u:p@h"]),
])
def test_sanitizar_redige_host_dsn_usuario_ip(bruto, proibido):
    s = pub.sanitizar(RuntimeError(bruto))
    assert s.startswith("RuntimeError: ")
    for p in proibido:
        assert p not in s, (p, s)


def test_logs_que_iam_para_o_stdout_vao_para_o_stderr(capsys):
    lg = logging.getLogger("pipelines.teste_t0019_stdout")
    h = logging.StreamHandler(sys.stdout)
    lg.addHandler(h)
    try:
        pub._logs_para_stderr()
        assert h.stream is sys.stderr
    finally:
        lg.removeHandler(h)


def test_print_durante_o_run_nao_suja_o_stdout(ambiente, monkeypatch, capsys):
    _sem_neon(monkeypatch)
    original = pub.run

    def barulhento(**kw):
        print("ruido no stdout")
        return original(**kw)

    monkeypatch.setattr(pub, "run", barulhento)
    codigo, j = _main_json(capsys, ["--ref-date", str(REF)])
    assert codigo == 0 and j["modo"] == "dry_run"


# ---------------------------------------------------------------------------
# Diagnostico so' leitura
# ---------------------------------------------------------------------------
class ConnLeitura:
    def __init__(self, presentes=(True, True, True)):
        self.presentes, self.sqls, self.rollbacks = presentes, [], 0

    def execute(self, sql, params=None):
        s = str(sql)
        self.sqls.append(s)
        if "to_regclass" in s:
            return Res(linhas=[tuple(self.presentes)])
        if "GROUP BY brand" in s:
            n = {pub.FACT_INVENTARIO: 5, pub.FACT_STATUS: 1, pub.FACT_MARCA: 1}[s.split("FROM ")[1].split()[0]]
            return Res(linhas=[("barbours", n)])
        if "ref_date <> :ref_date" in s:
            return Res(escalar=0)
        if "DISTINCT brand, source_run_id" in s:
            return Res(linhas=[("barbours", PUB), ("kokeshi", "outro")])
        if "source_run_id = :publish_run_id" in s:
            return Res(escalar=False)
        return Res()

    def rollback(self):
        self.rollbacks += 1


class EngineLeitura:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connect(self):
        yield self.conn

    def begin(self):
        pytest.fail("diagnostico abriu transacao de escrita")


def _assert_so_leitura(conn):
    assert conn.sqls[0] == "SET TRANSACTION READ ONLY" and conn.rollbacks == 1
    for s in conn.sqls:
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b|advisory", s, re.I), s


def test_diagnosticar_so_le_e_acha_as_linhas(monkeypatch, capsys):
    monkeypatch.delenv(pub.FLAG_AMBIENTE, raising=False)            # nao exige flag
    conn = ConnLeitura()
    monkeypatch.setattr(pub, "local_engine", lambda: EngineLeitura(conn))
    monkeypatch.setattr(pub, "_ler_fonte", lambda *a: pytest.fail("diagnostico leu o Data Mart"))
    codigo, j = _main_json(capsys, ["--diagnosticar", "--ref-date", str(REF), "--publish-run-id", PUB])
    assert codigo == 0 and j["veredito"] == "commit_confirmado" and j["grava"] is False
    assert j["linhas_do_run"][pub.FACT_INVENTARIO] == {"barbours": 5} and j["total_linhas_do_run"] == 7
    assert j["source_run_id_vigente"] == {"barbours": PUB, "kokeshi": "outro"}
    assert j["vigente_para_as_marcas_do_run"] is True
    _assert_so_leitura(conn)


def test_diagnosticar_sem_029(monkeypatch):
    conn = ConnLeitura(presentes=(True, False, True))
    monkeypatch.setattr(pub, "local_engine", lambda: EngineLeitura(conn))
    assert pub.diagnosticar(REF, PUB)["veredito"] == "fato_inexistente"
    _assert_so_leitura(conn)


def test_pre_checagem_do_publish_run_id_so_le(monkeypatch):
    conn = ConnLeitura()
    monkeypatch.setattr(pub, "local_engine", lambda: EngineLeitura(conn))
    assert pub.publish_run_id_em_uso(PUB) is False
    _assert_so_leitura(conn)


def test_ler_fonte_le_tudo_numa_transacao_repeatable_read_read_only(monkeypatch):
    eventos, sqls = [], []

    class Raw:
        def set_session(self, **kw):
            eventos.append(("set_session", kw))

    class Linha:
        def __init__(self, m):
            self._mapping = m

    class Conexao:
        connection = Raw()

        def execute(self, sql, params=None):
            s = str(sql)
            sqls.append(s)
            if s == fonte_mod.SQL_CONFERE_LEITURA:
                return Res(linhas=[("repeatable read", "on")])
            if "raw.ml_full_stock_runs" in s and "to_regclass" in s:
                return Res(escalar=True)
            if "to_regclass" in s:
                return Res(linhas=[(True, True, True)])
            if "FROM raw.ml_full_stock_runs" in s:
                return [Linha(registro())]
            return []

    class Sessao:
        conexao = Conexao()

        def connection(self):
            return self.conexao

        def rollback(self):
            eventos.append("rollback")

        def close(self):
            eventos.append("close")

    monkeypatch.setattr(pub, "DataMartSession", Sessao)
    f = pub._ler_fonte(REF, (RAW,))
    assert f["runs"] == {RAW: registro()}
    # 1o comando: a transacao e' REPEATABLE READ READ ONLY, conferida antes de qualquer SELECT de dado;
    # snapshot, status, ponte, vendas e runs saem todos da MESMA conexao/transacao.
    assert sqls[:2] == [fonte_mod.SQL_LEITURA_CONSISTENTE, fonte_mod.SQL_CONFERE_LEITURA]
    for trecho in ("ml_full_inventory_snapshot", "ml_full_inventory_unavailable", "ml_full_inventory_listing",
                   "api.ml_orders", "FROM raw.ml_full_stock_runs"):
        assert any(trecho in s for s in sqls[2:]), trecho
    assert eventos == ["rollback", "close"]                      # sem set_session (persistiria no pool)
    assert not any(re.search(r"\b(INSERT|UPDATE|DELETE)\b", s, re.I) for s in sqls)


# ---------------------------------------------------------------------------
# Migration 029 (estatico)
# ---------------------------------------------------------------------------
def _mig():
    spec = importlib.util.spec_from_file_location("m029", MIG)
    m = importlib.util.module_from_spec(spec)
    import types
    sys.modules.setdefault("alembic", types.SimpleNamespace(op=None))
    spec.loader.exec_module(m)
    return m


def test_migration_029_cadeia_e_travas():
    m = _mig()
    assert (m.revision, m.down_revision) == ("029", "027")
    sql = " ".join(m._upgrade_sql())
    for t in pub.TABELAS:
        assert f"CREATE TABLE {t}" in sql
    assert "ck_fmlfsi_leitura_quantidade" in sql and "ck_fmlfsi_cobertura_exige_demanda" in sql
    assert sql.count("<> 'NaN'") >= 3
    assert "INSERT" not in sql.upper()
    for c in pub.COLS_INV + pub.COLS_MARCA:
        assert c in sql, c


def test_auditoria_marca_o_ml_como_2():
    # audit.source_sync_run.marketplace_id: 1=tiktok, 2=ml, 3=shopee
    assert pub.AUDIT_MARKETPLACE_ID == 2


# ---------------------------------------------------------------------------
# Destino esperado: impressao digital do Neon na transacao de escrita (fase 3)
# ---------------------------------------------------------------------------
def _ordem(conn):
    sqls = [s for s, _ in conn.sqls]
    lock = next(i for i, s in enumerate(sqls) if "pg_try_advisory_xact_lock" in s)
    ident = [i for i, s in enumerate(sqls) if "current_database()" in s]
    deletes = [i for i, s in enumerate(sqls) if s.startswith("DELETE")]
    return lock, ident, deletes


@pytest.mark.parametrize("destino", [None, ""])
def test_apply_sem_destino_esperado_recusa_sem_conexao(ambiente, monkeypatch, destino):
    _sem_neon(monkeypatch, "abriu o Neon sem --destino-esperado")
    monkeypatch.setattr(pub, "_ler_fonte", lambda *a: pytest.fail("leu o Data Mart sem --destino-esperado"))
    with pytest.raises(pub.PublicacaoRecusada, match="--destino-esperado"):
        pub.run(apply=True, ref_date=REF, **dict(CONTRATO, destino_esperado=destino))
    assert ambiente["audit"] == [] and ambiente["inseridos"] == []


@pytest.mark.parametrize("conteudo,erro", [
    (None, "destino_esperado_ilegivel"),                                          # arquivo inexistente
    ("isto nao e' json", "destino_esperado_ilegivel"),
    ("[1, 2]", "destino_esperado_ilegivel"),
    ('{"identidade": {"fonte_datamart": {"modo": "real"}}}', "destino_esperado_sem_identidade"),
    ('{"identidade": {"alvo_neon": {}}}', "destino_esperado_sem_identidade"),
    ('{"identidade": {"alvo_neon": "neondb"}}', "destino_esperado_sem_identidade"),
])
def test_apply_com_destino_ilegivel_recusa_sem_conexao(ambiente, monkeypatch, tmp_path, conteudo, erro):
    _sem_neon(monkeypatch, "abriu o Neon com destino ilegivel")
    monkeypatch.setattr(pub, "_ler_fonte", lambda *a: pytest.fail("leu o Data Mart com destino ilegivel"))
    arq = tmp_path / "destino_ruim.json"
    if conteudo is not None:
        arq.write_text(conteudo, encoding="utf-8")
    with pytest.raises(pub.PublicacaoRecusada, match=erro):
        pub.run(apply=True, ref_date=REF, **dict(CONTRATO, destino_esperado=str(arq)))
    assert ambiente["audit"] == []


def test_cli_apply_sem_destino_esperado_exit_2_nao_iniciada(ambiente, monkeypatch, capsys):
    _sem_neon(monkeypatch)
    codigo, j = _main_json(capsys, ARGV_APPLY[:-2])
    assert codigo == 2 and j["estado_transacao"] == "nao_iniciada" and "--destino-esperado" in j["motivo"]
    assert ambiente["audit"] == [] and ambiente["leituras"] == []


def test_destino_igual_segue_e_confere_depois_do_lock_antes_do_delete(ambiente, monkeypatch):
    conn = _conn_ok()
    eng = Engine(conn)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    r = pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert r["applied"] is True and eng.commits == 1
    lock, ident, deletes = _ordem(conn)
    assert lock == 0 and ident and deletes and lock < ident[0] < deletes[0]


@pytest.mark.parametrize("campo,valor", [
    ("current_database", "outro_banco"),
    ("system_identifier", "7350000000000000999"),
    ("neon_timeline_id", "timeline-b"),
    ("alembic_version_num", "027"),
    ("em_recuperacao", True),
    ("server_version_num", "170000"),
])
def test_destino_divergente_recusa_sem_delete_e_revertida(ambiente, monkeypatch, capsys, campo, valor):
    conn = Conn(ident=dict(IDENT, **{campo: valor}))
    eng = Engine(conn)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 2 and j["resultado"] == "recusada" and j["erro_classe"] == "PublicacaoRecusada"
    assert j["estado_transacao"] == "revertida_comprovada" and j["applied"] is False
    assert j["motivo"] == f"destino_divergente: alvo_neon.{campo}"
    assert str(valor) not in j["motivo"] and str(IDENT[campo]) not in j["motivo"]      # sem valores
    assert not any(s.startswith("DELETE") for s, _ in conn.sqls) and ambiente["inseridos"] == []
    assert not any("source_run_id = :publish_run_id" in s for s, _ in conn.sqls)
    assert eng.rollbacks == 1 and eng.commits == 0 and ambiente["audit"][-1][0] == "failed"


@pytest.mark.parametrize("campo,atual,aprovado", [
    ("neon_tenant_id", None, "tenant-a"),             # leitura null x arquivo com valor
    ("neon_tenant_id", "tenant-a", None),             # leitura com valor x arquivo null
    ("system_identifier", None, "7350000000000000001"),
    ("alembic_version_num", "029", None),
])
def test_null_so_bate_com_null(ambiente, monkeypatch, tmp_path, campo, atual, aprovado):
    gravar_destino(tmp_path / ARQ_DESTINO, dict(IDENT, **{campo: aprovado}))
    conn = Conn(ident=dict(IDENT, **{campo: atual}))
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.PublicacaoRecusada, match=f"destino_divergente: alvo_neon.{campo}$") as e:
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert e.value.estado_transacao == pub.ESTADO_REVERTIDA
    assert not any(s.startswith("DELETE") for s, _ in conn.sqls)


@pytest.mark.parametrize("nulos", [
    {"neon_tenant_id": None, "neon_timeline_id": None},       # forte = system_identifier
    {"system_identifier": None},                              # forte = par Neon
])
def test_null_com_null_segue_se_sobra_identificador_forte(ambiente, monkeypatch, tmp_path, nulos):
    gravar_destino(tmp_path / ARQ_DESTINO, dict(IDENT, **nulos))
    conn = _conn_ok()
    conn.ident = dict(IDENT, **nulos)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    assert pub.run(apply=True, ref_date=REF, **CONTRATO)["applied"] is True


def test_tipo_tambem_conta_texto_nao_bate_com_booleano(ambiente, monkeypatch, tmp_path):
    gravar_destino(tmp_path / ARQ_DESTINO, dict(IDENT, em_recuperacao="false"))
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_conn_ok()))
    with pytest.raises(pub.PublicacaoRecusada, match="alvo_neon.em_recuperacao$"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)


def test_campo_a_mais_ou_a_menos_no_arquivo_diverge(ambiente, monkeypatch, tmp_path):
    sem_modo = {k: v for k, v in IDENT.items() if k != "modo"}
    gravar_destino(tmp_path / ARQ_DESTINO, dict(sem_modo, extra="x"))
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_conn_ok()))
    with pytest.raises(pub.PublicacaoRecusada, match="alvo_neon.extra, alvo_neon.modo$"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "/var/run/postgresql", None, ""])
def test_conexao_local_e_modo_teste_e_nao_confere_com_arquivo_real(ambiente, monkeypatch, host):
    # o fallback localhost do config.py cai aqui: o arquivo aprovado real tem modo=real
    conn = Conn(host=host)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.PublicacaoRecusada, match="destino_divergente: alvo_neon.modo$"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert not any(s.startswith("DELETE") for s, _ in conn.sqls)


def test_modo_teste_confere_com_arquivo_de_teste(ambiente, monkeypatch, tmp_path):
    gravar_destino(tmp_path / ARQ_DESTINO, dict(IDENT, modo="teste"))
    conn = _conn_ok()
    conn.host, conn.ident = "127.0.0.1", dict(IDENT, modo="teste")
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    assert pub.run(apply=True, ref_date=REF, **CONTRATO)["applied"] is True


def test_identidade_igual_ao_helper_permissao_negada_vira_null_com_savepoint():
    conn = Conn()
    conn.negar = {"SELECT current_setting('neon.tenant_id', true)"}
    conn.pode_control = False
    with conn.cursor() as cur:
        r = pub.identidade_destino(cur)
    assert r == dict(IDENT, neon_tenant_id=None, system_identifier=None)
    sqls = [s for s, _ in conn.sqls]
    assert "ROLLBACK TO SAVEPOINT ml_full_ident" in sqls
    assert not any("FROM pg_catalog.pg_control_system()" in s for s in sqls)
    assert isinstance(r["server_version_num"], str) and r["em_recuperacao"] is False


def test_identidade_sem_alembic_e_system_identifier_em_texto():
    conn = Conn()
    conn.tem_alembic = False
    with conn.cursor() as cur:
        r = pub.identidade_destino(cur)
    assert r["alembic_version_num"] is None and r["system_identifier"] == IDENT["system_identifier"]
    assert not any("FROM public.alembic_version" in s for s, _ in conn.sqls)


def test_diagnosticar_nao_exige_destino_esperado(monkeypatch, capsys):
    conn = ConnLeitura()
    monkeypatch.setattr(pub, "local_engine", lambda: EngineLeitura(conn))
    codigo, j = _main_json(capsys, ["--diagnosticar", "--ref-date", str(REF), "--publish-run-id", PUB])
    assert codigo == 0 and j["modo"] == "diagnostico"


# ---------------------------------------------------------------------------
# R31-1: auditoria tambem so' no destino aprovado (contagem de TODAS as escritas)
# ---------------------------------------------------------------------------
def _escritas(*conns):
    """Comandos de escrita enviados (fatos via conn.execute + auditoria via cursor)."""
    return [s for c in conns for s, _ in c.sqls if ESCRITA.match(s)]


def _audit_real(monkeypatch, *conns):
    """Liga as funcoes REAIS da auditoria; cada `_audit_conn()` devolve a proxima conexao."""
    fila = list(conns)
    monkeypatch.setattr(pub, "audit_start", AUDIT_START)
    monkeypatch.setattr(pub, "audit_finish", AUDIT_FINISH)
    monkeypatch.setattr(pub, "_audit_conn", lambda: fila.pop(0) if fila else pytest.fail("conexao de audit a mais"))
    return fila


def _ok(conn):
    pac = pub.montar(fonte_barbours(), REF, "r")["esperado"]
    conn.destino = {"n": pac["inventarios"], "soma": pac["soma_disponivel"]}
    return conn


@pytest.mark.parametrize("campo,valor", [
    ("current_database", "outro_banco"), ("system_identifier", "7350000000000000999"),
    ("neon_tenant_id", "tenant-b"), ("modo", "teste")])
def test_destino_divergente_zero_escritas_em_fatos_e_auditoria_nao_iniciada(ambiente, monkeypatch, capsys,
                                                                           campo, valor):
    errado = dict(IDENT, **{campo: valor})
    host = "127.0.0.1" if campo == "modo" else "ep-x.sa-east-1.aws.neon.tech"     # modo vem do host real
    fato, aud1, aud2 = (Conn(ident=errado, host=host) for _ in range(3))
    eng = Engine(fato)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    sobra = _audit_real(monkeypatch, aud1, aud2)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 2 and j["estado_transacao"] == "nao_iniciada" and j["applied"] is False
    assert j["motivo"] == f"destino_divergente: alvo_neon.{campo}"
    # NENHUMA escrita em lugar nenhum: fatos (DELETE/INSERT) e auditoria (INSERT/UPDATE), nem COMMIT
    assert _escritas(fato, aud1, aud2) == [] and ambiente["inseridos"] == []
    assert eng.begins == 0 and eng.commits == 0 and aud1.commits == 0 and aud1.closes == 1
    assert sobra == [aud2]                                       # o audit_finish nem foi chamado
    assert any("current_database()" in s for s, _ in aud1.sqls)  # conferiu NA conexao do audit


def test_audit_confere_identidade_antes_do_insert_e_do_update(ambiente, monkeypatch, capsys):
    fato, aud1, aud2 = _ok(Conn()), Conn(), Conn()
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(fato))
    _audit_real(monkeypatch, aud1, aud2)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 0 and j["auditoria_ok"] is True and j["sync_run_id"] == 77
    for aud, comando in ((aud1, "INSERT"), (aud2, "UPDATE")):
        sqls = [s for s, _ in aud.sqls]
        ident = next(i for i, s in enumerate(sqls) if "current_database()" in s)
        escrita = next(i for i, s in enumerate(sqls) if ESCRITA.match(s))
        assert ident < escrita and sqls[escrita].lstrip().startswith(comando) and aud.commits == 1
    assert len(_escritas(aud1, aud2)) == 2


def test_divergencia_so_na_transacao_das_fatos_audit_finish_tambem_recusa(ambiente, monkeypatch, capsys):
    # audit_start viu o aprovado; o destino trocou antes da transacao das fatos e do audit_finish
    errado = dict(IDENT, system_identifier="7350000000000000999")
    fato, aud1, aud2 = Conn(ident=errado), Conn(), Conn(ident=errado)
    eng = Engine(fato)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    _audit_real(monkeypatch, aud1, aud2)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 2 and j["estado_transacao"] == "revertida_comprovada"
    assert j["motivo"] == "destino_divergente: alvo_neon.system_identifier"
    assert j["auditoria_ok"] is False and "destino_divergente" in j["erro_auditoria"]
    # escritas: so' o INSERT `running` no banco APROVADO; zero no divergente (fatos e UPDATE)
    assert [s.split()[0] for s in _escritas(aud1)] == ["INSERT"] and aud1.commits == 1
    assert _escritas(fato, aud2) == [] and ambiente["inseridos"] == []
    assert eng.commits == 0 and aud2.commits == 0


def test_divergencia_no_audit_finish_pos_commit_nao_grava_e_exit_5(ambiente, monkeypatch, capsys):
    fato, aud1, aud2 = _ok(Conn()), Conn(), Conn(ident=dict(IDENT, current_database="outro"))
    eng = Engine(fato)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    _audit_real(monkeypatch, aud1, aud2)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 5 and j["estado_transacao"] == "commit_confirmado" and eng.commits == 1
    assert j["auditoria_ok"] is False and "destino_divergente: alvo_neon.current_database" in j["erro_auditoria"]
    assert _escritas(aud2) == [] and aud2.commits == 0


# ---------------------------------------------------------------------------
# R31-2: identificador forte obrigatorio (arquivo E destino vivo)
# ---------------------------------------------------------------------------
NULOS_FORTES = {"system_identifier": None, "neon_tenant_id": None, "neon_timeline_id": None}


@pytest.mark.parametrize("fracos", [
    NULOS_FORTES,
    dict(NULOS_FORTES, neon_tenant_id="tenant-a"),          # par Neon incompleto
    dict(NULOS_FORTES, neon_timeline_id="timeline-a"),
    dict(NULOS_FORTES, system_identifier=""),
])
def test_arquivo_sem_identificador_forte_recusa_sem_conexao(ambiente, monkeypatch, tmp_path, capsys, fracos):
    gravar_destino(tmp_path / ARQ_DESTINO, dict(IDENT, **fracos))
    _sem_neon(monkeypatch, "abriu o Neon com arquivo sem identificador forte")
    monkeypatch.setattr(pub, "_audit_conn", lambda: pytest.fail("abriu conexao de auditoria"))
    monkeypatch.setattr(pub, "_ler_fonte", lambda *a: pytest.fail("leu o Data Mart"))
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 2 and j["estado_transacao"] == "nao_iniciada"
    assert j["motivo"].startswith("destino_nao_identificavel: o arquivo aprovado")
    assert ambiente["audit"] == [] and ambiente["inseridos"] == []


def test_dois_servidores_com_metadados_genericos_iguais_e_fortes_nulos_recusa(ambiente, monkeypatch, tmp_path,
                                                                            capsys):
    # servidor A (aprovado) e B (vivo): mesmo banco/versao/alembic/modo, nenhum id forte
    a = dict(IDENT, **NULOS_FORTES)
    b = Conn(ident=dict(a))
    assert pub.campos_divergentes(b.ident, a) == []              # pelos genericos, "bateriam"
    gravar_destino(tmp_path / ARQ_DESTINO, a)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(b))
    _audit_real(monkeypatch, Conn(ident=dict(a)))
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 2 and j["motivo"].startswith("destino_nao_identificavel")
    assert _escritas(b) == [] and ambiente["inseridos"] == []


def test_destino_vivo_sem_identificador_forte_recusa_no_audit_start(ambiente, monkeypatch, capsys):
    vivo = dict(IDENT, **NULOS_FORTES)
    fato, aud1 = Conn(ident=vivo), Conn(ident=vivo)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(fato))
    _audit_real(monkeypatch, aud1)
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 2 and j["estado_transacao"] == "nao_iniciada"
    assert j["motivo"] == f"destino_nao_identificavel: o destino nao expoe {pub.IDENT_FORTE_TEXTO}"
    assert _escritas(fato, aud1) == [] and aud1.commits == 0


def test_destino_vivo_sem_identificador_forte_recusa_na_transacao_das_fatos():
    conn = Conn(ident=dict(IDENT, **NULOS_FORTES))
    with pytest.raises(pub.PublicacaoRecusada, match="destino_nao_identificavel: o destino"):
        pub.conferir_destino(conn, IDENT)
    with pytest.raises(pub.PublicacaoRecusada, match="destino_nao_identificavel: o arquivo"):
        pub.conferir_destino(Conn(), dict(IDENT, **NULOS_FORTES))


def test_identificador_forte_regra():
    assert pub.identificador_forte(IDENT)
    assert pub.identificador_forte(dict(IDENT, system_identifier=None))
    assert pub.identificador_forte(dict(IDENT, neon_tenant_id=None, neon_timeline_id=None))
    assert not pub.identificador_forte(dict(IDENT, system_identifier=None, neon_timeline_id=None))
    assert not pub.identificador_forte({})


# ---------------------------------------------------------------------------
# R31-3: fonte coerente (regras do produtor) antes de qualquer escrita
# ---------------------------------------------------------------------------
def _sem_ponte(f):
    f["ponte"] = []
    return f


def _sem_snapshot_na_ponte(f):           # IB (M4) some da ponte; o snapshot ainda tem IB
    f["ponte"] = [p for p in f["ponte"] if p["inventory_id"] != "IB"]
    return f


def _status_fora_do_snapshot(f):
    f["status"].append(dict(f["status"][0], inventory_id="IZ"))
    return f


def _ponte_de_outro_seller(f):           # mesmo inventory_id, conta diferente: nao cobre
    f["ponte"] = [dict(p, seller_id="S9") if p["inventory_id"] == "IA" else p for p in f["ponte"]]
    return f


@pytest.mark.parametrize("adultera,trecho", [
    (_sem_ponte, "sem linha do run run-raw-barbours-1 em ponte"),
    (_sem_snapshot_na_ponte, "1 inventario(s) do snapshot fora da ponte"),
    (_ponte_de_outro_seller, "1 inventario(s) do snapshot fora da ponte"),
    (_status_fora_do_snapshot, "1 inventario(s) do status fora do snapshot"),
])
def test_fonte_incoerente_recusa_nao_iniciada_sem_escrita(ambiente, monkeypatch, capsys, adultera, trecho):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): adultera(fonte_barbours()))
    _sem_neon(monkeypatch, "tocou o Neon com fonte incoerente")
    monkeypatch.setattr(pub, "_audit_conn", lambda: pytest.fail("abriu conexao de auditoria"))
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 2 and j["estado_transacao"] == "nao_iniciada" and j["erro_classe"] == "PublicacaoRecusada"
    assert j["motivo"].startswith("fonte_incoerente: barbours") and trecho in j["motivo"]
    for inv in ("IA", "IB", "IZ", "S1", "S9"):                     # contagens, nunca ids
        assert inv not in j["motivo"].replace("barbours", "")
    assert ambiente["audit"] == [] and ambiente["inseridos"] == []


def test_fonte_incoerente_tambem_recusa_no_dry_run_com_raw_run(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): _sem_ponte(fonte_barbours()))
    _sem_neon(monkeypatch)
    with pytest.raises(pub.PublicacaoRecusada, match="fonte_incoerente"):
        pub.run(apply=False, ref_date=REF, marcas=["barbours"], raw_runs={"barbours": RAW})


def test_snapshot_vazio_do_run_e_incoerente():
    f = fonte_barbours()
    f["snapshot"] = []
    with pytest.raises(pub.PublicacaoRecusada, match="sem linha do run .* em snapshot"):
        pub.conferir_coerencia(f, {"barbours": RAW})


def test_status_vazio_e_legitimo_e_publica(ambiente, monkeypatch):
    # o produtor so' emite status para indisponivel > 0: dia sem indisponivel nao e' incoerente
    f = fonte_barbours()
    f["status"] = []
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): f)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_ok(Conn())))
    r = pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert r["applied"] is True and r["status_publicados"] == 0


def test_ponte_com_anuncio_sem_inventario_nao_conta_como_cobertura():
    f = fonte_barbours()
    f["ponte"] = [dict(p, inventory_id=None) for p in f["ponte"]]
    with pytest.raises(pub.PublicacaoRecusada, match="5 inventario\\(s\\) do snapshot fora da ponte"):
        pub.conferir_coerencia(f, {"barbours": RAW})


def test_ler_fonte_que_nao_fica_repeatable_read_falha_antes_de_ler_dado(monkeypatch):
    eventos, sqls = [], []

    class Conexao:
        def execute(self, sql, params=None):
            sqls.append(str(sql))
            return Res(linhas=[("read committed", "on")])

    class Sessao:
        conexao = Conexao()

        def connection(self):
            return self.conexao

        def rollback(self):
            eventos.append("rollback")

        def close(self):
            eventos.append("close")

    monkeypatch.setattr(pub, "DataMartSession", Sessao)
    with pytest.raises(fonte_mod.LeituraInconsistente):
        pub._ler_fonte(REF, (RAW,))
    assert sqls == [fonte_mod.SQL_LEITURA_CONSISTENTE, fonte_mod.SQL_CONFERE_LEITURA]
    assert eventos == ["rollback", "close"]
