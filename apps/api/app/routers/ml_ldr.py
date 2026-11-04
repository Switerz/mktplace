"""CX-MKT-3 — rota read-only do LDR do Mercado Livre para a faixa do topo da Expedicao.

Mesma flag da secao Performance (`expedicao_performance_api_enabled`): desligada,
404 antes de qualquer consulta. Entrada fora da allowlist = 422 sem eco.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.services import ml_ldr_service as svc
from app.services.gold_service import ML_BRANDS


def exigir_flag() -> None:
    if not settings.expedicao_performance_api_enabled:
        raise HTTPException(404, "Not Found")


router = APIRouter(prefix="/api/v1/expedicao/ml", tags=["expedicao"], dependencies=[Depends(exigir_flag)])

_MSG_BRANDS = "Marca invalida. Valores aceitos: " + ", ".join(ML_BRANDS)


def brands_query(brands: Optional[list[str]] = Query(None)) -> Optional[list[str]]:
    if brands is None:
        return None
    itens = [x.strip().lower() for valor in brands for x in valor.split(",") if x.strip()]
    # Marca sem conta ML (ex.: apice) e' valida na Torre: devolve lista vazia, nao 422.
    from app.services.gold_service import BRANDS_IN_SCOPE
    if not itens or len(itens) > 20 or any(x not in BRANDS_IN_SCOPE for x in itens):
        raise HTTPException(422, _MSG_BRANDS)
    return sorted(set(itens))


@router.get("/ldr")
def ldr_ml(
    days: int = Query(7, description="Dias fechados ate' ontem (1 a 30)."),
    brands: Optional[list[str]] = Depends(brands_query),
):
    if days < 1 or days > svc.JANELA_MAX_DIAS:
        raise HTTPException(422, f"Janela invalida. Informe entre 1 e {svc.JANELA_MAX_DIAS} dias.")
    return svc.obter_ldr_ml(dias=days, brands=brands)
