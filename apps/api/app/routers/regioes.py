from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps.filters import ResolvedFilters, filters_query
from app.schemas.regioes import (
    RegioesByBrandResponse, RegioesByUfResponse, RegioesSummaryResponse, RegioesTrendResponse,
)
from app.services import regioes_service as svc

router = APIRouter(prefix="/api/v1/regioes", tags=["regioes"])


def _require_db(db: Session) -> Session:
    if db is None:
        raise HTTPException(503, "Banco de dados indisponivel. Verifique DATABASE_URL.")
    return db


def uf_query(
    uf: Optional[str] = Query(
        None,
        description="UF(s) separadas por virgula, ex: 'SP,RJ'. 27 UFs oficiais + 'XX' (desconhecida). Omitido = todas.",
    ),
) -> Optional[list[str]]:
    try:
        return svc.parse_uf_param(uf)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/summary", response_model=RegioesSummaryResponse)
def summary(
    filters: ResolvedFilters = Depends(filters_query),
    uf: Optional[list[str]] = Depends(uf_query),
    db: Session = Depends(get_db),
):
    return svc.get_summary(
        _require_db(db), filters.mkt_ids, filters.brands, filters.period,
        uf_filter=uf, channels=filters.channels,
    )


@router.get("/by-uf", response_model=RegioesByUfResponse)
def by_uf(
    filters: ResolvedFilters = Depends(filters_query),
    uf: Optional[list[str]] = Depends(uf_query),
    db: Session = Depends(get_db),
):
    return svc.get_by_uf(
        _require_db(db), filters.mkt_ids, filters.brands, filters.period,
        uf_filter=uf, channels=filters.channels,
    )


@router.get("/by-brand", response_model=RegioesByBrandResponse)
def by_brand(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    return svc.get_by_brand(
        _require_db(db), filters.mkt_ids, filters.brands, filters.period,
        channels=filters.channels,
    )


@router.get("/trend", response_model=RegioesTrendResponse)
def trend(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    return svc.get_trend(
        _require_db(db), filters.mkt_ids, filters.brands, filters.period,
        channels=filters.channels,
    )
