"""Aba CX (menu principal) — rotas read-only `/api/v1/cx/*`.

  * `/shopee`      chat (Saude da Conta) + cancelamentos e devolucoes da Shopee;
  * `/atendimento` cancelamentos e devolucoes dos tres canais (base do CX-MKT-1);
  * `/shopee/pedidos` os pedidos de um motivo do Pareto (CX-PEDIDOS-MOTIVO).

Flag `settings.cx_api_enabled` (ligada por padrao): desligada, toda requisicao
responde 404 antes de qualquer consulta.

Le o Data Mart direto (ver `cx_atendimento_service`). SOMENTE GET. Entrada fora
da allowlist vira 422 com mensagem FIXA, sem ecoar o que veio.
"""
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.schemas.cx_atendimento import (
    JANELAS_DIAS,
    AtendimentoResponse,
    CxShopeeResponse,
    PedidosMotivoResponse,
)
from app.services import cx_atendimento_service as svc
from app.services import cx_pedidos_service


def exigir_flag() -> None:
    """Flag desligada = 404 igual ao de rota inexistente. Lida a cada requisicao."""
    if not settings.cx_api_enabled:
        raise HTTPException(404, "Not Found")


router = APIRouter(
    prefix="/api/v1/cx",
    tags=["cx"],
    dependencies=[Depends(exigir_flag)],
)

_MSG_BRANDS = "Marca invalida. Valores aceitos: " + ", ".join(svc.MARCAS_PERMITIDAS)
_MSG_DIAS = "Janela invalida. Valores aceitos: " + ", ".join(str(d) for d in JANELAS_DIAS)
_MAX_ITENS = 20


def brands_query(
    brands: Optional[list[str]] = Query(
        None, description="Marcas separadas por virgula ou repetidas. Omitido = todas."),
) -> Optional[list[str]]:
    if brands is None:
        return None
    itens = [x.strip().lower() for valor in brands for x in valor.split(",")]
    itens = [x for x in itens if x]
    if (not itens or len(itens) > _MAX_ITENS
            or any(x not in svc.MARCAS_PERMITIDAS for x in itens)):
        raise HTTPException(422, _MSG_BRANDS)
    return sorted(set(itens))


def dias_query(
    dias: Optional[str] = Query(None, description="Dias fechados ate' ontem: 7, 30 ou 90."),
) -> int:
    if dias is None:
        return svc.JANELA_PADRAO_DIAS
    if not dias.isdigit() or int(dias) not in JANELAS_DIAS:
        raise HTTPException(422, _MSG_DIAS)
    return int(dias)


@router.get("/shopee", response_model=CxShopeeResponse)
def cx_shopee(
    brands: Optional[list[str]] = Depends(brands_query),
    dias: int = Depends(dias_query),
):
    """Chat (Saude da Conta) e cancelamentos/devolucoes da Shopee."""
    return svc.montar_cx_shopee(brands=brands, dias=dias)


@router.get("/atendimento", response_model=AtendimentoResponse)
def atendimento(
    brands: Optional[list[str]] = Depends(brands_query),
    dias: int = Depends(dias_query),
):
    """Cancelamentos, reclamacoes e devolucoes por canal, cada bloco com estado explicito."""
    return svc.montar_atendimento(brands=brands, dias=dias)


_RE_CODIGO = re.compile(r"^[a-z0-9_]{1,80}$")
_MSG_MOTIVO = "Motivo invalido."


@router.get("/shopee/pedidos", response_model=PedidosMotivoResponse)
def cx_shopee_pedidos(
    tipo: Literal["cancelamento", "devolucao"] = Query(..., description="cancelamento ou devolucao"),
    motivo: str = Query(..., description="Codigo do motivo, como vem no Pareto (`codigo`)."),
    brands: Optional[list[str]] = Depends(brands_query),
    dias: int = Depends(dias_query),
):
    """Pedidos de um motivo do Pareto: numero, marca, dia, situacao e valor (ate' 500)."""
    if not _RE_CODIGO.match(motivo):
        raise HTTPException(422, _MSG_MOTIVO)
    return cx_pedidos_service.montar_pedidos_motivo(tipo=tipo, codigo=motivo, brands=brands, dias=dias)
