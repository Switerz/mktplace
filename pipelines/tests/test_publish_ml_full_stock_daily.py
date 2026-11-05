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
MIG_038 = MIG.parent / "038_ml_full_inventario_anuncio_ancora.py"
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
             inicio=datetime(2026, 10, 20, 10, 20, tzinfo=timezone.utc), fim="padrao"):
    desfecho = desfecho or {m: "PUBLICADA" for m in marcas.split(",")}
    if fim == "padrao":           # o produtor grava status terminal E finished_at juntos (registrar_fim)
        fim = None if status == "running" else datetime(2026, 10, 20, 10, 50, tzinfo=timezone.utc)
    return {"run_id": run_id, "started_at": inicio, "finished_at": fim, "brands_requested": marcas,
            "status": status, "outcome_by_brand": desfecho}


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
        #: resposta da reconciliacao por marca: [(brand, inventarios_marca, linhas_inv, linhas_status)]
        self.por_marca = []
        self.ident, self.host = dict(IDENT) if ident is None else ident, host
        self.pode_control, self.tem_alembic, self.negar = True, True, set()
        self.connection = self
        self.commits = self.rollbacks = self.closes = 0

    def execute(self, sql, params=None):
        s = str(sql)
        self.sqls.append((s, params))
        if "pg_try_advisory_xact_lock" in s:
            return Res(escalar=self.lock)
        if "unnest(CAST(:marcas" in s:
            return Res(linhas=self.por_marca)
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


def _conn_ok(fonte=None, zero=frozenset()):
    return _ok(Conn(), fonte, zero)


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
    monkeypatch.setattr(pub, "montar", lambda f, d, rid, **kw: capturado.append(rid) or original(f, d, rid, **kw))
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
    assert ambiente["audit"][0] == ("start", 7) and ambiente["audit"][-1][0] == "success"   # 5 + IV1/IV2
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
    """Como a Connection legada do SQLAlchemy 1.4 (Airflow): sem `rollback()`/`commit()` proprios.

    A transacao e' a do `begin()`; `begin_em` guarda quantos comandos ja' tinham
    saido quando ela abriu (tem de ser 0).
    """

    def __init__(self, presentes=(True, True, True)):
        self.presentes, self.sqls, self.rollbacks, self.begin_em = presentes, [], 0, None

    def begin(self):
        conn = self
        conn.begin_em = len(self.sqls)

        class Transacao:
            def rollback(self):
                conn.rollbacks += 1

            def commit(self):
                pytest.fail("leitura commitou")

        return Transacao()

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


class EngineLeitura:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connect(self):
        yield self.conn

    def begin(self):
        pytest.fail("diagnostico abriu transacao de escrita")


def _assert_so_leitura(conn):
    assert conn.begin_em == 0 and conn.sqls[0] == "SET TRANSACTION READ ONLY" and conn.rollbacks == 1
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
    sql_038 = MIG_038.read_text(encoding="utf-8")
    for c in pub.COLS_INV + pub.COLS_MARCA:
        assert c in sql or f"ADD COLUMN {c} " in sql_038, c


# ---------------------------------------------------------------------------
# T-0018: anuncio-ancora por inventario (migration 038)
# ---------------------------------------------------------------------------
class _OpGravador:
    def __init__(self):
        self.sqls = []

    def execute(self, sql):
        self.sqls.append(sql)


def _mig_038():
    import types
    op = _OpGravador()
    spec = importlib.util.spec_from_file_location("m038", MIG_038)
    m = importlib.util.module_from_spec(spec)
    antigo = sys.modules.get("alembic")
    sys.modules["alembic"] = types.SimpleNamespace(op=op)
    try:
        spec.loader.exec_module(m)
    finally:
        if antigo is None:
            sys.modules.pop("alembic", None)
        else:
            sys.modules["alembic"] = antigo
    return m, op


def test_migration_038_tres_colunas_nulas_sem_default_e_downgrade_inverso():
    m, op = _mig_038()
    assert (m.revision, m.down_revision) == ("038", "037")
    m.upgrade()
    up = op.sqls[:]
    assert len(up) == 3 and all(s.startswith(f"ALTER TABLE {pub.FACT_INVENTARIO} ADD COLUMN ") for s in up)
    for col, sql in zip(m.COLUNAS, up):
        assert f"ADD COLUMN {col} " in sql and " NULL " in sql
        assert "DEFAULT" not in sql.upper() and "NOT NULL" not in sql.upper()
    assert m.COLUNAS == pub.COLS_INV[-3:]
    op.sqls.clear()
    m.downgrade()
    assert op.sqls == [f"ALTER TABLE {pub.FACT_INVENTARIO} DROP COLUMN IF EXISTS {c}"
                       for c in reversed(m.COLUNAS)]
    for sql in up + op.sqls:                       # so' DDL: nenhum dado e' escrito
        assert not ESCRITA.match(sql), sql


def _anuncio(item, var="0", lt="fulfillment", st="active"):
    return {"item_id": item, "variation_id": var, "logistic_type": lt, "listing_status": st}


def test_ancora_full_ativo_vence_full_inativo_que_vence_nao_full():
    from pipelines.transforms import ml_full_stock as T
    nao_full = _anuncio("A1", lt="cross_docking")
    full_inativo = _anuncio("B2", st="paused")
    full_ativo = _anuncio("C3")
    assert T.ancora([nao_full, full_inativo, full_ativo])["anuncio_ancora_item_id"] == "C3"
    assert T.ancora([nao_full, full_inativo])["anuncio_ancora_item_id"] == "B2"
    assert T.ancora([nao_full])["anuncio_ancora_item_id"] == "A1"
    # nao-Full ATIVO nao vence Full inativo
    assert T.ancora([_anuncio("A0", lt="cross_docking"), full_inativo])["anuncio_ancora_item_id"] == "B2"
    # a ordem de entrada nao muda o ancora
    assert T.ancora([full_ativo, full_inativo, nao_full]) == T.ancora([nao_full, full_ativo, full_inativo])


