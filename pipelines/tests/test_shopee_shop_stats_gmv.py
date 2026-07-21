"""
Testes focais do Gate R2.1 (Projeto R) — shop-stats como fonte autoritativa
do GMV Shopee: gmv = Vendas (BRL) - Vendas Canceladas - Vendas Devolvidas ou
Reembolsadas, calculado por linha diária em
pipelines/connectors/shopee/_parser_shop_stats.py, reutilizando o parser
numérico canônico (_numeric.py::parse_brl_float — nenhum parser monetário
novo).
"""
from __future__ import annotations

import openpyxl
import pytest

from pipelines.connectors.shopee import _parser_shop_stats as sps
from pipelines.connectors.shopee._numeric import ShopeeNumericParseError
from pipelines.ingestion import daily_performance
from pipelines.transforms import shopee_shop_stats_daily as transform_mod

HEADER = [
    "Data", "Visitantes", "Taxa de Conversão de Pedidos",
    "# de compradores", "# de novos compradores", "# de compradores existentes",
    "Repetir Índice de Compras",
    "Vendas (BRL)", "Vendas Canceladas", "Vendas Devolvidas / Reembolsadas",
]


def _write_shop_stats_xlsx(path, daily_rows, header=HEADER, total_row=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)  # row 0: header "de totais"
    ws.append(total_row or (["01/01/2026-31/01/2026"] + [None] * (len(header) - 1)))  # row 1: totais do periodo
    ws.append([None] * len(header))  # row 2: separador vazio
    ws.append(header)  # row 3: header das linhas diarias
    for row in daily_rows:
        ws.append(row)
    wb.save(path)


def _daily_row(date_str="01/01/2026", visitors="100", conv="1,50%",
               buyers="10", new_buyers="5", repeat_buyers="5", repeat_rate="5,00%",
               sales_brl="1000.00", cancelled="100.00", refunded="50.00"):
    return [date_str, visitors, conv, buyers, new_buyers, repeat_buyers, repeat_rate,
            sales_brl, cancelled, refunded]


def _parse(tmp_path, daily_rows, brand="apice", **kwargs):
    brand_dir = tmp_path / brand
    brand_dir.mkdir(exist_ok=True)
    _write_shop_stats_xlsx(
        brand_dir / f"{brand}.shopee-shop-stats.20260101-20260131.xlsx", daily_rows, **kwargs
    )
    return sps.parse_brand_shop_stats(tmp_path, brand)


# ---------------------------------------------------------------------------
# Leitura das 3 colunas financeiras e cálculo do GMV líquido
# ---------------------------------------------------------------------------
def test_le_as_tres_colunas_financeiras_e_calcula_gmv_liquido(tmp_path):
    result = _parse(tmp_path, [_daily_row(sales_brl="1000.00", cancelled="100.00", refunded="50.00")])
    assert len(result) == 1
    r = result[0]
    assert r["sales_brl"] == 1000.00
    assert r["cancelled_sales_brl"] == 100.00
    assert r["refunded_sales_brl"] == 50.00
    assert r["gmv"] == 850.00  # 1000 - 100 - 50


def test_formato_br_com_milhar_aceito_pelo_parser_canonico(tmp_path):
    result = _parse(tmp_path, [_daily_row(sales_brl="1.234,56", cancelled="100,00", refunded="34,56")])
    r = result[0]
    assert r["sales_brl"] == 1234.56
    assert r["gmv"] == pytest.approx(1100.00)  # 1234.56 - 100.00 - 34.56


def test_arredonda_gmv_para_duas_casas(tmp_path):
    result = _parse(tmp_path, [_daily_row(sales_brl="100.005", cancelled="0.001", refunded="0.001")])
    r = result[0]
    assert r["gmv"] == round(100.005 - 0.001 - 0.001, 2)


# ---------------------------------------------------------------------------
# Valor ausente/inválido: ausente bloqueia (linha valida), invalido propaga
# ---------------------------------------------------------------------------
def test_valor_ausente_em_coluna_financeira_obrigatoria_bloqueia(tmp_path):
    with pytest.raises(sps.ShopeeShopStatsError) as excinfo:
        _parse(tmp_path, [_daily_row(sales_brl=None)])
    assert "financeiro" in str(excinfo.value) or "financeira" in str(excinfo.value)


