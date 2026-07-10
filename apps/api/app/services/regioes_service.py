"""
Queries de `marts.fact_marketplace_region_daily` (Neon, sincronizada do Data
Mart no Gate 6B — ver docs/regional_design_draft.md). Modulo separado de
performance_service.py (ja grande) por dominio: cobertura regional/UF, nao
KPIs de canal x marca x tempo.

Regras de negocio fixadas no Gate 6A/6B, nao redescobrir aqui:
- TikTok (marketplace_id=1) NUNCA tem linha nesta tabela — nenhuma fonte
  mapeada tem UF do pedido. Ausencia de linhas != GMV=0 regional; os
  endpoints sempre expoem `channels_sem_cobertura_regional` para deixar
  isso explicito ao cliente.
- Percentuais de cobertura sao SEMPRE derivados de numerador/denominador
  explicitos (uf_known_orders/uf_eligible_orders,
  shipping_cost_covered_orders/shipping_cost_eligible_orders) — nunca
  armazenados prontos. Denominador 0 vira `None` (N/A), nunca 0%.
- seller_shipping_cost e NULL para Shopee (sem campo equivalente na fonte);
  SUM() em SQL ja ignora NULL, entao "quando aplicavel" sai de graca.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps.period import EffectivePeriod
from app.services.performance_service import (
    BRAND_LABELS, ML_ID, MES_LABELS, SHOPEE_ID, TIKTOK_ID, _brand_filter_sql, _f,
)

REGION_TABLE = "marts.fact_marketplace_region_daily"

# TikTok e' a unica marketplace sem NENHUMA linha estrutural nesta tabela —
# fato de dominio confirmado em Gate 6A/6B (0 linhas), nao uma consulta.
NO_REGIONAL_COVERAGE_IDS = {TIKTOK_ID}
_MKT_NAME = {TIKTOK_ID: "tiktok", ML_ID: "ml", SHOPEE_ID: "shopee"}

VALID_UFS = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    "XX",
})

COVERAGE_OK_THRESHOLD = 80.0
COVERAGE_PARTIAL_THRESHOLD = 50.0


def parse_uf_param(uf_param: str | None) -> list[str] | None:
    """Faz parse de `uf=SP,RJ` e valida contra as 27 UFs oficiais + 'XX'
    (desconhecida). Retorna None quando nenhuma UF foi informada (= todas)."""
    if not uf_param:
        return None
    tokens = sorted({t.strip().upper() for t in uf_param.split(",") if t.strip()})
    if not tokens:
        raise ValueError("uf deve conter ao menos uma UF valida.")
    invalid = sorted(set(tokens) - VALID_UFS)
    if invalid:
        raise ValueError(
            f"uf invalido(s): {', '.join(invalid)}. Validas: 27 UFs oficiais + 'XX' (desconhecida)."
        )
    return tokens


def _pct(num: float, denom: float, decimals: int = 2) -> float | None:
    """Denominador <= 0 vira None (N/A) — nunca 0%. Mesma regra em toda a
    camada de cobertura regional (uf_fill_pct e shipping_cost_coverage_pct)."""
    return round(num / denom * 100, decimals) if denom > 0 else None


def coverage_level(pct: float | None) -> str:
    if pct is None:
        return "not_applicable"
    if pct >= COVERAGE_OK_THRESHOLD:
        return "ok"
    if pct >= COVERAGE_PARTIAL_THRESHOLD:
        return "partial"
    return "low"


def coverage_warning(level: str) -> bool:
    return level in ("partial", "low")


def channels_sem_cobertura_regional(mkt_ids: list[int]) -> list[str]:
    """Canais pedidos que estruturalmente nunca tem linha na tabela regional
    (hoje, so TikTok). Usado para o cliente nunca confundir ausencia de dado
    regional com GMV=0 real."""
    return [_MKT_NAME[i] for i in mkt_ids if i in NO_REGIONAL_COVERAGE_IDS]


def _uf_filter_sql(uf_filter: list[str] | None, params: dict) -> str:
    if not uf_filter:
        return ""
    params["ufs"] = uf_filter
    return " AND f.uf = ANY(:ufs)"


def _max_refreshed_at(
    db: Session, start: date, end: date, mkt_ids: list[int], brand_keys: list[str] | None = None,
) -> str | None:
    params: dict = {"start": start, "end": end, "mkt_ids": mkt_ids}
    brand_filter = _brand_filter_sql(brand_keys, params)
    sql = text(f"""
        SELECT MAX(f.ingested_at) AS refreshed_at
        FROM {REGION_TABLE} f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
          {brand_filter}
    """)
    row = db.execute(sql, params).mappings().first()
    ts = row["refreshed_at"] if row else None
    return ts.isoformat() if ts else None


def _round_or_none(v) -> float | None:
    return round(_f(v), 2) if v is not None else None


# ---------------------------------------------------------------------------
# GET /regioes/summary
# ---------------------------------------------------------------------------
def get_summary(
    db: Session, mkt_ids: list[int], brand_keys: list[str] | None, period: EffectivePeriod,
    *, uf_filter: list[str] | None = None, channels: str = "all",
) -> dict:
    params: dict = {"start": period.start, "end": period.end, "mkt_ids": mkt_ids}
    brand_filter = _brand_filter_sql(brand_keys, params)
    uf_sql = _uf_filter_sql(uf_filter, params)

    sql = text(f"""
        SELECT
            COALESCE(SUM(f.gmv), 0)                          AS gmv,
            COALESCE(SUM(f.orders), 0)                       AS orders,
            COALESCE(SUM(f.units_sold), 0)                   AS units_sold,
            COUNT(DISTINCT CASE WHEN f.uf <> 'XX' AND f.orders > 0 THEN f.uf END) AS ufs_com_venda,
            COALESCE(SUM(f.uf_known_orders), 0)               AS uf_known_orders,
            COALESCE(SUM(f.uf_eligible_orders), 0)            AS uf_eligible_orders,
            COALESCE(SUM(f.shipping_cost_covered_orders), 0)  AS shipping_cost_covered_orders,
            COALESCE(SUM(f.shipping_cost_eligible_orders), 0) AS shipping_cost_eligible_orders,
            SUM(f.seller_shipping_cost)                       AS seller_shipping_cost
        FROM {REGION_TABLE} f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
          {brand_filter}{uf_sql}
    """)
    row = db.execute(sql, params).mappings().first() or {}

    uf_known = int(_f(row.get("uf_known_orders")))
    uf_eligible = int(_f(row.get("uf_eligible_orders")))
    cost_covered = int(_f(row.get("shipping_cost_covered_orders")))
    cost_eligible = int(_f(row.get("shipping_cost_eligible_orders")))
    uf_fill_pct = _pct(uf_known, uf_eligible)
    level = coverage_level(uf_fill_pct)

    return {
        "gmv": round(_f(row.get("gmv")), 2),
        "orders": int(_f(row.get("orders"))),
        "units_sold": int(_f(row.get("units_sold"))),
        "ufs_com_venda": int(_f(row.get("ufs_com_venda"))),
        "uf_known_orders": uf_known,
        "uf_eligible_orders": uf_eligible,
        "uf_fill_pct": uf_fill_pct,
        "shipping_cost_covered_orders": cost_covered,
        "shipping_cost_eligible_orders": cost_eligible,
        "shipping_cost_coverage_pct": _pct(cost_covered, cost_eligible),
        "seller_shipping_cost": _round_or_none(row.get("seller_shipping_cost")),
        "coverage_level": level,
        "coverage_warning": coverage_warning(level),
        "date_from": period.start,
        "date_to": period.end,
        "filters": {"channels": channels, "brands": brand_keys},
        "refreshed_at": _max_refreshed_at(db, period.start, period.end, mkt_ids, brand_keys),
        "channels_sem_cobertura_regional": channels_sem_cobertura_regional(mkt_ids),
    }


# ---------------------------------------------------------------------------
# GET /regioes/by-uf
# ---------------------------------------------------------------------------
def get_by_uf(
    db: Session, mkt_ids: list[int], brand_keys: list[str] | None, period: EffectivePeriod,
    *, uf_filter: list[str] | None = None, channels: str = "all",
) -> dict:
    params: dict = {"start": period.start, "end": period.end, "mkt_ids": mkt_ids}
    brand_filter = _brand_filter_sql(brand_keys, params)
    uf_sql = _uf_filter_sql(uf_filter, params)

    sql = text(f"""
        SELECT
            f.uf,
            COALESCE(SUM(f.gmv), 0)                          AS gmv,
            COALESCE(SUM(f.orders), 0)                       AS orders,
            COALESCE(SUM(f.units_sold), 0)                   AS units_sold,
            COALESCE(SUM(f.canceled_orders), 0)              AS canceled_orders,
            COALESCE(SUM(f.returned_orders), 0)              AS returned_orders,
            SUM(f.seller_shipping_cost)                       AS seller_shipping_cost,
            COALESCE(SUM(f.uf_known_orders), 0)               AS uf_known_orders,
            COALESCE(SUM(f.uf_eligible_orders), 0)            AS uf_eligible_orders,
            COALESCE(SUM(f.shipping_cost_covered_orders), 0)  AS shipping_cost_covered_orders,
            COALESCE(SUM(f.shipping_cost_eligible_orders), 0) AS shipping_cost_eligible_orders
        FROM {REGION_TABLE} f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
          {brand_filter}{uf_sql}
        GROUP BY f.uf
        ORDER BY f.uf
    """)
    rows = db.execute(sql, params).mappings().all()

    data = []
    for r in rows:
        uf_known = int(_f(r["uf_known_orders"]))
        uf_eligible = int(_f(r["uf_eligible_orders"]))
        cost_covered = int(_f(r["shipping_cost_covered_orders"]))
        cost_eligible = int(_f(r["shipping_cost_eligible_orders"]))
        uf_fill_pct = _pct(uf_known, uf_eligible)
        level = coverage_level(uf_fill_pct)
        data.append({
            "uf": r["uf"],
            "gmv": round(_f(r["gmv"]), 2),
            "orders": int(_f(r["orders"])),
            "units_sold": int(_f(r["units_sold"])),
            "canceled_orders": int(_f(r["canceled_orders"])),
            "returned_orders": int(_f(r["returned_orders"])),
            "seller_shipping_cost": _round_or_none(r.get("seller_shipping_cost")),
            "uf_known_orders": uf_known,
            "uf_eligible_orders": uf_eligible,
            "shipping_cost_covered_orders": cost_covered,
            "shipping_cost_eligible_orders": cost_eligible,
            "uf_fill_pct": uf_fill_pct,
            "shipping_cost_coverage_pct": _pct(cost_covered, cost_eligible),
            "coverage_level": level,
            "coverage_warning": coverage_warning(level),
        })

    return {
        "data": data,
        "date_from": period.start,
        "date_to": period.end,
        "filters": {"channels": channels, "brands": brand_keys},
        "refreshed_at": _max_refreshed_at(db, period.start, period.end, mkt_ids, brand_keys),
        "channels_sem_cobertura_regional": channels_sem_cobertura_regional(mkt_ids),
    }


# ---------------------------------------------------------------------------
# GET /regioes/by-brand
# ---------------------------------------------------------------------------
def get_by_brand(
    db: Session, mkt_ids: list[int], brand_keys: list[str] | None, period: EffectivePeriod,
    *, channels: str = "all",
) -> dict:
    params: dict = {"start": period.start, "end": period.end, "mkt_ids": mkt_ids}
    brand_filter = _brand_filter_sql(brand_keys, params)

    sql = text(f"""
        SELECT
            l.brand_key,
            f.marketplace_id,
            COALESCE(SUM(f.gmv), 0)                          AS gmv,
            COALESCE(SUM(f.orders), 0)                       AS orders,
            COALESCE(SUM(f.units_sold), 0)                   AS units_sold,
            COALESCE(SUM(f.uf_known_orders), 0)               AS uf_known_orders,
            COALESCE(SUM(f.uf_eligible_orders), 0)            AS uf_eligible_orders,
            COALESCE(SUM(f.shipping_cost_covered_orders), 0)  AS shipping_cost_covered_orders,
            COALESCE(SUM(f.shipping_cost_eligible_orders), 0) AS shipping_cost_eligible_orders
        FROM {REGION_TABLE} f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
          {brand_filter}
        GROUP BY l.brand_key, f.marketplace_id
        ORDER BY l.brand_key, f.marketplace_id
    """)
    rows = db.execute(sql, params).mappings().all()

    data = []
    for r in rows:
        uf_known = int(_f(r["uf_known_orders"]))
        uf_eligible = int(_f(r["uf_eligible_orders"]))
        cost_covered = int(_f(r["shipping_cost_covered_orders"]))
        cost_eligible = int(_f(r["shipping_cost_eligible_orders"]))
        uf_fill_pct = _pct(uf_known, uf_eligible)
        level = coverage_level(uf_fill_pct)
        brand = r["brand_key"]
        data.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "marketplace_id": r["marketplace_id"],
            "marketplace": _MKT_NAME.get(r["marketplace_id"], str(r["marketplace_id"])),
            "gmv": round(_f(r["gmv"]), 2),
            "orders": int(_f(r["orders"])),
            "units_sold": int(_f(r["units_sold"])),
            "uf_known_orders": uf_known,
            "uf_eligible_orders": uf_eligible,
            "uf_fill_pct": uf_fill_pct,
            "shipping_cost_covered_orders": cost_covered,
            "shipping_cost_eligible_orders": cost_eligible,
            "shipping_cost_coverage_pct": _pct(cost_covered, cost_eligible),
            "coverage_level": level,
            "coverage_warning": coverage_warning(level),
        })

    return {
        "data": data,
        "date_from": period.start,
        "date_to": period.end,
        "filters": {"channels": channels, "brands": brand_keys},
        "refreshed_at": _max_refreshed_at(db, period.start, period.end, mkt_ids, brand_keys),
        "channels_sem_cobertura_regional": channels_sem_cobertura_regional(mkt_ids),
    }


# ---------------------------------------------------------------------------
# GET /regioes/trend
# ---------------------------------------------------------------------------
def get_trend(
    db: Session, mkt_ids: list[int], brand_keys: list[str] | None, period: EffectivePeriod,
    *, channels: str = "all",
) -> dict:
    # Mesma regra de granularidade de perf_svc.get_trend: diaria ate 92 dias,
    # mensal acima disso (intervalos longos em barras diarias ficam ilegiveis).
    granularity = "day" if period.days <= 92 else "month"
    trunc_expr = "f.date" if granularity == "day" else "DATE_TRUNC('month', f.date)::date"

    params: dict = {"start": period.start, "end": period.end, "mkt_ids": mkt_ids}
    brand_filter = _brand_filter_sql(brand_keys, params)

    sql = text(f"""
        SELECT {trunc_expr} AS bucket,
               COALESCE(SUM(f.gmv), 0)               AS gmv,
               COALESCE(SUM(f.orders), 0)            AS orders,
               COALESCE(SUM(f.uf_known_orders), 0)    AS uf_known_orders,
               COALESCE(SUM(f.uf_eligible_orders), 0) AS uf_eligible_orders
        FROM {REGION_TABLE} f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
          {brand_filter}
        GROUP BY {trunc_expr}
        ORDER BY {trunc_expr}
    """)
    rows = db.execute(sql, params).mappings().all()

    data = []
    for r in rows:
        bucket: date = r["bucket"]
        label = (
            f"{bucket.day:02d}/{bucket.month:02d}"
            if granularity == "day"
            else f"{MES_LABELS[bucket.month]}/{str(bucket.year)[2:]}"
        )
        uf_known = int(_f(r["uf_known_orders"]))
        uf_eligible = int(_f(r["uf_eligible_orders"]))
        data.append({
            "date": bucket.isoformat(),
            "label": label,
            "gmv": round(_f(r["gmv"]), 2),
            "orders": int(_f(r["orders"])),
            "uf_fill_pct": _pct(uf_known, uf_eligible),
        })

    return {
        "granularity": granularity,
        "data": data,
        "date_from": period.start,
        "date_to": period.end,
        "filters": {"channels": channels, "brands": brand_keys},
        "refreshed_at": _max_refreshed_at(db, period.start, period.end, mkt_ids, brand_keys),
        "channels_sem_cobertura_regional": channels_sem_cobertura_regional(mkt_ids),
    }
