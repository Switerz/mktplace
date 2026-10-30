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
from app.schemas.expedicao import (
    ExpedicaoResponse,
    JornadaResponse,
    ShopeeLdrResponse,
    TendenciaResponse,
    TikTokDispatchResponse,
)
from app.services import expedicao_service as svc
from app.services import expedicao_shopee_ldr_service as shopee_ldr_svc
from app.services import expedicao_tiktok_service as tiktok_svc

router = APIRouter(prefix="/api/v1/expedicao", tags=["expedicao"])

_MSG_SITUACAO = "Situacao invalida. Valores aceitos: " + ", ".join(
    sorted(svc.SITUACOES_ACEITAS))
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
                    "deadline_unavailable, over_48h, stalled, slow, zombie, "
                    "due_next_day (prazo no proximo dia de vencimento depois "
                    "do dia da fotografia, em Brasilia; vazio sem prazo), "
                    "due_soon (perto de vencer: nao vencidos com prazo hoje "
                    "ate' 23:59 ou no proximo dia de vencimento; a mesma "
                    "definicao de `due_soon_count`). "
                    "Varias somam-se como E logico."),
) -> Optional[list[str]]:
    itens = _lista(situacao)
    if itens and any(x not in svc.SITUACOES_ACEITAS for x in itens):
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


# ---------------------------------------------------------------------------
# Gate EXP-TK-OPS-1 — TikTok Shop
# ---------------------------------------------------------------------------
# Rota IRMA e nao parametro de `channel`: a resposta tem outra forma (serie por
# coorte, nao fila paginada) e outro grao. Tenta-la caber no `ExpedicaoResponse`
# obrigaria metade dos campos a serem opcionais nos dois lados, e o consumidor
# deixaria de saber o que esperar de cada canal.
_MSG_EVENTO = (
    "Evento invalido. Valores aceitos: " + ", ".join(sorted(tiktok_svc.EVENTOS))
)
_MSG_DIAS = f"Janela invalida. Informe entre 1 e {tiktok_svc.JANELA_MAX_DIAS} dias."


@router.get("/tiktok", response_model=TikTokDispatchResponse)
def tiktok_dispatch(
    event: str = Query(
        tiktok_svc.EVENTO_PADRAO,
        description=(
            "`coleta` = retirada pela transportadora (padrao). "
            "`despacho` = etiqueta criada pelo vendedor."
        ),
    ),
    days: int = Query(
        tiktok_svc.JANELA_PADRAO_DIAS,
        description="Dias ANTES de hoje. 7 devolve 8 coortes (7 dias + hoje).",
    ),
    brands: Optional[list[str]] = Depends(brands_query),
    db: Session = Depends(get_db),
):
    """Serie diaria de atraso de despacho por data de pagamento.

    O prazo desta serie e' RECONSTRUIDO da regra de 2 dias uteis: o SLA oficial
    do TikTok nao e' ingerido. A resposta sempre traz
    `deadline_is_reconstructed = true` para que nenhuma tela exiba o numero
    como se fosse a medicao da plataforma.
    """
    if days < 1 or days > tiktok_svc.JANELA_MAX_DIAS:
        raise HTTPException(422, _MSG_DIAS)
    try:
        evento = tiktok_svc.resolver_evento(event)
    except tiktok_svc.EventoInvalido:
        # Mensagem FIXA: a entrada nunca e' ecoada de volta.
        raise HTTPException(422, _MSG_EVENTO) from None
    return tiktok_svc.obter_serie(
        _require_db(db), evento=evento, dias=days, brands=brands,
    )


# ---------------------------------------------------------------------------
# EXPEDICAO-SHOPEE-LDR-1 — LDR operacional da Shopee
# ---------------------------------------------------------------------------
# Rota IRMA da `/tiktok`, pelo mesmo motivo: serie por vencimento, nao fila.
_MSG_DIAS_SHOPEE = (
    f"Janela invalida. Informe entre 1 e {shopee_ldr_svc.JANELA_MAX_DIAS} dias.")


