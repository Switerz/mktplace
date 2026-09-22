"""Gate EXP-2A — rotas read-only da Expedicao.

SOMENTE GET. Nao existe POST, PATCH nem DELETE nesta superficie, e o servico so'
emite SELECT.

Toda entrada do cliente e validada na BORDA contra a allowlist do servico. O que
nao passa aqui vira 422 com mensagem FIXA — a entrada nunca e' ecoada, para que
um payload com script nao volte renderizado para outro consumidor.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.expedicao import ExpedicaoResponse, TendenciaResponse
from app.services import expedicao_service as svc

router = APIRouter(prefix="/api/v1/expedicao", tags=["expedicao"])

_MSG_SITUACAO = "Situacao invalida. Valores aceitos: " + ", ".join(
    sorted(svc.SITUACAO_SQL))
_MSG_ORDEM = "Ordenacao invalida. Valores aceitos: " + ", ".join(
    sorted(svc.ORDENACOES))
_MSG_JANELA = (
    f"Janela invalida. Informe entre 1 e {svc.JANELA_MAX_HORAS} horas.")
_MSG_LISTA = "Lista invalida: use valores separados por virgula, sem vazios."
_MSG_CANAL = "Canal invalido. Valores aceitos: " + ", ".join(sorted(svc.CANAIS))


def _require_db(db: Session) -> Session:
    if db is None:
        raise HTTPException(503, "Banco de dados indisponivel. Verifique DATABASE_URL.")
    return db


def _lista(valor: Optional[str]) -> Optional[list[str]]:
    """CSV -> lista. Sem eco da entrada na mensagem de erro."""
    if valor is None:
        return None
    itens = [x.strip() for x in valor.split(",")]
    itens = [x for x in itens if x]
    if not itens:
        raise HTTPException(422, _MSG_LISTA)
    if len(itens) > 50 or any(len(x) > 64 for x in itens):
        raise HTTPException(422, _MSG_LISTA)
    return itens


def brands_query(
    brands: Optional[str] = Query(
        None, description="Marcas separadas por virgula. Omitido = todas."),
) -> Optional[list[str]]:
    return _lista(brands)


def accounts_query(
    accounts: Optional[str] = Query(
        None, description="Contas (shop_account) separadas por virgula."),
) -> Optional[list[str]]:
    return _lista(accounts)


def channel_query(
    channel: Optional[str] = Query(
        None,
        description="Canal da fotografia: shopee ou mercadolivre. "
                    "Omitido = shopee, o canal historico desta rota."),
) -> str:
    """Canal por ALLOWLIST, com default para nao quebrar quem ja' consome.

    A mensagem de erro lista os canais ACEITOS e nunca repete o que o cliente
    mandou: ecoar a entrada devolveria um payload arbitrario renderizado para
    outro consumidor.
    """
    if channel is None:
        return svc.CANAL_PADRAO
    if channel not in svc.CANAIS:
        raise HTTPException(422, _MSG_CANAL)
    return channel


def situacoes_query(
    situacao: Optional[str] = Query(
        None,
        description="Situacao operacional: overdue, due_within_24h, on_time, "
                    "deadline_unavailable, over_48h, stalled, slow, zombie. "
                    "Varias somam-se como E logico."),
) -> Optional[list[str]]:
    itens = _lista(situacao)
    if itens and any(x not in svc.SITUACAO_SQL for x in itens):
        raise HTTPException(422, _MSG_SITUACAO)
    return itens


@router.get("", response_model=ExpedicaoResponse)
@router.get("/", response_model=ExpedicaoResponse, include_in_schema=False)
def expedicao(
    channel: str = Depends(channel_query),
    brands: Optional[list[str]] = Depends(brands_query),
    accounts: Optional[list[str]] = Depends(accounts_query),
    situacao: Optional[list[str]] = Depends(situacoes_query),
    order_by: str = Query("criticidade", description=_MSG_ORDEM),
    limit: int = Query(svc.LIMIT_PADRAO, ge=1, le=svc.LIMIT_MAX),
    offset: int = Query(0, ge=0),
    include_queue: bool = Query(True, description="false devolve so' o resumo."),
    db: Session = Depends(get_db),
):
    """Resumo operacional + pagina da fila, sempre do MESMO batch."""
    if order_by not in svc.ORDENACOES:
        raise HTTPException(422, _MSG_ORDEM)
    return svc.get_expedicao(
        _require_db(db), channel=channel, brands=brands, accounts=accounts,
        situacoes=situacao, order_by=order_by, limit=limit, offset=offset,
        include_queue=include_queue,
    )


@router.get("/trend", response_model=TendenciaResponse)
def trend(
    channel: str = Depends(channel_query),
    window_hours: int = Query(svc.JANELA_PADRAO_HORAS, description=_MSG_JANELA),
    brands: Optional[list[str]] = Depends(brands_query),
    accounts: Optional[list[str]] = Depends(accounts_query),
    db: Session = Depends(get_db),
):
    """Serie horaria POR CONTA. Horas diferentes nunca sao somadas."""
    if window_hours < 1 or window_hours > svc.JANELA_MAX_HORAS:
        raise HTTPException(422, _MSG_JANELA)
    return svc.get_tendencia(
        _require_db(db), channel=channel, window_hours=window_hours,
        brands=brands, accounts=accounts,
    )
