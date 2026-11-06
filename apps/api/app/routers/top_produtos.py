import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps.filters import ResolvedFilters, filters_query
from app.deps.period import resolve_period, today_brt
from app.schemas.top_produtos import Canal, DetalheProduto, TopProdutosResponse
from app.services import datamart_leitura
from app.services.gold_service import BRANDS_IN_SCOPE
from app.services.top_produtos_service import canais_do_filtro, get_detalhe, get_top_produtos

router = APIRouter(prefix="/api/v1/gerencial", tags=["gerencial"])
logger = logging.getLogger(__name__)


@router.get("/top-produtos", response_model=TopProdutosResponse)
def top_produtos(filters: ResolvedFilters = Depends(filters_query)):
    """Top 15 anuncios por marca (o front mostra 5), como foram vendidos —
    kit e' kit, sem de-para. Respeita canal, marca, periodo e comparacao."""
    cmp = filters.compare_period
    try:
        return get_top_produtos(
            datamart_leitura.consultar, filters.period.start, filters.period.end,
            (cmp.start, cmp.end) if cmp else None, filters.brands, canais_do_filtro(filters.mkt_ids),
        )
    except Exception as exc:  # noqa: BLE001 — texto do banco nunca sai
        logger.exception("top-produtos falhou")
        raise HTTPException(503, "Base de produtos indisponivel.") from exc


@router.get("/top-produtos/detalhe", response_model=DetalheProduto)
def top_produtos_detalhe(
    canal: Canal = Query(...),
    marca: str = Query(...),
    sku: str = Query(..., min_length=1, max_length=120),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    if marca not in BRANDS_IN_SCOPE:
        raise HTTPException(422, "Marca fora do escopo da Torre.")
    period = resolve_period(date_from=date_from, date_to=date_to, default_mode="previous_month", today=today_brt())
    try:
        detalhe = get_detalhe(datamart_leitura.consultar, canal, marca, sku, period.start, period.end)
    except Exception as exc:  # noqa: BLE001
        logger.exception("top-produtos/detalhe falhou")
        raise HTTPException(503, "Base de produtos indisponivel.") from exc
    if detalhe is None:
        raise HTTPException(404, "Produto sem venda nos ultimos 12 meses.")
    return detalhe
