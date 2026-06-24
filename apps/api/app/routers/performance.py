from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.performance import (
    BrandDetailResponse, BrandsResponse, CanaisResponse, DailyResponse, FinanceiroResponse,
    MonthlyResponse, OverviewResponse, PedidosResponse, ProdutosMLResponse,
    ProdutosMLSummaryResponse, ProdutosTikTokResponse, ProdutosShopeeResponse,
    QualityResponse, TempoRealResponse,
)
from app.services import gold_service as svc

router = APIRouter(prefix="/api/v1/performance", tags=["performance"])

MarketplaceParam = Literal["all", "tiktok", "ml", "shopee"]

VALID_ML_BRANDS = {"barbours", "kokeshi", "lescent"}
VALID_TK_BRANDS = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
VALID_ML_PARETO = {"A_top50", "B_next30", "C_next15", "D_tail"}
VALID_ML_STATUS = {"sells+advertised", "sells_organic_only", "ad_spend_no_sales", "inactive"}
VALID_ML_VELOCITY = {"high", "medium", "low", "zero"}


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
    marketplace: MarketplaceParam = Query("all"),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    year, month = _parse_month(ref_month)
    return svc.get_overview(_require_db(db), marketplace, year, month)


@router.get("/brands", response_model=BrandsResponse)
def brands(
    marketplace: MarketplaceParam = Query("all"),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    year, month = _parse_month(ref_month)
    return svc.get_brands(_require_db(db), marketplace, year, month)


@router.get("/monthly", response_model=MonthlyResponse)
def monthly(
    marketplace: MarketplaceParam = Query("all"),
    months_back: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
):
    return svc.get_monthly(_require_db(db), marketplace, months_back)


@router.get("/daily", response_model=DailyResponse)
def daily(
    brand: str = Query(...),
    marketplace: MarketplaceParam = Query("all"),
    days_back: int = Query(60, ge=7, le=365),
    db: Session = Depends(get_db),
):
    if brand not in VALID_TK_BRANDS:
        raise HTTPException(404, f"Brand '{brand}' nao encontrado.")
    return svc.get_daily(_require_db(db), brand, marketplace, days_back)


@router.get("/produtos/ml/summary", response_model=ProdutosMLSummaryResponse)
def produtos_ml_summary(
    brand: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if brand and brand not in VALID_ML_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para ML.")
    return svc.get_produtos_ml_summary(_require_db(db), brand)


@router.get("/produtos/ml", response_model=ProdutosMLResponse)
def produtos_ml(
    brand: Optional[str] = Query(None),
    pareto_bucket: Optional[str] = Query(None),
    action_signal: Optional[str] = Query(None),
    product_status: Optional[str] = Query(None),
    revenue_velocity: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if brand and brand not in VALID_ML_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para ML.")
    if pareto_bucket and pareto_bucket not in VALID_ML_PARETO:
        raise HTTPException(422, f"pareto_bucket '{pareto_bucket}' invalido.")
    if product_status and product_status not in VALID_ML_STATUS:
        raise HTTPException(422, f"product_status '{product_status}' invalido.")
    if revenue_velocity and revenue_velocity not in VALID_ML_VELOCITY:
        raise HTTPException(422, f"revenue_velocity '{revenue_velocity}' invalido.")
    return svc.get_produtos_ml(_require_db(db), brand, pareto_bucket, action_signal, product_status, revenue_velocity, limit, offset)


@router.get("/produtos/tiktok", response_model=ProdutosTikTokResponse)
def produtos_tiktok(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if brand and brand not in VALID_TK_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para TikTok.")
    year, month = _parse_month(ref_month)
    return svc.get_produtos_tiktok(_require_db(db), brand, year, month, limit, offset)


@router.get("/produtos/shopee", response_model=ProdutosShopeeResponse)
def produtos_shopee(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _VALID_SHOPEE = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
    if brand and brand not in _VALID_SHOPEE:
        raise HTTPException(422, f"Brand '{brand}' inválida.")
    year, month = _parse_month(ref_month)
    return svc.get_produtos_shopee(_require_db(db), brand, year, month, limit, offset)


@router.get("/canais", response_model=CanaisResponse)
def canais(
    marketplace: MarketplaceParam = Query("all"),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    year, month = _parse_month(ref_month)
    return svc.get_canais(_require_db(db), marketplace, year, month)


@router.get("/financeiro", response_model=FinanceiroResponse)
def financeiro(
    marketplace: MarketplaceParam = Query("all"),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    year, month = _parse_month(ref_month)
    return svc.get_financeiro(_require_db(db), marketplace, year, month)


@router.get("/quality", response_model=QualityResponse)
def quality(
    marketplace: MarketplaceParam = Query("all"),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    year, month = _parse_month(ref_month)
    return svc.get_quality(_require_db(db), marketplace, year, month)


@router.get("/tempo-real", response_model=TempoRealResponse)
def tempo_real(db: Session = Depends(get_db)):
    return svc.get_tempo_real(_require_db(db))


@router.get("/brand-detail", response_model=BrandDetailResponse)
def brand_detail(
    brand: str = Query(...),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if brand not in VALID_TK_BRANDS:
        raise HTTPException(404, f"Brand '{brand}' nao encontrada.")
    year, month = _parse_month(ref_month)
    return svc.get_brand_detail(_require_db(db), brand, year, month)


@router.get("/pedidos", response_model=PedidosResponse)
def pedidos(
    days_back: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
):
    return svc.get_pedidos(_require_db(db), days_back)


@router.get("/health-datasource")
def datasource_health(db: Session = Depends(get_db)):
    return {
        "active_source": "gold_tables" if db is not None else "unavailable",
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

