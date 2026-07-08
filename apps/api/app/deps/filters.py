"""
Dependencia compartilhada de filtros globais (canal, marca, periodo,
comparacao) para os endpoints agregados de KPI (overview, brands, canais,
financeiro, quality, pedidos).

Contrato (ver docs/filtros_globais_contrato.md):
- `channels`/`brands`/`date_from`/`date_to`/`compare` sao os nomes novos.
- `marketplace` e `ref_month` continuam aceitos como aliases legados, mas
  nunca se misturam com os nomes novos: se `channels` vier, `marketplace` e
  ignorado; se `date_from`/`date_to` vierem, `ref_month` e ignorado.
- Enviar so um de `date_from`/`date_to` e erro (422), nunca preenchido
  silenciosamente com um default.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps.period import (
    EffectivePeriod, MAX_RANGE_DAYS, resolve_period, resolve_previous_period, today_brt,
)
from app.services.performance_service import normalize_marketplace_param, parse_marketplace_param

__all__ = [
    "EffectivePeriod",
    "ResolvedFilters",
    "resolve_period",
    "resolve_previous_period",
    "resolve_brands",
    "get_scope_brand_keys",
    "filters_query",
    "filters_query_default_days",
]


@dataclass(frozen=True)
class ResolvedFilters:
    channels: str
    mkt_ids: list[int]
    brands: Optional[list[str]]
    period: EffectivePeriod
    compare_period: Optional[EffectivePeriod]


def get_scope_brand_keys(db: Session) -> set[str]:
    rows = db.execute(text("SELECT brand_key FROM marts.dim_loja WHERE ativo")).scalars().all()
    return set(rows)


def resolve_brands(brands_param: Optional[str], db: Optional[Session]) -> Optional[list[str]]:
    """Faz parse de `brands=barbours,kokeshi` e valida contra `marts.dim_loja`
    quando ha conexao disponivel. Retorna None quando nenhuma marca foi
    informada (= todas as marcas no escopo)."""
    if not brands_param:
        return None
    tokens = sorted({t.strip() for t in brands_param.split(",") if t.strip()})
    if not tokens:
        raise HTTPException(422, "brands deve conter ao menos uma marca valida.")
    if db is None:
        # Banco indisponivel: o endpoint ja vai falhar com 503 antes de usar
        # isso — nao ha por que barrar aqui com um erro de validacao diferente.
        return tokens
    valid = get_scope_brand_keys(db)
    invalid = sorted(set(tokens) - valid)
    if invalid:
        raise HTTPException(422, f"brands invalido(s): {', '.join(invalid)}. Validas: {', '.join(sorted(valid))}.")
    return tokens


def _resolve_channels(channels: Optional[str], marketplace: Optional[str]) -> str:
    channel_param = channels if channels is not None else (marketplace if marketplace is not None else "all")
    try:
        return normalize_marketplace_param(channel_param)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def filters_query(
    channels: Optional[str] = Query(None, description="Canal(is): 'all', isolado ou lista separada por virgula."),
    marketplace: Optional[str] = Query(None, description="Alias legado de 'channels'."),
    brands: Optional[str] = Query(None, description="Marca(s) (brand_key) separadas por virgula. Omitido = todas."),
    date_from: Optional[date] = Query(None, description="Inicio do periodo (inclusive), YYYY-MM-DD."),
    date_to: Optional[date] = Query(None, description="Fim do periodo (inclusive), YYYY-MM-DD."),
    ref_month: Optional[str] = Query(None, description="Alias legado de date_from/date_to, formato YYYY-MM."),
    compare: bool = Query(False, description="Se true, calcula tambem o periodo imediatamente anterior de mesma duracao."),
    db: Session = Depends(get_db),
) -> ResolvedFilters:
    today = today_brt()  # relogio lido uma unica vez por request, em America/Sao_Paulo
    canonical = _resolve_channels(channels, marketplace)
    mkt_ids = parse_marketplace_param(canonical)
    # default_mode="previous_month": sem date_from/date_to/ref_month, estes 5
    # endpoints (overview/brands/canais/financeiro/quality) sempre usaram o
    # mes calendario anterior como padrao (_parse_month(None) no router
    # legado; mesmo comportamento documentado em refMonth() no frontend) —
    # nunca "ultimos 30 dias", que e o default especifico de Pedidos.
    period = resolve_period(
        ref_month=ref_month, date_from=date_from, date_to=date_to,
        default_mode="previous_month", today=today,
    )
    compare_period = resolve_previous_period(period) if compare else None
    brand_keys = resolve_brands(brands, db)

    return ResolvedFilters(
        channels=canonical, mkt_ids=mkt_ids, brands=brand_keys,
        period=period, compare_period=compare_period,
    )


def filters_query_default_days(default_days: int):
    """Fabrica um dependency de filtros com um default de periodo diferente
    de 30 dias e aceitando `days_back` como alias legado adicional (usado por
    Pedidos, que hoje so tem days_back)."""

    def _dep(
        channels: Optional[str] = Query(None),
        marketplace: Optional[str] = Query(None),
        brands: Optional[str] = Query(None),
        date_from: Optional[date] = Query(None),
        date_to: Optional[date] = Query(None),
        ref_month: Optional[str] = Query(None),
        days_back: Optional[int] = Query(None, ge=1, le=MAX_RANGE_DAYS, description="Alias legado: janela de dias ate hoje."),
        compare: bool = Query(False),
        db: Session = Depends(get_db),
    ) -> ResolvedFilters:
        today = today_brt()  # relogio lido uma unica vez por request, em America/Sao_Paulo
        canonical = _resolve_channels(channels, marketplace)
        mkt_ids = parse_marketplace_param(canonical)

        effective_date_from, effective_date_to = date_from, date_to
        if effective_date_from is None and effective_date_to is None and ref_month is None and days_back is not None:
            effective_date_to = today
            effective_date_from = effective_date_to - timedelta(days=days_back - 1)

        period = resolve_period(
            ref_month=ref_month, date_from=effective_date_from, date_to=effective_date_to,
            default_days=default_days, today=today,
        )
        compare_period = resolve_previous_period(period) if compare else None
        brand_keys = resolve_brands(brands, db)

        return ResolvedFilters(
            channels=canonical, mkt_ids=mkt_ids, brands=brand_keys,
            period=period, compare_period=compare_period,
        )

    return _dep
