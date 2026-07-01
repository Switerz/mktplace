"""
Regressao do bug de data futura em marts.fact_shopee_product_monthly
(ver docs/sections/produtos_audit.md e docs/backlog.md).

Causa raiz: "Data de criacao do pedido" nos exports Shopee vem em formato
ISO ("YYYY-MM-DD HH:MM"), mas o parser antigo usava
pd.to_datetime(..., dayfirst=True), que troca dia/mes mesmo em datas ISO
quando o dia de origem e' <= 12 — projetando pedidos de jan-jun/2026 para
meses futuros inexistentes (jul-dez/2026).
"""
import openpyxl
import pandas as pd

from etl.load_shopee_products import _load_brand


def _write_orders_xlsx(path, order_dates):
    wb = openpyxl.Workbook()
    ws = wb.active
    header = [
        "Data de criação do pedido",
        "Nº de referência do SKU principal",
        "Nome do Produto",
        "Nome da variação",
        "Quantidade",
        "Subtotal do produto",
        "Status do pedido",
        "Nome de usuário (comprador)",
    ]
    ws.append(header)
    for i, d in enumerate(order_dates):
        ws.append([d, f"SKU{i}", "Produto Teste", None, 1, "10,00", "Concluído", f"user{i}"])
    wb.save(path)


def test_ref_month_nao_avanca_para_mes_futuro_com_datas_iso(tmp_path, monkeypatch):
    brand = "apice"
    brand_dir = tmp_path / brand
    brand_dir.mkdir()

    # Todos os pedidos sao de janeiro/2026, incluindo dias <=12 (o caso ambiguo do bug).
    order_dates = [
        "2026-01-01 12:21",
        "2026-01-05 09:00",
        "2026-01-12 08:54",   # dia=12 -> era invertido para mes=12 (dezembro) no bug antigo
        "2026-01-20 18:00",
        "2026-01-31 23:08",
    ]
    _write_orders_xlsx(brand_dir / "Order.all.20260101_20260131.xlsx", order_dates)

    monkeypatch.setattr("etl.load_shopee_products.SHOPEE_ROOT", tmp_path)

    df = _load_brand(brand)

    assert df is not None
    assert not df.empty
    assert set(df["ref_month"].dt.month.unique()) == {1}
    assert set(df["ref_month"].dt.year.unique()) == {2026}
    # nenhuma linha deve cair em um mes >= 7 (impossivel dado o range dos exports)
    assert (df["ref_month"].dt.month < 7).all()


def test_datas_invalidas_sao_descartadas_nao_geram_ref_month_incorreto(tmp_path, monkeypatch):
    brand = "kokeshi"
    brand_dir = tmp_path / brand
    brand_dir.mkdir()

    _write_orders_xlsx(
        brand_dir / "Order.all.20260201_20260228.xlsx",
        ["2026-02-01 10:00", "texto-invalido", None],
    )
    monkeypatch.setattr("etl.load_shopee_products.SHOPEE_ROOT", tmp_path)

    df = _load_brand(brand)

    assert df is not None
    # linhas sem data valida sao descartadas (dropna em order_date/ref_month)
    assert len(df) == 1
    assert df.iloc[0]["ref_month"] == pd.Timestamp("2026-02-01")
