"""
Testes unitários do que resta em sql_rules.py após a revisão de 2026-07-06
(texto, acesso ao payload, coalesce e dispatch de shop-stats). Toda regra
numérica/data/booleana com validação semântica foi movida para
semantics.py — ver test_shopee_staging_semantics.py.
"""
from __future__ import annotations

import pytest

from pipelines.staging.shopee import sql_rules


def test_payload_monta_acesso_jsonb():
    assert sql_rules.payload("Quantidade") == "r.raw_payload ->> 'Quantidade'"
    assert sql_rules.payload("Data", alias="x") == "x.raw_payload ->> 'Data'"


def test_payload_rejeita_header_com_aspas():
    with pytest.raises(ValueError):
        sql_rules.payload("Nome d'água")


def test_text_null_blank_vira_null():
    assert sql_rules.text_null_blank("v") == "NULLIF(btrim(v), '')"


def test_text_null_placeholder_trata_traco():
    assert sql_rules.text_null_placeholder("v") == "NULLIF(NULLIF(btrim(v), ''), '-')"


def test_coalesce_headers_desambigua_colunas_duplicadas():
    expr = sql_rules.coalesce_headers(
        ("Cidade__col58", "Cidade__col59"), sql_rules.text_null_blank
    )
    assert expr.startswith("COALESCE(")
    assert "Cidade__col58" in expr and "Cidade__col59" in expr


def test_shop_stats_row_type_formatos():
    expr = sql_rules.shop_stats_row_type("v")
    assert "'daily'" in expr and "'period_total'" in expr
    assert "ELSE NULL" in expr


def test_shop_stats_stat_date_usa_make_date_por_componentes():
    expr = sql_rules.shop_stats_stat_date("v")
    assert "make_date" in expr
    assert "to_date" not in expr, "não deve usar to_date — semântica compartilhada usa make_date"
