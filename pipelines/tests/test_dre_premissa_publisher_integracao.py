"""DRE-PREMISSAS-1 — migration 026 e publisher contra PostgreSQL DESCARTAVEL.

Lock, transacao, rollback e CHECK sao comportamento do servidor: um dublê
concordaria com o teste. Nunca toca o Neon: a URL sai de `postgres_descartavel`.
Sem binarios do PostgreSQL, o arquivo inteiro e' SKIP.
"""
from __future__ import annotations

import types
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from pipelines.dre import premissa_publisher as P
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_da_sessao,
    postgres_disponivel,
)
from pipelines.tests.test_dre_premissa_publisher import matriz

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

REPO = Path(__file__).resolve().parents[2]
OUT, SET = date(2026, 10, 1), date(2026, 9, 1)
AGORA = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 64

DDL_BASE = [
    "CREATE SCHEMA marts", "CREATE SCHEMA audit",
    """CREATE TABLE audit.source_sync_run (
        sync_run_id SERIAL PRIMARY KEY, source_name VARCHAR(100) NOT NULL, marketplace_id INT, loja_id INT,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
        status VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
        rows_extracted INT, rows_loaded INT, error_message TEXT, source_min_date DATE, source_max_date DATE)""",
]


def carregar_migration_026():
    """Constantes da migration 026 SEM importar `alembic` (namespace isolado, `op = None`)."""
    caminho = REPO / "apps" / "api" / "alembic" / "versions" / "026_create_fact_dre_premissa_mensal.py"
    fonte = caminho.read_text(encoding="utf-8")
    assert fonte.count("from alembic import op") == 1
    ns: dict = {}
    exec(compile(fonte.replace("from alembic import op", "op = None"), str(caminho), "exec"), ns)  # noqa: S102
    return types.SimpleNamespace(**{k: v for k, v in ns.items() if not k.startswith("__")})


@pytest.fixture
def banco():
    with cluster_da_sessao() as base:
        adm = create_engine(base, future=True, isolation_level="AUTOCOMMIT")
        with adm.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS dre_prem WITH (FORCE)"))
            conn.execute(text("CREATE DATABASE dre_prem"))
        adm.dispose()
        url = base.rsplit("/", 1)[0] + "/dre_prem"
        eng = create_engine(url, future=True)
        with eng.begin() as c:
            for s in DDL_BASE + carregar_migration_026().UPGRADE_SQL:
                c.execute(text(s))
        yield eng
        eng.dispose()


def _run(eng) -> int:
    with eng.begin() as c:
        return c.execute(text("INSERT INTO audit.source_sync_run (source_name, status) "
                              "VALUES ('x', 'success') RETURNING sync_run_id")).scalar_one()


def _insere(eng, **kw):
    linha = {"competencia": OUT, "brand": "kokeshi", "component": "icms", "rate": Decimal("0.1"),
             "somavel": True, "status": "ok", "source_period": "out/26", "source_sha256": SHA,
             "source_note": "Premissas de outubro/2026", "source_sent_on": date(2026, 10, 13),
             "loaded_at": AGORA, "run_id": _run(eng)}
    linha.update(kw)
    with eng.begin() as c:
        c.execute(text(P.SQL_INSERE), linha)


def _linhas(eng, comp=None) -> list[dict]:
    with eng.connect() as c:
        sql = "SELECT * FROM marts.fact_dre_premissa_mensal" + (" WHERE competencia = :c" if comp else "")
        return [dict(r) for r in c.execute(text(sql), {"c": comp}).mappings().all()]


def _audit(eng) -> list[dict]:
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT source_name, status, error_message FROM audit.source_sync_run WHERE source_name = :f"),
            {"f": P.SOURCE_NAME}).mappings().all()]


# ---------------------------------------------------------------------------
# Migration 026: o CHECK e' a ultima barreira
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kw, motivo", [
    ({"rate": Decimal("1.5")}, "ck_fdpm_rate_faixa"),
    ({"rate": Decimal("-0.01")}, "ck_fdpm_rate_faixa"),
    ({"rate": Decimal("NaN")}, "ck_fdpm_rate_faixa"),
    ({"rate": None}, "ck_fdpm_ausencia_explicita"),
    ({"brand": "bysamia"}, "ck_fdpm_brand"),
    ({"component": "receita_bruta"}, "ck_fdpm_component"),
    ({"component": "outras_receitas_operacionais"}, "ck_fdpm_nao_somavel"),
    ({"component": "comissoes"}, "ck_fdpm_nao_somavel"),
    ({"component": "comissoes", "somavel": False}, "ck_fdpm_nao_somavel"),
    ({"status": "sobreposicao_nao_ratificada"}, "ck_fdpm_nao_somavel"),
    ({"source_note": "C:\\Users\\alguem\\premissas.xlsx"}, "ck_fdpm_nota_rotulo"),
    ({"source_note": "/home/alguem/premissas.xlsx"}, "ck_fdpm_nota_rotulo"),
    ({"source_note": "Orcamento_Exemplo.xlsx (enviado em 2026-10-13)"}, "ck_fdpm_nota_rotulo"),
    ({"source_sha256": "nao-e-hash"}, "ck_fdpm_sha256"),
    ({"competencia": date(2026, 10, 15)}, "ck_fdpm_competencia_dia_1"),
])
def test_migration_recusa_linha_fora_do_contrato(banco, kw, motivo):
    with pytest.raises(IntegrityError, match=motivo):
        _insere(banco, **kw)


