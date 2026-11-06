import logging

from fastapi import APIRouter, Depends, HTTPException

from app.deps.filters import ResolvedFilters, filters_query
from app.schemas.top_produtos import TopProdutosResponse
from app.services import datamart_leitura
from app.services.top_produtos_service import get_top_produtos

router = APIRouter(prefix="/api/v1/gerencial", tags=["gerencial"])


@router.get("/top-produtos", response_model=TopProdutosResponse)
def top_produtos(filters: ResolvedFilters = Depends(filters_query)):
    """Top 5 produtos de marketplace por marca (receita faturada, base mensal).
    `channels` e' aceito mas nao filtra: a base junta os marketplaces."""
    try:
        return get_top_produtos(
            datamart_leitura.consultar, filters.period.start, filters.period.end, filters.brands,
        )
    except Exception as exc:  # noqa: BLE001 — texto do banco nunca sai
        logging.getLogger(__name__).exception("top-produtos falhou")
        raise HTTPException(503, "Base de produtos indisponivel.") from exc