def test_ancora_empate_por_item_e_depois_variacao_como_texto():
    from pipelines.transforms import ml_full_stock as T
    r = T.ancora([_anuncio("MLB20"), _anuncio("MLB100")])
    assert r["anuncio_ancora_item_id"] == "MLB100"            # texto: "MLB100" < "MLB20"
    r = T.ancora([_anuncio("MLB1", var=9), _anuncio("MLB1", var=10)])
    assert (r["anuncio_ancora_item_id"], r["anuncio_ancora_variation_id"]) == ("MLB1", "10")
    assert r["anuncios_extras"] == 1
    sem_var = T.ancora([_anuncio("MLB1", var=0)])
    assert sem_var["anuncio_ancora_variation_id"] is None and sem_var["anuncios_extras"] == 0


def test_ancora_sem_ponte_e_nula_com_zero_extras_e_extras_contam_distintos():
    from pipelines.transforms import ml_full_stock as T
    from pipelines.tests.test_sync_ml_full_stock_daily import snap
    assert T.ancora([]) == {"anuncio_ancora_item_id": None, "anuncio_ancora_variation_id": None,
                            "anuncios_extras": 0}
    # linha duplicada na ponte nao vira anuncio extra
    assert T.ancora([_anuncio("X"), _anuncio("X")])["anuncios_extras"] == 0
    # inventario do snapshot sem ponte: so' aparece no transform puro (a coerencia recusa no apply)
    linhas, _ = T.indicadores([snap("b", "SEM-PONTE", 3)], [], [], {})
    assert (linhas[0]["anuncio_ancora_item_id"], linhas[0]["anuncios_extras"]) == (None, 0)


def test_montar_uma_linha_por_inventario_e_soma_igual_com_ancora():
    f = fonte_barbours()
    pac = pub.montar(f, REF, "r")
    invs = {(r["seller_id"], r["inventory_id"]) for r in f["snapshot"]}
    assert len(pac["inventario"]) == len(invs) == pac["esperado"]["inventarios"]
    assert all(len(r) == len(pub.COLS_INV) for r in pac["inventario"])
    i = {c: n for n, c in enumerate(pub.COLS_INV)}
    soma = sum(r[i["disponivel"]] or 0 for r in pac["inventario"] if r[i["read_status"]] == "OK")
    # mesma soma de antes da T-0018 (test_montar_status_so_de_inventario_lido_e_estoque_uma_vez)
    assert soma == pac["esperado"]["soma_disponivel"] == 56 + 0 + 30 + 500
    linha = {r[i["inventory_id"]]: r for r in pac["inventario"]}
    # IA: M1/M2 Full ativos e M3 fechado/nao-Full -> M1; 3 anuncios -> 2 extras
    assert (linha["IA"][i["anuncio_ancora_item_id"]], linha["IA"][i["anuncios_extras"]]) == ("M1", 2)
    assert linha["IV1"][i["anuncio_ancora_variation_id"]] == "11"
    assert linha["ID"][i["anuncio_ancora_item_id"]] == "M6"      # so' nao-Full: "qualquer"


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


def _ok(conn, fonte=None, zero=frozenset()):
    """Destino que devolve exatamente o pacote: totais e (inventarios, linhas inv, linhas status) por marca."""
    pac = pub.montar(fonte or fonte_barbours(), REF, "r", zero=zero)["esperado"]
    conn.destino = {"n": pac["inventarios"], "soma": pac["soma_disponivel"]}
    conn.por_marca = [(m,) + v for m, v in sorted(pac["por_marca"].items())]
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


def _ponte_fora_do_snapshot(f):          # o produtor le TODO inventory_id nao nulo da ponte (L424)
    f["ponte"].append(dict(f["ponte"][0], item_id="M9", inventory_id="IZ"))
    return f


def _variacoes_sem_snapshot(f):          # a fixture antiga: IV1/IV2 na ponte e fora do snapshot
    f["snapshot"] = [s for s in f["snapshot"] if s["inventory_id"] not in ("IV1", "IV2")]
    return f


def _read_status_contrato_quebrado(f):   # coleta incompleta: o produtor nao publica a marca (L438-440)
    f["snapshot"] = [dict(s, read_status="CONTRATO_QUEBRADO") if s["inventory_id"] == "IB" else s
                     for s in f["snapshot"]]
    return f


def _status_de_inventario_nao_encontrado(f):   # status so' nasce de leitura OK (L443-449)
    f["status"].append(dict(f["status"][0], inventory_id="IC", status_raw="lost"))
    return f


def _snapshot_duplicado(f):              # uma linha por inventario distinto (L424)
    f["snapshot"].append(dict(f["snapshot"][0]))
    return f


@pytest.mark.parametrize("adultera,trecho", [
    (_sem_ponte, "sem linha do run run-raw-barbours-1 em ponte"),
    (_sem_snapshot_na_ponte, "1 inventario(s) do snapshot fora da ponte"),
    (_ponte_de_outro_seller, "1 inventario(s) do snapshot fora da ponte"),
    (_ponte_fora_do_snapshot, "1 inventario(s) da ponte fora do snapshot"),
    (_variacoes_sem_snapshot, "2 inventario(s) da ponte fora do snapshot"),
    (_read_status_contrato_quebrado, "1 inventario(s) do snapshot com read_status fora de"),
    (_status_fora_do_snapshot, "1 inventario(s) do status fora do snapshot"),
    (_status_de_inventario_nao_encontrado, "1 inventario(s) do status sem leitura OK no snapshot"),
    (_snapshot_duplicado, "1 linha(s) duplicada(s) no snapshot"),
])
def test_fonte_incoerente_recusa_nao_iniciada_sem_escrita(ambiente, monkeypatch, capsys, adultera, trecho):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): adultera(fonte_barbours()))
    _sem_neon(monkeypatch, "tocou o Neon com fonte incoerente")
    monkeypatch.setattr(pub, "_audit_conn", lambda: pytest.fail("abriu conexao de auditoria"))
    codigo, j = _main_json(capsys, ARGV_APPLY)
    assert codigo == 2 and j["estado_transacao"] == "nao_iniciada" and j["erro_classe"] == "PublicacaoRecusada"
    assert j["motivo"].startswith("fonte_incoerente: barbours") and trecho in j["motivo"]
    for inv in ("IA", "IB", "IC", "IV1", "IZ", "M9", "S1", "S9"):   # contagens, nunca ids
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
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_ok(Conn(), f)))
    r = pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert r["applied"] is True and r["status_publicados"] == 0


