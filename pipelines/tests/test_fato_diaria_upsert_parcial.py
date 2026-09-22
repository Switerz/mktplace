"""
Gate SH-AUTO-1 — prova de NÃO-REGRESSÃO dos upserts parciais da Shopee.

Este gate adicionou exclusão mútua e mais nada. Estes testes executam os SQLs
REAIS de `daily_performance` contra PostgreSQL descartável e provam que cada
writer continua escrevendo só as colunas de que é fonte.

POR QUE ISSO PRECISA DE TESTE

A tentação natural ao mexer neste módulo é "simplificar" os quatro SQLs em um
só. Já custou caro: o Gate SD2-C registra que `--source shopee` usava o
`UPSERT_SQL` completo e, como o transform de orders devolve `None` para funil e
mídia, rodar orders isoladamente APAGAVA visitantes e Ads já publicados e ainda
trocava o GMV autoritativo pelo subtotal dos pedidos. Nada ficava vermelho — a
linha simplesmente perdia metade do conteúdo.

Com o lock, os três writers deixam de rodar ao mesmo tempo. Isso NÃO os torna
idempotentes entre si: continuam parciais por desenho, e é isso que se mede
aqui.

Requer `FATO_DIARIA_LOCK_TEST_DSN` — sem ela, SKIP. Ver `banco_descartavel.py`.
"""
from __future__ import annotations

import pytest

from pipelines.ingestion import daily_performance as dp
from pipelines.tests.banco_descartavel import (
    DSN,
    SKIP_REASON,
    criar_esquema,
)

pytestmark = pytest.mark.skipif(not DSN, reason=SKIP_REASON)

CHAVE = {"date": "2026-09-01", "loja_id": 2, "marketplace_id": 3, "empresa_id": 1}


@pytest.fixture()
def banco():
    eng = criar_esquema()
    yield eng
    eng.dispose()


def _executar(banco, sql, params):
    with banco.connect() as conn:
        conn.execute(sql, params)
        conn.commit()


def _linha(banco):
    from sqlalchemy import text

    with banco.connect() as conn:
        row = conn.execute(
            text(
                "SELECT * FROM marts.fact_marketplace_daily_performance "
                "WHERE date=:date AND loja_id=:loja_id AND marketplace_id=:marketplace_id"
            ),
            CHAVE,
        ).mappings().first()
    return dict(row) if row is not None else None


def _params_orders(**over):
    p = dict(CHAVE)
    p.update({
        "orders": 100, "units_sold": 120, "avg_ticket": 50, "unique_buyers": 90,
        "canceled_orders": 5, "returned_orders": 2, "cancel_rate_pct": 5,
        "delivered_orders": 80, "total_settlement": 5200, "total_fees": 600,
        "avg_fee_pct": 12, "avg_settlement_pct": 104, "seller_shipping_cost": 300,
        "shipping_pct_of_gmv": 6,
    })
    p.update(over)
    return p


def _params_stats(**over):
    p = dict(CHAVE)
    p.update({
        "visitors": 4000, "conversion_rate": 2.5, "new_buyers": 60,
        "repeat_buyers": 30, "repeat_buyer_rate_pct": 33, "unique_buyers": 90,
        "gmv": 5000,
    })
    p.update(over)
    return p


def _params_ads(**over):
    p = dict(CHAVE)
    p.update({
        "ad_spend": 700, "ad_revenue": 2100, "ad_impressions": 50000,
        "ad_clicks": 1200, "roas": 3, "acos_pct": 33, "ctr_pct": 2.4, "cpc": 0.58,
    })
    p.update(over)
    return p


# ---------------------------------------------------------------------------
# orders preserva funil e mídia
# ---------------------------------------------------------------------------
def test_orders_preserva_funil_e_ads(banco):
    """O caso do Gate SD2-C: rodar orders depois de stats e ads não pode zerar
    visitantes nem investimento."""
    _executar(banco, dp.PATCH_SHOP_STATS_SQL, _params_stats())
    _executar(banco, dp.PATCH_ADS_SQL, _params_ads())
    _executar(banco, dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders())

    linha = _linha(banco)
    assert linha["visitors"] == 4000
    assert linha["conversion_rate"] == 2.5
    assert linha["new_buyers"] == 60
    assert linha["repeat_buyers"] == 30
    assert linha["ad_spend"] == 700
    assert linha["ad_clicks"] == 1200
    assert linha["orders"] == 100, "e as colunas de orders foram atualizadas"


