from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps.filters import ResolvedFilters, filters_query, filters_query_default_days
from app.deps.period import EffectivePeriod, resolve_period, today_brt
from app.schemas.performance import (
    BrandDetailResponse, BrandsResponse, CanaisResponse, DailyResponse, FinanceiroResponse,
    MonthlyResponse, OverviewResponse, PedidosResponse, ProdutosMLResponse,
    ProdutosMLSummaryResponse, ProdutosTikTokResponse, ProdutosTikTokSummaryResponse,
    ProdutosShopeeResponse, ProdutosShopeeSummaryResponse,
    QualityResponse, TempoRealResponse, TrendResponse,
)
from app.services import gold_service as svc
from app.services import performance_service as perf_svc

router = APIRouter(prefix="/api/v1/performance", tags=["performance"])

MARKETPLACE_QUERY_DESCRIPTION = (
    "Canal(is) de marketplace: 'all' (padrao), um canal isolado "
    "('tiktok'|'ml'|'shopee') ou combinacao separada por virgula, ex: 'tiktok,ml'."
)


def marketplace_query(marketplace: str = Query("all", description=MARKETPLACE_QUERY_DESCRIPTION)) -> str:
    """Valida o parametro marketplace na borda HTTP e devolve a forma canonica."""
    try:
        return perf_svc.normalize_marketplace_param(marketplace)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _year_month_for_service(period: EffectivePeriod) -> tuple[int, int]:
    """Deriva (year, month) para as assinaturas legadas dos services a partir
    do periodo resolvido. So e usado de fato quando period.ref_month existe
    (branch de compatibilidade calendario); para intervalos personalizados
    o valor e ignorado internamente pelo service."""
    if period.ref_month:
        y, m = period.ref_month.split("-")
        return int(y), int(m)
    return period.start.year, period.start.month


def _validate_sort_by(sort_by: Optional[str], allowlist: dict[str, str]) -> None:
    if sort_by is not None and sort_by not in allowlist:
        raise HTTPException(
            422,
            f"sort_by invalido: {sort_by}. Valores aceitos: {', '.join(sorted(allowlist))}.",
        )


VALID_ML_BRANDS = {"barbours", "kokeshi", "lescent", "rituaria"}  # rituaria incluida em 2026-07-01 (ver docs/backlog.md)
VALID_TK_BRANDS = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
VALID_PARETO_BUCKETS = {"A_top50", "B_next30", "C_next15", "D_tail"}  # compartilhado: ML, TikTok e Shopee
VALID_ML_STATUS = {"sells+advertised", "sells_organic_only", "ad_spend_no_sales", "inactive"}
VALID_ML_VELOCITY = {"high", "medium", "low", "zero"}
VALID_ML_ACTION_SIGNALS = {
    "ACAO: aumentar investimento (ROAS > 15x)",
    "ACAO: considerar pausar ads (ROAS < 3x)",
    "ALERTA: taxa cancelamento alta (> 10%)",
    "OPORTUNIDADE: produto vende organico, considerar ads",
    "REVIEW: spend sem vendas no período de orders",
    "ATENCAO: grande variacao de preco",
}


def _require_db(db: Session) -> Session:
    if db is None:
        raise HTTPException(503, "Banco de dados indisponivel. Verifique DATABASE_URL.")
    return db


def _parse_month(ref_month: Optional[str]) -> tuple[int, int]:
    if ref_month:
        try:
            year, month = ref_month.split("-")
            return int(year), int(month)
        except Exception:
            raise HTTPException(status_code=422, detail="ref_month deve ser YYYY-MM")
    today = date.today()
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