def test_valor_invalido_no_campo_financeiro_propaga_erro_numerico(tmp_path):
    with pytest.raises(ShopeeNumericParseError):
        _parse(tmp_path, [_daily_row(sales_brl="lixo_nao_numerico")])


def test_gmv_negativo_bloqueia(tmp_path):
    with pytest.raises(sps.ShopeeShopStatsError) as excinfo:
        _parse(tmp_path, [_daily_row(sales_brl="100.00", cancelled="80.00", refunded="50.00")])
    assert "negativo" in str(excinfo.value)


def test_colunas_financeiras_obrigatorias_ausentes_no_header_bloqueia(tmp_path):
    header_sem_financeiro = [h for h in HEADER if h not in
                              {"Vendas (BRL)", "Vendas Canceladas", "Vendas Devolvidas / Reembolsadas"}]
    with pytest.raises(sps.ShopeeShopStatsError) as excinfo:
        _parse(
            tmp_path,
            [[row for row, h in zip(_daily_row(), HEADER) if h in header_sem_financeiro]],
            header=header_sem_financeiro,
        )
    assert "ausentes" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Linha mensal total nunca é usada como substituta das linhas diárias
# ---------------------------------------------------------------------------
def test_linha_mensal_total_e_ignorada(tmp_path):
    # total_row com um valor absurdo (999999999) que NUNCA deveria aparecer
    # como GMV de um dia — se o parser usasse a linha de totais por engano,
    # esse valor vazaria para o resultado diario.
    total_row = ["01/01/2026-31/01/2026"] + [None] * 6 + ["999999999.00", "0.00", "0.00"]
    result = _parse(
        tmp_path,
        [_daily_row(date_str="01/01/2026", sales_brl="1000.00", cancelled="100.00", refunded="50.00")],
        total_row=total_row,
    )
    assert len(result) == 1
    assert result[0]["date"].isoformat() == "2026-01-01"
    assert result[0]["gmv"] == 850.00  # nunca 999999999-derivado


# ---------------------------------------------------------------------------
# Transform inclui gmv, sem tocar outras metricas
# ---------------------------------------------------------------------------
def test_transform_inclui_gmv():
    row = {
        "date": __import__("datetime").date(2026, 1, 5),
        "brand": "kokeshi",
        "gmv": 850.00,
        "visitors": 100,
        "conversion_rate": 1.5,
        "unique_buyers": 10,
        "new_buyers": 5,
        "repeat_buyers": 5,
        "repeat_buyer_rate_pct": 5.0,
    }
    canonical = transform_mod.transform(row)
    assert canonical is not None
    assert canonical["gmv"] == 850.00
    assert canonical["visitors"] == 100
    assert canonical["loja_id"] == 3
    assert canonical["marketplace_id"] == 3


# ---------------------------------------------------------------------------
# PATCH_SHOP_STATS_SQL: insere/atualiza gmv, sem tocar orders/units/cancelamentos
# ---------------------------------------------------------------------------
def test_patch_shop_stats_sql_insere_e_atualiza_gmv():
    sql = str(daily_performance.PATCH_SHOP_STATS_SQL)
    insert_clause, _, values_and_conflict = sql.partition("VALUES")
    values_clause, _, on_conflict_clause = values_and_conflict.partition("ON CONFLICT")
    assert "gmv" in insert_clause          # lista de colunas do INSERT
    assert ":gmv" in values_clause         # bind parameter no VALUES
    assert "EXCLUDED.gmv" in on_conflict_clause  # atualizado no DO UPDATE SET


def test_patch_shop_stats_sql_nao_altera_orders_units_cancelamentos():
    sql = str(daily_performance.PATCH_SHOP_STATS_SQL)
    for campo in ("orders", "units_sold", "canceled_orders", "returned_orders", "avg_ticket"):
        assert campo not in sql, f"PATCH_SHOP_STATS_SQL nao deveria mencionar '{campo}'"
