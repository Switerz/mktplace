"""
Testes de performance_service.get_produtos_* com uma Session falsa
(sem banco real) — cobre o comportamento com fonte vazia e a
serializacao basica de uma linha valida.
"""
from app.schemas import performance as perf_schemas
from app.services import performance_service as perf_svc


class FakeRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeCountRow:
    def __init__(self, n):
        self.n = n


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeSession:
    """Retorna, em ordem, um FakeResult por chamada a .execute()."""

    def __init__(self, responses):
        self._responses = list(responses)

    def execute(self, stmt, params=None):
        return self._responses.pop(0)


def test_get_produtos_shopee_fonte_vazia_retorna_lista_vazia():
    # get_produtos_shopee usa chave ESTRITA (ref_month, brand, sku_ref_key,
    # product_name) — SQL puro, sem consolidacao entre linhas (ver Bug 9).
    # variation_name e atributo descritivo, nao parte da chave: count + rows,
    # igual ao padrao de ML/TikTok.
    db = FakeSession([FakeResult([FakeCountRow(0)]), FakeResult([])])
    result = perf_svc.get_produtos_shopee(db, brand=None, year=2026, month=6)
    assert result["total"] == 0
    assert result["items"] == []
    assert result["ref_month"] == "2026-06"


def test_get_produtos_shopee_com_linha_serializa_campos():
    row = FakeRow(
        brand="kokeshi", sku_ref="SKU1", product_name="Produto X", variation_name=None,
        gmv=1000.0, units_sold=10, completed_orders=8, canceled_orders=2,
        cancel_rate_pct=20.0, unique_buyers=7, avg_price=100.0, pareto_bucket="A_top50",
    )
    db = FakeSession([FakeResult([FakeCountRow(1)]), FakeResult([row])])
    result = perf_svc.get_produtos_shopee(db, brand="kokeshi", year=2026, month=6)
    assert result["total"] == 1
    assert result["items"][0]["brand"] == "kokeshi"
    assert result["items"][0]["orders"] == 8
    assert result["items"][0]["cancel_rate_pct"] == 20.0
    assert result["items"][0]["unique_buyers"] == 7
    assert result["items"][0]["pareto_bucket"] == "A_top50"


def test_get_produtos_ml_fonte_vazia_retorna_lista_vazia():
    db = FakeSession([FakeResult([FakeCountRow(0)]), FakeResult([]), FakeResult([FakeRow(r=None)])])
    result = perf_svc.get_produtos_ml(
        db, brand=None, pareto_bucket=None, action_signal=None,
        product_status=None, revenue_velocity=None,
    )
    assert result["total"] == 0
    assert result["items"] == []


def test_get_produtos_tiktok_fonte_vazia_retorna_lista_vazia():
    db = FakeSession([FakeResult([FakeCountRow(0)]), FakeResult([])])
    result = perf_svc.get_produtos_tiktok(db, brand=None, year=2026, month=6)
    assert result["total"] == 0
    assert result["items"] == []
    assert result["ref_month"] == "2026-06"


def test_get_produtos_ml_summary_sem_dados_nao_divide_por_zero():
    db = FakeSession([
        FakeResult([FakeRow(total_count=0, eligible_count=0, eligible_units=None)]),
        FakeResult([]),
        FakeResult([FakeRow(r=None)]),
    ])
    result = perf_svc.get_produtos_ml_summary(db, brand=None)
    assert result["total_gmv"] == 0.0
    assert result["total_count"] == 0
    assert result["eligible_count"] == 0
    assert result["excluded_zero_gmv_count"] == 0
    assert result["avg_price_weighted"] is None
    for bucket in result["buckets"]:
        assert bucket["gmv_pct"] == 0.0


# ---------------------------------------------------------------------------
# Gate 2 (2026-07-13) — preco medio (avg_price) e preco medio ponderado do
# summary (avg_price_weighted). Ver docs/sections/produtos_audit.md secao 10.
# ---------------------------------------------------------------------------

def test_get_produtos_tiktok_avg_price_e_receita_por_unidade():
    # A fonte ja entrega avg_price calculado pela SQL (gmv/items_sold); o
    # service so precisa repassar arredondado, igual aos demais campos.
    row = FakeRow(
        brand="barbours", product_id="1", product_name="Produto",
        gmv=1000.0, orders=10, items_sold=8, avg_price=125.0,
        pct_gmv_video=None, pct_gmv_live=None, pct_gmv_card=None,
        problem_rate=None, rating_avg=None, total_ratings=None,
        pareto_bucket="A_top50",
    )
    db = FakeSession([FakeResult([FakeCountRow(1)]), FakeResult([row])])
    result = perf_svc.get_produtos_tiktok(db, brand=None, year=2026, month=6)
    assert result["items"][0]["avg_price"] == 125.0


