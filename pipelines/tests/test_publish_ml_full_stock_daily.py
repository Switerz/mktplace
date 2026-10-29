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

import pytest

from pipelines import publish_ml_full_stock_daily as pub
from pipelines.tests.test_sync_ml_full_stock_daily import cenario

REF = date(2026, 10, 20)
RAW = "run-raw-barbours-1"
PUB = "0123456789abcdef0123456789abcdef"
MIG = Path(__file__).resolve().parents[2] / "apps" / "api" / "alembic" / "versions" / "029_create_fact_ml_full_stock_daily.py"
CONTRATO = {"marcas": ["barbours"], "raw_runs": {"barbours": RAW}, "publish_run_id": PUB}
ARGV_APPLY = ["--apply", "--ref-date", str(REF), "--marca", "barbours", "--raw-run", f"barbours={RAW}",
              "--publish-run-id", PUB]


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


class Conn:
    def __init__(self, lock=True, destino=None, em_uso=False):
        self.lock, self.destino, self.em_uso, self.sqls = lock, destino, em_uso, []
        self.connection = self

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
        yield self


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
def ambiente(monkeypatch):
    estado = {"audit": [], "inseridos": [], "marcas_inseridas": set(), "leituras": []}

    def ler(ref, runs=()):
        estado["leituras"].append((ref, runs))
        return fonte_barbours()

    monkeypatch.setattr(pub, "_ler_fonte", ler)
    monkeypatch.setattr(pub, "tabelas_destino_existem", lambda: True)
    monkeypatch.setattr(pub, "publish_run_id_em_uso", lambda rid: False)
    monkeypatch.setattr(pub, "audit_start", lambda n: estado["audit"].append(("start", n)) or 77)
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
                raw_runs={"barbours": RAW, "kokeshi": RAW}, publish_run_id=PUB)
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
    assert "pg_try_advisory_xact_lock" in conn.sqls[0][0] and "source_run_id" in conn.sqls[1][0]
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
        assert not re.search(r"(INSERT|UPDATE|DELETE|TRUNCATE)|advisory", s, re.I), s


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


def test_ler_fonte_le_os_runs_na_mesma_sessao_read_only(monkeypatch):
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
    assert eventos[0] == ("set_session", {"readonly": True}) and eventos[-2:] == ["rollback", "close"]
    assert not any(re.search(r"(INSERT|UPDATE|DELETE)", s, re.I) for s in sqls)


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
