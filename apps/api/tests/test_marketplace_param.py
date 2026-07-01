"""
Testes do suporte a selecao multipla de marketplace (ex: "tiktok,ml").

Cobre: normalizacao/validacao pura (normalize_marketplace_param), conversao
para IDs usados em SQL parametrizado (parse_marketplace_param), validacao na
borda HTTP (422 em combinacao invalida) e agregacao correta por combinacao de
dois e tres canais via Session falsa (sem banco real).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import performance_service as perf_svc

client = TestClient(app)


# ---------------------------------------------------------------------------
# normalize_marketplace_param — validacao e normalizacao pura
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("all", "all"),
        ("", "all"),
        ("tiktok", "tiktok"),
        ("ml", "ml"),
        ("shopee", "shopee"),
        ("tiktok,ml", "tiktok,ml"),
        ("ml,tiktok", "tiktok,ml"),          # ordem canonica: tiktok, ml, shopee
        ("shopee,tiktok", "tiktok,shopee"),
        ("ml,shopee", "ml,shopee"),
        ("tiktok,tiktok", "tiktok"),          # dedup
        ("tiktok,ml,shopee", "all"),          # combinacao completa colapsa para "all"
        ("shopee,ml,tiktok", "all"),
        (" tiktok , ml ", "tiktok,ml"),       # tolera espacos
    ],
)
def test_normalize_marketplace_param_casos_validos(raw, expected):
    assert perf_svc.normalize_marketplace_param(raw) == expected


@pytest.mark.parametrize("raw", ["foo", "tiktok,foo", ",", "  ", "tiktok;ml"])
def test_normalize_marketplace_param_rejeita_valores_invalidos(raw):
    with pytest.raises(ValueError):
        perf_svc.normalize_marketplace_param(raw)


# ---------------------------------------------------------------------------
# parse_marketplace_param — conversao para IDs (uso em SQL parametrizado)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected_ids",
    [
        ("all", [1, 2, 3]),
        ("tiktok", [1]),
        ("ml", [2]),
        ("shopee", [3]),
        ("tiktok,ml", [1, 2]),
        ("ml,tiktok", [1, 2]),
        ("tiktok,shopee", [1, 3]),
        ("ml,shopee", [2, 3]),
        ("tiktok,ml,shopee", [1, 2, 3]),
    ],
)
def test_parse_marketplace_param_retorna_ids_na_ordem_canonica(raw, expected_ids):
    assert perf_svc.parse_marketplace_param(raw) == expected_ids


def test_parse_marketplace_param_propaga_erro_de_valor_invalido():
    with pytest.raises(ValueError):
        perf_svc.parse_marketplace_param("tiktok,invalido")


# ---------------------------------------------------------------------------
# Validacao na borda HTTP (router) — sem necessidade de banco real
# ---------------------------------------------------------------------------

def test_overview_marketplace_invalido_retorna_422():
    resp = client.get("/api/v1/performance/overview", params={"marketplace": "tiktok,invalido"})
    assert resp.status_code == 422


def test_overview_marketplace_combinacao_dois_canais_nao_e_rejeitada():
    # Sem banco de dados no ambiente de teste, o proximo erro esperado e 503
    # (banco indisponivel), nao 422 — confirma que a combinacao passou pela validacao.
    resp = client.get("/api/v1/performance/overview", params={"marketplace": "tiktok,ml"})
    assert resp.status_code != 422


def test_canais_marketplace_combinacao_tres_canais_redundante_nao_e_rejeitada():
    resp = client.get("/api/v1/performance/canais", params={"marketplace": "tiktok,ml,shopee"})
    assert resp.status_code != 422


def test_financeiro_marketplace_compat_legado_all_nao_e_rejeitada():
    resp = client.get("/api/v1/performance/financeiro", params={"marketplace": "all"})
    assert resp.status_code != 422


def test_quality_marketplace_vazio_apos_virgulas_retorna_422():
    resp = client.get("/api/v1/performance/quality", params={"marketplace": ",,"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Agregacao por combinacao de canais — Session falsa (sem banco real)
# ---------------------------------------------------------------------------

class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeMappingSession:
    """Retorna, em ordem, uma lista de linhas (dict) por chamada a .execute(),
    e registra os parametros usados — permite verificar que o filtro de
    marketplace chega parametrizado (mkt_ids), nunca concatenado em SQL."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.captured_params = []

    def execute(self, stmt, params=None):
        self.captured_params.append(params)
        rows = self._responses.pop(0)
        return _FakeMappingsResult(rows)


def test_get_overview_combinacao_tiktok_ml_soma_apenas_canais_selecionados():
    cur_rows = [
        {"marketplace_id": 1, "gmv": 1000, "orders": 10, "canceled_orders": 0,
         "unique_buyers": 5, "ad_spend": 0, "ad_revenue": 0},
        {"marketplace_id": 2, "gmv": 500, "orders": 5, "canceled_orders": 1,
         "unique_buyers": 3, "ad_spend": 50, "ad_revenue": 600},
    ]
    prev_rows: list = []
    db = FakeMappingSession([cur_rows, prev_rows])

    result = perf_svc.get_overview(db, "tiktok,ml", 2026, 6)

    assert result["current"]["gmv"] == 1500
    assert result["current"]["tiktok_gmv"] == 1000
    assert result["current"]["ml_gmv"] == 500
    assert result["current"]["shopee_gmv"] is None  # shopee nao selecionado -> nunca somado
    assert result["current"]["ml_roas"] == 12.0      # 600 / 50, ponderado pela base, nao media simples
    assert db.captured_params[0]["mkt_ids"] == [1, 2]
    assert db.captured_params[1]["mkt_ids"] == [1, 2]


def test_get_overview_combinacao_all_inclui_os_tres_canais():
    cur_rows = [
        {"marketplace_id": 1, "gmv": 1000, "orders": 10, "canceled_orders": 0,
         "unique_buyers": 5, "ad_spend": 0, "ad_revenue": 0},
        {"marketplace_id": 2, "gmv": 500, "orders": 5, "canceled_orders": 1,
         "unique_buyers": 3, "ad_spend": 50, "ad_revenue": 600},
        {"marketplace_id": 3, "gmv": 200, "orders": 2, "canceled_orders": 0,
         "unique_buyers": 2, "ad_spend": 20, "ad_revenue": 100},
    ]
    db = FakeMappingSession([cur_rows, []])

    result = perf_svc.get_overview(db, "all", 2026, 6)

    assert result["current"]["gmv"] == 1700
    assert result["current"]["shopee_gmv"] == 200
    assert db.captured_params[0]["mkt_ids"] == [1, 2, 3]


def test_get_overview_canal_isolado_shopee_nao_soma_outros_canais():
    # Mesmo que a fonte (mock/engano) retorne linhas de outros canais, a
    # query real filtra por mkt_ids=[3]; aqui simulamos a fonte ja filtrada
    # (como a SQL parametrizada faria) para confirmar a montagem do resultado.
    cur_rows = [{"marketplace_id": 3, "gmv": 300, "orders": 3, "canceled_orders": 0,
                 "unique_buyers": 2, "ad_spend": 30, "ad_revenue": 450}]
    db = FakeMappingSession([cur_rows, []])

    result = perf_svc.get_overview(db, "shopee", 2026, 6)

    assert result["current"]["shopee_gmv"] == 300
    assert result["current"]["tiktok_gmv"] is None
    assert result["current"]["ml_gmv"] is None
    assert db.captured_params[0]["mkt_ids"] == [3]
