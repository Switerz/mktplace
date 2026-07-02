"""
Testes de integracao (best-effort, real Neon, somente leitura) do Pareto
dinamico end-to-end: filtro por marca, filtro por periodo, reconciliacao
total x summary, ausencia de duplicidade entre buckets e paginacao
deterministica. Pula (skip) se o banco nao estiver acessivel.

Nenhum destes testes escreve, cria ou altera qualquer tabela — apenas SELECT.
"""
import pytest

from app.services import performance_service as perf_svc

try:
    from app.database import SessionLocal
except Exception:  # pragma: no cover
    SessionLocal = None


@pytest.fixture()
def db():
    if SessionLocal is None:
        pytest.skip("banco nao configurado nesta maquina (DATABASE_URL ausente)")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _reconcile(summary: dict) -> None:
    s = sum(b["gmv"] for b in summary["buckets"])
    assert abs(s - summary["total_gmv"]) < 0.01, f"soma dos buckets ({s}) != total_gmv ({summary['total_gmv']})"
    # soma dos buckets == eligible_count (apenas GMV>0), NUNCA total_count
    # (que inclui produtos com GMV=0/negativo, fora dos buckets por definicao)
    assert sum(b["count"] for b in summary["buckets"]) == summary["eligible_count"]
    assert summary["total_count"] == summary["eligible_count"] + summary["excluded_zero_gmv_count"]
    assert summary["eligible_count"] <= summary["total_count"]


def test_ml_summary_filtro_por_marca_muda_o_conjunto_e_reconcilia(db):
    all_brands = perf_svc.get_produtos_ml_summary(db, brand=None)
    one_brand = perf_svc.get_produtos_ml_summary(db, brand="barbours")
    _reconcile(all_brands)
    _reconcile(one_brand)
    assert one_brand["total_gmv"] < all_brands["total_gmv"]
    assert one_brand["brand"] == "barbours"


def test_ml_items_filtrados_por_marca_pertencem_todos_a_marca(db):
    result = perf_svc.get_produtos_ml(
        db, brand="barbours", pareto_bucket=None, action_signal=None,
        product_status=None, revenue_velocity=None, limit=50, offset=0,
    )
    assert result["total"] > 0
    assert all(it["brand"] == "barbours" for it in result["items"])


def test_tiktok_summary_filtro_por_periodo_muda_o_conjunto_e_reconcilia(db):
    maio = perf_svc.get_produtos_tiktok_summary(db, brand=None, year=2026, month=5)
    junho = perf_svc.get_produtos_tiktok_summary(db, brand=None, year=2026, month=6)
    _reconcile(maio)
    _reconcile(junho)
    assert maio["ref_month"] == "2026-05"
    assert junho["ref_month"] == "2026-06"
    # periodos diferentes tem GMV total diferente (dados reais, nao mockados)
    assert maio["total_gmv"] != junho["total_gmv"]


def test_shopee_summary_filtro_por_periodo_reconcilia(db):
    summary = perf_svc.get_produtos_shopee_summary(db, brand=None, year=2026, month=5)
    _reconcile(summary)
    assert summary["total_gmv"] > 0


def test_ml_summary_expoe_produtos_com_gmv_zero_fora_dos_buckets(db):
    """ML tem produtos reais com gross_revenue<=0 (inativos, sem venda no
    periodo) — devem contar em total_count mas nunca em eligible_count/buckets."""
    from sqlalchemy import text
    raw = db.execute(text(
        "SELECT COUNT(*) AS n FROM marts.fact_ml_produto_ranking WHERE gross_revenue <= 0"
    )).fetchone()
    summary = perf_svc.get_produtos_ml_summary(db, brand=None)
    _reconcile(summary)
    assert summary["excluded_zero_gmv_count"] == raw.n
    assert summary["excluded_zero_gmv_count"] > 0, "nenhum produto ML com GMV<=0 no momento — invariante ainda vale, mas nao ha exemplo real para provar visibilidade"


def test_tiktok_e_shopee_summary_tambem_expoe_contagens_zero_gmv(db):
    """Mesma semantica (total/eligible/excluded) exposta nos 3 canais."""
    tk = perf_svc.get_produtos_tiktok_summary(db, brand=None, year=2026, month=5)
    sh = perf_svc.get_produtos_shopee_summary(db, brand=None, year=2026, month=5)
    for summary in (tk, sh):
        _reconcile(summary)
        assert summary["total_count"] >= summary["eligible_count"] >= 0
        assert summary["excluded_zero_gmv_count"] >= 0


def test_ml_bucket_filter_e_summary_count_coincidem_para_cada_bucket(db):
    summary = perf_svc.get_produtos_ml_summary(db, brand=None)
    for bucket in summary["buckets"]:
        items = perf_svc.get_produtos_ml(
            db, brand=None, pareto_bucket=bucket["bucket"], action_signal=None,
            product_status=None, revenue_velocity=None, limit=1000, offset=0,
        )
        assert items["total"] == bucket["count"], f"bucket {bucket['bucket']}: total={items['total']} != summary count={bucket['count']}"
        # nenhum produto do bucket filtrado pertence a outro bucket
        assert all(it["pareto_bucket"] == bucket["bucket"] for it in items["items"])


def test_ml_paginacao_sem_duplicacao_ou_omissao_com_muitos_empates(db):
    seen: set[tuple[str, str]] = set()
    offset = 0
    limit = 100
    total = None
    while True:
        page = perf_svc.get_produtos_ml(
            db, brand=None, pareto_bucket=None, action_signal=None,
            product_status=None, revenue_velocity=None,
            limit=limit, offset=offset, sort_by="cancel_rate_pct", sort_dir="asc",
        )
        total = page["total"]
        if not page["items"]:
            break
        for it in page["items"]:
            key = (it["brand"], it["item_id"])
            assert key not in seen, f"chave duplicada entre paginas: {key}"
            seen.add(key)
        offset += limit
        if offset > total + limit:
            break
    assert len(seen) == total


def test_produtos_ml_summary_action_signal_invalido_retorna_422():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = client.get("/api/v1/performance/produtos/ml/summary", params={"action_signal": "'; DROP TABLE x; --"})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/performance/produtos/ml",
        "/api/v1/performance/produtos/tiktok",
        "/api/v1/performance/produtos/shopee",
    ],
)
def test_produtos_pareto_bucket_invalido_retorna_422(path):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = client.get(path, params={"pareto_bucket": "E_invalido"})
    assert resp.status_code == 422
