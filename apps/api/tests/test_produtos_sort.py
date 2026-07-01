"""
Testes de ordenacao server-side (sort_by/sort_dir) dos endpoints /produtos/*.

Cobre: allowlist de colunas (422 em coluna invalida), construcao segura do
ORDER BY (sem interpolar sort_by/sort_dir livres na query) e o fragmento
default quando nenhum sort e pedido.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import performance_service as perf_svc

client = TestClient(app)


# ---------------------------------------------------------------------------
# _build_order_by — construcao pura do ORDER BY
# ---------------------------------------------------------------------------

def test_build_order_by_sem_sort_by_usa_default():
    result = perf_svc._build_order_by(None, None, {"gmv": "gmv"}, "gmv", "DESC", [])
    assert result == "ORDER BY gmv DESC NULLS LAST"


def test_build_order_by_coluna_valida_desc_padrao():
    result = perf_svc._build_order_by("orders", None, {"orders": "completed_orders"}, "gmv", "DESC", [])
    assert result == "ORDER BY completed_orders DESC NULLS LAST"


def test_build_order_by_coluna_valida_asc():
    result = perf_svc._build_order_by("orders", "asc", {"orders": "completed_orders"}, "gmv", "DESC", [])
    assert result == "ORDER BY completed_orders ASC NULLS LAST"


def test_build_order_by_coluna_invalida_levanta_erro():
    with pytest.raises(ValueError):
        perf_svc._build_order_by("'; DROP TABLE x; --", "asc", {"gmv": "gmv"}, "gmv", "DESC", [])


def test_build_order_by_sort_dir_invalido_cai_para_desc():
    result = perf_svc._build_order_by("gmv", "qualquer-coisa", {"gmv": "gmv"}, "gmv", "DESC", [])
    assert result == "ORDER BY gmv DESC NULLS LAST"


# ---------------------------------------------------------------------------
# Validacao na borda HTTP — 422 em coluna fora da allowlist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path,valid_column,invalid_column",
    [
        ("/api/v1/performance/produtos/ml", "ad_roas", "'; DROP TABLE x; --"),
        ("/api/v1/performance/produtos/tiktok", "problem_rate", "product_id; DELETE"),
        ("/api/v1/performance/produtos/shopee", "unique_buyers", "sku_ref' OR '1'='1"),
    ],
)
def test_produtos_sort_by_invalido_retorna_422(path, valid_column, invalid_column):
    resp = client.get(path, params={"sort_by": invalid_column})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "path,valid_column",
    [
        ("/api/v1/performance/produtos/ml", "ad_roas"),
        ("/api/v1/performance/produtos/tiktok", "problem_rate"),
        ("/api/v1/performance/produtos/shopee", "unique_buyers"),
    ],
)
def test_produtos_sort_by_valido_nao_e_rejeitado_pela_allowlist(path, valid_column):
    # Sem banco no ambiente de teste, uma coluna valida deve passar da
    # validacao de allowlist (qualquer status != 422 confirma isso).
    resp = client.get(path, params={"sort_by": valid_column, "sort_dir": "asc"})
    assert resp.status_code != 422


def test_produtos_sort_dir_invalido_retorna_422():
    resp = client.get(
        "/api/v1/performance/produtos/ml",
        params={"sort_by": "ad_roas", "sort_dir": "sideways"},
    )
    assert resp.status_code == 422