def test_ponte_com_anuncio_sem_inventario_nao_conta_como_cobertura():
    f = fonte_barbours()
    f["ponte"] = [dict(p, inventory_id=None) for p in f["ponte"]]
    with pytest.raises(pub.PublicacaoRecusada, match="7 inventario\\(s\\) do snapshot fora da ponte"):
        pub.conferir_coerencia(f, {"barbours": RAW})


def test_fixture_do_cenario_e_fiel_ao_produtor():
    # A fixture tem de satisfazer as invariantes do produtor; nao o contrario.
    f = fonte_barbours()
    pub.conferir_coerencia(f, {"barbours": RAW})
    snap = {s["inventory_id"] for s in f["snapshot"]}
    assert snap == {p["inventory_id"] for p in f["ponte"] if p["inventory_id"]}
    assert {s["read_status"] for s in f["snapshot"]} == {pub.LEITURA_OK, pub.LEITURA_NAO_ENCONTRADO}


def test_ponte_com_inventory_id_nulo_e_legitima_e_publica(ambiente, monkeypatch):
    # anuncio sem inventario: o produtor grava a linha da ponte e nao le snapshot dela (L424)
    f = fonte_barbours()
    assert any(p["inventory_id"] is None for p in f["ponte"])
    f["ponte"] += [dict(f["ponte"][0], item_id="M10", inventory_id=None),
                   dict(f["ponte"][0], item_id="M11", inventory_id="")]
    pub.conferir_coerencia(f, {"barbours": RAW})
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): f)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(_ok(Conn())))
    r = pub.run(apply=True, ref_date=REF, **CONTRATO)
    assert r["applied"] is True and r["inventarios_publicados"] == 7


def test_status_vazio_e_coerente():
    f = fonte_barbours()
    f["status"] = []
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


# ---------------------------------------------------------------------------
# T-0052: Full zero comprovado (marca PUBLICADA pelo produtor com 0 inventarios)
# ---------------------------------------------------------------------------
QUATRO = "barbours,kokeshi,lescent,rituaria"


def ponte_sem_inventario(marca, item="MZ1", seller="SK", run_id=RAW):
    # o produtor grava o anuncio sem inventario na ponte e nao le snapshot dele (L424)
    return {"brand": marca, "seller_id": seller, "item_id": item, "variation_id": "0", "inventory_id": None,
            "logistic_type": "cross_docking", "listing_status": "active", "run_id": run_id}


def fonte_com_zero(ponte_zero=True, reg=None):
    """barbours com fotografia + kokeshi PUBLICADA com 0 inventarios, mesmo run."""
    f = fonte_barbours(runs={RAW: reg or registro(marcas="barbours,kokeshi")})
    if ponte_zero:
        f["ponte"].append(ponte_sem_inventario("kokeshi"))
    return f


def _run_zero(ambiente, monkeypatch, fonte, marcas, conn=None, apply=True):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte)
    zero = frozenset(m for m in marcas if not any(s["brand"] == m for s in fonte["snapshot"]))
    conn = conn or _ok(Conn(), pub.filtrar_fonte(fonte, marcas)[0], zero)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    kw = dict(marcas=marcas, raw_runs={m: RAW for m in marcas})
    if apply:
        kw.update(publish_run_id=PUB, destino_esperado=ARQ_DESTINO)
    return pub.run(apply=apply, ref_date=REF, **kw), conn


def test_full_zero_comprovado_publica_linha_zero_na_fato_de_marca_e_nada_mais(ambiente, monkeypatch):
    r, conn = _run_zero(ambiente, monkeypatch, fonte_com_zero(), ["barbours", "kokeshi"])
    assert r["applied"] is True and r["marcas_publicadas"] == ["barbours", "kokeshi"]
    assert r["marcas_full_zero_publicadas"] == ["kokeshi"] == r["marcas_full_zero_comprovado"]
    assert r["por_marca"]["kokeshi"] == {"estado": "full_zero_comprovado", "inventarios": 0,
                                         "evidencia_conta": pub.CONTA_UNICA_NA_PONTE}
    assert r["inventarios_publicados"] == 7                         # so' os da barbours
    deletes = [p for s, p in conn.sqls if s.startswith("DELETE")]
    assert len(deletes) == 3 and all(p["marcas"] == ["barbours", "kokeshi"] for p in deletes)
    pac = pub.montar(pub.filtrar_fonte(fonte_com_zero(), ["barbours", "kokeshi"])[0], REF, PUB,
                     zero=frozenset({"kokeshi"}))
    assert {x[1] for x in pac["inventario"]} == {"barbours"} and {x[1] for x in pac["status"]} == {"barbours"}
    zero = dict(zip(pub.COLS_MARCA, next(x for x in pac["marca"] if x[1] == "kokeshi")))
    assert zero == {"ref_date": REF, "brand": "kokeshi", "inventarios": 0, "inventarios_full_lidos": 0,
                    "inventarios_compartilhados": 0, "disponivel_full": None, "nao_disponivel_full": None,
                    "transfer_full": None, "unidades_28d_alocadas": 0, "unidades_28d_item_varios_inventarios": 0,
                    "unidades_28d_sem_inventory_id": 0, "unidades_28d_fora_da_ponte": 0,
                    "cobertura_da_alocacao": None, "source_run_id": PUB}
    assert pac["esperado"]["por_marca"]["kokeshi"] == (0, 0, 0)


