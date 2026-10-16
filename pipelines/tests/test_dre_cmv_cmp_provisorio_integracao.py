"""DRE-CMV-CMP-1 — migration 028 e publicacao contra PostgreSQL DESCARTAVEL.

CHECK, lock, transacao e rollback sao comportamento do servidor. Nunca toca o
Neon. Sem binarios do PostgreSQL, o arquivo inteiro e' SKIP.
"""
from __future__ import annotations

import types
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from pipelines.dre import cmv_cmp_provisorio as M
from pipelines.tests.postgres_descartavel import MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

REPO = Path(__file__).resolve().parents[2]
OUT, SET = date(2026, 10, 1), date(2026, 9, 1)
AGORA = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
SHA = "c" * 64
DDL_BASE = [
    "CREATE SCHEMA marts", "CREATE SCHEMA audit",
    """CREATE TABLE audit.source_sync_run (
        sync_run_id SERIAL PRIMARY KEY, source_name VARCHAR(100) NOT NULL, marketplace_id INT, loja_id INT,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
        status VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
        rows_extracted INT, rows_loaded INT, error_message TEXT, source_min_date DATE, source_max_date DATE)""",
]


def carregar_migration_027():
    caminho = REPO / "apps" / "api" / "alembic" / "versions" / "028_create_fact_dre_cmv_provisorio_mensal.py"
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
            conn.execute(text("DROP DATABASE IF EXISTS dre_cmvp WITH (FORCE)"))
            conn.execute(text("CREATE DATABASE dre_cmvp"))
        adm.dispose()
        eng = create_engine(base.rsplit("/", 1)[0] + "/dre_cmvp", future=True)
        with eng.begin() as c:
            for s in DDL_BASE + carregar_migration_027().UPGRADE_SQL:
                c.execute(text(s))
        yield eng
        eng.dispose()


def celula(comp=OUT, canal="tiktok", marca="apice", **kw):
    c = {"competencia": comp, "canal": canal, "marca": marca, "data_corte": date(2026, 10, 15), "status": "calculado",
         "receita_fonte": Decimal(1000), "unidades": Decimal(50), "receita_coberta": Decimal(300),
         "cobertura": Decimal("0.3"), "cmv_provisorio": Decimal(90), "unidades_sem_cmp": Decimal(35),
         "skus_total": 10, "skus_cobertos": 3, "skus_conflito": 1, "skus_sem_cmp": 7, "receita_gtin": Decimal(100),
         "receita_volume": Decimal(0), "receita_codigo_provisorio": Decimal(200), "receita_carry_forward": Decimal(300),
         "receita_kit_homogeneo": Decimal(0), "receita_kit_misto": Decimal(0), "receita_depara": Decimal(0),
         "confianca": "baixa", "regra_temporal": "entidade_vigencia", "fonte_vendas": "x (API)"}
    c.update(kw)
    return c


def ausente(**kw):
    return celula(status="ausente_na_fonte", receita_fonte=None, unidades=None, receita_coberta=None, cobertura=None,
                  cmv_provisorio=None, unidades_sem_cmp=None, skus_total=0, skus_cobertos=0, skus_conflito=0,
                  skus_sem_cmp=0, receita_gtin=None, receita_volume=None, receita_codigo_provisorio=None,
                  receita_carry_forward=None, receita_kit_homogeneo=None, receita_kit_misto=None, receita_depara=None,
                  confianca=None,
                  **kw)


def _linhas(eng, comp=None):
    with eng.connect() as c:
        sql = f"SELECT * FROM {M.TABELA}" + (" WHERE competencia = :c" if comp else "")
        return [dict(r) for r in c.execute(text(sql), {"c": comp}).mappings().all()]


def _audit(eng):
    with eng.connect() as c:
        return [r[0] for r in c.execute(text("SELECT status FROM audit.source_sync_run WHERE source_name = :f "
                                             "ORDER BY sync_run_id"), {"f": M.SOURCE_NAME})]