def test_get_produtos_tiktok_avg_price_null_quando_sem_unidades():
    # Denominador zero/null nunca vira 0 disfarcado — sempre None (N/A).
    row = FakeRow(
        brand="barbours", product_id="1", product_name="Produto",
        gmv=0.0, orders=0, items_sold=0, avg_price=None,
        pct_gmv_video=None, pct_gmv_live=None, pct_gmv_card=None,
        problem_rate=None, rating_avg=None, total_ratings=None,
        pareto_bucket=None,
    )
    db = FakeSession([FakeResult([FakeCountRow(1)]), FakeResult([row])])
    result = perf_svc.get_produtos_tiktok(db, brand=None, year=2026, month=6)
    assert result["items"][0]["avg_price"] is None


def test_get_produtos_ml_summary_avg_price_weighted_usa_receita_total_sobre_unidades_totais():
    # 5000/50 = 100.0 — NUNCA a media simples de avg_price por linha.
    db = FakeSession([
        FakeResult([FakeRow(total_count=10, eligible_count=8, eligible_units=50)]),
        FakeResult([FakeRow(pareto_bucket="A_top50", count=8, gmv=5000.0)]),
        FakeResult([FakeRow(r=None)]),
    ])
    result = perf_svc.get_produtos_ml_summary(db, brand=None)
    assert result["avg_price_weighted"] == 100.0


def test_get_produtos_ml_summary_avg_price_weighted_null_sem_unidades_elegiveis():
    db = FakeSession([
        FakeResult([FakeRow(total_count=3, eligible_count=0, eligible_units=None)]),
        FakeResult([]),
        FakeResult([FakeRow(r=None)]),
    ])
    result = perf_svc.get_produtos_ml_summary(db, brand=None)
    assert result["avg_price_weighted"] is None


def test_get_produtos_tiktok_summary_avg_price_weighted():
    db = FakeSession([
        FakeResult([FakeRow(total_count=4, eligible_count=4, eligible_units=20)]),
        FakeResult([FakeRow(pareto_bucket="A_top50", count=4, gmv=2000.0)]),
    ])
    result = perf_svc.get_produtos_tiktok_summary(db, brand=None, year=2026, month=6)
    assert result["avg_price_weighted"] == 100.0


def test_get_produtos_shopee_summary_avg_price_weighted():
    db = FakeSession([
        FakeResult([FakeRow(total_count=2, eligible_count=2, eligible_units=10)]),
        FakeResult([FakeRow(pareto_bucket="A_top50", count=2, gmv=1500.0)]),
    ])
    result = perf_svc.get_produtos_shopee_summary(db, brand=None, year=2026, month=6)
    assert result["avg_price_weighted"] == 150.0


def test_produto_ml_row_preserva_roas_acos_ad_spend_reais():
    # ROAS/ACOS/ad_spend sao dados reais do ML (fact_ml_produto_ranking) —
    # devem continuar disponiveis no schema para a UI expor "Eficiencia Ads".
    fields = perf_schemas.ProdutoMLRow.model_fields
    assert "ad_roas" in fields
    assert "ad_acos_pct" in fields
    assert "ad_spend" in fields


def test_produto_tiktok_e_shopee_row_nao_expoe_margem_ou_ads_por_produto():
    # TikTok e Shopee nao tem fonte de ads/fee/CMV por produto (Gate 1) —
    # o contrato do schema nao pode inventar esses campos.
    forbidden = {"estimated_margin", "margin", "ad_roas", "ad_acos_pct", "ad_spend"}
    assert forbidden.isdisjoint(perf_schemas.ProdutoTikTokRow.model_fields)
    assert forbidden.isdisjoint(perf_schemas.ProdutoShopeeRow.model_fields)


def test_nenhum_schema_de_produto_usa_a_palavra_margem_exceto_o_campo_tecnico_ml():
    # estimated_margin (ML) e o unico campo com semantica de margem no
    # schema, e seu nome ja e tecnico (nao "margin"/"margem" isolado) —
    # trava contra reintroduzir "margem" em algum lugar sem CMV real.
    for model in (perf_schemas.ProdutoMLRow, perf_schemas.ProdutoTikTokRow, perf_schemas.ProdutoShopeeRow):
        for name in model.model_fields:
            assert name in ("estimated_margin",) or "margin" not in name.lower()
