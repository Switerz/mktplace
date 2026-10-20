"""Publicacao do estoque Full ML (inerte): travas, transacao, reconciliacao, auditoria.

Sem banco: engine, sessao e auditoria sao dubles. A migration 029 e o
publicador sao executados contra PostgreSQL real descartavel pelo script de
validacao do gate, nao aqui.
"""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import publish_ml_full_stock_daily as pub
from pipelines.tests.test_sync_ml_full_stock_daily import cenario

REF = date(2026, 10, 20)
MIG = Path(__file__).resolve().parents[2] / "apps" / "api" / "alembic" / "versions" / "029_create_fact_ml_full_stock_daily.py"


def fonte_barbours():
    s, st, p, v = cenario()
    troca = lambda xs: [dict(x, brand="barbours") for x in xs]
    return {"snapshot": troca(s), "status": troca(st), "ponte": troca(p),
            "vendas": {("barbours",) + k[1:]: q for k, q in v.items()}}


class Res:
    def __init__(self, linhas=None, escalar=None, mapa=None):
        self.linhas, self.escalar, self.mapa = linhas or [], escalar, mapa

    def scalar(self):
        return self.escalar

    def fetchone(self):
        return self.linhas[0] if self.linhas else None

    def mappings(self):
        return self

    def one(self):
        return self.mapa


class Conn:
    def __init__(self, lock=True, destino=None):
        self.lock, self.destino, self.sqls = lock, destino, []
        self.connection = self

    def execute(self, sql, params=None):
        s = str(sql)
        self.sqls.append((s, params))
        if "pg_try_advisory_xact_lock" in s:
            return Res(escalar=self.lock)
        if "COUNT(*)" in s:
            return Res(mapa=self.destino)
        return Res()

    @contextmanager
    def cursor(self):
        yield self


class Engine:
    def __init__(self, conn):
        self.conn = conn
        self.begins = 0

    @contextmanager
    def begin(self):
        self.begins += 1
        yield self.conn


@pytest.fixture
def ambiente(monkeypatch):
    estado = {"audit": [], "inseridos": []}
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref: fonte_barbours())
    monkeypatch.setattr(pub, "tabelas_destino_existem", lambda: True)
    monkeypatch.setattr(pub, "audit_start", lambda n: estado["audit"].append(("start", n)) or 77)
    monkeypatch.setattr(pub, "audit_finish", lambda sid, st, **kw: estado["audit"].append((st, kw)))
    monkeypatch.setattr(pub, "execute_values",
                        lambda cur, sql, linhas, page_size: estado["inseridos"].append((sql.split()[2], len(linhas))))
    monkeypatch.setenv(pub.FLAG_AMBIENTE, "true")
    return estado


def _conn_ok():
    pacote = pub.montar(fonte_barbours(), REF, "r")
    return Conn(destino={"n": pacote["esperado"]["inventarios"], "soma": pacote["esperado"]["soma_disponivel"]})


# ---------------------------------------------------------------------------
# Travas
# ---------------------------------------------------------------------------
def test_sem_apply_e_dry_run_e_nao_abre_o_neon(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "local_engine", lambda: pytest.fail("abriu o Neon sem --apply"))
    r = pub.run(apply=False, ref_date=REF)
    assert r["applied"] is False and r["modo"] == "dry_run"
    assert ambiente["audit"] == []


def test_apply_sem_flag_recusa_sem_escrever(ambiente, monkeypatch):
    monkeypatch.delenv(pub.FLAG_AMBIENTE)
    monkeypatch.setattr(pub, "local_engine", lambda: pytest.fail("escreveu sem flag"))
    with pytest.raises(pub.PublicacaoRecusada, match="desligada"):
        pub.run(apply=True, ref_date=REF)
    assert ambiente["audit"] == []


@pytest.mark.parametrize("valor", ["false", "1", "TRUE ", "yes", ""])
def test_so_o_texto_true_liga(monkeypatch, valor):
    monkeypatch.setenv(pub.FLAG_AMBIENTE, valor)
    assert pub.flag_ligada() is (valor.strip().lower() == "true")


def test_apply_sem_fonte_recusa(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "_ler_fonte", lambda ref: None)
    with pytest.raises(pub.PublicacaoRecusada, match="fonte_inexistente"):
        pub.run(apply=True, ref_date=REF)


def test_apply_sem_migration_recusa(ambiente, monkeypatch):
    monkeypatch.setattr(pub, "tabelas_destino_existem", lambda: False)
    with pytest.raises(pub.PublicacaoRecusada, match="029"):
        pub.run(apply=True, ref_date=REF)
    assert ambiente["audit"] == []


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
def test_publicacao_lock_primeiro_delete_por_marca_e_reconcilia(ambiente, monkeypatch):
    conn = _conn_ok()
    eng = Engine(conn)
    monkeypatch.setattr(pub, "local_engine", lambda: eng)
    r = pub.run(apply=True, ref_date=REF)
    assert r["applied"] is True and r["marcas_publicadas"] == ["barbours"]
    assert "pg_try_advisory_xact_lock" in conn.sqls[0][0]
    deletes = [p for s, p in conn.sqls if s.startswith("DELETE")]
    assert len(deletes) == 3 and all(p["marcas"] == ["barbours"] for p in deletes)
    tabelas = [t for t, _ in ambiente["inseridos"]]
    assert tabelas == [pub.FACT_INVENTARIO, pub.FACT_STATUS, pub.FACT_MARCA]
    assert ambiente["audit"][0] == ("start", 5) and ambiente["audit"][-1][0] == "success"
    assert eng.begins == 1


def test_reconciliacao_divergente_aborta_e_audita_falha(ambiente, monkeypatch):
    conn = Conn(destino={"n": 999, "soma": 0})
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.ReconciliacaoFalhou):
        pub.run(apply=True, ref_date=REF)
    assert ambiente["audit"][-1][0] == "failed"


def test_lock_ocupado_nao_escreve_e_audita_falha(ambiente, monkeypatch):
    conn = Conn(lock=False)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.ConcurrentRunError):
        pub.run(apply=True, ref_date=REF)
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
# Migration 029 (estatico)
# ---------------------------------------------------------------------------
def _mig():
    spec = importlib.util.spec_from_file_location("m029", MIG)
    m = importlib.util.module_from_spec(spec)
    import sys
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
