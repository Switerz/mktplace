"""
Testes de validacao de filtros dos endpoints /produtos/* (whitelist -> 422).

Nao toca o banco: os parametros invalidos sao rejeitados pelo router antes
de qualquer chamada a performance_service/gold_service, entao a fixture do
FastAPI TestClient nao precisa de uma conexao real.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_produtos_ml_action_signal_invalido_retorna_422():
    resp = client.get("/api/v1/performance/produtos/ml", params={"action_signal": "'; DROP TABLE x; --"})
    assert resp.status_code == 422


def test_produtos_ml_action_signal_valido_nao_e_rejeitado_pela_whitelist():
    # Usa um valor valido; se a whitelist rejeitar, cai em 422 - qualquer outro
    # status (200, 503 sem DB, etc.) confirma que passou pela validacao de whitelist.
    resp = client.get(
        "/api/v1/performance/produtos/ml",
        params={"action_signal": "ACAO: aumentar investimento (ROAS > 15x)"},
    )
    assert resp.status_code != 422


def test_produtos_ml_brand_invalida_retorna_422():
    resp = client.get("/api/v1/performance/produtos/ml", params={"brand": "azbuy"})
    assert resp.status_code == 422


def test_produtos_ml_pareto_bucket_invalido_retorna_422():
    resp = client.get("/api/v1/performance/produtos/ml", params={"pareto_bucket": "Z_invalido"})
    assert resp.status_code == 422


def test_produtos_tiktok_brand_invalida_retorna_422():
    resp = client.get("/api/v1/performance/produtos/tiktok", params={"brand": "gocase"})
    assert resp.status_code == 422


def test_produtos_shopee_brand_invalida_retorna_422():
    resp = client.get("/api/v1/performance/produtos/shopee", params={"brand": "azbuy"})
    assert resp.status_code == 422


def test_daily_brand_invalida_retorna_404():
    resp = client.get("/api/v1/performance/daily", params={"brand": "naoexiste"})
    assert resp.status_code == 404


def test_ref_month_mal_formatado_retorna_422():
    resp = client.get("/api/v1/performance/overview", params={"ref_month": "junho-2026"})
    assert resp.status_code == 422
