"""
Regressao do Bug 8 em marts.fact_shopee_product_monthly (ver
docs/sections/produtos_audit.md e docs/backlog.md).

Causa raiz: _aggregate() fazia agg_completed.merge(agg_canceled, how="left").
Um grupo (brand, ref_month, sku_ref, product_name, variation_name) com
SOMENTE pedidos "Cancelado" (zero "Concluido") nunca existe em
agg_completed, entao era descartado inteiro pelo left merge — o pedido
cancelado desaparecia de canceled_orders/cancel_rate_pct. GMV/units_sold
nunca sao afetados (esses grupos tem gmv=0 de qualquer forma).

Corrigido para how="outer" + fillna nas colunas do lado agg_completed.
Decisao explicita: unique_buyers de um grupo so-cancelado fica 0 — nunique()
so' e' calculado sobre compradores de pedidos concluidos, nunca sobre
cancelados (nao muda o significado da metrica para nenhum grupo existente).
"""
import pandas as pd

from etl.load_shopee_products import _aggregate

GRP_COLS = ["brand", "ref_month", "sku_ref", "product_name", "variation_name"]


def _row(brand="kokeshi", ref_month="2026-01-01", sku_ref="SKU1", product_name="Produto A",
         variation_name=None, status="Concluído", qty=1, subtotal=10.0, buyer="user1"):
    return {
        "brand": brand, "ref_month": pd.Timestamp(ref_month), "sku_ref": sku_ref,
        "product_name": product_name, "variation_name": variation_name,
        "status": status, "qty": qty, "subtotal": subtotal, "buyer_username": buyer,
    }


def test_grupo_so_cancelado_nao_e_mais_descartado():
    df = pd.DataFrame([
        _row(product_name="Produto A", status="Concluído", qty=2, subtotal=100.0, buyer="buyerA"),
        _row(product_name="Produto B", sku_ref="SKU2", status="Cancelado", subtotal=50.0, buyer="buyerB"),
        _row(product_name="Produto B", sku_ref="SKU2", status="Cancelado", subtotal=50.0, buyer="buyerC"),
    ])
    result = _aggregate(df)

    assert len(result) == 2, "Produto A e Produto B devem aparecer, mesmo Produto B sendo 100% cancelado"

    produto_b = result[result["product_name"] == "Produto B"].iloc[0]
    assert produto_b["completed_orders"] == 0
    assert produto_b["canceled_orders"] == 2
    assert produto_b["gmv"] == 0.0
    assert produto_b["units_sold"] == 0
    assert produto_b["cancel_rate_pct"] == 100.0
    # avg_price=None e' coagido para NaN ao entrar numa coluna float do
    # DataFrame (comportamento padrao do pandas); o loader em main() ja
    # trata isso com pd.notna() antes de gravar no banco.
    assert pd.isna(produto_b["avg_price"])
    # decisao explicita: buyers de pedido cancelado nao contam em unique_buyers
    assert produto_b["unique_buyers"] == 0


def test_grupo_misto_preserva_comportamento_atual():
    df = pd.DataFrame([
        _row(product_name="Produto C", status="Concluído", qty=3, subtotal=90.0, buyer="buyerX"),
        _row(product_name="Produto C", status="Cancelado", subtotal=30.0, buyer="buyerY"),
    ])
    result = _aggregate(df)

    assert len(result) == 1
    produto_c = result.iloc[0]
    assert produto_c["completed_orders"] == 1
    assert produto_c["canceled_orders"] == 1
    assert produto_c["gmv"] == 90.0
    assert produto_c["units_sold"] == 3
    assert produto_c["unique_buyers"] == 1
    assert produto_c["cancel_rate_pct"] == 50.0


def test_soma_canceled_orders_bate_com_bruto():
    df = pd.DataFrame([
        _row(product_name="Produto A", status="Concluído"),
        _row(product_name="Produto B", sku_ref="SKU2", status="Cancelado"),
        _row(product_name="Produto B", sku_ref="SKU2", status="Cancelado"),
        _row(product_name="Produto C", sku_ref="SKU3", status="Cancelado"),
    ])
    result = _aggregate(df)

    raw_canceled = int((df["status"] == "Cancelado").sum())
    assert int(result["canceled_orders"].sum()) == raw_canceled == 3


def test_gmv_total_nao_muda_com_o_fix():
    df = pd.DataFrame([
        _row(product_name="Produto A", status="Concluído", qty=2, subtotal=100.0),
        _row(product_name="Produto B", sku_ref="SKU2", status="Cancelado", subtotal=999.0),
        _row(product_name="Produto C", sku_ref="SKU3", status="Concluído", qty=1, subtotal=25.0),
        _row(product_name="Produto C", sku_ref="SKU3", status="Cancelado", subtotal=999.0),
    ])
    result = _aggregate(df)

    # GMV nunca inclui subtotal de pedidos cancelados, com ou sem o fix —
    # a correcao so adiciona linhas com gmv=0, nunca soma valor cancelado.
    assert float(result["gmv"].sum()) == 125.0
