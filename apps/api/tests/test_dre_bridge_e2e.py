"""DRE-DATA-BRIDGE-1 — ponta a ponta: publica num banco descartavel e serve pela rota.

Prova o que o Render vai fazer: ler SO' o Neon. O Data Mart e' trocado por um
objeto que explode em qualquer uso — se a rota encostar nele, o teste cai.

Banco descartavel (`pipelines.tests.postgres_descartavel`); SKIP sem binarios.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from pipelines.dre import insumo_publisher as P
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_da_sessao,
    postgres_disponivel,
)
from pipelines.tests.test_dre_insumo_publisher import carregar_migration_025
from pipelines.tests.test_dre_insumo_publisher_integracao import (
    DDL_FONTE,
    _executar,
    _url_db,
    semear,
)

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

JUL = date(2026, 7, 1)
PUBLICADO = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)

#: O alvo precisa das colunas que a ROTA le, nao so' das que o publisher le.
DDL_ALVO_API = [
    "CREATE SCHEMA marts", "CREATE SCHEMA audit",
    """CREATE TABLE audit.source_sync_run (
        sync_run_id SERIAL PRIMARY KEY, source_name VARCHAR(100) NOT NULL, marketplace_id INT, loja_id INT,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
        status VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
        rows_extracted INT, rows_loaded INT, error_message TEXT, source_min_date DATE, source_max_date DATE)""",
    "CREATE TABLE marts.dim_loja (loja_id INT PRIMARY KEY, brand_key TEXT NOT NULL, ativo BOOLEAN NOT NULL)",
    """CREATE TABLE marts.fact_marketplace_daily_performance (
        date DATE NOT NULL, loja_id INT NOT NULL, marketplace_id INT NOT NULL, gmv NUMERIC,
        total_fees NUMERIC, ad_spend NUMERIC, seller_shipping_cost NUMERIC,
        ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE (date, loja_id, marketplace_id))""",
    """CREATE TABLE marts.fact_tiktok_affiliate_cost_order_monthly (
        ref_month DATE, brand TEXT, affiliate_creator_commission NUMERIC,
        affiliate_partner_commission NUMERIC, affiliate_ads_commission NUMERIC)""",
    "INSERT INTO marts.dim_loja VALUES (1, 'kokeshi', true), (2, 'barbours', true)",
    "INSERT INTO marts.fact_tiktok_affiliate_cost_order_monthly VALUES ('2026-07-01', 'kokeshi', -60, -30, -10)",
]


class _DataMartQueExplode:
    def __getattr__(self, nome):
        raise AssertionError(f"a DRE tocou o Data Mart ({nome})")


@pytest.fixture
def ambiente(monkeypatch):
    with cluster_da_sessao() as base:
        adm = create_engine(base, future=True, isolation_level="AUTOCOMMIT")
        with adm.connect() as conn:
            for nome in ("dre_e2e_alvo", "dre_e2e_fonte"):
                conn.execute(text(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)"))
                conn.execute(text(f"CREATE DATABASE {nome}"))
        adm.dispose()
        alvo_url, fonte_url = _url_db(base, "dre_e2e_alvo"), _url_db(base, "dre_e2e_fonte")
        _executar(alvo_url, DDL_ALVO_API + carregar_migration_025().UPGRADE_SQL)
        _executar(fonte_url, DDL_FONTE)
        alvo, fonte = create_engine(alvo_url, future=True), create_engine(fonte_url, future=True)
        semear(alvo, fonte)
        with alvo.begin() as c:  # colunas que so' a rota le: comissao ML, Ads, frete
            c.execute(text("""UPDATE marts.fact_marketplace_daily_performance
                SET total_fees = CASE marketplace_id WHEN 2 THEN gmv * 0.15 WHEN 3 THEN gmv * 0.2 ELSE -gmv * 0.3 END,
                    ad_spend = CASE WHEN marketplace_id IN (2, 3) THEN gmv * 0.04 END,
                    seller_shipping_cost = CASE WHEN marketplace_id IN (2, 3) THEN gmv * 0.1 END"""))
        P.publicar(alvo, fonte, [JUL], ["tiktok", "ml", "shopee"], agora=PUBLICADO)
        fonte.dispose()  # daqui em diante so' o "Neon" existe

        from app import database
        monkeypatch.setattr(database, "datamart_engine", _DataMartQueExplode())
        yield alvo
        alvo.dispose()


def _dre(alvo, agora, canais=("tiktok", "ml", "shopee")):
    from app.services import dre_service as dre

    sessao = sessionmaker(bind=alvo)()
    try:
        return dre.get_dre(sessao, inicio=JUL, fim=date(2026, 7, 31), canais=list(canais), marcas=None,
                           hoje=date(2026, 10, 13), agora=agora)
    finally:
        sessao.close()


def _cel(res, canal, marca):
    return next(c for c in res["por_canal_marca"] if (c["canal"], c["marca"]) == (canal, marca))


def test_rota_serve_insumo_publicado_sem_data_mart(ambiente):
    res = _dre(ambiente, PUBLICADO + timedelta(hours=1))
    assert res["fontes"]["insumos_dre"]["estado"] == "ok"
    ml = _cel(res, "ml", "kokeshi")
    assert ml["classificacao"] == "PARCIAL"
    assert ml["componentes"]["cmv"]["valor"] == pytest.approx(300_000)
    assert ml["componentes"]["cmv"]["publicado_em"] == PUBLICADO
    tk = _cel(res, "tiktok", "kokeshi")
    assert tk["componentes"]["taxas"]["valor"] == pytest.approx(295)  # repasse, nunca o total_fees do mart
    assert tk["componentes"]["afiliados"]["somavel"] is False
    assert "afiliados" not in tk["margem_contribuicao_conhecida"]["componentes_usados"]
    sh = _cel(res, "shopee", "kokeshi")
    assert sh["classificacao"] == "NAO_CALCULAVEL"
    assert sh["componentes"]["cmv"]["status"] == "nao_reconciliado"


def test_comissao_ml_entra_uma_vez_no_fim_a_fim(ambiente):
    ml = _cel(_dre(ambiente, PUBLICADO + timedelta(hours=1)), "ml", "kokeshi")
    c = ml["componentes"]
    esperado = c["receita"]["valor"] - c["taxas"]["valor"] - c["ads"]["valor"] - c["frete"]["valor"] - c["cmv"]["valor"]
    assert ml["margem_contribuicao_conhecida"]["valor"] == pytest.approx(esperado, abs=0.01)
    assert ml["margem_contribuicao_conhecida"]["componentes_usados"] == ["taxas", "ads", "frete", "cmv"]


def test_lote_vencido_vira_indisponivel_na_rota(ambiente):
    res = _dre(ambiente, PUBLICADO + timedelta(hours=49))
    assert res["fontes"]["insumos_dre"]["estado"] == "vencido"
    for canal in ("ml", "tiktok"):
        cel = _cel(res, canal, "kokeshi")
        assert cel["componentes"]["cmv"]["status"] == "snapshot_vencido"
        assert cel["componentes"]["cmv"]["valor"] is None
        assert cel["classificacao"] == "NAO_CALCULAVEL"


def test_lote_que_falhou_depois_nao_apaga_o_anterior_mas_ele_vence(ambiente):
    """Falha na publicacao seguinte preserva o lote bom (transacao desfeita), e
    o serving continua vendo-o como do instante em que foi publicado: depois de
    48 h ele vence, nao se renova sozinho."""
    res = _dre(ambiente, PUBLICADO + timedelta(hours=60))
    assert _cel(res, "ml", "kokeshi")["componentes"]["cmv"]["status"] == "snapshot_vencido"


def test_tempo_de_resposta_so_com_neon(ambiente):
    _dre(ambiente, PUBLICADO + timedelta(hours=1))  # aquece pool e catalogo
    inicio = time.perf_counter()
    for _ in range(5):
        _dre(ambiente, PUBLICADO + timedelta(hours=1))
    media_ms = (time.perf_counter() - inicio) / 5 * 1000
    # Banco local: o numero prova a ORDEM de grandeza (sem varredura de 3,8 GB),
    # nao a latencia de rede do Neon — essa e' medida no PR contra o Neon real.
    assert media_ms < 2000, media_ms