def test_orders_nao_sobrescreve_o_gmv_autoritativo_de_stats(banco):
    """Gate R2.1: shop-stats é a fonte autoritativa do GMV Shopee. `gmv` está
    fora da lista de UPDATE de orders de propósito."""
    _executar(banco, dp.PATCH_SHOP_STATS_SQL, _params_stats(gmv=5000))
    _executar(banco, dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders())
    assert _linha(banco)["gmv"] == 5000


def test_orders_em_linha_nova_deixa_gmv_nulo(banco):
    """Ausência de shop-stats não é venda zero."""
    _executar(banco, dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders())
    assert _linha(banco)["gmv"] is None


def test_orders_preenche_unique_buyers_so_quando_vazio(banco):
    """O COALESCE existe para a linha criada só pelo patch de Ads: orders
    preenche o buraco, mas não derruba o valor autoritativo de stats."""
    _executar(banco, dp.PATCH_ADS_SQL, _params_ads())
    assert _linha(banco)["unique_buyers"] is None
    _executar(banco, dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders(unique_buyers=77))
    assert _linha(banco)["unique_buyers"] == 77

    _executar(banco, dp.PATCH_SHOP_STATS_SQL, _params_stats(unique_buyers=90))
    _executar(banco, dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders(unique_buyers=11))
    assert _linha(banco)["unique_buyers"] == 90


# ---------------------------------------------------------------------------
# stats preserva pedidos e mídia
# ---------------------------------------------------------------------------
def test_stats_preserva_pedidos_e_ads(banco):
    _executar(banco, dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders())
    _executar(banco, dp.PATCH_ADS_SQL, _params_ads())
    _executar(banco, dp.PATCH_SHOP_STATS_SQL, _params_stats())

    linha = _linha(banco)
    assert linha["orders"] == 100
    assert linha["units_sold"] == 120
    assert linha["canceled_orders"] == 5
    assert linha["total_fees"] == 600
    assert linha["seller_shipping_cost"] == 300
    assert linha["ad_spend"] == 700
    assert linha["visitors"] == 4000, "e as colunas de stats foram atualizadas"


# ---------------------------------------------------------------------------
# ads preserva pedidos e funil
# ---------------------------------------------------------------------------
def test_ads_preserva_pedidos_e_funil(banco):
    _executar(banco, dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders())
    _executar(banco, dp.PATCH_SHOP_STATS_SQL, _params_stats())
    _executar(banco, dp.PATCH_ADS_SQL, _params_ads())

    linha = _linha(banco)
    assert linha["orders"] == 100
    assert linha["total_settlement"] == 5200
    assert linha["visitors"] == 4000
    assert linha["gmv"] == 5000
    assert linha["new_buyers"] == 60
    assert linha["ad_spend"] == 700, "e as colunas de ads foram atualizadas"


# ---------------------------------------------------------------------------
# A sequência real do refresh manual, em qualquer ordem
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ordem",
    [
        ("orders", "stats", "ads"),
        ("ads", "orders", "stats"),
        ("stats", "ads", "orders"),
        ("ads", "stats", "orders"),
    ],
)
def test_linha_completa_em_qualquer_ordem_dos_tres_writers(banco, ordem):
    """Serializados pelo lock, os três em qualquer ordem montam a linha inteira.
    O único campo dependente de ordem é `unique_buyers`, por causa do COALESCE —
    e por isso ele não entra nesta asserção."""
    sqls = {
        "orders": (dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders()),
        "stats": (dp.PATCH_SHOP_STATS_SQL, _params_stats()),
        "ads": (dp.PATCH_ADS_SQL, _params_ads()),
    }
    for nome in ordem:
        sql, params = sqls[nome]
        _executar(banco, sql, params)

    linha = _linha(banco)
    assert linha["orders"] == 100
    assert linha["units_sold"] == 120
    assert linha["canceled_orders"] == 5
    assert linha["delivered_orders"] == 80
    assert linha["total_settlement"] == 5200
    assert linha["visitors"] == 4000
    assert linha["gmv"] == 5000
    assert linha["new_buyers"] == 60
    assert linha["ad_spend"] == 700
    assert linha["roas"] == 3


