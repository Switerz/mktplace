"""CX-MKT-2 — rota read-only da secao "Performance" da Expedicao.

INERTE POR FLAG: com `settings.expedicao_performance_api_enabled` desligada
(padrao) toda requisicao responde 404, antes de qualquer consulta.

SOMENTE GET. `canal` e `brands` sao validados na borda contra allowlist; o que
nao passa vira 422 com mensagem FIXA, sem ecoar a entrada.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.schemas.performance_oficial import PerformanceOficialResponse
from app.services import performance_oficial_service as svc


def exigir_flag() -> None:
    if not settings.expedicao_performance_api_enabled:
        raise HTTPException(404, "Not Found")


router = APIRouter(
    prefix="/api/v1/expedicao/performance",
    tags=["expedicao"],
    dependencies=[Depends(exigir_flag)],
)

_MSG_CANAL = "Canal invalido. Valores aceitos: " + ", ".join(svc.CANAIS)
_MSG_BRANDS = "Marca invalida. Valores aceitos: " + ", ".join(svc.MARCAS_PERMITIDAS)
_MAX_ITENS = 20


def canal_query(canal: str = Query(..., description="mercadolivre, shopee ou tiktokshop")) -> str:
    if canal not in svc.CANAIS:
        raise HTTPException(422, _MSG_CANAL)
    return canal


def brands_query(
    brands: Optional[list[str]] = Query(None, description="Marcas separadas por virgula. Omitido = todas."),
) -> Optional[list[str]]:
    if brands is None:
        return None
    itens = [x.strip().lower() for valor in brands for x in valor.split(",")]
    itens = [x for x in itens if x]
    if (not itens or len(itens) > _MAX_ITENS
            or any(x not in svc.MARCAS_PERMITIDAS for x in itens)):
        raise HTTPException(422, _MSG_BRANDS)
    return sorted(set(itens))


@router.get("", response_model=PerformanceOficialResponse)
@router.get("/", response_model=PerformanceOficialResponse, include_in_schema=False)
def performance_oficial(
    canal: str = Depends(canal_query),
    brands: Optional[list[str]] = Depends(brands_query),
):
    """Nota oficial de cada conta do canal, a partir da fotografia diaria mais recente."""
    return svc.montar_performance_oficial(canal=canal, brands=brands)
