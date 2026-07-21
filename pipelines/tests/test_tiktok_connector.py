"""
Testes focais do Gate R2/R2.1 (Projeto R) — correção semântica de GMV TikTok:
GMV passa a ser SUM(sub_total) de pedidos elegíveis em raw.tiktok_shop_orders,
em vez de passthrough de gold.tiktok_brand_daily.gmv (próximo de total_amount,
incluindo frete do comprador). R2.1 endurece: orders/avg_ticket consistentes,
gmv_video/live/card preservados, status nulo conta como inesperado, sub_total
nulo em pedido elegível bloqueia fetch().

Sem banco disponível neste ambiente de teste: a query é validada por forma
(shape assertions sobre o texto SQL — mesmo padrão já usado no repositório em
pipelines/tests/test_shopee_staging_sql_rules.py) e o comportamento de
fetch() é validado com datamart_query monkeypatchado.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipelines.connectors.tiktok import connector


# ---------------------------------------------------------------------------
# Forma da query (shape assertions sobre o texto SQL)
# ---------------------------------------------------------------------------
def test_query_usa_sub_total_como_base_do_gmv():
    assert "sub_total" in connector.QUERY
    assert "SUM(CASE WHEN order_status IN :eligible_statuses THEN sub_total ELSE 0 END)" in connector.QUERY


def test_query_nao_usa_total_amount_nem_shipping_fee_no_gmv():
    # frete (shipping_fee) e total_amount (que inclui frete) nao podem
    # aparecer na query — o GMV corrigido e' so' produtos, sem frete do
    # comprador.
    assert "total_amount" not in connector.QUERY
    assert "shipping_fee" not in connector.QUERY


def test_query_nunca_seleciona_cpf():
    assert "cpf" not in connector.QUERY.lower()


def test_query_cancelled_esta_fora_da_allowlist_elegivel():
    assert "CANCELLED" not in connector.ELIGIBLE_ORDER_STATUSES
    assert "CANCELLED" in connector.KNOWN_ORDER_STATUSES
    assert set(connector.ELIGIBLE_ORDER_STATUSES) == {"COMPLETED", "DELIVERED", "IN_TRANSIT"}


def test_query_raw_e_a_tabela_dirigente_gold_e_left_join():
    # raw_daily deve ser o FROM (tabela dirigente); gold so' entra via
    # LEFT JOIN — um dia com pedidos na Raw nunca pode desaparecer so'
    # porque a Gold nao tem linha correspondente.
    assert "FROM raw_daily r" in connector.QUERY
    assert "LEFT JOIN gold.tiktok_brand_daily g" in connector.QUERY
    # a Gold nao pode aparecer como FROM principal
    assert "FROM gold.tiktok_brand_daily" not in connector.QUERY


def test_query_dedup_deterministico_de_order_id():
    assert "DISTINCT ON (order_id)" in connector.QUERY
    assert "ORDER BY order_id, updated_at DESC NULLS LAST, id DESC" in connector.QUERY


def test_query_usa_bind_parameters_para_brands_e_datas():
    assert ":brands" in connector.QUERY
    assert ":date_from" in connector.QUERY
    assert ":date_to_exclusive" in connector.QUERY
    assert ":eligible_statuses" in connector.QUERY
    assert ":known_statuses" in connector.QUERY


def test_query_status_nulo_conta_como_inesperado():
    assert "order_status IS NULL OR order_status NOT IN :known_statuses" in connector.QUERY


def test_query_orders_prefere_gold_com_fallback_para_raw_elegivel():
    assert "COALESCE(g.orders, r.orders_eligible) AS orders" in connector.QUERY


def test_query_avg_ticket_usa_o_mesmo_valor_escolhido_para_orders():
    assert "ROUND(r.gmv / COALESCE(g.orders, r.orders_eligible), 2)" in connector.QUERY


def test_query_gmv_video_live_card_preservados():
    assert "g.gmv_video" in connector.QUERY
    assert "g.gmv_live" in connector.QUERY
    assert "g.gmv_card" in connector.QUERY


def test_query_conta_sub_total_nulo_em_pedido_elegivel():
    assert "orders_eligible_null_subtotal" in connector.QUERY
    assert "order_status IN :eligible_statuses AND sub_total IS NULL" in connector.QUERY


# ---------------------------------------------------------------------------
# fetch(): parametrização e pós-processamento (datamart_query monkeypatchado)
# ---------------------------------------------------------------------------
def test_fetch_filtra_intervalo_e_marcas_antes_da_agregacao(monkeypatch):
    captured = {}

    def fake_datamart_query(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(connector, "datamart_query", fake_datamart_query)

    date_from = date(2026, 1, 1)
    date_to = date(2026, 5, 31)
    connector.fetch(date_from, date_to)

    assert captured["params"]["brands"] == connector.BRANDS_IN_SCOPE
    assert captured["params"]["date_from"] == date_from
    assert captured["params"]["date_to_exclusive"] == date_to + timedelta(days=1)
    assert captured["params"]["eligible_statuses"] == connector.ELIGIBLE_ORDER_STATUSES
    assert captured["params"]["known_statuses"] == connector.KNOWN_ORDER_STATUSES


def _base_row(**overrides):
    row = {
        "date": date(2026, 1, 5),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders_unexpected_status": 0,
        "orders_eligible_null_subtotal": 0,
        "orders": 15,
        "avg_ticket": 66.67,
        "units_sold": 20,
        "unique_buyers": 10,
        "visitors": None,
        "conversion_rate": None,
        "canceled_orders": 3,
        "returned_orders": 0,
        "refunded_orders": 0,
        "problem_rate": 0.0,
        "delivered_orders": 12,
        "avg_delivery_hours": 10.0,
        "gmv_video": 400.0,
        "gmv_live": 300.0,
        "gmv_card": 300.0,
        "total_settlement": 950.0,
        "total_fees": -50.0,
    }
    row.update(overrides)
    return row


def test_fetch_nao_vaza_campos_internos_nem_pii_na_saida(monkeypatch):
    def fake_datamart_query(sql, params):
        return [_base_row()]

    monkeypatch.setattr(connector, "datamart_query", fake_datamart_query)

    rows = connector.fetch(date(2026, 1, 1), date(2026, 1, 31))

    assert len(rows) == 1
    row = rows[0]
    # campos internos (usados so' para calcular/alertar/bloquear) nao vazam
    assert "orders_unexpected_status" not in row
    assert "orders_eligible_null_subtotal" not in row
    assert "orders_eligible" not in row
    # nenhuma PII jamais deveria estar aqui — nenhum caminho do conector
    # seleciona cpf/order_id, mas a defesa aqui garante que um retorno
    # inesperado da fonte tambem nao vazaria por acidente no output esperado
    assert "cpf" not in row
    assert "order_id" not in row
    assert row["gmv"] == 1000.0
    # conteudo TikTok preservado (Gate R2.1)
    assert row["gmv_video"] == 400.0
    assert row["gmv_live"] == 300.0
    assert row["gmv_card"] == 300.0


def test_fetch_avisa_no_log_quando_ha_status_fora_da_allowlist(monkeypatch, caplog):
    def fake_datamart_query(sql, params):
        return [_base_row(orders_unexpected_status=2)]

    monkeypatch.setattr(connector, "datamart_query", fake_datamart_query)

    import logging

    with caplog.at_level(logging.WARNING, logger=connector.logger.name):
        connector.fetch(date(2026, 1, 1), date(2026, 1, 31))

    assert any("fora da allowlist" in rec.message for rec in caplog.records)


def test_fetch_sem_status_inesperado_nao_gera_warning(monkeypatch, caplog):
    def fake_datamart_query(sql, params):
        return [_base_row()]

    monkeypatch.setattr(connector, "datamart_query", fake_datamart_query)

    import logging

    with caplog.at_level(logging.WARNING, logger=connector.logger.name):
        connector.fetch(date(2026, 1, 1), date(2026, 1, 31))

    assert not any("fora da allowlist" in rec.message for rec in caplog.records)


def test_fetch_dia_sem_linha_na_gold_nao_desaparece(monkeypatch):
    """Simula o resultado do LEFT JOIN quando a Gold nao tem linha para o
    dia: os campos passthrough de gold vem None (inclusive `orders`, que
    nesse caso cai no fallback SQL para orders_eligible — aqui simulado
    diretamente no valor ja' resolvido pelo COALESCE), mas a linha (com GMV
    da Raw) continua presente."""

    def fake_datamart_query(sql, params):
        return [
            _base_row(
                orders=5,  # já resolvido pelo COALESCE (fallback = orders_eligible)
                units_sold=None,
                unique_buyers=None,
                canceled_orders=None,
                returned_orders=None,
                refunded_orders=None,
                problem_rate=None,
                delivered_orders=None,
                avg_delivery_hours=None,
                gmv_video=None,
                gmv_live=None,
                gmv_card=None,
                total_settlement=None,
                total_fees=None,
                gmv=500.0,
                avg_ticket=100.0,
            )
        ]

    monkeypatch.setattr(connector, "datamart_query", fake_datamart_query)

    rows = connector.fetch(date(2026, 1, 1), date(2026, 1, 31))

    assert len(rows) == 1
    assert rows[0]["gmv"] == 500.0
    assert rows[0]["orders"] == 5


def test_fetch_bloqueia_quando_ha_sub_total_nulo_em_pedido_elegivel(monkeypatch):
    def fake_datamart_query(sql, params):
        return [_base_row(orders_eligible_null_subtotal=3)]

    monkeypatch.setattr(connector, "datamart_query", fake_datamart_query)

    with pytest.raises(connector.TikTokConnectorError) as excinfo:
        connector.fetch(date(2026, 1, 1), date(2026, 1, 31))

    assert "sub_total" in str(excinfo.value)


def test_fetch_sem_sub_total_nulo_nao_bloqueia(monkeypatch):
    def fake_datamart_query(sql, params):
        return [_base_row(orders_eligible_null_subtotal=0)]

    monkeypatch.setattr(connector, "datamart_query", fake_datamart_query)

    rows = connector.fetch(date(2026, 1, 1), date(2026, 1, 31))
    assert len(rows) == 1
