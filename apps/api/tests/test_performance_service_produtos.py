"""
Testes de performance_service.get_produtos_* com uma Session falsa
(sem banco real) — cobre o comportamento com fonte vazia e a
serializacao basica de uma linha valida.
"""
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
    # product_name, variation_name) — SQL puro, sem consolidacao entre linhas
    # (ver Bug 9): count + rows, igual ao padrao de ML/TikTok.
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
        FakeResult([FakeRow(total_count=0, eligible_count=0)]),
        FakeResult([]),
        FakeResult([FakeRow(r=None)]),
    ])
    result = perf_svc.get_produtos_ml_summary(db, brand=None)
    assert result["total_gmv"] == 0.0
    assert result["total_count"] == 0
    assert result["eligible_count"] == 0
    assert result["excluded_zero_gmv_count"] == 0
    for bucket in result["buckets"]:
        assert bucket["gmv_pct"] == 0.0
