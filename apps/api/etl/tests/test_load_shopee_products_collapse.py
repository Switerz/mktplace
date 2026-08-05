"""Testes da consolidação de colisões de variation_name na chave real
(ref_month, brand, sku_ref_key, product_name) — Bug 5.

Cobre a função pura `_collapse_variation_collisions` (incorporada ao loader
recorrente com a mesma semântica já aplicada aos dados históricos por
`pipelines/reconciliation/fix_shopee_product_dates.py`) e a guarda defensiva
da Fase B (`_assert_unique_keys` via `_write_prepared_brands`), que recusa
abrir qualquer conexão se ainda houver duplicidade na chave real.

Nenhum banco real: a guarda é exercida com stubs que só registram chamadas.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from etl import load_shopee_products as mod
from etl.load_shopee_products import _aggregate, _collapse_variation_collisions

# Colunas exatamente na ordem que _aggregate produz.
AGG_COLS = [
    "brand", "ref_month", "sku_ref", "product_name", "variation_name",
    "gmv", "units_sold", "completed_orders", "unique_buyers",
    "canceled_orders", "cancel_rate_pct", "avg_price", "sku_ref_key",
]


def _agg_row(brand="kokeshi", ref_month="2026-06-01", sku_ref="SKU1",
             product_name="Produto A", variation_name=None, gmv=100.0,
             units_sold=2, completed_orders=1, unique_buyers=1,
             canceled_orders=0, cancel_rate_pct=0.0, avg_price=50.0):
    """Uma linha no formato de saída de _aggregate (pré-consolidação).
    sku_ref_key deriva de sku_ref como no _aggregate real."""
    return {
        "brand": brand, "ref_month": pd.Timestamp(ref_month), "sku_ref": sku_ref,
        "product_name": product_name, "variation_name": variation_name,
        "gmv": gmv, "units_sold": units_sold, "completed_orders": completed_orders,
        "unique_buyers": unique_buyers, "canceled_orders": canceled_orders,
        "cancel_rate_pct": cancel_rate_pct, "avg_price": avg_price,
        "sku_ref_key": "" if sku_ref is None else str(sku_ref),
    }


def _df(rows):
    return pd.DataFrame(rows, columns=AGG_COLS)


def _one(result, product_name="Produto A"):
    sub = result[result["product_name"] == product_name]
    assert len(sub) == 1, f"esperado 1 linha para {product_name}, veio {len(sub)}"
    return sub.iloc[0]


# ---------------------------------------------------------------------------
# 1-7: consolidação de colisões
# ---------------------------------------------------------------------------
def test_1_duas_variacoes_mesma_chave_viram_uma_linha():
    result = _collapse_variation_collisions(_df([
        _agg_row(variation_name="Azul", gmv=100.0, units_sold=2),
        _agg_row(variation_name="Rosa", gmv=50.0, units_sold=1),
    ]))
    assert len(result) == 1


def test_2_tres_variacoes_mesma_chave_viram_uma_linha():
    result = _collapse_variation_collisions(_df([
        _agg_row(variation_name="Azul", gmv=100.0),
        _agg_row(variation_name="Rosa", gmv=50.0),
        _agg_row(variation_name="Lilas", gmv=25.0),
    ]))
    assert len(result) == 1


def test_3_gmv_somado_exatamente():
    result = _collapse_variation_collisions(_df([
        _agg_row(variation_name="Azul", gmv=3690.90),
        _agg_row(variation_name="Lilas", gmv=6990.17),
        _agg_row(variation_name="Rosa", gmv=7996.79),
    ]))
    assert _one(result)["gmv"] == pytest.approx(3690.90 + 6990.17 + 7996.79)


def test_4_units_completed_canceled_buyers_somados():
    result = _collapse_variation_collisions(_df([
        _agg_row(variation_name="Azul", units_sold=10, completed_orders=8, canceled_orders=2, unique_buyers=7),
        _agg_row(variation_name="Rosa", units_sold=5, completed_orders=4, canceled_orders=1, unique_buyers=3),
    ]))
    row = _one(result)
    assert int(row["units_sold"]) == 15
    assert int(row["completed_orders"]) == 12
    assert int(row["canceled_orders"]) == 3
    assert int(row["unique_buyers"]) == 10


def test_5_cancel_rate_pct_recalculado_dos_totais():
    result = _collapse_variation_collisions(_df([
        _agg_row(variation_name="Azul", completed_orders=8, canceled_orders=2, cancel_rate_pct=20.0),
        _agg_row(variation_name="Rosa", completed_orders=0, canceled_orders=2, cancel_rate_pct=100.0),
    ]))
    # total: canceled=4, completed=8 -> 4/12*100 = 33.3333 (nao a media das taxas)
    assert _one(result)["cancel_rate_pct"] == pytest.approx(round(4 / 12 * 100, 4))


def test_6_avg_price_recalculado_dos_totais():
    result = _collapse_variation_collisions(_df([
        _agg_row(variation_name="Azul", gmv=100.0, units_sold=2, avg_price=50.0),
        _agg_row(variation_name="Rosa", gmv=50.0, units_sold=3, avg_price=16.67),
    ]))
    # (100+50) / (2+3) = 30.00, nao a media dos avg_price individuais
    assert _one(result)["avg_price"] == pytest.approx(30.0)


def test_7_variation_name_combinado_sem_duplicatas_e_deterministico():
    result = _collapse_variation_collisions(_df([
        _agg_row(variation_name="Azul"),
        _agg_row(variation_name="Rosa"),
        _agg_row(variation_name="Azul"),  # repetida -> nao duplica
    ]))
    assert _one(result)["variation_name"] == "Azul; Rosa"


# ---------------------------------------------------------------------------
# 8-12: casos de borda / não-combinação
# ---------------------------------------------------------------------------
def test_8_variation_name_nulo():
    # todas nulas -> None; nula + nomeada -> só a nomeada
    r_all_null = _collapse_variation_collisions(_df([
        _agg_row(variation_name=None, gmv=10.0),
        _agg_row(variation_name=None, gmv=20.0),
    ]))
    assert r_all_null.iloc[0]["variation_name"] is None
    assert r_all_null.iloc[0]["gmv"] == pytest.approx(30.0)

    r_mixed = _collapse_variation_collisions(_df([
        _agg_row(variation_name=None, gmv=10.0),
        _agg_row(variation_name="Rosa", gmv=20.0),
    ]))
    assert r_mixed.iloc[0]["variation_name"] == "Rosa"


def test_9_grupo_sem_colisao_permanece_inalterado():
    result = _collapse_variation_collisions(_df([
        _agg_row(product_name="Produto A", sku_ref="SKU1", variation_name="Azul",
                 gmv=123.45, units_sold=3, completed_orders=2, canceled_orders=1,
                 unique_buyers=2, cancel_rate_pct=33.3333, avg_price=41.15),
    ]))
    assert len(result) == 1
    row = result.iloc[0]
    assert row["gmv"] == pytest.approx(123.45)
    assert int(row["units_sold"]) == 3
    assert row["variation_name"] == "Azul"
    assert row["cancel_rate_pct"] == pytest.approx(33.3333)
    assert row["avg_price"] == pytest.approx(41.15)


def test_10_grupo_somente_cancelado_preservado_sem_regressao_bug8():
    # linha só-cancelada (gmv=0, units=0, cancel_rate=100) deve sobreviver
    result = _collapse_variation_collisions(_df([
        _agg_row(product_name="So Cancelado", sku_ref="SKUC", variation_name=None,
                 gmv=0.0, units_sold=0, completed_orders=0, canceled_orders=2,
                 unique_buyers=0, cancel_rate_pct=100.0, avg_price=None),
    ]))
    row = _one(result, "So Cancelado")
    assert int(row["completed_orders"]) == 0
    assert int(row["canceled_orders"]) == 2
    assert row["gmv"] == 0.0
    assert int(row["units_sold"]) == 0
    assert row["cancel_rate_pct"] == pytest.approx(100.0)
    assert pd.isna(row["avg_price"])  # units==0 -> None/NaN


def test_11_product_names_diferentes_nao_sao_combinados():
    result = _collapse_variation_collisions(_df([
        _agg_row(product_name="Produto A", sku_ref="SKU1", variation_name="Azul"),
        _agg_row(product_name="Produto B", sku_ref="SKU1", variation_name="Rosa"),
    ]))
    assert len(result) == 2
    assert set(result["product_name"]) == {"Produto A", "Produto B"}


def test_12_sku_ref_key_diferentes_nao_sao_combinados():
    result = _collapse_variation_collisions(_df([
        _agg_row(product_name="Produto A", sku_ref="SKU1", variation_name="Azul"),
        _agg_row(product_name="Produto A", sku_ref="SKU2", variation_name="Rosa"),
    ]))
    assert len(result) == 2
    assert set(result["sku_ref_key"]) == {"SKU1", "SKU2"}


# ---------------------------------------------------------------------------
# 13: guarda da Fase B bloqueia duplicidade antes de engine/conexão
# ---------------------------------------------------------------------------
def test_13_guarda_faseB_bloqueia_duplicidade_antes_de_conexao(monkeypatch):
    opened = []
    monkeypatch.setattr(mod, "_get_local_pg_url", lambda: opened.append("url"))
    monkeypatch.setattr(mod, "create_engine", lambda *a, **k: opened.append("engine"))

    # DataFrame preparado AINDA com colisão na chave real (simula regressão:
    # bypass da consolidação) — duas linhas com a mesma chave de 4 campos.
    dup = _df([
        _agg_row(variation_name="Azul", gmv=100.0),
        _agg_row(variation_name="Rosa", gmv=50.0),
    ])
    with pytest.raises(mod.ShopeeProductKeyCollisionError) as exc:
        mod._write_prepared_brands([("kokeshi", dup)])

    assert "kokeshi" in str(exc.value)
    assert opened == [], "nenhuma conexão/engine deve ser aberta quando a guarda dispara"


def test_13b_guarda_nao_expoe_valor_de_celula():
    dup = _df([
        _agg_row(sku_ref="SEGREDO_SKU", product_name="Produto Secreto", variation_name="Azul"),
        _agg_row(sku_ref="SEGREDO_SKU", product_name="Produto Secreto", variation_name="Rosa"),
    ])
    with pytest.raises(mod.ShopeeProductKeyCollisionError) as exc:
        mod._assert_unique_keys([("kokeshi", dup)])
    msg = str(exc.value)
    assert "SEGREDO_SKU" not in msg and "Produto Secreto" not in msg


# ---------------------------------------------------------------------------
# 14-15: chaves únicas na saída / sem NaN/Infinity introduzido
# ---------------------------------------------------------------------------
def test_14_saida_tem_somente_chaves_unicas():
    result = _collapse_variation_collisions(_df([
        _agg_row(sku_ref="SKU1", product_name="A", variation_name="Azul"),
        _agg_row(sku_ref="SKU1", product_name="A", variation_name="Rosa"),
        _agg_row(sku_ref="SKU1", product_name="A", variation_name="Lilas"),
        _agg_row(sku_ref="SKU2", product_name="A", variation_name="Verde"),
        _agg_row(sku_ref="SKU1", product_name="B", variation_name="Azul"),
    ]))
    keys = list(zip(result["ref_month"], result["brand"], result["sku_ref_key"], result["product_name"]))
    assert len(keys) == len(set(keys)), "saída deve ter chaves reais únicas"
    assert len(result) == 3  # (SKU1,A), (SKU2,A), (SKU1,B)


def test_15_sem_nan_ou_infinity_nas_metricas_somadas():
    result = _collapse_variation_collisions(_df([
        _agg_row(variation_name="Azul", gmv=100.0, units_sold=2),
        _agg_row(variation_name="Rosa", gmv=50.0, units_sold=1),
    ]))
    for col in ("gmv", "units_sold", "completed_orders", "canceled_orders", "unique_buyers"):
        v = float(result.iloc[0][col])
        assert math.isfinite(v), f"{col} não finito"
    # derivados quando calculáveis também são finitos
    assert math.isfinite(float(result.iloc[0]["avg_price"]))
    assert math.isfinite(float(result.iloc[0]["cancel_rate_pct"]))


# ---------------------------------------------------------------------------
# Integração: saída de _aggregate real passa pela consolidação sem colisão
# ---------------------------------------------------------------------------
def test_integracao_aggregate_mais_collapse_elimina_colisao_de_variacao():
    # Duas variações do mesmo sku_ref/produto no mesmo mês -> _aggregate gera
    # 2 linhas (colidem na chave real); collapse deve reduzir para 1.
    raw = pd.DataFrame([
        {"brand": "kokeshi", "ref_month": pd.Timestamp("2026-06-01"), "sku_ref": "KIT1",
         "product_name": "Kit", "variation_name": "Azul", "status": "Concluído",
         "qty": 2, "subtotal": 100.0, "buyer_username": "b1"},
        {"brand": "kokeshi", "ref_month": pd.Timestamp("2026-06-01"), "sku_ref": "KIT1",
         "product_name": "Kit", "variation_name": "Rosa", "status": "Concluído",
         "qty": 1, "subtotal": 50.0, "buyer_username": "b2"},
    ])
    agg = _aggregate(raw)
    assert len(agg) == 2  # colidem na chave real (variation diferente)
    collapsed = _collapse_variation_collisions(agg)
    assert len(collapsed) == 1
    assert collapsed.iloc[0]["gmv"] == pytest.approx(150.0)
    assert collapsed.iloc[0]["variation_name"] in ("Azul; Rosa", "Rosa; Azul")
