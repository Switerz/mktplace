"""
MARGEM-REAL-2 — comissão do Mercado Livre: transform e forma da consulta.

Estes testes não tocam banco. Provam as regras que o gate fixou para o valor
que atravessa `connector -> transform -> daily_performance`:

  * o sinal da fonte é preservado (positivo, como a Shopee);
  * ausência continua `None` — nunca zero;
  * `avg_fee_pct` só existe com denominador válido;
  * `total_fees` carrega SOMENTE a tarifa do marketplace.

A prova contra PostgreSQL real está em `test_ml_comissao_sql.py`; aqui se
mede o que não precisa de banco para ser verdadeiro.
"""
from __future__ import annotations

from datetime import date

from pipelines.connectors.mercadolivre import connector as ml_connector
from pipelines.transforms import ml_gestao_diaria


def _row(**over) -> dict:
    base = {
        "date": date(2026, 7, 15),
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders": 10,
        "marketplace_fee": 170.0,
        "fee_orders": 10,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Sinal e conteúdo
# ---------------------------------------------------------------------------

def test_sinal_positivo_da_fonte_e_preservado():
    """A fonte grava positivo e o armazenamento não troca o sinal.

    Medido em `api.ml_order_line_items`: 566.509 linhas com `sale_fee > 0`,
    3 com zero, ZERO negativas. A Shopee também grava positivo; o TikTok grava
    negativo. Quem lê precisa saber qual convenção está lendo, e o jeito de
    garantir isso é não alterá-la no meio do caminho.
    """
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=170.0))
    assert canonical["total_fees"] == 170.0
    assert canonical["total_fees"] > 0


def test_sinal_negativo_tambem_atravessa_sem_correcao_silenciosa():
    """Se a fonte um dia mudar de convenção, o transform não mascara.

    Um `abs()` aqui esconderia a mudança e faria o número parecer certo — é
    exatamente o tipo de correção silenciosa que o gate proíbe.
    """
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=-170.0))
    assert canonical["total_fees"] == -170.0


def test_total_fees_nao_soma_ads_frete_nem_afiliado():
    """`total_fees` é só a tarifa. Ads e frete têm colunas próprias.

    Somá-los aqui seria dupla contagem na mesma linha — o mesmo defeito que o
    diagnóstico mediu no TikTok, onde a comissão de afiliado já está dentro de
    `total_fee_tax_amount`.
    """
    canonical = ml_gestao_diaria.transform(
        _row(marketplace_fee=170.0, ad_spend=500.0, seller_shipping_cost=300.0)
    )
    assert canonical["total_fees"] == 170.0
    assert canonical["ad_spend"] == 500.0
    assert canonical["seller_shipping_cost"] == 300.0


# ---------------------------------------------------------------------------
# Ausência nunca vira zero
# ---------------------------------------------------------------------------

def test_fee_ausente_permanece_none_e_nao_zero():
    """Dia sem pedido pago: o LEFT JOIN devolve NULL e NULL é o que se grava.

    Zero diria "o marketplace não cobrou nada", que é afirmação diferente de
    "não sabemos quanto cobrou".
    """
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=None))
    assert canonical["total_fees"] is None
    assert canonical["avg_fee_pct"] is None


def test_fee_zero_legitimo_atravessa_como_zero():
    """Zero MEDIDO é dado, e não se confunde com ausência."""
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=0.0))
    assert canonical["total_fees"] == 0.0
    assert canonical["avg_fee_pct"] == 0.0


def test_chave_ausente_no_row_nao_quebra_o_transform():
    row = _row()
    del row["marketplace_fee"]
    canonical = ml_gestao_diaria.transform(row)
    assert canonical["total_fees"] is None
    assert canonical["avg_fee_pct"] is None


# ---------------------------------------------------------------------------
# avg_fee_pct: só com denominador válido
# ---------------------------------------------------------------------------

def test_avg_fee_pct_calculado_sobre_o_gmv_da_mesma_linha():
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=170.0, gmv=1000.0))
    assert canonical["avg_fee_pct"] == 17.0


def test_avg_fee_pct_none_quando_gmv_zero():
    """GMV zero não vira divisão por zero nem percentual inventado."""
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=170.0, gmv=0.0))
    assert canonical["total_fees"] == 170.0
    assert canonical["avg_fee_pct"] is None


