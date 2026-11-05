"""EXPEDICAO-TIKTOK-ABERTOS — pedidos do TikTok em aberto agora (so' GET).

Rota irma da `/expedicao/tiktok` (serie por vencimento): esta e' a fotografia
dos pedidos abertos, no modelo da fila da Shopee e do Mercado Livre.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.services import expedicao_tiktok_abertos_service as svc

router = APIRouter(prefix="/api/v1/expedicao/tiktok", tags=["expedicao"])

_MSG_LISTA = "Lista invalida. Valores aceitos: " + ", ".join(svc.LISTAS)
_MSG_BRANDS = "Marcas invalidas: use valores separados por virgula."


@router.get("/abertos")
def tiktok_abertos(
    brands: Optional[str] = Query(None, description="Marcas separadas por virgula. Omitido = todas."),
    lista: Optional[str] = Query(
        None, description="Lista de pedidos a devolver: " + ", ".join(svc.LISTAS)
                          + ". Omitido = so' as contagens."),
    page: int = Query(1, description=f"Pagina da lista ({svc.TAMANHO_PAGINA} pedidos por pagina)."),
):
    if not getattr(settings, "expedicao_tiktok_abertos_api_enabled", True):
        raise HTTPException(404, "Not Found")
    marcas = None
    if brands is not None:
        marcas = [x.strip().lower() for x in brands.split(",") if x.strip()]
        if not marcas or len(marcas) > 20 or any(len(x) > 32 or not x.replace("_", "").isalnum() for x in marcas):
            raise HTTPException(422, _MSG_BRANDS)
    if lista is not None and lista not in svc.LISTAS:
        raise HTTPException(422, _MSG_LISTA)
    if page < 1 or page > svc.PAGINA_MAX:
        raise HTTPException(422, f"Pagina invalida. Informe entre 1 e {svc.PAGINA_MAX}.")
    return svc.obter_abertos(brands=marcas, lista=lista, pagina=page)
