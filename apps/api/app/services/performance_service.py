from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

BRAND_LABELS = {
    "apice": "ÁPICE",
    "barbours": "BARBOURS",
    "kokeshi": "KOKESHI",
    "lescent": "LESCENT",
    "rituaria": "RITUÁRIA",
}

MES_LABELS = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

_MKT_FILTER = {
    "all": None,
    "tiktok": 1,
    "ml": 2,
}


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def get_overview(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER.get(marketplace)
    start, end = _month_bounds(year, month)
    p_year, p_month = _prev_month(year, month)
    p_start, p_end = _month_bounds(p_year, p_month)

    sql = text("""
        SELECT
            COALESCE(SUM(gmv), 0)           AS gmv,
            COALESCE(SUM(orders), 0)        AS orders,
            COALESCE(SUM(ad_spend), 0)      AS ad_spend,
            CASE WHEN SUM(orders) > 0
                 THEN SUM(gmv) / SUM(orders) ELSE 0 END AS avg_ticket
        FROM marts.fact_marketplace_daily_performance
        WHERE date BETWEEN :start AND :end
          AND (:mkt_id IS NULL OR marketplace_id = :mkt_id)
    """)

    def run(s, e):
        row = db.execute(sql, {"start": s, "end": e, "mkt_id": mkt_id}).mappings().one()
        return dict(row)

    cur = run(start, end)
    prev = run(p_start, p_end)

    mom = None
    if prev["gmv"] and prev["gmv"] > 0:
        mom = (cur["gmv"] - prev["gmv"]) / prev["gmv"] * 100

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "current": {
            "gmv": float(cur["gmv"]),
            "orders": int(cur["orders"]),
            "avg_ticket": float(cur["avg_ticket"]),
            "ad_spend": float(cur["ad_spend"]) if cur["ad_spend"] else None,
        },
        "previous": {
            "gmv": float(prev["gmv"]),
            "orders": int(prev["orders"]),
            "avg_ticket": float(prev["avg_ticket"]),
            "ad_spend": float(prev["ad_spend"]) if prev["ad_spend"] else None,
        },
        "gmv_mom_pct": round(mom, 2) if mom is not None else None,
    }