@router.get("/overview", response_model=OverviewResponse)
def overview(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_overview(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/brands", response_model=BrandsResponse)
def brands(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_brands(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/monthly", response_model=MonthlyResponse)
def monthly(
    marketplace: str = Depends(marketplace_query),
    months_back: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
):
    return perf_svc.get_monthly(_require_db(db), marketplace, months_back)


@router.get("/trend", response_model=TrendResponse)
def trend(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    """Serie de GMV/pedidos no grao adequado ao intervalo filtrado — respeita
    channels/brands/date_from/date_to. A soma de `data[].gmv` sempre bate com
    o GMV de /overview para o mesmo escopo (mesma WHERE clause)."""
    return perf_svc.get_trend(_require_db(db), filters.channels, filters.brands, filters.period)


@router.get("/daily", response_model=DailyResponse)
def daily(
    brand: str = Query(...),
    marketplace: str = Depends(marketplace_query),
    days_back: int = Query(60, ge=7, le=365),
    date_from: Optional[date] = Query(None, description="Alternativa a days_back: inicio do intervalo (inclusive)."),
    date_to: Optional[date] = Query(None, description="Alternativa a days_back: fim do intervalo (inclusive)."),
    db: Session = Depends(get_db),
):
    if brand not in VALID_TK_BRANDS:
        raise HTTPException(404, f"Brand '{brand}' nao encontrado.")
    period = (
        resolve_period(date_from=date_from, date_to=date_to, default_days=days_back, today=today_brt())
        if (date_from or date_to) else None
    )
    return perf_svc.get_daily(_require_db(db), brand, marketplace, days_back, period=period)


@router.get("/produtos/ml/summary", response_model=ProdutosMLSummaryResponse)
def produtos_ml_summary(
    brand: Optional[str] = Query(None),
    action_signal: Optional[str] = Query(None),
    product_status: Optional[str] = Query(None),
    revenue_velocity: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Cards A/B/C/D calculados dinamicamente (CTE + window function) sobre o
    MESMO conjunto filtrado da tabela (brand + action_signal + product_status
    + revenue_velocity), exceto o proprio filtro de pareto_bucket — os 4
    cards continuam visiveis mesmo com um bucket selecionado na tabela.
    fact_ml_produto_ranking nao tem competencia mensal; o campo `scope`
    identifica isso explicitamente em vez de um seletor de mes falso.
    """
    if brand and brand not in VALID_ML_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para ML.")
    if product_status and product_status not in VALID_ML_STATUS:
        raise HTTPException(422, f"product_status '{product_status}' invalido.")
    if revenue_velocity and revenue_velocity not in VALID_ML_VELOCITY:
        raise HTTPException(422, f"revenue_velocity '{revenue_velocity}' invalido.")
    if action_signal and action_signal not in VALID_ML_ACTION_SIGNALS:
        raise HTTPException(422, "action_signal invalido.")
    return perf_svc.get_produtos_ml_summary(_require_db(db), brand, action_signal, product_status, revenue_velocity)


@router.get("/produtos/ml", response_model=ProdutosMLResponse)
def produtos_ml(
    brand: Optional[str] = Query(None),
    pareto_bucket: Optional[str] = Query(None),
    action_signal: Optional[str] = Query(None),
    product_status: Optional[str] = Query(None),
    revenue_velocity: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None, description="Coluna de ordenacao (allowlist)."),
    sort_dir: Optional[Literal["asc", "desc"]] = Query(None),
    db: Session = Depends(get_db),
):
    if brand and brand not in VALID_ML_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para ML.")
    if pareto_bucket and pareto_bucket not in VALID_PARETO_BUCKETS:
        raise HTTPException(422, f"pareto_bucket '{pareto_bucket}' invalido.")
    if product_status and product_status not in VALID_ML_STATUS:
        raise HTTPException(422, f"product_status '{product_status}' invalido.")
    if revenue_velocity and revenue_velocity not in VALID_ML_VELOCITY:
        raise HTTPException(422, f"revenue_velocity '{revenue_velocity}' invalido.")
    if action_signal and action_signal not in VALID_ML_ACTION_SIGNALS:
        raise HTTPException(422, f"action_signal invalido.")
    _validate_sort_by(sort_by, perf_svc.PRODUTOS_ML_SORT_COLUMNS)
    return perf_svc.get_produtos_ml(
        _require_db(db), brand, pareto_bucket, action_signal, product_status, revenue_velocity,
        limit, offset, sort_by, sort_dir,
    )


@router.get("/produtos/tiktok", response_model=ProdutosTikTokResponse)
def produtos_tiktok(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    pareto_bucket: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None, description="Coluna de ordenacao (allowlist)."),
    sort_dir: Optional[Literal["asc", "desc"]] = Query(None),
    db: Session = Depends(get_db),
):
    if brand and brand not in VALID_TK_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para TikTok.")
    if pareto_bucket and pareto_bucket not in VALID_PARETO_BUCKETS:
        raise HTTPException(422, f"pareto_bucket '{pareto_bucket}' invalido.")
    _validate_sort_by(sort_by, perf_svc.PRODUTOS_TIKTOK_SORT_COLUMNS)
    year, month = _parse_month(ref_month)
    return perf_svc.get_produtos_tiktok(
        _require_db(db), brand, year, month, limit, offset, sort_by, sort_dir, pareto_bucket,
    )


@router.get("/produtos/tiktok/summary", response_model=ProdutosTikTokSummaryResponse)
def produtos_tiktok_summary(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Cards A/B/C/D dinamicos — mesmos filtros da tabela (brand, ref_month), exceto pareto_bucket."""
    if brand and brand not in VALID_TK_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para TikTok.")
    year, month = _parse_month(ref_month)
    return perf_svc.get_produtos_tiktok_summary(_require_db(db), brand, year, month)


@router.get("/produtos/shopee", response_model=ProdutosShopeeResponse)
def produtos_shopee(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    pareto_bucket: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None, description="Coluna de ordenacao (allowlist)."),
    sort_dir: Optional[Literal["asc", "desc"]] = Query(None),
    db: Session = Depends(get_db),
):
    _VALID_SHOPEE = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
    if brand and brand not in _VALID_SHOPEE:
        raise HTTPException(422, f"Brand '{brand}' inválida.")
    if pareto_bucket and pareto_bucket not in VALID_PARETO_BUCKETS:
        raise HTTPException(422, f"pareto_bucket '{pareto_bucket}' invalido.")
    _validate_sort_by(sort_by, perf_svc.PRODUTOS_SHOPEE_SORT_COLUMNS)
    year, month = _parse_month(ref_month)
    return perf_svc.get_produtos_shopee(
        _require_db(db), brand, year, month, limit, offset, sort_by, sort_dir, pareto_bucket,
    )


@router.get("/produtos/shopee/summary", response_model=ProdutosShopeeSummaryResponse)
def produtos_shopee_summary(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Cards A/B/C/D dinamicos — mesmos filtros da tabela (brand, ref_month), exceto pareto_bucket."""
    _VALID_SHOPEE = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
    if brand and brand not in _VALID_SHOPEE:
        raise HTTPException(422, f"Brand '{brand}' inválida.")
    year, month = _parse_month(ref_month)
    return perf_svc.get_produtos_shopee_summary(_require_db(db), brand, year, month)


@router.get("/canais", response_model=CanaisResponse)
def canais(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_canais(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/financeiro", response_model=FinanceiroResponse)
def financeiro(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_financeiro(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/quality", response_model=QualityResponse)
def quality(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_quality(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/tempo-real", response_model=TempoRealResponse)
def tempo_real(db: Session = Depends(get_db)):
    return svc.get_tempo_real(_require_db(db))


@router.get("/brand-detail", response_model=BrandDetailResponse)
def brand_detail(
    brand: str = Query(...),
    ref_month: Optional[str] = Query(None),
    channels: Optional[str] = Query(
        None,
        description=(
            "A fonte (gold.tiktok_brand_daily) e TikTok-only. Se informado, "
            "precisa incluir 'tiktok' (ou ser 'all') — canais que excluem "
            "TikTok sao rejeitados com 422 em vez de retornar dados de "
            "TikTok como se o filtro tivesse sido aplicado."
        ),
    ),
    db: Session = Depends(get_db),
):
    if brand not in VALID_TK_BRANDS:
        raise HTTPException(404, f"Brand '{brand}' nao encontrada.")
    if channels is not None:
        try:
            canonical = perf_svc.normalize_marketplace_param(channels)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        mkt_ids = perf_svc.parse_marketplace_param(canonical)
        if perf_svc.TIKTOK_ID not in mkt_ids:
            raise HTTPException(
                422,
                "brand-detail so tem dados de TikTok Shop (gold.tiktok_brand_daily); "
                "'channels' precisa incluir 'tiktok' (ou ser omitido/'all').",
            )
    year, month = _parse_month(ref_month)
    return svc.get_brand_detail(_require_db(db), brand, year, month)


@router.get("/pedidos", response_model=PedidosResponse)
def pedidos(
    filters: ResolvedFilters = Depends(filters_query_default_days(30)),
    db: Session = Depends(get_db),
):
    return perf_svc.get_pedidos(
        _require_db(db), filters.period.days,
        marketplace=filters.channels, brand_keys=filters.brands, period=filters.period,
    )


@router.get("/health-datasource")
def datasource_health(db: Session = Depends(get_db)):
    return {
        "active_source": "neon_marts" if db is not None else "unavailable",
        "db_connected": db is not None,
    }


@router.get("/inteligencia")
def inteligencia(db: Session = Depends(get_db)):
    return svc.get_inteligencia(_require_db(db))


@router.get("/operacoes")
def operacoes(db: Session = Depends(get_db)):
    return svc.get_operacoes(_require_db(db))


@router.get("/debug/raw-tempo-real")
def debug_raw_tempo_real(db: Session = Depends(get_db)):
    """
    Investiga se raw.tiktok_shop_orders tem dados mais frescos que
    gold.tiktok_shop_hourly para o uso em tempo real.
    """
    return svc.diagnose_raw_tempo_real(_require_db(db))

