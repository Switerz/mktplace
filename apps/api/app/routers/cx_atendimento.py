"""CX-MKT-1 — rota read-only da aba "Atendimento" da Performance das contas.

INERTE POR FLAG PROPRIA: com `settings.performance_contas_atendimento_api_enabled`
desligada (padrao) toda requisicao responde 404, antes de qualquer consulta.

Le o Data Mart direto (ver `cx_atendimento_service`). SOMENTE GET. Entrada fora
da allowlist vira 422 com mensagem FIXA, sem ecoar o que veio.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.schemas.cx_atendimento import JANELAS_DIAS, AtendimentoResponse
from app.services import cx_atendimento_service as svc


def exigir_flag() -> None:
    """Flag desligada = 404 igual ao de rota inexistente. Lida a cada requisicao."""
    if not settings.performance_contas_atendimento_api_enabled:
        raise HTTPException(404, "Not Found")


router = APIRouter(
    prefix="/api/v1/performance-contas/atendimento",
    tags=["performance-contas"],
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


@router.get("", response_model=AtendimentoResponse)
@router.get("/", response_model=AtendimentoResponse, include_in_schema=False)
def atendimento(
    brands: Optional[list[str]] = Depends(brands_query),
    dias: int = Depends(dias_query),
):
    """Cancelamentos, reclamacoes e devolucoes por canal, cada bloco com estado explicito."""
    return svc.montar_atendimento(brands=brands, dias=dias)
