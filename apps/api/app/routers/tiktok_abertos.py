"""EXPEDICAO-TIKTOK-ABERTOS — pedidos do TikTok em aberto agora (so' GET).

Rota irma da `/expedicao/tiktok` (serie por vencimento): esta e' a fotografia
dos pedidos abertos, no modelo da fila da Shopee e do Mercado Livre.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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


@router.get("/coleta-perdida")
def tiktok_coleta_perdida(
    vencimento: Optional[str] = Query(
        None, description="Dia do prazo de COLETA (AAAA-MM-DD). Omitido = ultimo dia util antes de hoje."),
    brands: Optional[str] = Query(None, description="Marcas separadas por virgula. Omitido = todas."),
    status: Optional[str] = Query(
        None, description="Status ATUAL do pedido no TikTok, separados por virgula: "
                          + ", ".join(svc.STATUS_COLETA_PERDIDA) + ". Omitido = todos."),
    escopo: str = Query(
        "etiqueta_pronta",
        description="etiqueta_pronta = so' com etiqueta pronta ate' o fim do prazo; "
                    "todos = todos os que venceram sem coleta (leitura da LDR)."),
    page: int = Query(1, description=f"Pagina da lista ({svc.TAMANHO_PAGINA} pedidos por pagina)."),
):
    """Pedidos com prazo de coleta no dia, etiqueta pronta ate' o fim dele e
    SEM coleta ate' o fim dele (inclusive os coletados depois)."""
    if not getattr(settings, "expedicao_tiktok_abertos_api_enabled", True):
        raise HTTPException(404, "Not Found")
    hoje = (datetime.now(timezone.utc) + svc.BRT).date()
    if vencimento is None:
        dia = hoje - timedelta(days=1)
        while not svc.dia_util(dia):
            dia -= timedelta(days=1)
    else:
        try:
            dia = date.fromisoformat(vencimento)
        except ValueError:
            raise HTTPException(422, "Data invalida. Use AAAA-MM-DD.")
        if not (hoje - timedelta(days=svc.COLETA_PERDIDA_DIAS) <= dia <= hoje):
            raise HTTPException(422, f"Data fora da janela: ate' {svc.COLETA_PERDIDA_DIAS} dias atras.")
    marcas = None
    if brands is not None:
        marcas = [x.strip().lower() for x in brands.split(",") if x.strip()]
        if not marcas or len(marcas) > 20 or any(len(x) > 32 or not x.replace("_", "").isalnum() for x in marcas):
            raise HTTPException(422, _MSG_BRANDS)
    if page < 1 or page > svc.PAGINA_MAX:
        raise HTTPException(422, f"Pagina invalida. Informe entre 1 e {svc.PAGINA_MAX}.")
    lista_status = None
    if status is not None:
        lista_status = [x.strip().upper() for x in status.split(",") if x.strip()]
        if not lista_status or any(x not in svc.STATUS_COLETA_PERDIDA for x in lista_status):
            raise HTTPException(422, "Status invalido. Valores aceitos: " + ", ".join(svc.STATUS_COLETA_PERDIDA))
    if escopo not in svc.ESCOPOS_COLETA:
        raise HTTPException(422, "Escopo invalido. Valores aceitos: " + ", ".join(svc.ESCOPOS_COLETA))
    return svc.obter_coleta_perdida(vencimento=dia, brands=marcas, status=lista_status,
                                    escopo=escopo, pagina=page)