@router.get("/shopee/ldr", response_model=ShopeeLdrResponse)
def shopee_ldr(
    days: int = Query(
        shopee_ldr_svc.JANELA_PADRAO_DIAS,
        description="Dias de VENCIMENTO antes de hoje (maximo 35, a janela "
                    "que o publisher recalcula).",
    ),
    brands: Optional[list[str]] = Depends(brands_query),
    db: Session = Depends(get_db),
):
    """LDR operacional (coleta x prazo Shopee), por dia de vencimento.

    Indicador INTERNO: a resposta sempre traz `official = false` e o aviso de
    que nao e' a taxa usada pela Shopee para penalizacao. FBS, cancelados sem
    coleta e pedidos sem prazo ficam fora da taxa e voltam em `exclusions`.
    """
    if days < 1 or days > shopee_ldr_svc.JANELA_MAX_DIAS:
        raise HTTPException(422, _MSG_DIAS_SHOPEE)
    if not shopee_ldr_svc.habilitado():
        # Flag off: nem a sessao de banco e' exigida.
        return shopee_ldr_svc.obter_ldr(None, dias=days, brands=brands)
    return shopee_ldr_svc.obter_ldr(_require_db(db), dias=days, brands=brands)


# ---------------------------------------------------------------------------
# T-0011 — Jornada de pedidos (contrato jornada v1)
# ---------------------------------------------------------------------------
_MSG_CANAL_JORNADA = "Canal invalido. Valores aceitos: " + ", ".join(
    sorted(svc.JORNADA_CANAIS))
_MSG_EVENTO_JORNADA = (
    "Evento invalido. Valores aceitos: coleta, despacho (despacho so' no tiktok).")


@router.get("/jornada", response_model=JornadaResponse)
def jornada(
    channel: str = Query(
        ..., description="Canal: shopee, mercadolivre ou tiktok (allowlist)."),
    brands: Optional[list[str]] = Depends(brands_query),
    days: int = Query(
        svc.JORNADA_DIAS_PADRAO,
        description="Janela da coorte em dias FECHADOS (Brasilia), terminando "
                    "ontem: 1-35 (Shopee) ou 1-90 (TikTok). Ignorado no "
                    "Mercado Livre, que nao tem coorte."),
    event: str = Query(
        svc.JORNADA_EVENTO_PADRAO,
        description="`coleta` (padrao) ou `despacho` (so' TikTok; a Shopee e' "
                    "sempre coleta)."),
    db: Session = Depends(get_db),
):
    """Jornada do pedido: fila aberta + coorte, agregadas NO SERVIDOR.

    Etapas sao exclusivas e somam a base (conferido; se nao fechar, `partial`).
    Sobreposicoes nunca entram na soma. Nunca ha percentual: so' `count` e
    `base`. Ausencia de medida sai `null` com `reason`, nunca zero.
    """
    # Mensagens FIXAS: a entrada do cliente nunca e' ecoada num erro.
    if channel not in svc.JORNADA_CANAIS:
        raise HTTPException(422, _MSG_CANAL_JORNADA)
    # ML nao tem coorte: `days` e' IGNORADO, nao validado (contrato v1.1 §3.1.6).
    if channel in svc.JORNADA_DIAS_MAX and not 1 <= days <= svc.JORNADA_DIAS_MAX[channel]:
        raise HTTPException(
            422, f"Janela invalida. Informe entre 1 e "
                 f"{svc.JORNADA_DIAS_MAX[channel]} dias.")
    try:
        evento = tiktok_svc.resolver_evento(event)
    except tiktok_svc.EventoInvalido:
        raise HTTPException(422, _MSG_EVENTO_JORNADA) from None
    if channel != "tiktok" and evento != "coleta":
        raise HTTPException(422, _MSG_EVENTO_JORNADA)
    if not svc.jornada_habilitada(channel):
        # Flag off: nem a sessao de banco e' exigida.
        return svc.get_jornada(None, channel=channel, brands=brands, dias=days,
                               evento=evento)
    return svc.get_jornada(_require_db(db), channel=channel, brands=brands,
                           dias=days, evento=evento)
