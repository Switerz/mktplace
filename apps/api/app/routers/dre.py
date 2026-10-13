"""`GET /api/v1/dre` — DRE gerencial por periodo, canal, marca e canal x marca.

Contrato: `docs/dre_contrato_0.md`. Especificacao do endpoint e do componente
visual: `docs/dre_mvp_1.md`.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps.filters import ResolvedFilters, filters_query
from app.deps.period import today_brt
from app.schemas.dre import DREResponse
from app.services import dre_service

router = APIRouter(prefix="/api/v1/dre", tags=["dre"])

_CANAL_POR_ID = {1: "tiktok", 2: "ml", 3: "shopee"}


@router.get("", response_model=DREResponse)
def get_dre(filters: ResolvedFilters = Depends(filters_query), db: Session = Depends(get_db)):
    """DRE mensal. O periodo precisa cobrir meses inteiros (dia 1 ao ultimo dia).

    Sem periodo informado, vale o mes anterior (padrao dos endpoints agregados).
    `compare` e' aceito pelo filtro global e ignorado aqui: a DRE ainda nao tem
    comparacao entre periodos.

    Le SO' o Neon. CMV e repasse TikTok vem de `marts.fact_dre_insumo_mensal`,
    publicada pelo `pipelines.dre.insumo_publisher`; insumo ausente ou vencido
    torna o componente indisponivel (falha fechada), nunca derruba a rota.
    """
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
        agora=datetime.now(timezone.utc),
    )