def test_full_zero_sem_ponte_nao_prova_a_conta_e_recusa_sem_nenhuma_escrita(ambiente, monkeypatch):
    # R103: run PUBLICADA com 0 linhas e sem ponte nao prova a conta -> ausente, nunca zero
    f = {"snapshot": [], "status": [], "ponte": [], "vendas": {}, "runs": {RAW: registro(marcas="kokeshi")}}
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): f)
    _sem_neon(monkeypatch, "tocou o Neon (DELETE/INSERT) sem prova de conta")
    monkeypatch.setattr(pub, "_audit_conn", lambda: pytest.fail("abriu conexao de auditoria"))
    with pytest.raises(pub.PublicacaoRecusada) as e:
        pub.run(apply=True, ref_date=REF, marcas=["kokeshi"], raw_runs={"kokeshi": RAW}, publish_run_id=PUB,
                destino_esperado=ARQ_DESTINO)
    assert str(e.value).startswith(
        f"marca_pedida_sem_fotografia: kokeshi em {REF}; full zero nao comprovado: conta_sem_evidencia_no_raw")
    assert ambiente["audit"] == [] and ambiente["inseridos"] == []      # sem audit_start, sem INSERT
    with pytest.raises(pub.PublicacaoRecusada, match="conta_sem_evidencia_no_raw"):   # dry-run: mesma recusa
        pub.run(apply=False, ref_date=REF, marcas=["kokeshi"], raw_runs={"kokeshi": RAW})


def test_todas_as_marcas_full_zero_total_zero(ambiente, monkeypatch):
    marcas = QUATRO.split(",")
    f = {"snapshot": [], "status": [], "vendas": {}, "runs": {RAW: registro(marcas=QUATRO)},
         "ponte": [ponte_sem_inventario(m, seller=f"S{i}") for i, m in enumerate(marcas)]}   # 4 contas
    r, _ = _run_zero(ambiente, monkeypatch, f, marcas)
    assert r["applied"] is True and r["marcas_full_zero_publicadas"] == marcas
    assert r["evidencia_conta_full_zero"] == {m: pub.CONTA_UNICA_NA_PONTE for m in marcas}
    assert r["inventarios_publicados"] == 0 and r["disponivel_publicado"] == 0 and r["estado"] == "medido"
    assert [t for t, _ in ambiente["inseridos"]] == [pub.FACT_MARCA]     # 4 linhas zero, so' a fato de marca
    assert ambiente["inseridos"][0][1] == 4 and ambiente["marcas_inseridas"] == set(marcas)


def test_full_zero_com_venda_na_janela_fica_como_nao_alocada():
    f = {"snapshot": [], "status": [], "ponte": [ponte_sem_inventario("kokeshi")], "vendas":
         {("kokeshi", "MZ1", "0"): 3, ("kokeshi", "MQ", "0"): 2}}
    pac = pub.montar(f, REF, PUB, zero=frozenset({"kokeshi"}))
    linha = dict(zip(pub.COLS_MARCA, pac["marca"][0]))
    assert linha["inventarios"] == 0 and linha["disponivel_full"] is None and linha["unidades_28d_alocadas"] == 0
    assert (linha["unidades_28d_sem_inventory_id"], linha["unidades_28d_fora_da_ponte"]) == (3, 2)
    assert linha["cobertura_da_alocacao"] == 0 and pac["inventario"] == [] and pac["status"] == []


def _zero(reg=None, ponte=None):
    """So' kokeshi pedida, sem snapshot; `reg` e `ponte` adulteram a prova."""
    return {"snapshot": [], "status": [], "vendas": {},
            "ponte": [ponte_sem_inventario("kokeshi")] if ponte is None else ponte,
            "runs": {RAW: registro(marcas="kokeshi")} if reg is None else reg}


@pytest.mark.parametrize("fonte,trecho", [
    (lambda: _zero({RAW: registro(marcas="kokeshi", desfecho={"kokeshi": "INCOMPLETA_NAO_PUBLICADA"})}),
     "run_raw_nao_publicado"),                                              # coleta incompleta (400/paginacao)
    (lambda: _zero({RAW: registro(marcas="kokeshi", desfecho={"kokeshi": "PERMISSAO_NEGADA"})}),
     "run_raw_nao_publicado"),                                              # 403
    (lambda: _zero({RAW: registro(marcas="kokeshi", desfecho={"barbours": "PUBLICADA"})}),
     "run_raw_nao_publicado"),                                              # desfecho ausente
    (lambda: _zero({RAW: dict(registro(marcas="kokeshi"), outcome_by_brand="nao-json")}),
     "run_raw_nao_publicado"),                                              # desfecho mal formado
    (lambda: _zero({RAW: registro(marcas="kokeshi", status="running")}), "run_raw_nao_terminado"),
    (lambda: _zero({RAW: registro(marcas="kokeshi", fim=None)}), "run_raw_nao_terminado"),
    (lambda: _zero({RAW: registro(marcas="kokeshi", status="parcial", fim=None)}), "run_raw_nao_terminado"),
    (lambda: _zero({RAW: registro(marcas="kokeshi", inicio=datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc))}),
     "run_raw_de_outra_data"),
    (lambda: _zero({RAW: registro(marcas="barbours")}), "run_raw_de_outra_marca"),
    (lambda: _zero({}), "run_raw_nao_registrado"),                          # ausencia
    (lambda: _zero(ponte=[ponte_sem_inventario("kokeshi", run_id="run-novo")]), "linhagem_divergente"),
    (lambda: _zero(ponte=[]), "conta_sem_evidencia_no_raw"),               # sem anuncio: conta nao provada
    (lambda: _zero(ponte=[ponte_sem_inventario("kokeshi"), ponte_sem_inventario("kokeshi", "MZ2", "SK2")]),
     "conta_ambigua: 2 contas"),
    (lambda: dict(_zero(), snapshot=fonte_marca("barbours", "run-b")["snapshot"],
                  ponte=[ponte_sem_inventario("kokeshi", seller="S1")]),
     "conta_em_outra_marca"),                                               # conta da barbours (nao pedida)
])
def test_marca_sem_fotografia_sem_prova_de_zero_recusa_sem_escrever(ambiente, monkeypatch, fonte, trecho):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte())
    _sem_neon(monkeypatch, "tocou o Neon com zero nao comprovado")
    with pytest.raises(pub.PublicacaoRecusada) as e:
        pub.run(apply=True, ref_date=REF, marcas=["kokeshi"], raw_runs={"kokeshi": RAW}, publish_run_id=PUB,
                destino_esperado=ARQ_DESTINO)
    msg = str(e.value)
    assert msg.startswith(f"marca_pedida_sem_fotografia: kokeshi em {REF}; full zero nao comprovado: ")
    assert trecho in msg
    assert "SK" not in msg and "S1" not in msg                              # contagens, nunca ids de conta
    assert ambiente["audit"] == [] and ambiente["inseridos"] == []


