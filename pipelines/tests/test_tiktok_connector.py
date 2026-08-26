"""
Testes focais do Gate DQ-TK1 — coerência comercial do TikTok.

O que este gate fixou, e que estes testes travam:
  - GMV = SUM(total_amount) (inclui o frete pago pelo comprador), não sub_total;
  - GMV, pedidos e ticket saem da MESMA população comercial;
  - `g.orders` (relatório de conteúdo) nunca volta a ser denominador do ticket;
  - AWAITING_COLLECTION/AWAITING_SHIPMENT são comerciais (prova da Fase A:
    100% com `paid_at`); UNPAID/CANCELLED/ON_HOLD não são;
  - status desconhecido/nulo BLOQUEIA antes da escrita (era só warning);
  - total_amount nulo em pedido comercial BLOQUEIA;
  - gmv_video/live/card seguem intocados e nenhuma fórmula força sua soma a
    igualar o headline;
  - cancelados/devolvidos/reembolsados indisponíveis resultam em None;
  - o lookback incremental do TikTok tem piso de 10 dias.

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
# Allowlists — a decisão do gate, travada
# ---------------------------------------------------------------------------
def test_populacao_comercial_e_exatamente_a_decidida_no_gate():
    assert set(connector.COMMERCIAL_ORDER_STATUSES) == {
        "COMPLETED",
        "DELIVERED",
        "IN_TRANSIT",
        "AWAITING_COLLECTION",
        "AWAITING_SHIPMENT",
    }


def test_awaiting_collection_e_shipment_sao_comerciais():
    """Fase A: 3.876/3.876 e 4/4 com paid_at (100%); docs/data_contracts.md
    descreve AWAITING_SHIPMENT como 'Pago, aguardando envio pelo seller'."""
    assert "AWAITING_COLLECTION" in connector.COMMERCIAL_ORDER_STATUSES
    assert "AWAITING_SHIPMENT" in connector.COMMERCIAL_ORDER_STATUSES


def test_unpaid_cancelled_on_hold_ficam_fora_do_gmv():
    for status in ("UNPAID", "CANCELLED", "ON_HOLD"):
        assert status not in connector.COMMERCIAL_ORDER_STATUSES
        assert status in connector.NON_COMMERCIAL_ORDER_STATUSES
        assert status in connector.KNOWN_ORDER_STATUSES


def test_known_statuses_lista_todos_os_oito_observados_explicitamente():
    assert set(connector.KNOWN_ORDER_STATUSES) == {
        "COMPLETED",
        "DELIVERED",
        "IN_TRANSIT",
        "AWAITING_COLLECTION",
        "AWAITING_SHIPMENT",
        "UNPAID",
        "CANCELLED",
        "ON_HOLD",
    }
    # nenhum status pode estar nas duas listas ao mesmo tempo
    assert not set(connector.COMMERCIAL_ORDER_STATUSES) & set(
        connector.NON_COMMERCIAL_ORDER_STATUSES
    )
    # KNOWN é exatamente a união, sem sobra nem falta
    assert len(connector.KNOWN_ORDER_STATUSES) == len(
        connector.COMMERCIAL_ORDER_STATUSES
    ) + len(connector.NON_COMMERCIAL_ORDER_STATUSES)


# ---------------------------------------------------------------------------
# Forma da query (shape assertions sobre o texto SQL)
# ---------------------------------------------------------------------------
def test_query_usa_total_amount_como_base_do_gmv():
    assert (
        "SUM(CASE WHEN order_status IN :commercial_statuses THEN total_amount ELSE 0 END)"
        in connector.QUERY
    )


def test_query_nao_usa_mais_sub_total():
    """sub_total era o GMV do Gate R2 (sem frete). O gate decidiu incluir o
    frete: nenhuma referência a sub_total pode sobrar."""
    assert "sub_total" not in connector.QUERY


def test_query_gmv_e_orders_usam_a_mesma_allowlist():
    assert (
        "COUNT(*) FILTER (WHERE order_status IN :commercial_statuses)" in connector.QUERY
    )
    assert "r.orders_commercial AS orders" in connector.QUERY


def test_query_avg_ticket_usa_os_mesmos_orders_comerciais():
    assert "ROUND(r.gmv / r.orders_commercial, 2)" in connector.QUERY
    assert "WHEN r.orders_commercial > 0" in connector.QUERY


def test_query_g_orders_nao_volta_a_ser_denominador_comercial():
    """A regressão que este gate existe para impedir."""
    assert "COALESCE(g.orders, r.orders_eligible)" not in connector.QUERY
    assert "COALESCE(g.orders" not in connector.QUERY
    # g.orders só pode aparecer com nome próprio e distinto
    assert "g.orders AS content_orders" in connector.QUERY


def test_query_nunca_seleciona_cpf():
    assert "cpf" not in connector.QUERY.lower()


def test_query_raw_e_a_tabela_dirigente_gold_e_left_join():
    assert "FROM raw_daily r" in connector.QUERY
    assert "LEFT JOIN gold.tiktok_brand_daily g" in connector.QUERY
    assert "FROM gold.tiktok_brand_daily" not in connector.QUERY


def test_query_dedup_deterministico_de_order_id():
    assert "DISTINCT ON (order_id)" in connector.QUERY
    assert "ORDER BY order_id, updated_at DESC NULLS LAST, id DESC" in connector.QUERY


def test_query_usa_bind_parameters_para_brands_datas_e_status():
    for bind in (":brands", ":date_from", ":date_to_exclusive",
                 ":commercial_statuses", ":known_statuses"):
        assert bind in connector.QUERY


def test_query_conta_status_desconhecido_ou_nulo():
    assert "order_status IS NULL OR order_status NOT IN :known_statuses" in connector.QUERY
    assert "orders_unknown_status" in connector.QUERY


def test_query_conta_total_amount_nulo_em_pedido_comercial():
    assert "orders_commercial_null_amount" in connector.QUERY
    assert (
        "order_status IN :commercial_statuses AND total_amount IS NULL" in connector.QUERY
    )


def test_query_cancelados_sao_contados_na_raw_deduplicada():
    """Cancelamento TEM fonte: o status CANCELLED na Raw já deduplicada por
    order_id. Contado no MESMO raw_daily do GMV, por data/marca."""
    assert "COUNT(*) FILTER (WHERE order_status = 'CANCELLED')" in connector.QUERY
    assert "AS orders_canceled" in connector.QUERY
    assert "r.orders_canceled             AS canceled_orders" in connector.QUERY
    # o COUNT vive dentro de raw_daily, que agrupa por data/marca sobre raw_dedup
    assert "FROM raw_dedup" in connector.QUERY
    assert "GROUP BY created_at::date, brand" in connector.QUERY


def test_query_nunca_le_cancelado_devolvido_reembolsado_da_gold():
    """A Gold grava 0 literal nos três — nenhum deles pode voltar a ser lido
    de lá."""
    assert "g.canceled" not in connector.QUERY
    assert "g.returned" not in connector.QUERY
    assert "g.refunded" not in connector.QUERY
    assert "g.problem_rate" not in connector.QUERY


def test_query_cancelled_fica_fora_do_gmv_e_dos_orders_comerciais():
    """Contar CANCELLED não pode contaminar o headline."""
    assert "CANCELLED" not in connector.COMMERCIAL_ORDER_STATUSES
    # o GMV soma apenas :commercial_statuses
    assert (
        "SUM(CASE WHEN order_status IN :commercial_statuses THEN total_amount ELSE 0 END)"
        in connector.QUERY
    )
    # `orders` é a contagem comercial, não a total nem a comercial+cancelada
    assert "r.orders_commercial AS orders" in connector.QUERY
    assert "r.orders_commercial + r.orders_canceled) AS orders" not in connector.QUERY


def test_query_taxa_de_cancelamento_usa_comercial_mais_cancelado():
    """Contrato já adotado pela Torre: canceled / (comercial + canceled)."""
    assert "WHEN (r.orders_commercial + r.orders_canceled) > 0" in connector.QUERY
    assert "r.orders_canceled::numeric" in connector.QUERY
    assert "/ (r.orders_commercial + r.orders_canceled) * 100, 2)" in connector.QUERY
    assert "END AS cancel_rate_pct" in connector.QUERY


def test_query_taxa_de_cancelamento_e_null_com_denominador_zero():
    """Sem pedido resolvido no dia não existe taxa — 0% seria falso."""
    trecho = connector.QUERY[
        connector.QUERY.index("cancel_rate_pct") - 400 :
        connector.QUERY.index("END AS cancel_rate_pct") + 30
    ]
    assert "ELSE NULL" in trecho


def test_query_taxa_de_cancelamento_evita_divisao_inteira():
    """Dois COUNT são bigint: sem cast, 1/(1+99) daria 0."""
    assert "r.orders_canceled::numeric" in connector.QUERY


def test_query_devolvido_reembolsado_e_problem_rate_sao_null():
    """Sem fonte na Raw (nenhum status de devolução/reembolso existe) e sem
    fonte completa para problem_rate: NULL, nunca 0."""
    assert "NULL::bigint                  AS returned_orders" in connector.QUERY
    assert "NULL::bigint                  AS refunded_orders" in connector.QUERY
    assert "NULL::numeric                 AS problem_rate" in connector.QUERY


def test_nenhum_status_de_devolucao_existe_nos_conhecidos():
    """Justifica returned/refunded/problem_rate serem NULL: não há de onde
    tirá-los. Se um status de devolução aparecer na fonte, o bloqueio de
    status desconhecido força a revisão desta decisão."""
    for status in connector.KNOWN_ORDER_STATUSES:
        assert "RETURN" not in status.upper()
        assert "REFUND" not in status.upper()


def test_query_gmv_video_live_card_permanecem_passthrough_inalterado():
    assert "g.gmv_video" in connector.QUERY
    assert "g.gmv_live" in connector.QUERY
    assert "g.gmv_card" in connector.QUERY


def test_query_nao_forca_conteudo_a_fechar_com_o_headline():
    """Nenhum rateio/escala: os três campos entram crus, sem operador
    aritmético que os reescale para igualar `gmv`."""
    q = connector.QUERY
    for campo in ("gmv_video", "gmv_live", "gmv_card"):
        # nenhuma forma de rateio do tipo `gmv * gmv_video / (...)`
        assert f"gmv * g.{campo}" not in q
        assert f"g.{campo} * " not in q
        assert f"g.{campo} / " not in q
    # a soma dos três nunca é usada para reescalar nada
    assert "g.gmv_video + g.gmv_live + g.gmv_card" not in q


# ---------------------------------------------------------------------------
# Lookback — piso de 10 dias
# ---------------------------------------------------------------------------
def test_piso_do_lookback_incremental_e_dez_dias():
    assert connector.MIN_INCREMENTAL_LOOKBACK_DAYS == 10


def test_fetch_incremental_eleva_janela_curta_ao_piso(monkeypatch):
    capturado = {}

    def fake_fetch(date_from, date_to):
        capturado["dias"] = (date_to - date_from).days
        return []

    monkeypatch.setattr(connector, "fetch", fake_fetch)
    connector.fetch_incremental(days_back=3)
    assert capturado["dias"] == 10


def test_fetch_incremental_nao_reduz_janela_maior(monkeypatch):
    capturado = {}

    def fake_fetch(date_from, date_to):
        capturado["dias"] = (date_to - date_from).days
        return []

    monkeypatch.setattr(connector, "fetch", fake_fetch)
    connector.fetch_incremental(days_back=30)
    assert capturado["dias"] == 30


def test_fetch_incremental_default_e_o_piso(monkeypatch):
    capturado = {}

    def fake_fetch(date_from, date_to):
        capturado["dias"] = (date_to - date_from).days
        return []

    monkeypatch.setattr(connector, "fetch", fake_fetch)
    connector.fetch_incremental()
    assert capturado["dias"] == 10


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

    date_from = date(2026, 8, 1)
    date_to = date(2026, 8, 24)
    connector.fetch(date_from, date_to)

    assert captured["params"]["brands"] == connector.BRANDS_IN_SCOPE
    assert captured["params"]["date_from"] == date_from
    assert captured["params"]["date_to_exclusive"] == date_to + timedelta(days=1)
    assert captured["params"]["commercial_statuses"] == connector.COMMERCIAL_ORDER_STATUSES
    assert captured["params"]["known_statuses"] == connector.KNOWN_ORDER_STATUSES


def _base_row(**overrides):
    row = {
        "date": date(2026, 8, 5),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders_unknown_status": 0,
        "orders_commercial_null_amount": 0,
        "orders": 15,
        "avg_ticket": 66.67,
        "content_orders": 19,
        "units_sold": 20,
        "unique_buyers": 10,
        "visitors": None,
        "conversion_rate": None,
        "canceled_orders": 4,
        "cancel_rate_pct": 21.05,
        "returned_orders": None,
        "refunded_orders": None,
        "problem_rate": None,
        "delivered_orders": 12,
        "avg_delivery_hours": 10.0,
        "gmv_video": 400.0,
        "gmv_live": 300.0,
        "gmv_card": 350.0,
        "total_settlement": 950.0,
        "total_fees": -50.0,
    }
    row.update(overrides)
    return row


def test_fetch_nao_vaza_campos_internos_nem_pii_na_saida(monkeypatch):
    monkeypatch.setattr(connector, "datamart_query", lambda sql, params: [_base_row()])

    rows = connector.fetch(date(2026, 8, 1), date(2026, 8, 24))

    assert len(rows) == 1
    row = rows[0]
    # campos internos (usados so' para bloquear) nao vazam
    assert "orders_unknown_status" not in row
    assert "orders_commercial_null_amount" not in row
    assert "orders_commercial" not in row
    # nenhuma PII jamais deveria estar aqui
    assert "cpf" not in row
    assert "order_id" not in row
    assert row["gmv"] == 1000.0
    # conteudo preservado intacto
    assert row["gmv_video"] == 400.0
    assert row["gmv_live"] == 300.0
    assert row["gmv_card"] == 350.0
    # pedidos de conteudo existem, com nome proprio e distinto de `orders`
    assert row["content_orders"] == 19
    assert row["orders"] == 15


def test_fetch_conteudo_nao_e_ajustado_para_fechar_com_o_gmv(monkeypatch):
    """gmv_video+live+card = 1050 contra gmv = 1000: a diferença é REAL e
    deve sobreviver ao conector sem nenhum ajuste."""
    monkeypatch.setattr(connector, "datamart_query", lambda sql, params: [_base_row()])

    row = connector.fetch(date(2026, 8, 1), date(2026, 8, 24))[0]

    soma_conteudo = row["gmv_video"] + row["gmv_live"] + row["gmv_card"]
    assert soma_conteudo == 1050.0
    assert row["gmv"] == 1000.0
    assert soma_conteudo != row["gmv"]


def test_fetch_bloqueia_status_desconhecido_ou_nulo(monkeypatch):
    """Antes era warning — e foi assim que AWAITING_COLLECTION ficou fora do
    GMV sem ninguém ver."""
    monkeypatch.setattr(
        connector, "datamart_query", lambda sql, params: [_base_row(orders_unknown_status=2)]
    )

    with pytest.raises(connector.TikTokConnectorError) as excinfo:
        connector.fetch(date(2026, 8, 1), date(2026, 8, 24))

    assert "order_status" in str(excinfo.value)
    assert "bloqueado" in str(excinfo.value)


def test_fetch_bloqueia_total_amount_nulo_em_pedido_comercial(monkeypatch):
    monkeypatch.setattr(
        connector,
        "datamart_query",
        lambda sql, params: [_base_row(orders_commercial_null_amount=3)],
    )

    with pytest.raises(connector.TikTokConnectorError) as excinfo:
        connector.fetch(date(2026, 8, 1), date(2026, 8, 24))

    assert "total_amount" in str(excinfo.value)


def test_fetch_sem_anomalia_nao_bloqueia(monkeypatch):
    monkeypatch.setattr(connector, "datamart_query", lambda sql, params: [_base_row()])
    assert len(connector.fetch(date(2026, 8, 1), date(2026, 8, 24))) == 1


def test_fetch_entrega_cancelados_medidos_e_taxa_derivada(monkeypatch):
    """canceled vem do COUNT da Raw; a taxa acompanha, sem passar pela Gold."""
    monkeypatch.setattr(connector, "datamart_query", lambda sql, params: [_base_row()])

    row = connector.fetch(date(2026, 8, 1), date(2026, 8, 24))[0]

    assert row["canceled_orders"] == 4
    assert row["cancel_rate_pct"] == 21.05
    # a taxa confere com canceled / (comercial + canceled) * 100
    assert round(4 / (15 + 4) * 100, 2) == 21.05


def test_fetch_devolvido_reembolsado_problem_rate_sempre_none(monkeypatch):
    monkeypatch.setattr(connector, "datamart_query", lambda sql, params: [_base_row()])

    row = connector.fetch(date(2026, 8, 1), date(2026, 8, 24))[0]

    assert row["returned_orders"] is None
    assert row["refunded_orders"] is None
    assert row["problem_rate"] is None


def test_fetch_dia_sem_linha_na_gold_nao_desaparece(monkeypatch):
    """A Raw é a tabela dirigente: um dia com pedidos comerciais produz linha
    mesmo sem correspondente na Gold — e agora `orders` vem da Raw sempre,
    então não existe mais fallback para resolver."""
    monkeypatch.setattr(
        connector,
        "datamart_query",
        lambda sql, params: [
            _base_row(
                orders=5,
                gmv=500.0,
                avg_ticket=100.0,
                content_orders=None,
                units_sold=None,
                unique_buyers=None,
                problem_rate=None,
                delivered_orders=None,
                avg_delivery_hours=None,
                gmv_video=None,
                gmv_live=None,
                gmv_card=None,
                total_settlement=None,
                total_fees=None,
            )
        ],
    )

    rows = connector.fetch(date(2026, 8, 1), date(2026, 8, 24))

    assert len(rows) == 1
    assert rows[0]["gmv"] == 500.0
    assert rows[0]["orders"] == 5
    assert rows[0]["content_orders"] is None
