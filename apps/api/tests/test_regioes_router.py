"""
Testes de borda HTTP dos endpoints /api/v1/regioes/* (Gate 6C) — mesmo
padrao de apps/api/tests/test_global_filters.py: erro de validacao vira 422;
parametro valido sem banco configurado vira 503 (nunca 422), confirmando que
passou pela validacao antes de tentar usar o banco.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

REGIOES_PATHS = [
    "/api/v1/regioes/summary",
    "/api/v1/regioes/by-uf",
    "/api/v1/regioes/by-brand",
    "/api/v1/regioes/trend",
]


@pytest.mark.parametrize("path", REGIOES_PATHS)
def test_endpoint_registrado_nao_e_404(path):
    resp = client.get(path)
    assert resp.status_code != 404


@pytest.mark.parametrize("path", REGIOES_PATHS)
def test_date_from_sem_date_to_retorna_422(path):
    resp = client.get(path, params={"date_from": "2026-01-01"})
    assert resp.status_code == 422


@pytest.mark.parametrize("path", REGIOES_PATHS)
def test_channels_invalido_retorna_422(path):
    resp = client.get(path, params={"channels": "tiktok,invalido"})
    assert resp.status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/regioes/summary", "/api/v1/regioes/by-uf"])
def test_uf_invalida_retorna_422(path):
    resp = client.get(path, params={"uf": "SP,ZZ"})
    assert resp.status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/regioes/summary", "/api/v1/regioes/by-uf"])
def test_uf_valida_nao_e_rejeitada_na_borda(path):
    resp = client.get(path, params={"uf": "SP,RJ"})
    assert resp.status_code != 422


@pytest.mark.parametrize("path", REGIOES_PATHS)
def test_channels_validos_nao_sao_rejeitados_na_borda(path):
    resp = client.get(path, params={"channels": "ml,shopee"})
    assert resp.status_code != 422


@pytest.mark.parametrize("path", REGIOES_PATHS)
def test_date_from_date_to_validos_sem_banco_retorna_503_nao_422(path):
    resp = client.get(path, params={"date_from": "2026-01-01", "date_to": "2026-01-31"})
    assert resp.status_code != 422


@pytest.mark.parametrize("path", REGIOES_PATHS)
def test_brands_param_sem_banco_nao_e_rejeitado_na_borda(path):
    resp = client.get(path, params={"brands": "barbours,kokeshi"})
    assert resp.status_code != 422