def test_zero_com_ponte_que_tem_inventario_e_incoerente(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): _zero(ponte=[
        dict(ponte_sem_inventario("kokeshi"), inventory_id="IZ")]))
    _sem_neon(monkeypatch)
    with pytest.raises(pub.PublicacaoRecusada, match=r"fonte_incoerente: kokeshi tem 1 inventario\(s\) da ponte"):
        pub.run(apply=True, ref_date=REF, marcas=["kokeshi"], raw_runs={"kokeshi": RAW}, publish_run_id=PUB,
                destino_esperado=ARQ_DESTINO)


def test_marca_sem_prova_derruba_a_invocacao_inteira_e_nao_grava_a_outra(ambiente, monkeypatch):
    # tudo-ou-nada POR INVOCACAO continua: o isolamento e' uma invocacao por marca
    reg = registro(marcas="barbours,kokeshi", status="parcial",
                   desfecho={"barbours": "PUBLICADA", "kokeshi": "INCOMPLETA_NAO_PUBLICADA"})
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte_com_zero(reg=reg))
    _sem_neon(monkeypatch)
    with pytest.raises(pub.PublicacaoRecusada, match="kokeshi .*run_raw_nao_publicado"):
        pub.run(apply=True, ref_date=REF, marcas=["barbours", "kokeshi"],
                raw_runs={"barbours": RAW, "kokeshi": RAW}, publish_run_id=PUB, destino_esperado=ARQ_DESTINO)
    assert ambiente["inseridos"] == []


def test_mesmo_run_parcial_a_marca_pronta_publica_sozinha(ambiente, monkeypatch):
    reg = registro(marcas="barbours,kokeshi", status="parcial",
                   desfecho={"barbours": "PUBLICADA", "kokeshi": "INCOMPLETA_NAO_PUBLICADA"})
    r, conn = _run_zero(ambiente, monkeypatch, fonte_com_zero(reg=reg), ["barbours"])
    assert r["applied"] is True and r["marcas_publicadas"] == ["barbours"]
    assert all(p["marcas"] == ["barbours"] for s, p in conn.sqls if s.startswith("DELETE"))


def test_dry_run_sem_raw_run_nao_comprova_zero(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): _zero())
    _sem_neon(monkeypatch)
    with pytest.raises(pub.PublicacaoRecusada, match="sem --raw-run o full zero nao e' comprovavel"):
        pub.run(apply=False, ref_date=REF, marcas=["kokeshi"])


def test_dry_run_com_raw_run_mostra_o_zero_comprovado_sem_tocar_o_neon(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): _zero())
    _sem_neon(monkeypatch)
    r = pub.run(apply=False, ref_date=REF, marcas=["kokeshi"], raw_runs={"kokeshi": RAW})
    assert r["applied"] is False and r["por_marca"]["kokeshi"]["estado"] == "full_zero_comprovado"


def test_reconciliacao_sem_a_linha_zero_no_destino_aborta(ambiente, monkeypatch):
    conn = _ok(Conn(), pub.filtrar_fonte(_zero(), ["kokeshi"])[0], frozenset({"kokeshi"}))
    conn.por_marca = [("kokeshi", None, 0, 0)]                     # a linha da fato de marca nao ficou
    eng = Engine(conn)
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): _zero())
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    with pytest.raises(pub.ReconciliacaoFalhou, match="kokeshi") as e:
        pub.run(apply=True, ref_date=REF, marcas=["kokeshi"], raw_runs={"kokeshi": RAW}, publish_run_id=PUB,
                destino_esperado=ARQ_DESTINO)
    assert e.value.estado_transacao == pub.ESTADO_REVERTIDA and eng.commits == 0


def test_reconciliacao_por_marca_pega_divergencia_de_status(ambiente, monkeypatch):
    conn = _conn_ok()
    conn.por_marca = [("barbours", 7, 7, 0)]
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.ReconciliacaoFalhou, match="barbours"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)


def test_run_terminado_sem_finished_at_recusa_tambem_marca_com_fotografia(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref, runs=(): fonte_barbours(runs={RAW: registro(fim=None)}))
    _sem_neon(monkeypatch)
    with pytest.raises(pub.PublicacaoRecusada, match="run_raw_nao_terminado.*finished_at=nulo"):
        pub.run(apply=True, ref_date=REF, **CONTRATO)


# ---------------------------------------------------------------------------
# T-0052 em PostgreSQL REAL descartavel: RAW do airflow + fatos da 029 + auditoria
#
#     docker run --rm -d --name pg-mlfull-zero -p 127.0.0.1:55452:5432 \
#         -e POSTGRES_PASSWORD=postgres postgres:16
#     ML_FULL_ZERO_TEST_DSN=postgresql://postgres:postgres@127.0.0.1:55452/postgres \
#         python -m pytest pipelines/tests/test_publish_ml_full_stock_daily.py -q -k pg_
#
# Sem a variavel: SKIP. A DSN tem de ser local (barreira do conftest + checagem
# abaixo). O MESMO banco faz de Data Mart (schema raw/api) e de Neon (marts/audit).
# ---------------------------------------------------------------------------
import os  # noqa: E402

PG_DSN = os.environ.get("ML_FULL_ZERO_TEST_DSN")
sem_pg = pytest.mark.skipif(not PG_DSN, reason="ML_FULL_ZERO_TEST_DSN ausente - exige PostgreSQL descartavel")