def test_avg_fee_pct_none_quando_gmv_none():
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=170.0, gmv=None))
    assert canonical["avg_fee_pct"] is None


def test_avg_fee_pct_none_quando_gmv_negativo():
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=170.0, gmv=-50.0))
    assert canonical["avg_fee_pct"] is None


def test_nan_no_fee_nao_vira_percentual():
    """`float('nan')` passa por qualquer comparação `>= 0` sem levantar.

    O guard tem de ser explícito, senão um NaN atravessa e contamina a fato.
    """
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee=float("nan")))
    assert canonical["avg_fee_pct"] is None


def test_nan_no_gmv_nao_vira_percentual():
    canonical = ml_gestao_diaria.transform(_row(gmv=float("nan")))
    assert canonical["avg_fee_pct"] is None


def test_fee_em_texto_nao_derruba_o_transform():
    """Numeric do psycopg2 pode chegar como Decimal ou string dependendo do
    driver. O que não pode é levantar no meio de uma carga."""
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee="170.0", gmv="1000.0"))
    assert canonical["avg_fee_pct"] == 17.0


def test_fee_em_texto_invalido_vira_none():
    canonical = ml_gestao_diaria.transform(_row(marketplace_fee="n/a"))
    assert canonical["avg_fee_pct"] is None


# ---------------------------------------------------------------------------
# Marca
# ---------------------------------------------------------------------------

def test_marca_desconhecida_retorna_none_mesmo_com_fee():
    """Fee não autoriza criar linha para loja que não existe na dimensão."""
    assert ml_gestao_diaria.transform(_row(brand="azbuy")) is None


def test_todas_as_marcas_do_escopo_do_conector_mapeiam_para_loja():
    """Contraprova de escopo: se o conector busca uma marca, o transform
    precisa saber mapeá-la — senão a linha é descartada em silêncio."""
    for brand in ml_connector.BRANDS_IN_SCOPE:
        assert brand in ml_gestao_diaria.BRAND_TO_LOJA, brand


# ---------------------------------------------------------------------------
# Forma da consulta do conector
# ---------------------------------------------------------------------------

def test_query_junta_por_order_id_e_nunca_pelo_surrogate():
    """`api.ml_orders.id` é surrogate sequencial (1, 2, 3, …).

    Um join por ele devolve ZERO linha sem erro — falso negativo silencioso.
    A prova executável dessa armadilha está em `test_ml_comissao_sql.py`.
    """
    sql = ml_connector.QUERY
    assert "li.order_id = o.order_id" in sql
    assert "li.order_id = o.id" not in sql
    assert "o.id" not in sql


def test_query_multiplica_o_fee_pela_quantidade():
    """`sale_fee` é POR UNIDADE. Somar cru subestima a comissão em ~2,5%."""
    assert "li.sale_fee * li.quantity" in ml_connector.QUERY


def test_query_restringe_a_populacao_paga():
    """A população da Torre é `status = 'paid'`; cancelado e parcialmente
    reembolsado ficam fora dos dois lados da razão."""
    assert "o.status = 'paid'" in ml_connector.QUERY


def test_query_usa_left_join_para_nao_perder_dia_da_gold():
    """A gold é a espinha. Um INNER JOIN apagaria da fato todo dia sem
    comissão conhecida — perda de GMV para ganhar cobertura de fee."""
    sql = ml_connector.QUERY
    assert "LEFT JOIN ml_fees" in sql
    assert "FROM gold.ml_gestao_diaria g" in sql


def test_query_fecha_a_janela_pela_direita_com_limite_exclusivo():
    """`date_created` é timestamp. `<= :date_to` perderia o último dia inteiro,
    porque nenhum pedido tem hora 00:00:00 exata."""
    assert "o.date_created < (CAST(:date_to AS date) + 1)" in ml_connector.QUERY


def test_query_filtra_marca_nos_dois_lados():
    """Sem o filtro no CTE, o agregado varre marcas fora de escopo para depois
    descartá-las no join — trabalho e risco sem ganho."""
    sql = ml_connector.QUERY
    assert "o.brand IN :brands" in sql
    assert "g.brand IN :brands" in sql