@pytest.mark.parametrize("kw, motivo", [
    ({"cmv_provisorio": Decimal(0), "skus_cobertos": 0, "skus_sem_cmp": 10}, "ck_fdcp_cmv_so_com_cobertura"),
    ({"cmv_provisorio": None}, "ck_fdcp_cmv_so_com_cobertura"),
    ({"receita_coberta": Decimal(2000)}, "ck_fdcp_coberta_le_fonte"),
    ({"cobertura": Decimal("NaN")}, "ck_fdcp_cobertura"),
    ({"cmv_provisorio": Decimal(-1)}, "ck_fdcp_nao_negativos"),
    ({"skus_sem_cmp": 1}, "ck_fdcp_nao_negativos"),
    ({"status": "ausente_na_fonte"}, "ck_fdcp_ausencia_explicita"),
    ({"marca": "gocase"}, "ck_fdcp_marca"),
    ({"regra_temporal": "ultimo_disponivel"}, "ck_fdcp_regra_temporal"),
])
def test_migration_027_recusa_linha_fora_do_contrato(banco, kw, motivo):
    with pytest.raises(IntegrityError, match=motivo):
        M.publicar(banco, [celula(**kw)], OUT, SHA, agora=AGORA)


@pytest.mark.parametrize("valor", [Decimal(301), Decimal("NaN")])
def test_migration_recusa_receita_depara_fora_do_contrato(banco, monkeypatch, valor):
    """O CHECK do banco e' a ultima barreira: prova-o com a trava do publicador desligada."""
    monkeypatch.setattr(M, "DEPARA_CADASTRO_AUTORIZADO", True)
    with pytest.raises(IntegrityError, match="ck_fdcp_depara"):
        M.publicar(banco, [celula(receita_depara=valor)], OUT, SHA, agora=AGORA)


def test_publica_ausente_com_null_e_e_idempotente(banco):
    r = M.publicar(banco, [celula(), ausente(canal="shopee", marca="kokeshi")], OUT, SHA, agora=AGORA)
    assert r["gravadas"] == 2
    M.publicar(banco, [celula(), ausente(canal="shopee", marca="kokeshi")], OUT, SHA, agora=AGORA)
    linhas = {(x["canal"], x["marca"]): x for x in _linhas(banco, OUT)}
    assert len(linhas) == 2 and linhas[("shopee", "kokeshi")]["cmv_provisorio"] is None
    assert _audit(banco) == ["success", "success"]


def test_substitui_so_a_competencia_e_preserva_as_outras(banco):
    M.publicar(banco, [celula(comp=SET)], SET, SHA, agora=AGORA)
    r = M.publicar(banco, [celula(), celula(marca="kokeshi")], OUT, SHA, agora=AGORA)
    assert r["outras_competencias_preservadas"] == 1
    assert len(_linhas(banco, SET)) == 1 and len(_linhas(banco, OUT)) == 2


def test_rollback_integral(banco):
    def explode(_conn):
        raise RuntimeError("falha injetada")

    with pytest.raises(RuntimeError, match="falha injetada"):
        M.publicar(banco, [celula()], OUT, SHA, agora=AGORA, antes_do_commit=explode)
    assert _linhas(banco) == [] and _audit(banco) == ["failed"]


def test_lock_ocupado_nao_escreve(banco):
    with banco.connect() as outra:
        outra.execute(text(f"SELECT pg_advisory_lock({M.LOCK_KEY})"))
        with pytest.raises(M.LockOcupado):
            M.publicar(banco, [celula()], OUT, SHA, agora=AGORA)
        outra.execute(text(f"SELECT pg_advisory_unlock({M.LOCK_KEY})"))
        outra.commit()
    assert _linhas(banco) == []


def test_migration_ausente_recusa():
    with cluster_da_sessao() as base:
        adm = create_engine(base, future=True, isolation_level="AUTOCOMMIT")
        with adm.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS dre_sem027 WITH (FORCE)"))
            conn.execute(text("CREATE DATABASE dre_sem027"))
        adm.dispose()
        eng = create_engine(base.rsplit("/", 1)[0] + "/dre_sem027", future=True)
        with eng.begin() as c:
            for s in DDL_BASE:
                c.execute(text(s))
        try:
            with pytest.raises(M.MigrationAusente):
                M.publicar(eng, [celula()], OUT, SHA, agora=AGORA)
        finally:
            eng.dispose()