#: DDL RAW copiada de airflow `src/mercadolivre/schema.sql` (T-0050 61467656), so' as 4 tabelas.
DDL_RAW = """
CREATE TABLE raw.ml_full_stock_runs (
    run_id VARCHAR(36) PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
    brands_requested VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('running', 'completo', 'parcial')),
    stop_reason VARCHAR(48), outcome_by_brand JSONB, gets_by_endpoint JSONB);
CREATE TABLE raw.ml_full_inventory_snapshot (
    brand VARCHAR(50) NOT NULL, seller_id VARCHAR(30) NOT NULL, inventory_id VARCHAR(60) NOT NULL,
    ref_date DATE NOT NULL, captured_at TIMESTAMPTZ NOT NULL,
    read_status VARCHAR(24) NOT NULL CHECK (read_status IN ('OK', 'NAO_ENCONTRADO', 'CONTRATO_QUEBRADO')),
    available_quantity INTEGER CHECK (available_quantity >= 0),
    not_available_quantity INTEGER CHECK (not_available_quantity >= 0),
    total_quantity INTEGER CHECK (total_quantity >= 0), total_coerente BOOLEAN, detalhe_coerente BOOLEAN,
    run_id VARCHAR(36) NOT NULL,
    CONSTRAINT uk_ml_full_inventory_snapshot UNIQUE (brand, seller_id, inventory_id, ref_date),
    CONSTRAINT ck_ml_full_snapshot_ok_tem_quantidade CHECK (
        (read_status = 'OK' AND available_quantity IS NOT NULL AND not_available_quantity IS NOT NULL)
        OR (read_status <> 'OK' AND available_quantity IS NULL AND not_available_quantity IS NULL
            AND total_quantity IS NULL)));
CREATE TABLE raw.ml_full_inventory_unavailable (
    brand VARCHAR(50) NOT NULL, seller_id VARCHAR(30) NOT NULL, inventory_id VARCHAR(60) NOT NULL,
    ref_date DATE NOT NULL, status_raw VARCHAR(40) NOT NULL, quantity INTEGER NOT NULL CHECK (quantity >= 0),
    run_id VARCHAR(36) NOT NULL,
    CONSTRAINT uk_ml_full_inventory_unavailable UNIQUE (brand, seller_id, inventory_id, ref_date, status_raw));
CREATE TABLE raw.ml_full_inventory_listing (
    brand VARCHAR(50) NOT NULL, seller_id VARCHAR(30) NOT NULL, item_id VARCHAR(30) NOT NULL,
    variation_id VARCHAR(30) NOT NULL, inventory_id VARCHAR(60), user_product_id VARCHAR(60),
    has_sku BOOLEAN NOT NULL, logistic_type VARCHAR(40), listing_status VARCHAR(30), ref_date DATE NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL, run_id VARCHAR(36) NOT NULL,
    CONSTRAINT uk_ml_full_inventory_listing UNIQUE (brand, seller_id, item_id, variation_id, ref_date));
"""
#: So' as colunas que `SQL_VENDAS_FULL` e a auditoria tocam (vazias: sem venda na janela).
DDL_APOIO = """
CREATE TABLE api.ml_orders (brand TEXT, order_id TEXT, shipping_id TEXT, status TEXT, date_created TIMESTAMPTZ);
CREATE TABLE api.ml_shipments (brand TEXT, shipment_id TEXT, logistic_type TEXT, updated_at TIMESTAMPTZ);
CREATE TABLE api.ml_order_line_items (brand TEXT, order_id TEXT, item_id TEXT, variation_id BIGINT,
                                      quantity INTEGER);
CREATE TABLE audit.source_sync_run (
    sync_run_id SERIAL PRIMARY KEY, source_name TEXT, marketplace_id INTEGER, loja_id INTEGER, status TEXT,
    started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, rows_extracted INTEGER, rows_loaded INTEGER,
    error_message TEXT);
"""
D, D2, D3, D4, ANTES = (date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23),
                        date(2026, 10, 19))


def _inicio(dia):           # 10:20 UTC = 07:20 em Sao Paulo, mesmo dia
    return datetime(dia.year, dia.month, dia.day, 10, 20, tzinfo=timezone.utc)


@pytest.fixture
def pg(monkeypatch, tmp_path):
    if not PG_DSN:
        pytest.skip("ML_FULL_ZERO_TEST_DSN ausente")
    from urllib.parse import urlparse
    assert urlparse(PG_DSN).hostname in ("localhost", "127.0.0.1", "::1"), "DSN do teste tem de ser local"
    from sqlalchemy import create_engine, text as sql
    from sqlalchemy.orm import sessionmaker
    eng = create_engine(PG_DSN)
    with eng.begin() as c:
        c.execute(sql("DROP SCHEMA IF EXISTS raw, marts, api, audit CASCADE"))
        c.execute(sql("CREATE SCHEMA raw; CREATE SCHEMA marts; CREATE SCHEMA api; CREATE SCHEMA audit"))
        c.execute(sql(DDL_RAW))
        c.execute(sql(DDL_APOIO))
        for ddl in _mig()._upgrade_sql():
            c.execute(sql(ddl))
        # T-0018: o publicador grava as colunas do anuncio-ancora, entao o destino
        # deste teste e' 029 + 038, como o Neon depois do `alembic upgrade 038`.
        # A 037 (T-0059, CHECK da marts.fact_dre_insumo_mensal) nao toca estas
        # tabelas e fica de fora: o teste so' aplica o que o publicador le.
        m038, op038 = _mig_038()
        m038.upgrade()
        for ddl in op038.sqls:
            c.execute(sql(ddl))
        # publicacao anterior de OUTRO dia, para provar que nada fora do pedido muda
        for m in QUATRO.split(","):
            c.execute(sql(f"INSERT INTO {pub.FACT_MARCA} (ref_date, brand, inventarios, inventarios_full_lidos, "
                          "inventarios_compartilhados, disponivel_full, nao_disponivel_full, transfer_full, "
                          "unidades_28d_alocadas, source_run_id) VALUES (:d, :m, 1, 1, 0, 3, 0, 0, 0, 'antigo')"),
                      {"d": ANTES, "m": m})
            c.execute(sql(f"INSERT INTO {pub.FACT_INVENTARIO} VALUES (:d, :m, 'S0', 'I0', 'OK', 3, 0, 0, true, "
                          "true, true, 1, 1, 1, 0, 0, NULL, 'SEM_GIRO_CANDIDATO', 'SEM_VENDA_NA_JANELA', "
                          "'antigo', now())"), {"d": ANTES, "m": m})
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    monkeypatch.setattr(pub, "LocalSession", sessionmaker(bind=eng))
    monkeypatch.setattr(pub, "DataMartSession", sessionmaker(bind=eng))
    monkeypatch.setenv(pub.FLAG_AMBIENTE, "true")
    monkeypatch.chdir(tmp_path)
    bruta = eng.raw_connection()
    try:
        with bruta.cursor() as cur:
            ident = pub.identidade_destino(cur)                      # o `-Identificar` deste banco
    finally:
        bruta.close()
    assert ident["modo"] == "teste" and ident["system_identifier"]
    gravar_destino(tmp_path / ARQ_DESTINO, ident)
    yield eng
    eng.dispose()


