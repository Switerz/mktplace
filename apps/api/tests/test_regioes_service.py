"""
Testes de app.services.regioes_service (Gate 6C) — Session falsa, nenhum
banco real tocado. Cobre: calculo puro de coverage (thresholds, denominador
zero), validacao de UF, TikTok sem cobertura regional, e o cenario real de
Barbours nov/2025-mar/2026 (baixa cobertura, confirmado em Gate 6A/6B) com
dados agregados falsos equivalentes.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.deps.period import EffectivePeriod
from app.services import regioes_service as svc
from app.services.performance_service import ML_ID, SHOPEE_ID, TIKTOK_ID


# ---------------------------------------------------------------------------
# parse_uf_param
# ---------------------------------------------------------------------------
def test_parse_uf_param_sem_parametro_retorna_none():
    assert svc.parse_uf_param(None) is None
    assert svc.parse_uf_param("") is None


def test_parse_uf_param_valida_e_normaliza_maiusculas():
    assert svc.parse_uf_param("sp,rj") == ["RJ", "SP"]


def test_parse_uf_param_aceita_xx():
    assert svc.parse_uf_param("XX") == ["XX"]


def test_parse_uf_param_uf_invalida_levanta_value_error():
    with pytest.raises(ValueError):
        svc.parse_uf_param("SP,ZZ")


def test_parse_uf_param_string_vazia_apos_split_levanta_value_error():
    with pytest.raises(ValueError):
        svc.parse_uf_param(" , , ")


# ---------------------------------------------------------------------------
# _pct — denominador zero vira None, nunca 0%
# ---------------------------------------------------------------------------
def test_pct_denominador_zero_e_none():
    assert svc._pct(0, 0) is None
    assert svc._pct(5, 0) is None


def test_pct_calculo_normal():
    assert svc._pct(50, 100) == 50.0
    assert svc._pct(1, 3) == 33.33


# ---------------------------------------------------------------------------
# coverage_level / coverage_warning
# ---------------------------------------------------------------------------
def test_coverage_level_not_applicable_quando_pct_none():
    assert svc.coverage_level(None) == "not_applicable"
    assert svc.coverage_warning("not_applicable") is False


def test_coverage_level_ok_acima_ou_igual_80():
    assert svc.coverage_level(80.0) == "ok"
    assert svc.coverage_level(100.0) == "ok"
    assert svc.coverage_warning("ok") is False


def test_coverage_level_partial_entre_50_e_80():
    assert svc.coverage_level(50.0) == "partial"
    assert svc.coverage_level(79.99) == "partial"
    assert svc.coverage_warning("partial") is True


def test_coverage_level_low_abaixo_de_50():
    assert svc.coverage_level(49.99) == "low"
    assert svc.coverage_level(0.0) == "low"
    assert svc.coverage_warning("low") is True


# ---------------------------------------------------------------------------
# channels_sem_cobertura_regional — TikTok e' o unico canal sem cobertura
# ---------------------------------------------------------------------------
def test_channels_sem_cobertura_regional_marca_tiktok():
    assert svc.channels_sem_cobertura_regional([TIKTOK_ID, ML_ID, SHOPEE_ID]) == ["tiktok"]


def test_channels_sem_cobertura_regional_vazio_sem_tiktok():
    assert svc.channels_sem_cobertura_regional([ML_ID, SHOPEE_ID]) == []


def test_channels_sem_cobertura_regional_so_tiktok():
    assert svc.channels_sem_cobertura_regional([TIKTOK_ID]) == ["tiktok"]


# ---------------------------------------------------------------------------
# Fake Session (mesmo padrao de apps/api/tests/test_global_filters.py)
# ---------------------------------------------------------------------------
class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeMappingSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.captured_params = []

    def execute(self, stmt, params=None):
        self.captured_params.append(params)
        rows = self._responses.pop(0)
        return _FakeMappingsResult(rows)


PERIOD = EffectivePeriod(start=date(2026, 1, 1), end=date(2026, 1, 31))


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------
def test_get_summary_calcula_pcts_e_coverage_level():
    summary_row = [{
        "gmv": 1000.0, "orders": 100, "units_sold": 200, "ufs_com_venda": 10,
        "uf_known_orders": 80, "uf_eligible_orders": 100,
        "shipping_cost_covered_orders": 40, "shipping_cost_eligible_orders": 100,
        "seller_shipping_cost": 500.0,
    }]
    refreshed_row = [{"refreshed_at": None}]
    db = FakeMappingSession([summary_row, refreshed_row])

    result = svc.get_summary(db, [ML_ID], None, PERIOD, channels="ml")

    assert result["uf_fill_pct"] == 80.0
    assert result["coverage_level"] == "ok"
    assert result["coverage_warning"] is False
    assert result["shipping_cost_coverage_pct"] == 40.0
    assert result["seller_shipping_cost"] == 500.0
    assert result["channels_sem_cobertura_regional"] == []


def test_get_summary_denominador_zero_vira_null_nao_zero_pct():
    summary_row = [{
        "gmv": 0.0, "orders": 0, "units_sold": 0, "ufs_com_venda": 0,
        "uf_known_orders": 0, "uf_eligible_orders": 0,
        "shipping_cost_covered_orders": 0, "shipping_cost_eligible_orders": 0,
        "seller_shipping_cost": None,
    }]
    refreshed_row = [{"refreshed_at": None}]
    db = FakeMappingSession([summary_row, refreshed_row])

    result = svc.get_summary(db, [SHOPEE_ID], None, PERIOD, channels="shopee")

    assert result["uf_fill_pct"] is None
    assert result["coverage_level"] == "not_applicable"
    assert result["coverage_warning"] is False
    assert result["seller_shipping_cost"] is None


def test_get_summary_tiktok_marca_sem_cobertura_regional_gmv_zero_honesto():
    # TikTok nunca tem linha na tabela -- a query real retornaria 0 linhas,
    # aqui simulado com uma linha totalmente zerada (COALESCE aplicado no SQL).
    summary_row = [{
        "gmv": 0.0, "orders": 0, "units_sold": 0, "ufs_com_venda": 0,
        "uf_known_orders": 0, "uf_eligible_orders": 0,
        "shipping_cost_covered_orders": 0, "shipping_cost_eligible_orders": 0,
        "seller_shipping_cost": None,
    }]
    refreshed_row = [{"refreshed_at": None}]
    db = FakeMappingSession([summary_row, refreshed_row])

    result = svc.get_summary(db, [TIKTOK_ID], None, PERIOD, channels="tiktok")

    assert result["gmv"] == 0.0
    assert result["channels_sem_cobertura_regional"] == ["tiktok"]


def test_get_summary_brand_keys_chega_parametrizado_no_sql():
    summary_row = [{
        "gmv": 100.0, "orders": 1, "units_sold": 1, "ufs_com_venda": 1,
        "uf_known_orders": 1, "uf_eligible_orders": 1,
        "shipping_cost_covered_orders": 0, "shipping_cost_eligible_orders": 1,
        "seller_shipping_cost": None,
    }]
    db = FakeMappingSession([summary_row, [{"refreshed_at": None}]])
    svc.get_summary(db, [ML_ID], ["barbours", "kokeshi"], PERIOD)
    assert db.captured_params[0]["brand_keys"] == ["barbours", "kokeshi"]


def test_get_summary_uf_filter_chega_parametrizado_no_sql():
    summary_row = [{
        "gmv": 0.0, "orders": 0, "units_sold": 0, "ufs_com_venda": 0,
        "uf_known_orders": 0, "uf_eligible_orders": 0,
        "shipping_cost_covered_orders": 0, "shipping_cost_eligible_orders": 0,
        "seller_shipping_cost": None,
    }]
    db = FakeMappingSession([summary_row, [{"refreshed_at": None}]])
    svc.get_summary(db, [SHOPEE_ID], None, PERIOD, uf_filter=["SP", "RJ"])
    assert db.captured_params[0]["ufs"] == ["SP", "RJ"]


# ---------------------------------------------------------------------------
# get_by_uf — Shopee: alta cobertura UF, frete seller N/A (None)
# ---------------------------------------------------------------------------
def test_get_by_uf_shopee_cobertura_alta_frete_seller_none():
    rows = [
        {"uf": "SP", "gmv": 1000.0, "orders": 50, "units_sold": 60,
         "canceled_orders": 2, "returned_orders": 1, "seller_shipping_cost": None,
         "uf_known_orders": 50, "uf_eligible_orders": 50,
         "shipping_cost_covered_orders": 0, "shipping_cost_eligible_orders": 0},
    ]
    db = FakeMappingSession([rows, [{"refreshed_at": None}]])
    result = svc.get_by_uf(db, [SHOPEE_ID], None, PERIOD, channels="shopee")

    row = result["data"][0]
    assert row["uf_fill_pct"] == 100.0
    assert row["coverage_level"] == "ok"
    assert row["seller_shipping_cost"] is None
    assert row["shipping_cost_coverage_pct"] is None  # denom 0 -> N/A, nao 0%
    assert row["coverage_warning"] is False


def test_get_by_uf_multiplas_ufs_ordenadas():
    rows = [
        {"uf": "RJ", "gmv": 200.0, "orders": 10, "units_sold": 10,
         "canceled_orders": 0, "returned_orders": 0, "seller_shipping_cost": None,
         "uf_known_orders": 10, "uf_eligible_orders": 10,
         "shipping_cost_covered_orders": 0, "shipping_cost_eligible_orders": 0},
        {"uf": "SP", "gmv": 800.0, "orders": 40, "units_sold": 40,
         "canceled_orders": 1, "returned_orders": 0, "seller_shipping_cost": None,
         "uf_known_orders": 40, "uf_eligible_orders": 40,
         "shipping_cost_covered_orders": 0, "shipping_cost_eligible_orders": 0},
    ]
    db = FakeMappingSession([rows, [{"refreshed_at": None}]])
    result = svc.get_by_uf(db, [SHOPEE_ID], None, PERIOD)
    assert [r["uf"] for r in result["data"]] == ["RJ", "SP"]


# ---------------------------------------------------------------------------
# get_by_uf — ML: mostra cobertura de frete regional (seller_shipping_cost
# preenchido, shipping_cost_coverage_pct derivado)
# ---------------------------------------------------------------------------
def test_get_by_uf_ml_mostra_cobertura_de_frete():
    rows = [
        {"uf": "MG", "gmv": 500.0, "orders": 20, "units_sold": 0,
         "canceled_orders": 1, "returned_orders": 0, "seller_shipping_cost": 150.0,
         "uf_known_orders": 15, "uf_eligible_orders": 20,
         "shipping_cost_covered_orders": 10, "shipping_cost_eligible_orders": 20},
    ]
    db = FakeMappingSession([rows, [{"refreshed_at": None}]])
    result = svc.get_by_uf(db, [ML_ID], None, PERIOD, channels="ml")

    row = result["data"][0]
    assert row["seller_shipping_cost"] == 150.0
    assert row["shipping_cost_coverage_pct"] == 50.0
    assert row["uf_fill_pct"] == 75.0
    assert row["coverage_level"] == "partial"
    assert row["coverage_warning"] is True


# ---------------------------------------------------------------------------
# Barbours nov/2025-mar/2026 — baixa cobertura DEVE aparecer como low/partial,
# nunca como erro/excluida. Numeros equivalentes aos confirmados no Gate 6A/6B
# (jan/2026: uf_fill_pct=1.72%).
# ---------------------------------------------------------------------------
def test_barbours_jan_2026_baixa_cobertura_aparece_como_low_nao_erro():
    rows = [{
        "gmv": 300000.0, "orders": 9036, "units_sold": 9036, "ufs_com_venda": 5,
        "uf_known_orders": 155, "uf_eligible_orders": 9036,
        "shipping_cost_covered_orders": 155, "shipping_cost_eligible_orders": 9036,
        "seller_shipping_cost": 5000.0,
    }]
    db = FakeMappingSession([rows, [{"refreshed_at": None}]])
    result = svc.get_summary(db, [ML_ID], ["barbours"], PERIOD, channels="ml")

    assert result["uf_fill_pct"] == pytest.approx(1.72, abs=0.01)
    assert result["coverage_level"] == "low"
    assert result["coverage_warning"] is True
    # Nao e' erro/excecao -- os pedidos continuam contabilizados normalmente.
    assert result["orders"] == 9036
    assert result["gmv"] == 300000.0


# ---------------------------------------------------------------------------
# get_by_brand
# ---------------------------------------------------------------------------
def test_get_by_brand_monta_label_e_nome_do_marketplace():
    rows = [
        {"brand_key": "barbours", "marketplace_id": ML_ID, "gmv": 100.0, "orders": 5, "units_sold": 5,
         "uf_known_orders": 3, "uf_eligible_orders": 5,
         "shipping_cost_covered_orders": 2, "shipping_cost_eligible_orders": 5},
    ]
    db = FakeMappingSession([rows, [{"refreshed_at": None}]])
    result = svc.get_by_brand(db, [ML_ID], None, PERIOD)

    row = result["data"][0]
    assert row["brand"] == "barbours"
    assert row["label"] == "BARBOURS"
    assert row["marketplace"] == "ml"
    assert row["marketplace_id"] == ML_ID


def test_get_by_brand_tiktok_nunca_aparece_nas_linhas_mas_meta_sinaliza():
    # A query real nunca retorna linha de TikTok (0 linhas estruturais) --
    # aqui simulado com "data" vazio quando so TikTok e' pedido.
    db = FakeMappingSession([[], [{"refreshed_at": None}]])
    result = svc.get_by_brand(db, [TIKTOK_ID], None, PERIOD, channels="tiktok")
    assert result["data"] == []
    assert result["channels_sem_cobertura_regional"] == ["tiktok"]


# ---------------------------------------------------------------------------
# get_trend — granularidade e uf_fill_pct por bucket
# ---------------------------------------------------------------------------
def test_get_trend_granularidade_diaria_ate_92_dias():
    period = EffectivePeriod(start=date(2026, 5, 1), end=date(2026, 5, 2))
    rows = [
        {"bucket": date(2026, 5, 1), "gmv": 100.0, "orders": 10,
         "uf_known_orders": 8, "uf_eligible_orders": 10},
        {"bucket": date(2026, 5, 2), "gmv": 200.0, "orders": 20,
         "uf_known_orders": 20, "uf_eligible_orders": 20},
    ]
    db = FakeMappingSession([rows, [{"refreshed_at": None}]])
    result = svc.get_trend(db, [ML_ID], None, period)

    assert result["granularity"] == "day"
    assert result["data"][0]["uf_fill_pct"] == 80.0
    assert result["data"][1]["uf_fill_pct"] == 100.0


def test_get_trend_granularidade_mensal_acima_de_92_dias():
    period = EffectivePeriod(start=date(2026, 1, 1), end=date(2026, 6, 30))
    rows = [{"bucket": date(2026, 1, 1), "gmv": 1000.0, "orders": 100,
             "uf_known_orders": 50, "uf_eligible_orders": 100}]
    db = FakeMappingSession([rows, [{"refreshed_at": None}]])
    result = svc.get_trend(db, [ML_ID], None, period)
    assert result["granularity"] == "month"
    assert result["data"][0]["label"] == "Jan/26"


def test_get_trend_denominador_zero_por_bucket_vira_none():
    period = EffectivePeriod(start=date(2026, 5, 1), end=date(2026, 5, 1))
    rows = [{"bucket": date(2026, 5, 1), "gmv": 0.0, "orders": 0,
             "uf_known_orders": 0, "uf_eligible_orders": 0}]
    db = FakeMappingSession([rows, [{"refreshed_at": None}]])
    result = svc.get_trend(db, [SHOPEE_ID], None, period)
    assert result["data"][0]["uf_fill_pct"] is None
