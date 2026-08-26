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


def test_tiktok_transform_cancelados_medidos_passam_com_a_taxa():
    """Gate DQ-TK1: cancelamento TEM fonte (COUNT de CANCELLED na Raw
    deduplicada). O valor medido e a taxa derivada atravessam o transform."""
    row = {
        "date": date(2026, 8, 5),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders": 15,
        "canceled_orders": 4,
        "cancel_rate_pct": 21.05,
    }
    canonical = tiktok_brand_daily.transform(row)
    assert canonical["canceled_orders"] == 4
    assert canonical["cancel_rate_pct"] == 21.05


def test_tiktok_transform_zero_falso_da_gold_nunca_atravessa():
    """Devolvido/reembolsado/problem_rate nao tem fonte. Mesmo que a fonte
    insista em mandar 0 — a Gold grava 0 literal em 120/120 linhas de
    ago/2026 — o transform forca None: um 0 aqui seria indistinguivel de
    'nao houve devolucao', o que nao se pode afirmar."""
    row = {
        "date": date(2026, 8, 5),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders": 15,
        # a fonte manda 0 nos tres — nenhum pode passar adiante
        "returned_orders": 0,
        "refunded_orders": 0,
        "problem_rate": 0.0,
    }
    canonical = tiktok_brand_daily.transform(row)
    assert canonical["returned_orders"] is None
    assert canonical["refunded_orders"] is None
    assert canonical["problem_rate"] is None


def test_tiktok_transform_cancel_rate_pct_nao_e_sobrescrito_por_none():
    """Regressao especifica: havia uma chave `cancel_rate_pct: None` duplicada
    depois da atribuicao real, e em Python a ultima vence — o valor medido era
    descartado em silencio."""
    row = {
        "date": date(2026, 8, 5),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders": 15,
        "cancel_rate_pct": 33.33,
    }
    canonical = tiktok_brand_daily.transform(row)
    assert canonical["cancel_rate_pct"] == 33.33


def test_tiktok_transform_taxa_ausente_continua_none():
    """Denominador zero no conector => NULL; o transform nao inventa 0."""
    row = {"date": date(2026, 8, 5), "brand": "kokeshi", "gmv": 0.0, "orders": 0}
    canonical = tiktok_brand_daily.transform(row)
    assert canonical["cancel_rate_pct"] is None
    assert canonical["canceled_orders"] is None


def test_tiktok_transform_conteudo_passa_intacto_sem_ratear_para_fechar():
    """Gate DQ-TK1: gmv_video+live+card tem base propria e NAO fecha com o
    headline. O transform nao ajusta, nao rateia e nao escala."""
    row = {
        "date": date(2026, 8, 5),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders": 15,
        "gmv_video": 400.0,
        "gmv_live": 300.0,
        "gmv_card": 350.0,
    }
    canonical = tiktok_brand_daily.transform(row)
    assert canonical["gmv_video"] == 400.0
    assert canonical["gmv_live"] == 300.0
    assert canonical["gmv_card"] == 350.0
    soma_conteudo = (
        canonical["gmv_video"] + canonical["gmv_live"] + canonical["gmv_card"]
    )
    # a divergencia com o headline sobrevive ao transform, de proposito
    assert soma_conteudo == 1050.0
    assert canonical["gmv"] == 1000.0


def test_tiktok_transform_nao_mapeia_content_orders_para_o_canonico():
    """`content_orders` existe no conector para rastreabilidade, mas nao tem
    coluna no fato canonico e NUNCA pode ocupar `orders` (que e' comercial)."""
    row = {
        "date": date(2026, 8, 5),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders": 15,
        "content_orders": 19,
    }
    canonical = tiktok_brand_daily.transform(row)
    assert canonical["orders"] == 15
    assert "content_orders" not in canonical


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
