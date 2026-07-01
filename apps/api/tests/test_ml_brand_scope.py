"""
Confirma que rituaria foi incluida oficialmente no escopo de ML em todos os
pontos de whitelist duplicados (Bug 4 — docs/sections/produtos_audit.md).
"""
from app.routers.performance import VALID_ML_BRANDS
from app.services import gold_service
from app.services import performance_service as perf_svc


def test_router_valid_ml_brands_inclui_rituaria():
    assert "rituaria" in VALID_ML_BRANDS
    assert "apice" not in VALID_ML_BRANDS  # apice confirmado sem dados reais no ML


def test_performance_service_ml_brands_inclui_rituaria():
    assert "rituaria" in perf_svc._ML_BRANDS


def test_gold_service_ml_brands_inclui_rituaria():
    assert "rituaria" in gold_service.ML_BRANDS
