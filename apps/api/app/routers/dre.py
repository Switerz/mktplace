"""`GET /api/v1/dre` — DRE gerencial por periodo, canal, marca e canal x marca.

Contrato: `docs/dre_contrato_0.md`. Especificacao do endpoint e do componente
visual: `docs/dre_mvp_1.md`.
"""
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps.filters import ResolvedFilters, resolve_brands
from app.deps.period import resolve_period, today_brt
from app.schemas.dre import DREResponse
from app.services import dre_service
from app.services.performance_service import (
    normalize_marketplace_param,
    parse_marketplace_param,
)

router = APIRouter(prefix="/api/v1/dre", tags=["dre"])

_CANAL_POR_ID = {1: "tiktok", 2: "ml", 3: "shopee"}

PADRAO = "padrao_competencia_madura"
INFORMADO = "informado"


def dre_filters(
    channels: Optional[str] = Query(None, description="Canal(is): 'all', isolado ou lista separada por virgula."),
    marketplace: Optional[str] = Query(None, description="Alias legado de 'channels'."),
    brands: Optional[str] = Query(None, description="Marca(s) (brand_key) separadas por virgula. Omitido = todas."),
    date_from: Optional[date] = Query(None, description="Inicio (dia 1 de um mes), YYYY-MM-DD."),
    date_to: Optional[date] = Query(None, description="Fim (ultimo dia de um mes), YYYY-MM-DD."),
    ref_month: Optional[str] = Query(None, description="Um mes, YYYY-MM."),
    db: Session = Depends(get_db),
) -> tuple[ResolvedFilters, str]:
    """Filtros da DRE. Difere do `filters_query` comum SO' no periodo padrao.

    O padrao dos endpoints agregados e' o mes anterior — que, pela regra de
    maturidade M+2 (D5), NUNCA e' maduro: a DRE abriria sempre NAO_CALCULAVEL.
    Sem periodo informado, a DRE usa a competencia madura mais recente. Periodo
    informado e' sempre respeitado, inclusive mes imaturo (que sai
    NAO_CALCULAVEL com o motivo). `filters_query` nao muda: os outros
    endpoints continuam com o mes anterior.
    """
    today = today_brt()  # relogio lido uma unica vez por request
    canal_param = channels if channels is not None else (marketplace if marketplace is not None else "all")
    try:
        canonical = normalize_marketplace_param(canal_param)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    origem = INFORMADO
    if date_from is None and date_to is None and not ref_month:
        mes = dre_service.competencia_madura_mais_recente(today)
        date_from, date_to, origem = mes, dre_service.ultimo_dia(mes), PADRAO
    period = resolve_period(ref_month=ref_month, date_from=date_from, date_to=date_to,
                            default_mode="previous_month", today=today)
    filtros = ResolvedFilters(channels=canonical, mkt_ids=parse_marketplace_param(canonical),
                              brands=resolve_brands(brands, db), period=period, compare_period=None)
    return filtros, origem


@router.get("", response_model=DREResponse)
def get_dre(resolvido: tuple = Depends(dre_filters), db: Session = Depends(get_db)):
    """DRE mensal. O periodo precisa cobrir meses inteiros (dia 1 ao ultimo dia).

    Sem periodo informado, vale a COMPETENCIA MADURA MAIS RECENTE (em
    13/10/2026: julho) e `periodo.origem = padrao_competencia_madura`.

    Le SO' o Neon. CMV e repasse TikTok vem de `marts.fact_dre_insumo_mensal`,
    publicada pelo `pipelines.dre.insumo_publisher`; insumo ausente ou vencido
    torna o componente indisponivel (falha fechada), nunca derruba a rota.
    """
    filters, origem = resolvido
    if db is None:
        raise HTTPException(503, "Banco indisponivel.")
    try:
        dre_service.meses_do_periodo(filters.period.start, filters.period.end)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    canais = [_CANAL_POR_ID[i] for i in filters.mkt_ids]
    return dre_service.get_dre(
        db,
        inicio=filters.period.start, fim=filters.period.end,
        canais=canais, marcas=filters.brands, hoje=today_brt(),
        agora=datetime.now(timezone.utc), periodo_origem=origem,
    )