def get_brands(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER.get(marketplace)
    start, end = _month_bounds(year, month)
    p_year, p_month = _prev_month(year, month)
    p_start, p_end = _month_bounds(p_year, p_month)

    sql = text("""
        SELECT
            l.brand_key,
            f.marketplace_id,
            COALESCE(SUM(f.gmv), 0)    AS gmv,
            COALESCE(SUM(f.orders), 0) AS orders
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND (:mkt_id IS NULL OR f.marketplace_id = :mkt_id)
        GROUP BY l.brand_key, f.marketplace_id
        ORDER BY l.brand_key, f.marketplace_id
    """)

    def run(s, e):
        rows = db.execute(sql, {"start": s, "end": e, "mkt_id": mkt_id}).mappings().all()
        result: dict[str, dict] = {}
        for r in rows:
            brand = r["brand_key"]
            if brand not in result:
                result[brand] = {"tiktok": 0.0, "ml": 0.0, "orders": 0}
            if r["marketplace_id"] == 1:
                result[brand]["tiktok"] = float(r["gmv"])
                result[brand]["orders"] += int(r["orders"])
            elif r["marketplace_id"] == 2:
                result[brand]["ml"] = float(r["gmv"])
                result[brand]["orders"] += int(r["orders"])
        return result

    cur = run(start, end)
    prev = run(p_start, p_end)

    brands = []
    for brand_key in sorted(cur.keys(), key=lambda b: -(cur[b]["tiktok"] + cur[b]["ml"])):
        c = cur[brand_key]
        p = prev.get(brand_key, {"tiktok": 0.0, "ml": 0.0, "orders": 0})
        total = c["tiktok"] + c["ml"]
        total_prev = p["tiktok"] + p["ml"]
        orders = c["orders"]
        mom = ((total - total_prev) / total_prev * 100) if total_prev > 0 else None

        brands.append({
            "brand": brand_key,
            "label": BRAND_LABELS.get(brand_key, brand_key.upper()),
            "tiktok_gmv": c["tiktok"] or None,
            "ml_gmv": c["ml"] or None,
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": total / orders if orders > 0 else None,
            "tiktok_gmv_prev": p["tiktok"] or None,
            "ml_gmv_prev": p["ml"] or None,
            "total_gmv_prev": total_prev,
            "mom_pct": round(mom, 2) if mom is not None else None,
        })

    return {"ref_month": f"{year:04d}-{month:02d}", "brands": brands}


def get_monthly(db: Session, marketplace: str, months_back: int = 6) -> dict:
    mkt_id = _MKT_FILTER.get(marketplace)
    # equivalente a months_back passos a partir do mês corrente
    cutoff = date.today().replace(day=1) - timedelta(days=1)
    year = cutoff.year
    month = cutoff.month
    for _ in range(months_back - 1):
        year, month = _prev_month(year, month)
    start = date(year, month, 1)

    sql = text("""
        SELECT
            DATE_TRUNC('month', f.date)::date AS mes,
            l.brand_key,
            COALESCE(SUM(f.gmv), 0)          AS gmv
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date >= :start
          AND (:mkt_id IS NULL OR f.marketplace_id = :mkt_id)
        GROUP BY DATE_TRUNC('month', f.date), l.brand_key
        ORDER BY mes, l.brand_key
    """)

    rows = db.execute(sql, {"start": start, "mkt_id": mkt_id}).mappings().all()

    months: dict[str, dict] = {}
    for r in rows:
        mes_dt: date = r["mes"]
        key = f"{mes_dt.year:04d}-{mes_dt.month:02d}"
        label = f"{MES_LABELS[mes_dt.month]}/{str(mes_dt.year)[2:]}"
        if key not in months:
            months[key] = {"mes": key, "mes_label": label,
                           "barbours": 0, "kokeshi": 0, "apice": 0,
                           "lescent": 0, "rituaria": 0}
        brand = r["brand_key"]
        if brand in months[key]:
            months[key][brand] = float(r["gmv"])

    return {"data": list(months.values())}


def get_daily(
    db: Session,
    brand: str,
    marketplace: str,
    days_back: int = 60,
) -> dict:
    mkt_id = _MKT_FILTER.get(marketplace)
    date_from = date.today() - timedelta(days=days_back)

    sql = text("""
        SELECT
            f.date,
            f.marketplace_id,
            COALESCE(f.gmv, 0)    AS gmv,
            COALESCE(f.orders, 0) AS orders,
            f.ad_spend
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE l.brand_key = :brand
          AND f.date >= :date_from
          AND (:mkt_id IS NULL OR f.marketplace_id = :mkt_id)
        ORDER BY f.date, f.marketplace_id
    """)

    rows = db.execute(sql, {"brand": brand, "date_from": date_from, "mkt_id": mkt_id}).mappings().all()

    # Agrupa por data quando marketplace = all (soma TikTok + ML)
    days: dict[date, dict] = {}
    for r in rows:
        d = r["date"]
        if d not in days:
            days[d] = {"date": d, "tiktok": 0.0, "ml": 0.0, "orders": 0, "ad_spend": None}
        if r["marketplace_id"] == 1:
            days[d]["tiktok"] = float(r["gmv"])
            days[d]["orders"] += int(r["orders"])
        elif r["marketplace_id"] == 2:
            days[d]["ml"] = float(r["gmv"])
            days[d]["orders"] += int(r["orders"])
            if r["ad_spend"]:
                days[d]["ad_spend"] = float(r["ad_spend"])

    result = []
    for d, v in sorted(days.items()):
        total = v["tiktok"] + v["ml"]
        orders = v["orders"]
        result.append({
            "date": d,
            "tiktok_gmv": v["tiktok"] or None,
            "ml_gmv": v["ml"] or None,
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": total / orders if orders > 0 else None,
            "ad_spend": v["ad_spend"],
        })

    return {"brand": brand, "marketplace": marketplace, "data": result}