# ---------------------------------------------------------------------------
# ML e TikTok: o UPSERT completo continua completo
# ---------------------------------------------------------------------------
def _params_completos(marketplace_id: int):
    from pipelines.transforms import tiktok_brand_daily  # noqa: F401  (só para o contrato)

    p = {
        "date": "2026-09-01", "loja_id": 2,
        "marketplace_id": marketplace_id, "empresa_id": 1,
    }
    campos = [
        "gmv", "orders", "units_sold", "avg_ticket", "unique_buyers", "new_buyers",
        "repeat_buyers", "repeat_buyer_rate_pct", "visitors", "conversion_rate",
        "canceled_orders", "returned_orders", "refunded_orders", "problem_rate",
        "cancel_rate_pct", "delivered_orders", "avg_delivery_hours",
        "avg_delivery_days", "ad_spend", "ad_revenue", "ad_impressions",
        "ad_clicks", "roas", "acos_pct", "ctr_pct", "cpc", "gmv_video", "gmv_live",
        "gmv_card", "total_settlement", "total_fees", "avg_fee_pct",
        "avg_settlement_pct", "seller_shipping_cost", "shipping_pct_of_gmv",
        "target_revenue", "target_attainment_pct", "projected_month_revenue",
        "data_quality_score",
    ]
    for i, campo in enumerate(campos, start=1):
        p[campo] = i
    p["source_updated_at"] = "2026-09-01T00:00:00+00:00"
    return p


@pytest.mark.parametrize("marketplace_id,canal", [(1, "tiktok"), (2, "ml")])
def test_upsert_completo_de_ml_e_tiktok_inalterado(banco, marketplace_id, canal):
    """Requisito 9: ML e TikTok preservados. Eles ganharam o lock e mais nada —
    o `UPSERT_SQL` continua escrevendo todas as colunas, como sempre escreveu."""
    params = _params_completos(marketplace_id)
    _executar(banco, dp.UPSERT_SQL, params)

    from sqlalchemy import text

    with banco.connect() as conn:
        linha = conn.execute(
            text(
                "SELECT * FROM marts.fact_marketplace_daily_performance "
                "WHERE marketplace_id=:mid"
            ),
            {"mid": marketplace_id},
        ).mappings().first()
    assert linha["gmv"] == 1
    assert linha["orders"] == 2
    assert linha["data_quality_score"] == 39

    # E o segundo upsert sobrescreve tudo, que é a semântica deste canal.
    params2 = {k: (v + 100 if isinstance(v, int) and k not in CHAVE else v)
               for k, v in params.items()}
    _executar(banco, dp.UPSERT_SQL, params2)
    with banco.connect() as conn:
        linha2 = conn.execute(
            text(
                "SELECT gmv, orders FROM marts.fact_marketplace_daily_performance "
                "WHERE marketplace_id=:mid"
            ),
            {"mid": marketplace_id},
        ).mappings().first()
    assert linha2["gmv"] == 101
    assert linha2["orders"] == 102


def test_upsert_de_ml_nao_toca_a_linha_da_shopee(banco):
    """A chave inclui `marketplace_id`: canais não se sobrepõem. É a razão pela
    qual o lock é por marketplace e não global."""
    _executar(banco, dp.PATCH_SHOPEE_ORDERS_SQL, _params_orders())
    _executar(banco, dp.PATCH_SHOP_STATS_SQL, _params_stats())
    _executar(banco, dp.UPSERT_SQL, _params_completos(2))

    linha_shopee = _linha(banco)
    assert linha_shopee["orders"] == 100
    assert linha_shopee["gmv"] == 5000
    assert linha_shopee["visitors"] == 4000
