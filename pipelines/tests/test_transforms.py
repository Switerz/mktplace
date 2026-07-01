from datetime import date

from pipelines.transforms import ml_gestao_diaria, tiktok_brand_daily


def test_tiktok_transform_mapeia_brand_valido_para_loja_id():
    row = {
        "date": date(2026, 6, 1),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders": 10,
        "units_sold": 20,
        "avg_ticket": 100.0,
        "unique_buyers": 8,
        "visitors": None,
        "conversion_rate": None,
        "canceled_orders": 1,
        "returned_orders": 0,
        "refunded_orders": 0,
        "problem_rate": 0.05,
        "delivered_orders": 9,
        "avg_delivery_hours": 24.0,
        "total_settlement": 950.0,
        "total_fees": -50.0,
        "avg_fee_pct": 5.0,
        "avg_settlement_pct": 95.0,
        "gmv_video": 400.0,
        "gmv_live": 300.0,
        "gmv_card": 300.0,
    }
    canonical = tiktok_brand_daily.transform(row)
    assert canonical is not None
    assert canonical["loja_id"] == 3
    assert canonical["marketplace_id"] == 1
    assert canonical["empresa_id"] == 1
    assert canonical["gmv"] == 1000.0
    # Campos nao disponiveis no gold TikTok devem ser None explicito, nunca 0
    assert canonical["ad_spend"] is None
    assert canonical["new_buyers"] is None


def test_tiktok_transform_brand_fora_do_escopo_retorna_none():
    row = {"date": date(2026, 6, 1), "brand": "azbuy", "gmv": 100.0}
    assert tiktok_brand_daily.transform(row) is None


def test_tiktok_transform_batch_filtra_fora_do_escopo():
    rows = [
        {"date": date(2026, 6, 1), "brand": "kokeshi", "gmv": 100.0},
        {"date": date(2026, 6, 1), "brand": "gocase", "gmv": 999.0},
    ]
    result = tiktok_brand_daily.transform_batch(rows)
    assert len(result) == 1
    assert result[0]["loja_id"] == 3


def test_ml_transform_mapeia_brand_valido_para_loja_id():
    row = {
        "date": date(2026, 6, 1),
        "brand": "barbours",
        "gmv": 500.0,
        "orders": 5,
        "units_sold": 6,
        "avg_ticket": 100.0,
        "unique_buyers": 4,
        "new_buyers": 2,
        "repeat_buyers": 2,
        "repeat_buyer_rate_pct": 50.0,
        "canceled_orders": 0,
        "cancel_rate_pct": 0.0,
        "delivered_orders": 5,
        "avg_delivery_days": 3.0,
        "ad_spend": 50.0,
        "ad_revenue": 200.0,
        "ad_impressions": 1000,
        "ad_clicks": 30,
        "roas": 4.0,
        "acos_pct": 25.0,
        "ctr_pct": 3.0,
        "cpc": 1.5,
        "seller_shipping_cost": 20.0,
        "shipping_pct_of_gmv": 4.0,
    }
    canonical = ml_gestao_diaria.transform(row)
    assert canonical is not None
    assert canonical["loja_id"] == 2
    assert canonical["marketplace_id"] == 2
    # ML nao tem funil (visitors/conversion_rate) no gold — deve ser None, nao 0
    assert canonical["visitors"] is None
    assert canonical["conversion_rate"] is None
    # TikTok-especifico nao se aplica ao ML
    assert canonical["gmv_video"] is None


def test_ml_transform_rituaria_mapeia_para_loja_id_5():
    # rituaria incluida oficialmente no escopo ML em 2026-07-01 (Bug 4 —
    # docs/sections/produtos_audit.md): gold.ml_gestao_diaria tem dados
    # reais desde 2025-12-28, mas conector/services filtravam por whitelist
    # desatualizada. O transform em si sempre soube mapear a brand.
    row = {"date": date(2026, 6, 1), "brand": "rituaria", "gmv": 500.0}
    canonical = ml_gestao_diaria.transform(row)
    assert canonical is not None
    assert canonical["loja_id"] == 5
    assert canonical["marketplace_id"] == 2


def test_ml_connector_inclui_rituaria_no_escopo():
    from pipelines.connectors.mercadolivre import connector as ml_connector
    assert "rituaria" in ml_connector.BRANDS_IN_SCOPE
    assert "azbuy" not in ml_connector.BRANDS_IN_SCOPE
    assert "gocase" not in ml_connector.BRANDS_IN_SCOPE


def test_ml_transform_brand_fora_do_escopo_retorna_none():
    # azbuy nao esta em marts.dim_loja (fora do grupo GoBeaute) — nao deve mapear.
    # Nota: "apice" e "rituaria" ESTAO em BRAND_TO_LOJA (sao lojas validas do
    # grupo); a ausencia de dados delas no ML e' um gap da fonte, nao um
    # filtro do transform — ver docs/backlog.md.
    row = {"date": date(2026, 6, 1), "brand": "azbuy", "gmv": 100.0}
    assert ml_gestao_diaria.transform(row) is None