def test_migration_aceita_ausencia_explicita_e_outras_receitas_nao_somavel(banco):
    _insere(banco, rate=None, status="ausente_na_origem")
    _insere(banco, component="outras_receitas_operacionais", somavel=False, status="sinal_nao_ratificado")
    _insere(banco, component="comissoes", somavel=False, status="sobreposicao_nao_ratificada")
    assert len(_linhas(banco)) == 3


def test_migration_nao_tem_coluna_monetaria(banco):
    with banco.connect() as c:
        cols = set(c.execute(text("SELECT column_name FROM information_schema.columns "
                                  "WHERE table_schema='marts' AND table_name='fact_dre_premissa_mensal'")).scalars())
    assert cols == {"competencia", "brand", "component", "rate", "somavel", "status", "source_period",
                    "source_sha256", "source_note", "source_sent_on", "loaded_at", "run_id"}


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------

def _matriz(tmp_path):
    p, sha = matriz(tmp_path)
    return P.ler_matriz(p, competencia=OUT, expected_sha256=sha)


def test_dry_run_nao_escreve(banco, tmp_path):
    m = _matriz(tmp_path)
    d = P.diagnosticar(banco, m)
    assert d["tabela_existe"] is True and d["diff"]["novas"] == 90
    assert _linhas(banco) == [] and _audit(banco) == []


def test_apply_grava_e_e_idempotente(banco, tmp_path):
    m = _matriz(tmp_path)
    r1 = P.publicar(banco, m, pasta_privada=tmp_path / "priv", agora=AGORA)
    assert (r1["apagadas"], r1["gravadas"]) == (0, 90)
    r2 = P.publicar(banco, m, pasta_privada=tmp_path / "priv", agora=AGORA)
    assert (r2["apagadas"], r2["gravadas"]) == (90, 90)
    assert len(_linhas(banco, OUT)) == 90
    assert [a["status"] for a in _audit(banco)] == ["success", "success"]
    assert P.diagnosticar(banco, m)["diff"]["iguais"] == 90
    # Backup antes do DELETE, um por apply, em pasta privada.
    assert len(list((tmp_path / "priv").glob("backup_premissas_2026-10_*.json"))) >= 1


def test_scoped_replace_preserva_outras_competencias(banco, tmp_path):
    _insere(banco, competencia=SET, component="pis")
    _insere(banco, competencia=SET, component="cofins")
    r = P.publicar(banco, _matriz(tmp_path), pasta_privada=tmp_path / "priv", agora=AGORA)
    assert r["fora_do_escopo_preservadas"] == 2
    assert len(_linhas(banco, SET)) == 2 and len(_linhas(banco, OUT)) == 90


def test_rollback_integral_quando_falha_antes_do_commit(banco, tmp_path):
    _insere(banco, component="pis", rate=Decimal("0.123"))
    antes = _linhas(banco)

    def explode(_conn):
        raise RuntimeError("falha injetada depois da escrita")

    with pytest.raises(RuntimeError, match="falha injetada"):
        P.publicar(banco, _matriz(tmp_path), pasta_privada=tmp_path / "priv", agora=AGORA,
                   antes_do_commit=explode)
    assert _linhas(banco) == antes
    assert [a["status"] for a in _audit(banco)] == ["failed"]


def test_lock_ocupado_nao_espera_nem_escreve(banco, tmp_path):
    with banco.connect() as outra:
        outra.execute(text(f"SELECT pg_advisory_lock({P.LOCK_KEY})"))
        with pytest.raises(P.LockOcupado):
            P.publicar(banco, _matriz(tmp_path), pasta_privada=tmp_path / "priv", agora=AGORA)
        outra.execute(text(f"SELECT pg_advisory_unlock({P.LOCK_KEY})"))
        outra.commit()
    assert _linhas(banco) == []


def test_migration_ausente_recusa_apply(tmp_path):
    with cluster_da_sessao() as base:
        adm = create_engine(base, future=True, isolation_level="AUTOCOMMIT")
        with adm.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS dre_sem026 WITH (FORCE)"))
            conn.execute(text("CREATE DATABASE dre_sem026"))
        adm.dispose()
        eng = create_engine(base.rsplit("/", 1)[0] + "/dre_sem026", future=True)
        with eng.begin() as c:
            for s in DDL_BASE:
                c.execute(text(s))
        try:
            with pytest.raises(P.MigrationAusente):
                P.publicar(eng, _matriz(tmp_path), pasta_privada=tmp_path / "priv", agora=AGORA)
            assert P.diagnosticar(eng, _matriz(tmp_path))["tabela_existe"] is False
        finally:
            eng.dispose()