def _exec(eng, q, **p):
    from sqlalchemy import text as sql
    with eng.begin() as c:
        return c.execute(sql(q), p)


def _run_raw(eng, rid, dia, marcas, status="completo", desfecho=None, fim=True):
    desfecho = {m: "PUBLICADA" for m in marcas} if desfecho is None else desfecho
    _exec(eng, "INSERT INTO raw.ml_full_stock_runs (run_id, started_at, finished_at, brands_requested, status, "
               "outcome_by_brand) VALUES (:r, :i, CASE WHEN :f THEN :i + interval '30 minutes' END, :b, :s, "
               "CAST(:o AS jsonb))",
          r=rid, i=_inicio(dia), f=fim, b=",".join(marcas), s=status, o=json.dumps(desfecho))


def _inventario(eng, rid, dia, marca, seller, inv, disp, nao=0, transfer=0):
    _exec(eng, "INSERT INTO raw.ml_full_inventory_snapshot VALUES (:m, :s, :i, :d, :c, 'OK', :a, :n, :t, true, "
               "true, :r)", m=marca, s=seller, i=inv, d=dia, c=_inicio(dia), a=disp, n=nao, t=disp + nao, r=rid)
    _ponte(eng, rid, dia, marca, seller, "M" + inv, inv, "fulfillment")
    if transfer:
        _exec(eng, "INSERT INTO raw.ml_full_inventory_unavailable VALUES (:m, :s, :i, :d, 'transfer', :qt, :r)",
              m=marca, s=seller, i=inv, d=dia, qt=transfer, r=rid)


def _ponte(eng, rid, dia, marca, seller, item, inv=None, lt="cross_docking"):
    _exec(eng, "INSERT INTO raw.ml_full_inventory_listing VALUES (:m, :s, :it, '0', :i, NULL, true, :lt, "
               "'active', :d, :c, :r)", m=marca, s=seller, it=item, i=inv, lt=lt, d=dia, c=_inicio(dia), r=rid)


def _contagens(eng):
    """(fato, ref_date, brand) -> linhas, nas tres fatos."""
    out = {}
    for t in pub.TABELAS:
        for d, b, n in _exec(eng, f"SELECT ref_date, brand, count(*) FROM {t} GROUP BY 1, 2").fetchall():
            out[(t, d, b)] = n
    return out


def _marca(eng, dia):
    return {r.brand: r for r in _exec(eng, f"SELECT * FROM {pub.FACT_MARCA} WHERE ref_date = :d", d=dia)}


def _audit(eng):
    return [tuple(r) for r in _exec(eng, "SELECT status, rows_loaded FROM audit.source_sync_run ORDER BY 1")]


def _publicar(dia, marcas, raw_runs, publish_run_id):
    return pub.run(apply=True, ref_date=dia, marcas=marcas, raw_runs=raw_runs, publish_run_id=publish_run_id,
                   destino_esperado=ARQ_DESTINO)


def _pid(n):
    return f"{n:032x}"


@sem_pg
def test_pg_quatro_marcas_uma_full_zero_por_invocacao_e_repeticao_idempotente(pg):
    _run_raw(pg, "R1", D, QUATRO.split(","))
    _inventario(pg, "R1", D, "barbours", "S1", "IA", 10)
    _inventario(pg, "R1", D, "kokeshi", "S2", "IK", 5, nao=1, transfer=1)
    _inventario(pg, "R1", D, "lescent", "S3", "IL", 0)              # disponivel 0 COM inventario: normal
    _ponte(pg, "R1", D, "rituaria", "S4", "MR")                     # Full zero: so' anuncio sem inventario
    for n, m in enumerate(QUATRO.split(","), start=1):              # uma invocacao por marca
        antes = _contagens(pg)
        r = _publicar(D, [m], {m: "R1"}, _pid(n))
        assert r["applied"] is True and r["marcas_publicadas"] == [m]
        depois = _contagens(pg)
        mudou = {k for k in set(antes) | set(depois) if antes.get(k) != depois.get(k)}
        assert {(d, b) for _, d, b in mudou} == {(D, m)}, mudou      # so' (D, marca pedida)
    fm = _marca(pg, D)
    assert (fm["lescent"].inventarios, fm["lescent"].inventarios_full_lidos, fm["lescent"].disponivel_full) == \
        (1, 1, 0)
    z = fm["rituaria"]
    assert (z.inventarios, z.inventarios_full_lidos, z.disponivel_full, z.nao_disponivel_full,
            z.transfer_full, z.source_run_id) == (0, 0, None, None, None, _pid(4))
    cont = _contagens(pg)
    assert (pub.FACT_INVENTARIO, D, "rituaria") not in cont and (pub.FACT_STATUS, D, "rituaria") not in cont
    assert cont[(pub.FACT_STATUS, D, "kokeshi")] == 1
    assert r["marcas_full_zero_publicadas"] == ["rituaria"]
    assert r["evidencia_conta_full_zero"] == {"rituaria": pub.CONTA_UNICA_NA_PONTE}
    # repeticao: mesmo estado zero, outro publish id -> mesmas metricas e mesmas contagens
    antes, metricas = _contagens(pg), {k: v for k, v in z._mapping.items()
                                       if k not in ("source_run_id", "published_at")}
    r2 = _publicar(D, ["rituaria"], {"rituaria": "R1"}, _pid(5))
    z2 = _marca(pg, D)["rituaria"]
    assert r2["applied"] is True and _contagens(pg) == antes and z2.source_run_id == _pid(5)
    assert {k: v for k, v in z2._mapping.items() if k not in ("source_run_id", "published_at")} == metricas
    # as 4 numa invocacao so' tambem fecham (zero incluido) e nao tocam o outro dia
    r3 = _publicar(D, QUATRO.split(","), {m: "R1" for m in QUATRO.split(",")}, _pid(6))
    assert r3["marcas_full_zero_publicadas"] == ["rituaria"] and _contagens(pg) == antes
    assert sorted(_audit(pg)) == sorted([("success", 1)] * 3 + [("success", 0)] * 2 + [("success", 3)])
    assert all(_marca(pg, ANTES)[m].source_run_id == "antigo" for m in QUATRO.split(","))


@sem_pg
def test_pg_todas_as_marcas_full_zero_total_zero(pg):
    _run_raw(pg, "R2", D2, QUATRO.split(","))
    for i, m in enumerate(QUATRO.split(",")):                      # cada marca prova a SUA conta na ponte
        _ponte(pg, "R2", D2, m, f"S{i}", "MZ")
    antes = _contagens(pg)
    r = _publicar(D2, QUATRO.split(","), {m: "R2" for m in QUATRO.split(",")}, _pid(10))
    assert r["applied"] is True and r["inventarios_publicados"] == 0 and r["disponivel_publicado"] == 0
    assert r["evidencia_conta_full_zero"] == {m: pub.CONTA_UNICA_NA_PONTE for m in QUATRO.split(",")}
    fm = _marca(pg, D2)
    assert sorted(fm) == QUATRO.split(",") and all(v.inventarios == 0 and v.disponivel_full is None
                                                   for v in fm.values())
    depois = _contagens(pg)
    assert {k for k in depois if antes.get(k) != depois[k]} == {(pub.FACT_MARCA, D2, m) for m in QUATRO.split(",")}
    assert pub.diagnosticar(D2, _pid(10))["veredito"] == pub.ESTADO_COMMIT


def _semear_recusas(eng):
    # D3: run parcial, kokeshi incompleta (0 linhas) | R4: running | D4: conta da kokeshi = conta da barbours
    _run_raw(eng, "R3", D3, ["barbours", "kokeshi"], status="parcial",
             desfecho={"barbours": "PUBLICADA", "kokeshi": "INCOMPLETA_NAO_PUBLICADA"})
    _inventario(eng, "R3", D3, "barbours", "S1", "IA", 7)
    _run_raw(eng, "R4", D3, ["lescent"], status="running", desfecho={}, fim=False)
    _run_raw(eng, "R1", D, ["kokeshi"])
    _run_raw(eng, "R6", D4, ["barbours", "kokeshi"])
    _inventario(eng, "R6", D4, "barbours", "S1", "IA", 7)
    _ponte(eng, "R6", D4, "kokeshi", "S1", "MK")
    _run_raw(eng, "R7", D4, ["rituaria"])                   # PUBLICADA, 0 linhas e SEM ponte: conta nao provada


@sem_pg
@pytest.mark.parametrize("dia,marca,run_raw,trecho", [
    (D3, "kokeshi", "R3", "run_raw_nao_publicado"),          # fonte incompleta / parcial sem PUBLICADA
    (D3, "lescent", "R4", "run_raw_nao_terminado"),          # run ainda rodando
    (D3, "kokeshi", "R1", "run_raw_de_outra_data"),          # run de outro dia
    (D3, "rituaria", "R3", "run_raw_de_outra_marca"),        # run errado: nao pediu a marca
    (D3, "rituaria", "NAO-EXISTE", "run_raw_nao_registrado"),  # ausencia
    (D4, "kokeshi", "R6", "conta_em_outra_marca"),           # conta errada
    (D4, "rituaria", "R7", "conta_sem_evidencia_no_raw"),    # zero sem anuncio: ausente, nunca zero
])
def test_pg_zero_sem_prova_recusa_sem_tocar_o_destino(pg, dia, marca, run_raw, trecho):
    _semear_recusas(pg)
    antes, audit = _contagens(pg), _audit(pg)
    with pytest.raises(pub.PublicacaoRecusada, match=f"^marca_pedida_sem_fotografia: {marca} .*{trecho}"):
        _publicar(dia, [marca], {marca: run_raw}, _pid(20))
    assert _contagens(pg) == antes and _audit(pg) == audit


@sem_pg
def test_pg_marca_pronta_do_run_parcial_publica_e_a_incompleta_fica_ausente(pg):
    _semear_recusas(pg)
    r = _publicar(D3, ["barbours"], {"barbours": "R3"}, _pid(21))
    assert r["applied"] is True and sorted(_marca(pg, D3)) == ["barbours"]   # kokeshi: sem fotografia (sem linha)


@sem_pg
def test_pg_destino_divergente_recusa_zero_sem_escrita(pg, tmp_path):
    _run_raw(pg, "R2", D2, ["rituaria"])
    _ponte(pg, "R2", D2, "rituaria", "S4", "MR")
    alvo = json.loads((tmp_path / ARQ_DESTINO).read_text(encoding="utf-8"))["identidade"]["alvo_neon"]
    gravar_destino(tmp_path / ARQ_DESTINO, dict(alvo, system_identifier="1"))
    antes, audit = _contagens(pg), _audit(pg)
    with pytest.raises(pub.PublicacaoRecusada, match="destino_divergente: alvo_neon.system_identifier"):
        _publicar(D2, ["rituaria"], {"rituaria": "R2"}, _pid(22))
    assert _contagens(pg) == antes and _audit(pg) == audit
