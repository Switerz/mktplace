"""
Queries diretas às marts tables do Neon via SQLAlchemy.
Migração principal de gold_service.py: endpoints sem dependência de RDS.

marketplace_id: 1=TikTok, 2=ML, 3=Shopee
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

TIKTOK_ID = 1
ML_ID = 2
SHOPEE_ID = 3

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

_MKT_IDS = {"tiktok": TIKTOK_ID, "ml": ML_ID, "shopee": SHOPEE_ID}
_MKT_ORDER = ["tiktok", "ml", "shopee"]  # ordem canonica: TikTok, ML, Shopee


def normalize_marketplace_param(marketplace: str) -> str:
    """
    Valida e normaliza o parametro `marketplace`. Aceita "all", um canal
    isolado ("tiktok"|"ml"|"shopee") ou uma combinacao canonica separada
    por virgula (ex: "tiktok,ml"). Remove duplicados, ordena na ordem
    canonica TikTok/ML/Shopee e colapsa para "all" quando os tres canais
    estao presentes. Levanta ValueError em valor invalido ou vazio.
    """
    if marketplace in ("all", ""):
        return "all"
    tokens = [t.strip() for t in marketplace.split(",") if t.strip()]
    if not tokens:
        raise ValueError(
            "marketplace deve conter ao menos um canal (tiktok, ml, shopee) ou 'all'."
        )
    invalid = sorted(set(tokens) - set(_MKT_ORDER))
    if invalid:
        raise ValueError(
            f"marketplace invalido: {', '.join(invalid)}. "
            "Valores aceitos: all, tiktok, ml, shopee (ou combinacao separada por virgula, ex: tiktok,ml)."
        )
    canonical = [mp for mp in _MKT_ORDER if mp in tokens]
    if len(canonical) == len(_MKT_ORDER):
        return "all"
    return ",".join(canonical)


def parse_marketplace_param(marketplace: str) -> list[int]:
    """Converte o parametro normalizado em lista de marketplace_id para uso em SQL (`= ANY(:mkt_ids)`)."""
    canonical = normalize_marketplace_param(marketplace)
    if canonical == "all":
        return [TIKTOK_ID, ML_ID, SHOPEE_ID]
    return [_MKT_IDS[mp] for mp in canonical.split(",")]


def _f(v) -> float:
    if v is None:
        return 0.0
    return float(Decimal(str(v)))


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _pct(num: float, denom: float, decimals: int = 1) -> float | None:
    return round(num / denom * 100, decimals) if denom > 0 else None


def _pct_from_source(value) -> float | None:
    """Converte campo conversion_rate do mart (ratio 0-1 ou pct >1) para percentagem."""
    v = _f(value)
    if not v:
        return None
    return round(v * 100, 2) if abs(v) <= 1 else round(v, 2)


def _mkt_kpis(db: Session, start: date, end: date, mkt_ids: list[int]) -> dict[int, dict]:
    """KPIs agregados por marketplace_id para um intervalo de datas."""
    sql = text("""
        SELECT
            marketplace_id,
            COALESCE(SUM(gmv), 0)              AS gmv,
            COALESCE(SUM(orders), 0)           AS orders,
            COALESCE(SUM(canceled_orders), 0)  AS canceled_orders,
            COALESCE(SUM(unique_buyers), 0)    AS unique_buyers,
            COALESCE(SUM(ad_spend), 0)         AS ad_spend,
            COALESCE(SUM(ad_revenue), 0)       AS ad_revenue
        FROM marts.fact_marketplace_daily_performance
        WHERE date BETWEEN :start AND :end
          AND marketplace_id = ANY(:mkt_ids)
        GROUP BY marketplace_id
    """)
    rows = db.execute(sql, {"start": start, "end": end, "mkt_ids": mkt_ids}).mappings().all()
    return {r["marketplace_id"]: dict(r) for r in rows}


def _brand_mkt_rows(
    db: Session, start: date, end: date, mkt_ids: list[int], extra_cols: str = ""
) -> dict[str, dict[int, dict]]:
    """KPIs por brand_key × marketplace_id para um intervalo de datas."""
    sql = text(f"""
        SELECT
            l.brand_key,
            f.marketplace_id,
            COALESCE(SUM(f.gmv), 0)              AS gmv,
            COALESCE(SUM(f.orders), 0)           AS orders,
            COALESCE(SUM(f.canceled_orders), 0)  AS canceled_orders,
            COALESCE(SUM(f.ad_spend), 0)         AS ad_spend,
            COALESCE(SUM(f.ad_revenue), 0)       AS ad_revenue
            {', ' + extra_cols if extra_cols else ''}
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
        GROUP BY l.brand_key, f.marketplace_id
        ORDER BY l.brand_key, f.marketplace_id
    """)
    rows = db.execute(sql, {"start": start, "end": end, "mkt_ids": mkt_ids}).mappings().all()
    result: dict[str, dict[int, dict]] = {}
    for r in rows:
        brand = r["brand_key"]
        if brand not in result:
            result[brand] = {}
        result[brand][r["marketplace_id"]] = dict(r)
    return result


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def get_overview(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_ids = parse_marketplace_param(marketplace)
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)

    def _assemble(data: dict[int, dict]) -> dict:
        tk = data.get(TIKTOK_ID, {})
        ml = data.get(ML_ID, {})
        sh = data.get(SHOPEE_ID, {})

        tk_gmv = _f(tk.get("gmv"))
        ml_gmv = _f(ml.get("gmv"))
        sh_gmv = _f(sh.get("gmv"))
        gmv = tk_gmv + ml_gmv + sh_gmv

        tk_orders = int(_f(tk.get("orders")))
        ml_orders = int(_f(ml.get("orders")))
        sh_orders = int(_f(sh.get("orders")))
        orders = tk_orders + ml_orders + sh_orders

        ml_canceled = int(_f(ml.get("canceled_orders")))
        ml_total = ml_orders + ml_canceled
        ml_cancel_rate = round(ml_canceled / ml_total * 100, 1) if ml_total > 0 else None

        ml_spend = _f(ml.get("ad_spend"))
        ml_revenue = _f(ml.get("ad_revenue"))
        sh_spend = _f(sh.get("ad_spend"))
        sh_revenue = _f(sh.get("ad_revenue"))

        return {
            "gmv": gmv,
            "tiktok_gmv": tk_gmv or None,
            "ml_gmv": ml_gmv or None,
            "shopee_gmv": sh_gmv or None,
            "orders": orders,
            "avg_ticket": gmv / orders if orders > 0 else 0.0,
            "ad_spend": (ml_spend + sh_spend) or None,
            "ml_roas": round(ml_revenue / ml_spend, 2) if ml_spend > 0 else None,
            "ml_cancel_rate_pct": ml_cancel_rate,
            "shopee_roas": round(sh_revenue / sh_spend, 2) if sh_spend > 0 else None,
            "tiktok_customers": int(_f(tk.get("unique_buyers"))) or None,
            "ml_unique_buyers": int(_f(ml.get("unique_buyers"))) or None,
            "shopee_unique_buyers": int(_f(sh.get("unique_buyers"))) or None,
        }

    cur = _assemble(_mkt_kpis(db, start, end, mkt_ids))
    prev = _assemble(_mkt_kpis(db, pstart, pend, mkt_ids))
    mom = round((cur["gmv"] - prev["gmv"]) / prev["gmv"] * 100, 2) if prev["gmv"] > 0 else None

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "current": cur,
        "previous": prev,
        "gmv_mom_pct": mom,
    }


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------

def get_brands(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_ids = parse_marketplace_param(marketplace)
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)

    extra = "COALESCE(SUM(f.total_fees), 0) AS total_fees"
    cur = _brand_mkt_rows(db, start, end, mkt_ids, extra)
    prev = _brand_mkt_rows(db, pstart, pend, mkt_ids, extra)

    result = []
    for brand in sorted(set(list(cur.keys()) + list(prev.keys()))):
        c = cur.get(brand, {})
        p = prev.get(brand, {})

        tk_c = c.get(TIKTOK_ID, {})
        ml_c = c.get(ML_ID, {})
        sh_c = c.get(SHOPEE_ID, {})
        tk_p = p.get(TIKTOK_ID, {})
        ml_p = p.get(ML_ID, {})
        sh_p = p.get(SHOPEE_ID, {})

        tk_gmv = _f(tk_c.get("gmv"))
        ml_gmv = _f(ml_c.get("gmv"))
        sh_gmv = _f(sh_c.get("gmv"))
        total = tk_gmv + ml_gmv + sh_gmv
        if total == 0:
            continue

        tk_prev_gmv = _f(tk_p.get("gmv"))
        ml_prev_gmv = _f(ml_p.get("gmv"))
        sh_prev_gmv = _f(sh_p.get("gmv"))
        total_prev = tk_prev_gmv + ml_prev_gmv + sh_prev_gmv

        tk_orders = int(_f(tk_c.get("orders")))
        ml_orders = int(_f(ml_c.get("orders")))
        sh_orders = int(_f(sh_c.get("orders")))
        orders = tk_orders + ml_orders + sh_orders

        ml_canceled = int(_f(ml_c.get("canceled_orders")))
        ml_total = ml_orders + ml_canceled
        ml_spend = _f(ml_c.get("ad_spend"))
        ml_revenue = _f(ml_c.get("ad_revenue"))

        tk_fees = abs(_f(tk_c.get("total_fees")))

        result.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_gmv": tk_gmv or None,
            "ml_gmv": ml_gmv or None,
            "shopee_gmv": sh_gmv or None,
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": round(total / orders, 2) if orders > 0 else None,
            "tiktok_gmv_prev": tk_prev_gmv or None,
            "ml_gmv_prev": ml_prev_gmv or None,
            "shopee_gmv_prev": sh_prev_gmv or None,
            "total_gmv_prev": total_prev,
            "mom_pct": round((total - total_prev) / total_prev * 100, 2) if total_prev > 0 else None,
            "cos_pct": round(tk_fees / tk_gmv * 100, 2) if tk_gmv > 0 else None,
            "gpm": None,  # requer total_views, não disponível no mart
            "ml_roas": round(ml_revenue / ml_spend, 2) if ml_spend > 0 else None,
            "ml_cancel_rate_pct": round(ml_canceled / ml_total * 100, 1) if ml_total > 0 else None,
        })

    result.sort(key=lambda r: -r["total_gmv"])
    return {"ref_month": f"{year:04d}-{month:02d}", "brands": result}


# ---------------------------------------------------------------------------
# Monthly
# ---------------------------------------------------------------------------

def get_monthly(db: Session, marketplace: str, months_back: int = 6) -> dict:
    mkt_ids = parse_marketplace_param(marketplace)
    today = date.today()
    year, month = today.year, today.month
    for _ in range(months_back):
        year, month = _prev_month(year, month)
    start = date(year, month, 1)

    sql = text("""
        SELECT
            DATE_TRUNC('month', f.date)::date AS mes,
            l.brand_key,
            COALESCE(SUM(f.gmv), 0)           AS gmv
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date >= :start
          AND f.marketplace_id = ANY(:mkt_ids)
        GROUP BY DATE_TRUNC('month', f.date), l.brand_key
        ORDER BY mes, l.brand_key
    """)

    rows = db.execute(sql, {"start": start, "mkt_ids": mkt_ids}).mappings().all()

    months: dict[str, dict] = {}
    for r in rows:
        mes_dt: date = r["mes"]
        key = f"{mes_dt.year:04d}-{mes_dt.month:02d}"
        if key not in months:
            months[key] = {
                "mes": key,
                "mes_label": f"{MES_LABELS[mes_dt.month]}/{str(mes_dt.year)[2:]}",
                "barbours": 0.0, "kokeshi": 0.0, "apice": 0.0,
                "lescent": 0.0, "rituaria": 0.0,
            }
        brand = r["brand_key"]
        if brand in months[key]:
            months[key][brand] = months[key][brand] + _f(r["gmv"])

    return {"data": sorted(months.values(), key=lambda x: x["mes"])}


# ---------------------------------------------------------------------------
# Daily
# ---------------------------------------------------------------------------

def get_daily(db: Session, brand: str, marketplace: str, days_back: int = 60) -> dict:
    mkt_ids = parse_marketplace_param(marketplace)
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
          AND f.marketplace_id = ANY(:mkt_ids)
        ORDER BY f.date, f.marketplace_id
    """)

    rows = db.execute(sql, {"brand": brand, "date_from": date_from, "mkt_ids": mkt_ids}).mappings().all()

    days: dict[date, dict] = {}
    for r in rows:
        d = r["date"]
        if d not in days:
            days[d] = {"date": d, "tiktok_gmv": None, "ml_gmv": None,
                       "shopee_gmv": None, "orders": 0, "ad_spend": None}
        mkt = r["marketplace_id"]
        gmv_val = _f(r["gmv"]) or None
        orders_val = int(_f(r["orders"]))
        spend_val = _f(r["ad_spend"]) if r["ad_spend"] else None

        if mkt == TIKTOK_ID:
            days[d]["tiktok_gmv"] = gmv_val
            days[d]["orders"] += orders_val
        elif mkt == ML_ID:
            days[d]["ml_gmv"] = gmv_val
            days[d]["orders"] += orders_val
            if spend_val:
                days[d]["ad_spend"] = spend_val
        elif mkt == SHOPEE_ID:
            days[d]["shopee_gmv"] = gmv_val
            days[d]["orders"] += orders_val
            if spend_val:
                prev_spend = _f(days[d].get("ad_spend"))
                days[d]["ad_spend"] = (prev_spend + spend_val) or None

    result = []
    for d, v in sorted(days.items()):
        total = _f(v["tiktok_gmv"]) + _f(v["ml_gmv"]) + _f(v["shopee_gmv"])
        orders = v["orders"]
        result.append({
            "date": d,
            "tiktok_gmv": v["tiktok_gmv"],
            "ml_gmv": v["ml_gmv"],
            "shopee_gmv": v["shopee_gmv"],
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": total / orders if orders > 0 else None,
            "ad_spend": v["ad_spend"],
        })

    return {"brand": brand, "marketplace": marketplace, "data": result}


# ---------------------------------------------------------------------------
# Canais
# ---------------------------------------------------------------------------

def get_canais(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_ids = parse_marketplace_param(marketplace)
    start, end = _month_bounds(year, month)

    sql = text("""
        SELECT
            l.brand_key,
            f.marketplace_id,
            COALESCE(SUM(f.gmv), 0)                      AS gmv,
            COALESCE(SUM(f.gmv_video), 0)                AS gmv_video,
            COALESCE(SUM(f.gmv_live), 0)                 AS gmv_live,
            COALESCE(SUM(f.gmv_card), 0)                 AS gmv_card,
            COALESCE(SUM(f.visitors), 0)                 AS visitors,
            COALESCE(SUM(f.unique_buyers), 0)            AS unique_buyers,
            COALESCE(SUM(f.new_buyers), 0)               AS new_buyers,
            COALESCE(SUM(f.repeat_buyers), 0)            AS repeat_buyers,
            COALESCE(SUM(f.canceled_orders), 0)          AS canceled_orders,
            COALESCE(SUM(f.orders), 0)                   AS orders,
            AVG(NULLIF(f.conversion_rate, 0))            AS avg_conversion_rate
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
        GROUP BY l.brand_key, f.marketplace_id
        ORDER BY l.brand_key, f.marketplace_id
    """)

    rows = db.execute(sql, {"start": start, "end": end, "mkt_ids": mkt_ids}).mappings().all()

    by_brand: dict[str, dict[int, dict]] = {}
    for r in rows:
        brand = r["brand_key"]
        if brand not in by_brand:
            by_brand[brand] = {}
        by_brand[brand][r["marketplace_id"]] = dict(r)

    brand_rows = []
    for brand, mkts in sorted(by_brand.items()):
        tk = mkts.get(TIKTOK_ID, {})
        ml = mkts.get(ML_ID, {})
        sh = mkts.get(SHOPEE_ID, {})
        row: dict = {"brand": brand, "label": BRAND_LABELS.get(brand, brand.upper())}

        if tk:
            tk_gmv = _f(tk["gmv"])
            tk_vid = _f(tk["gmv_video"])
            tk_live = _f(tk["gmv_live"])
            tk_card = _f(tk["gmv_card"])
            tk_vis = int(_f(tk["visitors"]))
            tk_buyers = int(_f(tk["unique_buyers"]))
            row.update({
                "tiktok_gmv": tk_gmv or None,
                "tiktok_gmv_video": tk_vid or None,
                "tiktok_gmv_live": tk_live or None,
                "tiktok_gmv_card": tk_card or None,
                "tiktok_video_pct": _pct(tk_vid, tk_gmv),
                "tiktok_live_pct": _pct(tk_live, tk_gmv),
                "tiktok_card_pct": _pct(tk_card, tk_gmv),
                "tiktok_visitors": tk_vis or None,
                "tiktok_customers": tk_buyers or None,
                "tiktok_conversion_rate": _pct_from_source(tk.get("avg_conversion_rate")),
            })

        if ml:
            ml_gmv = _f(ml["gmv"])
            ml_buyers = int(_f(ml["unique_buyers"]))
            ml_new = int(_f(ml["new_buyers"]))
            ml_repeat = int(_f(ml["repeat_buyers"]))
            row.update({
                "ml_gmv": ml_gmv or None,
                "ml_unique_buyers": ml_buyers or None,
                "ml_new_buyers": ml_new or None,
                "ml_repeat_buyers": ml_repeat or None,
                "ml_repeat_buyer_rate_pct": _pct(ml_repeat, ml_buyers),
                "ml_gmv_per_buyer": round(ml_gmv / ml_buyers, 2) if ml_buyers > 0 else None,
            })

        if sh:
            sh_gmv = _f(sh["gmv"])
            sh_buyers = int(_f(sh["unique_buyers"]))
            sh_new = int(_f(sh["new_buyers"]))
            sh_repeat = int(_f(sh["repeat_buyers"]))
            sh_vis = int(_f(sh["visitors"]))
            sh_orders = int(_f(sh["orders"]))
            sh_canceled = int(_f(sh["canceled_orders"]))
            row.update({
                "shopee_gmv": sh_gmv or None,
                "shopee_unique_buyers": sh_buyers or None,
                "shopee_new_buyers": sh_new or None,
                "shopee_repeat_buyers": sh_repeat or None,
                "shopee_new_buyer_pct": _pct(sh_new, sh_buyers),
                "shopee_repeat_buyer_rate_pct": _pct(sh_repeat, sh_buyers),
                "shopee_gmv_per_buyer": round(sh_gmv / sh_buyers, 2) if sh_buyers > 0 else None,
                "shopee_cancel_rate_pct": _pct(sh_canceled, sh_orders + sh_canceled, 2),
                "shopee_visitors": sh_vis or None,
                "shopee_conversion_rate": _pct(sh_buyers, sh_vis, 2),
            })

        brand_rows.append(row)

    def _sum_brand(field: str) -> float:
        return sum(_f(r.get(field) or 0) for r in brand_rows)

    def _sum_brand_int(field: str) -> int:
        return int(sum(r.get(field) or 0 for r in brand_rows))

    tk_gmv_t = _sum_brand("tiktok_gmv")
    tk_vid_t = _sum_brand("tiktok_gmv_video")
    tk_live_t = _sum_brand("tiktok_gmv_live")
    tk_card_t = _sum_brand("tiktok_gmv_card")
    tk_vis_t = _sum_brand_int("tiktok_visitors")
    tk_cust_t = _sum_brand_int("tiktok_customers")
    ml_buyers_t = _sum_brand_int("ml_unique_buyers")
    ml_new_t = _sum_brand_int("ml_new_buyers")
    ml_repeat_t = _sum_brand_int("ml_repeat_buyers")
    ml_gmv_t = _sum_brand("ml_gmv")
    sh_gmv_t = _sum_brand("shopee_gmv")
    sh_buyers_t = _sum_brand_int("shopee_unique_buyers")
    sh_new_t = _sum_brand_int("shopee_new_buyers")
    sh_repeat_t = _sum_brand_int("shopee_repeat_buyers")
    sh_vis_t = _sum_brand_int("shopee_visitors")

    kpis = {
        "tiktok_gmv": tk_gmv_t or None,
        "tiktok_gmv_video": tk_vid_t or None,
        "tiktok_gmv_live": tk_live_t or None,
        "tiktok_gmv_card": tk_card_t or None,
        "tiktok_video_pct": _pct(tk_vid_t, tk_gmv_t),
        "tiktok_live_pct": _pct(tk_live_t, tk_gmv_t),
        "tiktok_card_pct": _pct(tk_card_t, tk_gmv_t),
        "tiktok_visitors": tk_vis_t or None,
        "tiktok_customers": tk_cust_t or None,
        "tiktok_conversion_rate": (lambda vals: round(sum(vals) / len(vals), 2) if vals else None)(
            [v for v in (r.get("tiktok_conversion_rate") for r in brand_rows) if v is not None]
        ),
        "ml_unique_buyers": ml_buyers_t or None,
        "ml_new_buyers": ml_new_t or None,
        "ml_repeat_buyers": ml_repeat_t or None,
        "ml_new_buyer_pct": _pct(ml_new_t, ml_buyers_t),
        "ml_repeat_buyer_rate_pct": _pct(ml_repeat_t, ml_buyers_t),
        "ml_gmv_per_buyer": round(ml_gmv_t / ml_buyers_t, 2) if ml_buyers_t > 0 else None,
        "shopee_gmv": sh_gmv_t or None,
        "shopee_unique_buyers": sh_buyers_t or None,
        "shopee_new_buyers": sh_new_t or None,
        "shopee_repeat_buyers": sh_repeat_t or None,
        "shopee_new_buyer_pct": _pct(sh_new_t, sh_buyers_t),
        "shopee_repeat_buyer_rate_pct": _pct(sh_repeat_t, sh_buyers_t),
        "shopee_gmv_per_buyer": round(sh_gmv_t / sh_buyers_t, 2) if sh_buyers_t > 0 else None,
        "shopee_visitors": sh_vis_t or None,
        "shopee_conversion_rate": _pct(sh_buyers_t, sh_vis_t, 2),
    }

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": kpis,
        "brands": brand_rows,
    }


# ---------------------------------------------------------------------------
# Financeiro
# ---------------------------------------------------------------------------

def get_financeiro(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_ids = parse_marketplace_param(marketplace)
    start, end = _month_bounds(year, month)

    sql = text("""
        SELECT
            l.brand_key,
            f.marketplace_id,
            COALESCE(SUM(f.gmv), 0)                  AS gmv,
            COALESCE(SUM(f.total_settlement), 0)      AS total_settlement,
            COALESCE(SUM(f.total_fees), 0)            AS total_fees,
            COALESCE(SUM(f.ad_spend), 0)              AS ad_spend,
            COALESCE(SUM(f.ad_revenue), 0)            AS ad_revenue,
            COALESCE(SUM(f.ad_clicks), 0)             AS ad_clicks,
            COALESCE(SUM(f.ad_impressions), 0)        AS ad_impressions,
            COALESCE(SUM(f.seller_shipping_cost), 0)  AS seller_shipping_cost
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
        GROUP BY l.brand_key, f.marketplace_id
        ORDER BY l.brand_key, f.marketplace_id
    """)

    rows = db.execute(sql, {"start": start, "end": end, "mkt_ids": mkt_ids}).mappings().all()

    by_brand: dict[str, dict[int, dict]] = {}
    for r in rows:
        brand = r["brand_key"]
        if brand not in by_brand:
            by_brand[brand] = {}
        by_brand[brand][r["marketplace_id"]] = dict(r)

    brand_rows = []
    for brand, mkts in sorted(by_brand.items()):
        tk = mkts.get(TIKTOK_ID, {})
        ml = mkts.get(ML_ID, {})
        sh = mkts.get(SHOPEE_ID, {})
        row: dict = {"brand": brand, "label": BRAND_LABELS.get(brand, brand.upper())}

        if tk:
            tk_gmv = _f(tk["gmv"])
            tk_fees = abs(_f(tk["total_fees"]))
            tk_settlement = _f(tk["total_settlement"])
            row.update({
                "tiktok_gmv": tk_gmv or None,
                "tiktok_settlement": tk_settlement or None,
                "tiktok_fees": tk_fees or None,
                "tiktok_avg_fee_pct": round(tk_fees / tk_gmv * 100, 2) if tk_gmv > 0 else None,
                "tiktok_avg_settlement_pct": round(tk_settlement / tk_gmv * 100, 2) if tk_gmv > 0 else None,
            })

        if ml:
            ml_gmv = _f(ml["gmv"])
            ml_spend = _f(ml["ad_spend"])
            ml_revenue = _f(ml["ad_revenue"])
            ml_clicks = int(_f(ml["ad_clicks"]))
            ml_impressions = int(_f(ml["ad_impressions"]))
            ml_shipping = _f(ml["seller_shipping_cost"])
            row.update({
                "ml_gmv": ml_gmv or None,
                "ml_ad_spend": ml_spend or None,
                "ml_ad_revenue": ml_revenue or None,
                "ml_roas": round(ml_revenue / ml_spend, 2) if ml_spend > 0 else None,
                "ml_acos_pct": round(ml_spend / ml_revenue * 100, 2) if ml_revenue > 0 else None,
                "ml_cpc": round(ml_spend / ml_clicks, 4) if ml_clicks > 0 else None,
                "ml_ctr_pct": round(ml_clicks / ml_impressions * 100, 3) if ml_impressions > 0 else None,
                "ml_ad_clicks": ml_clicks or None,
                "ml_ad_impressions": ml_impressions or None,
                "ml_seller_shipping_cost": ml_shipping or None,
                "ml_shipping_pct_of_gmv": round(ml_shipping / ml_gmv * 100, 2) if ml_gmv > 0 else None,
                "ml_total_cost_pct": round((ml_spend + ml_shipping) / ml_gmv * 100, 2) if ml_gmv > 0 else None,
            })

        if sh:
            sh_gmv = _f(sh["gmv"])
            sh_fees = _f(sh["total_fees"])
            sh_settlement = _f(sh["total_settlement"])
            sh_spend = _f(sh["ad_spend"])
            sh_revenue = _f(sh["ad_revenue"])
            sh_shipping = _f(sh["seller_shipping_cost"])
            row.update({
                "shopee_gmv": sh_gmv or None,
                "shopee_settlement": sh_settlement or None,
                "shopee_fees": sh_fees or None,
                "shopee_avg_fee_pct": round(sh_fees / sh_gmv * 100, 2) if sh_gmv > 0 else None,
                "shopee_avg_settlement_pct": round(sh_settlement / sh_gmv * 100, 2) if sh_gmv > 0 else None,
                "shopee_ad_spend": sh_spend or None,
                "shopee_ad_revenue": sh_revenue or None,
                "shopee_roas": round(sh_revenue / sh_spend, 2) if sh_spend > 0 else None,
                "shopee_shipping_cost": sh_shipping or None,
                "shopee_shipping_pct_of_gmv": round(sh_shipping / sh_gmv * 100, 2) if sh_gmv > 0 else None,
            })

        brand_rows.append(row)

    def _s(field: str) -> float:
        return sum(_f(r.get(field) or 0) for r in brand_rows)

    def _si(field: str) -> int:
        return int(sum(r.get(field) or 0 for r in brand_rows))

    tk_gmv_t = _s("tiktok_gmv")
    tk_fees_t = _s("tiktok_fees")
    tk_set_t = _s("tiktok_settlement")
    ml_gmv_t = _s("ml_gmv")
    ml_spend_t = _s("ml_ad_spend")
    ml_rev_t = _s("ml_ad_revenue")
    ml_clicks_t = _si("ml_ad_clicks")
    ml_ship_t = _s("ml_seller_shipping_cost")
    sh_gmv_t = _s("shopee_gmv")
    sh_fees_t = _s("shopee_fees")
    sh_set_t = _s("shopee_settlement")
    sh_spend_t = _s("shopee_ad_spend")
    sh_rev_t = _s("shopee_ad_revenue")

    kpis = {
        "tiktok_gmv": tk_gmv_t or None,
        "tiktok_settlement": tk_set_t or None,
        "tiktok_fees": tk_fees_t or None,
        "tiktok_avg_fee_pct": round(tk_fees_t / tk_gmv_t * 100, 2) if tk_gmv_t > 0 else None,
        "tiktok_avg_settlement_pct": round(tk_set_t / tk_gmv_t * 100, 2) if tk_gmv_t > 0 else None,
        "ml_gmv": ml_gmv_t or None,
        "ml_ad_spend": ml_spend_t or None,
        "ml_ad_revenue": ml_rev_t or None,
        "ml_roas": round(ml_rev_t / ml_spend_t, 2) if ml_spend_t > 0 else None,
        "ml_acos_pct": round(ml_spend_t / ml_rev_t * 100, 2) if ml_rev_t > 0 else None,
        "ml_cpc": round(ml_spend_t / ml_clicks_t, 4) if ml_clicks_t > 0 else None,
        "ml_total_cost_pct": round((ml_spend_t + ml_ship_t) / ml_gmv_t * 100, 2) if ml_gmv_t > 0 else None,
        "shopee_gmv": sh_gmv_t or None,
        "shopee_settlement": sh_set_t or None,
        "shopee_fees": sh_fees_t or None,
        "shopee_avg_fee_pct": round(sh_fees_t / sh_gmv_t * 100, 2) if sh_gmv_t > 0 else None,
        "shopee_avg_settlement_pct": round(sh_set_t / sh_gmv_t * 100, 2) if sh_gmv_t > 0 else None,
        "shopee_ad_spend": sh_spend_t or None,
        "shopee_roas": round(sh_rev_t / sh_spend_t, 2) if sh_spend_t > 0 else None,
    }

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": kpis,
        "brands": brand_rows,
    }


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

def get_quality(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_ids = parse_marketplace_param(marketplace)
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)

    sql = text("""
        SELECT
            l.brand_key,
            f.marketplace_id,
            COALESCE(SUM(f.orders), 0)                AS orders,
            COALESCE(SUM(f.canceled_orders), 0)       AS canceled_orders,
            COALESCE(SUM(f.returned_orders), 0)       AS returned_orders,
            COALESCE(SUM(f.delivered_orders), 0)      AS delivered_orders,
            COALESCE(SUM(f.unique_buyers), 0)         AS unique_buyers,
            COALESCE(SUM(f.new_buyers), 0)            AS new_buyers,
            COALESCE(SUM(f.repeat_buyers), 0)         AS repeat_buyers,
            COALESCE(SUM(f.gmv), 0)                   AS gmv,
            COALESCE(SUM(f.seller_shipping_cost), 0)  AS seller_shipping_cost,
            CASE WHEN SUM(f.delivered_orders) > 0
                 THEN SUM(f.avg_delivery_days * f.delivered_orders) / SUM(f.delivered_orders)
                 ELSE NULL END                         AS avg_delivery_days,
            CASE WHEN SUM(f.orders) > 0
                 THEN SUM(f.avg_delivery_hours * f.orders) / SUM(f.orders)
                 ELSE NULL END                         AS avg_delivery_hours,
            CASE WHEN SUM(f.orders) > 0
                 THEN SUM(f.problem_rate * f.orders) / SUM(f.orders)
                 ELSE NULL END                         AS problem_rate
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = ANY(:mkt_ids)
        GROUP BY l.brand_key, f.marketplace_id
        ORDER BY l.brand_key, f.marketplace_id
    """)

    # GMV ML do mês anterior para gmv_mom_pct — sempre ML, independente da selecao de canais
    sql_prev_ml = text("""
        SELECT l.brand_key, COALESCE(SUM(f.gmv), 0) AS gmv
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id = :mkt_id_ml
        GROUP BY l.brand_key
    """)

    rows = db.execute(sql, {"start": start, "end": end, "mkt_ids": mkt_ids}).mappings().all()
    prev_rows = db.execute(sql_prev_ml, {"start": pstart, "end": pend, "mkt_id_ml": ML_ID}).mappings().all()
    prev_ml_gmv = {r["brand_key"]: _f(r["gmv"]) for r in prev_rows}

    by_brand: dict[str, dict[int, dict]] = {}
    for r in rows:
        row = dict(r)
        has_quality_signal = (
            any(
                _f(row.get(field)) > 0
                for field in (
                    "orders", "canceled_orders", "returned_orders", "delivered_orders",
                    "unique_buyers", "new_buyers", "repeat_buyers", "gmv", "seller_shipping_cost",
                )
            )
            or row.get("avg_delivery_days") is not None
            or row.get("avg_delivery_hours") is not None
            or row.get("problem_rate") is not None
        )
        if not has_quality_signal:
            continue
        brand = row["brand_key"]
        if brand not in by_brand:
            by_brand[brand] = {}
        by_brand[brand][row["marketplace_id"]] = row

    brand_rows = []
    for brand, mkts in sorted(by_brand.items()):
        tk = mkts.get(TIKTOK_ID, {})
        ml = mkts.get(ML_ID, {})
        sh = mkts.get(SHOPEE_ID, {})
        row: dict = {"brand": brand, "label": BRAND_LABELS.get(brand, brand.upper()),
                     "tiktok_canceled": None, "tiktok_refunded": None,
                     "tiktok_returned": None, "tiktok_cancel_rate": None}

        if tk:
            tk_orders = int(_f(tk["orders"]))
            tk_prob = _f(tk.get("problem_rate"))
            tk_hours = _f(tk.get("avg_delivery_hours"))
            row.update({
                "tiktok_orders": tk_orders or None,
                "tiktok_problem_rate": round(tk_prob * 100, 2) if tk_prob else None,
                "tiktok_avg_delivery_days": round(tk_hours / 24, 1) if tk_hours else None,
            })

        if ml:
            ml_orders = int(_f(ml["orders"]))
            ml_canceled = int(_f(ml["canceled_orders"]))
            ml_total = ml_orders + ml_canceled
            ml_delivered = int(_f(ml["delivered_orders"]))
            ml_not_del = max(0, ml_orders - ml_delivered) if ml_delivered > 0 else None
            ml_gmv = _f(ml["gmv"])
            ml_buyers = int(_f(ml["unique_buyers"]))
            ml_new = int(_f(ml["new_buyers"]))
            ml_repeat = int(_f(ml["repeat_buyers"]))
            ml_shipping = _f(ml["seller_shipping_cost"])
            ml_del_days = _f(ml.get("avg_delivery_days")) or None
            prev_gmv = prev_ml_gmv.get(brand, 0.0)
            row.update({
                "ml_cancel_rate_pct": round(ml_canceled / ml_total * 100, 2) if ml_total > 0 else None,
                "ml_not_delivered_rate_pct": round(ml_not_del / ml_orders * 100, 2) if (ml_not_del is not None and ml_orders > 0) else None,
                "ml_cancelled_orders": ml_canceled or None,
                "ml_total_orders": ml_total or None,
                "ml_not_delivered_shipments": ml_not_del,
                "ml_avg_delivery_days": round(ml_del_days, 1) if ml_del_days else None,
                "ml_repeat_buyer_rate_pct": round(ml_repeat / ml_buyers * 100, 1) if ml_buyers > 0 else None,
                "ml_gmv_per_buyer": round(ml_gmv / ml_buyers, 2) if ml_buyers > 0 else None,
                "ml_gmv_mom_pct": round((ml_gmv - prev_gmv) / prev_gmv * 100, 1) if prev_gmv > 0 else None,
                "ml_new_buyers": ml_new or None,
                "ml_unique_buyers": ml_buyers or None,
                "ml_shipping_pct_of_gmv": round(ml_shipping / ml_gmv * 100, 1) if ml_gmv > 0 else None,
            })

        if sh:
            sh_orders = int(_f(sh["orders"]))
            sh_canceled = int(_f(sh["canceled_orders"]))
            sh_returned = int(_f(sh["returned_orders"]))
            sh_total = sh_orders + sh_canceled
            row.update({
                "shopee_orders": sh_orders or None,
                "shopee_canceled_orders": sh_canceled or None,
                "shopee_returned_orders": sh_returned or None,
                "shopee_cancel_rate_pct": round(sh_canceled / sh_total * 100, 2) if sh_total > 0 else None,
                "shopee_return_rate_pct": round(sh_returned / sh_orders * 100, 2) if sh_orders > 0 else None,
            })

        brand_rows.append(row)

    # KPIs agregados
    tk_br = [r for r in brand_rows if r.get("tiktok_orders")]
    all_tk_orders = sum(r.get("tiktok_orders") or 0 for r in tk_br)
    tk_prob_pairs = [(r["tiktok_problem_rate"], r["tiktok_orders"] or 0) for r in tk_br if r.get("tiktok_problem_rate")]
    kpi_tk_prob = round(sum(v * w for v, w in tk_prob_pairs) / sum(w for _, w in tk_prob_pairs), 2) if tk_prob_pairs else None
    tk_del_pairs = [(r["tiktok_avg_delivery_days"], r["tiktok_orders"] or 0) for r in tk_br if r.get("tiktok_avg_delivery_days")]
    kpi_tk_del = round(sum(v * w for v, w in tk_del_pairs) / sum(w for _, w in tk_del_pairs), 1) if tk_del_pairs else None

    ml_br = [r for r in brand_rows if r.get("ml_total_orders")]
    ml_all_canceled = sum(r.get("ml_cancelled_orders") or 0 for r in ml_br)
    ml_all_total = sum(r.get("ml_total_orders") or 0 for r in ml_br)
    kpi_ml_cancel = round(ml_all_canceled / ml_all_total * 100, 2) if ml_all_total > 0 else None
    ml_nd_all = sum(r.get("ml_not_delivered_shipments") or 0 for r in ml_br)
    ml_paid_all = sum((r.get("ml_total_orders") or 0) - (r.get("ml_cancelled_orders") or 0) for r in ml_br)
    kpi_ml_nd = round(ml_nd_all / ml_paid_all * 100, 2) if ml_paid_all > 0 else None
    ml_del_pairs = [(r["ml_avg_delivery_days"], (r.get("ml_total_orders") or 0) - (r.get("ml_cancelled_orders") or 0))
                    for r in ml_br if r.get("ml_avg_delivery_days")]
    kpi_ml_del = round(sum(v * w for v, w in ml_del_pairs) / sum(w for _, w in ml_del_pairs), 1) if ml_del_pairs else None

    sh_br = [r for r in brand_rows if r.get("shopee_orders")]
    sh_orders_all = sum(r.get("shopee_orders") or 0 for r in sh_br)
    sh_canceled_all = sum(r.get("shopee_canceled_orders") or 0 for r in sh_br)
    sh_returned_all = sum(r.get("shopee_returned_orders") or 0 for r in sh_br)
    kpi_sh_cancel = round(sh_canceled_all / (sh_orders_all + sh_canceled_all) * 100, 2) if (sh_orders_all + sh_canceled_all) > 0 else None
    kpi_sh_return = round(sh_returned_all / sh_orders_all * 100, 2) if sh_orders_all > 0 else None

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_problem_rate": kpi_tk_prob,
            "tiktok_cancel_rate": None,
            "tiktok_avg_delivery_days": kpi_tk_del,
            "ml_cancel_rate_pct": kpi_ml_cancel,
            "ml_not_delivered_rate_pct": kpi_ml_nd,
            "ml_avg_delivery_days": kpi_ml_del,
            "shopee_cancel_rate_pct": kpi_sh_cancel,
            "shopee_return_rate_pct": kpi_sh_return,
        },
        "brands": brand_rows,
    }


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------

def get_pedidos(db: Session, days_back: int = 30) -> dict:
    end = date.today()
    start = end - timedelta(days=days_back - 1)

    sql_kpis = text("""
        SELECT
            marketplace_id,
            COALESCE(SUM(orders), 0)           AS orders,
            COALESCE(SUM(canceled_orders), 0)  AS canceled_orders,
            COALESCE(SUM(delivered_orders), 0) AS delivered_orders,
            COALESCE(SUM(gmv), 0)              AS gmv
        FROM marts.fact_marketplace_daily_performance
        WHERE date BETWEEN :start AND :end
          AND marketplace_id IN (1, 2)
        GROUP BY marketplace_id
    """)

    sql_daily = text("""
        SELECT date, marketplace_id,
               COALESCE(SUM(orders), 0)          AS orders,
               COALESCE(SUM(canceled_orders), 0) AS canceled_orders,
               COALESCE(SUM(gmv), 0)             AS gmv
        FROM marts.fact_marketplace_daily_performance
        WHERE date BETWEEN :start AND :end
          AND marketplace_id IN (1, 2)
        GROUP BY date, marketplace_id
        ORDER BY date, marketplace_id
    """)

    sql_brand = text("""
        SELECT l.brand_key, f.marketplace_id,
               COALESCE(SUM(f.orders), 0)          AS orders,
               COALESCE(SUM(f.canceled_orders), 0) AS canceled_orders,
               COALESCE(SUM(f.gmv), 0)             AS gmv
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND f.marketplace_id IN (1, 2)
        GROUP BY l.brand_key, f.marketplace_id
    """)

    params = {"start": start, "end": end}
    kpis_by_mkt = {r["marketplace_id"]: dict(r) for r in db.execute(sql_kpis, params).mappings().all()}
    daily_raw = db.execute(sql_daily, params).mappings().all()
    brand_raw = db.execute(sql_brand, params).mappings().all()

    tk = kpis_by_mkt.get(TIKTOK_ID, {})
    ml = kpis_by_mkt.get(ML_ID, {})

    tk_orders = int(_f(tk.get("orders")))
    tk_canceled = int(_f(tk.get("canceled_orders")))
    tk_gmv = _f(tk.get("gmv"))
    # canceled/(paid+canceled) — denominador padrão; None quando sem cobertura (canceled=0)
    tk_denom = tk_orders + tk_canceled
    tk_cancel_rate = round(tk_canceled / tk_denom * 100, 2) if (tk_canceled > 0 and tk_denom > 0) else None

    ml_orders = int(_f(ml.get("orders")))
    ml_canceled = int(_f(ml.get("canceled_orders")))
    ml_total = ml_orders + ml_canceled
    ml_gmv = _f(ml.get("gmv"))
    # canceled/(paid+canceled) — mesmo denominador que overview/brands/quality
    ml_cancel_rate = round(ml_canceled / ml_total * 100, 2) if ml_total > 0 else None

    total_paid = tk_orders + ml_orders
    total_canceled = tk_canceled + ml_canceled
    total_all = total_paid + total_canceled
    total_gmv = tk_gmv + ml_gmv

    # Daily
    daily_map: dict[date, dict] = {}
    for r in daily_raw:
        d = r["date"]
        if d not in daily_map:
            daily_map[d] = {
                "date": str(d)[:10],
                "tiktok_orders": 0, "tiktok_canceled": 0,
                "ml_orders": 0, "ml_canceled": 0,
                "total_orders": 0, "total_gmv": 0.0,
            }
        if r["marketplace_id"] == TIKTOK_ID:
            daily_map[d]["tiktok_orders"] = int(_f(r["orders"]))
            daily_map[d]["tiktok_canceled"] = int(_f(r["canceled_orders"]))
        elif r["marketplace_id"] == ML_ID:
            daily_map[d]["ml_orders"] = int(_f(r["orders"]))
            daily_map[d]["ml_canceled"] = int(_f(r["canceled_orders"]))
        daily_map[d]["total_gmv"] = round(daily_map[d]["total_gmv"] + _f(r["gmv"]), 2)

    for v in daily_map.values():
        v["total_orders"] = v["tiktok_orders"] + v["ml_orders"]

    # By brand
    brand_map: dict[str, dict[int, dict]] = {}
    for r in brand_raw:
        brand = r["brand_key"]
        if brand not in brand_map:
            brand_map[brand] = {}
        brand_map[brand][r["marketplace_id"]] = dict(r)

    brands = []
    for brand in sorted(brand_map.keys()):
        bm = brand_map[brand]
        tk_b = bm.get(TIKTOK_ID, {})
        ml_b = bm.get(ML_ID, {})

        tk_o = int(_f(tk_b["orders"])) if tk_b else None
        tk_c = int(_f(tk_b["canceled_orders"])) if tk_b else None
        tk_g = round(_f(tk_b["gmv"]), 2) if tk_b else None

        ml_o = int(_f(ml_b["orders"])) if ml_b else None
        ml_c = int(_f(ml_b["canceled_orders"])) if ml_b else None
        ml_total_b = (ml_o or 0) + (ml_c or 0)
        ml_g = round(_f(ml_b["gmv"]), 2) if ml_b else None

        # canceled/(paid+canceled) para ambos; None quando canceled=0 (sem cobertura TikTok)
        tk_denom_b = (tk_o or 0) + (tk_c or 0)
        brands.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_orders": tk_o,
            "tiktok_canceled": tk_c,
            "tiktok_cancel_rate_pct": round(tk_c / tk_denom_b * 100, 2) if (tk_c and tk_denom_b > 0) else None,
            "tiktok_gmv": tk_g,
            "ml_orders": ml_o,
            "ml_canceled": ml_c,
            "ml_cancel_rate_pct": round(ml_c / ml_total_b * 100, 2) if (ml_c is not None and ml_total_b > 0) else None,
            "ml_gmv": ml_g,
            "total_orders": (tk_o or 0) + ml_total_b,
            "total_gmv": round((tk_g or 0) + (ml_g or 0), 2),
        })

    return {
        "days_back": days_back,
        "kpis": {
            "total_orders": total_all,
            "total_gmv": round(total_gmv, 2),
            "avg_ticket": round(total_gmv / total_paid, 2) if total_paid > 0 else 0.0,
            "cancel_rate_pct": round(total_canceled / total_all * 100, 2) if total_all > 0 else None,
        },
        "tiktok": {
            "orders": tk_orders,
            "canceled": tk_canceled,
            "gmv": round(tk_gmv, 2),
            "cancel_rate_pct": tk_cancel_rate,
            "delivered": int(_f(tk.get("delivered_orders"))) or None,
        },
        "ml": {
            "orders": ml_total,
            "canceled": ml_canceled,
            "gmv": round(ml_gmv, 2),
            "cancel_rate_pct": ml_cancel_rate,
            "delivered": int(_f(ml.get("delivered_orders"))) or None,
        },
        "daily": [v for _, v in sorted(daily_map.items())],
        "by_brand": brands,
    }


# ---------------------------------------------------------------------------
# Produtos — leitura exclusiva do Neon (marts.*)
# ---------------------------------------------------------------------------

_ML_BRANDS    = {"barbours", "kokeshi", "lescent", "rituaria"}  # rituaria incluida em 2026-07-01 (ver docs/backlog.md)
_TK_BRANDS    = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
_SH_BRANDS    = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
_PARETO_ORDER = ("A_top50", "B_next30", "C_next15", "D_tail")
_PARETO_META  = {
    "A_top50":  ("A", "Top 50% GMV"),
    "B_next30": ("B", "Next 30%"),
    "C_next15": ("C", "Next 15%"),
    "D_tail":   ("D", "Cauda"),
}

# Allowlists de ordenacao — chave publica da API -> expressao SQL segura
# (nunca interpolar sort_by/sort_dir do cliente diretamente na query).
PRODUTOS_SHOPEE_SORT_COLUMNS = {
    "gmv": "gmv", "units_sold": "units_sold", "orders": "completed_orders",
    "canceled_orders": "canceled_orders", "cancel_rate_pct": "cancel_rate_pct",
    "unique_buyers": "unique_buyers", "avg_price": "avg_price", "product_name": "product_name",
}
PRODUTOS_ML_SORT_COLUMNS = {
    "gross_revenue": "gross_revenue", "units_sold": "units_sold", "unique_buyers": "unique_buyers",
    "avg_price": "avg_price", "cancel_rate_pct": "cancel_rate_pct", "ad_roas": "ad_roas",
    "ad_acos_pct": "ad_acos_pct", "ad_spend": "ad_spend", "estimated_margin": "estimated_margin",
    "revenue_share_pct": "revenue_share_pct", "title": "title",
}
PRODUTOS_TIKTOK_SORT_COLUMNS = {
    "gmv": "gmv", "orders": "orders", "items_sold": "items_sold", "pct_gmv_video": "pct_gmv_video",
    "pct_gmv_live": "pct_gmv_live", "pct_gmv_card": "pct_gmv_card", "problem_rate": "problem_rate",
    "rating_avg": "rating_avg", "total_ratings": "total_ratings", "product_name": "product_name",
}


def _build_order_by(
    sort_by: str | None,
    sort_dir: str | None,
    allowlist: dict[str, str],
    default_column: str,
    default_direction: str,
    tiebreak_columns: list[str],
) -> str:
    """
    Monta um ORDER BY seguro e deterministico a partir de uma allowlist
    explicita de colunas (nunca a partir de concatenacao direta de
    sort_by/sort_dir do cliente). Sempre acrescenta `tiebreak_columns` (chave
    estavel da linha, ex: brand+item_id) apos a coluna escolhida, em ASC
    NULLS LAST, para que paginas consecutivas nunca dupliquem ou omitam
    linhas quando ha valores empatados na coluna principal.
    """
    if sort_by and sort_by not in allowlist:
        raise ValueError(f"sort_by invalido: {sort_by}. Valores aceitos: {', '.join(sorted(allowlist))}.")

    if sort_by:
        column_expr = allowlist[sort_by]
        direction = "ASC" if (sort_dir or "desc").lower() == "asc" else "DESC"
    else:
        column_expr = default_column
        direction = default_direction

    order_parts = [f"{column_expr} {direction} NULLS LAST"]
    order_parts += [f"{col} ASC NULLS LAST" for col in tiebreak_columns]
    return "ORDER BY " + ", ".join(order_parts)


def get_produtos_shopee(
    db: Session,
    brand: str | None,
    year: int,
    month: int,
    limit: int = 25,
    offset: int = 0,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> dict:
    ref_month = date(year, month, 1)
    filters = ["ref_month = :ref_month", "gmv > 0"]
    params: dict = {"ref_month": ref_month, "limit": limit, "offset": offset}

    if brand and brand in _SH_BRANDS:
        filters.append("brand = :brand")
        params["brand"] = brand

    where = " AND ".join(filters)
    order_by = _build_order_by(
        sort_by, sort_dir, PRODUTOS_SHOPEE_SORT_COLUMNS, "gmv", "DESC",
        ["brand", "sku_ref", "product_name", "variation_name"],
    )

    total_row = db.execute(
        text(f"SELECT COUNT(*) AS n FROM marts.fact_shopee_product_monthly WHERE {where}"),
        params,
    ).fetchone()
    total = int(total_row.n) if total_row else 0

    rows = db.execute(
        text(f"""
            SELECT brand, sku_ref, product_name, variation_name,
                   gmv, units_sold, completed_orders, canceled_orders,
                   cancel_rate_pct, unique_buyers, avg_price
            FROM marts.fact_shopee_product_monthly
            WHERE {where}
            {order_by}
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).fetchall()

    items = [
        {
            "brand":           r.brand,
            "sku_ref":         r.sku_ref,
            "product_name":    r.product_name,
            "variation_name":  r.variation_name,
            "gmv":             _f(r.gmv),
            "units_sold":      int(_f(r.units_sold)),
            "orders":          int(_f(r.completed_orders)),
            "canceled_orders": int(_f(r.canceled_orders)),
            "cancel_rate_pct": round(_f(r.cancel_rate_pct), 2) if r.cancel_rate_pct is not None else None,
            "unique_buyers":   int(_f(r.unique_buyers)) if r.unique_buyers is not None else None,
            "avg_price":       round(_f(r.avg_price), 2) if r.avg_price is not None else None,
        }
        for r in rows
    ]

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "total":     total,
        "limit":     limit,
        "offset":    offset,
        "items":     items,
    }


def get_produtos_ml(
    db: Session,
    brand: str | None,
    pareto_bucket: str | None,
    action_signal: str | None,
    product_status: str | None,
    revenue_velocity: str | None,
    limit: int = 25,
    offset: int = 0,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> dict:
    brands_tuple = tuple(sorted(_ML_BRANDS))
    filters = ["brand IN :brands"]
    params: dict = {"brands": brands_tuple, "limit": limit, "offset": offset}

    if brand and brand in _ML_BRANDS:
        filters.append("brand = :brand")
        params["brand"] = brand
    if pareto_bucket:
        filters.append("pareto_bucket = :pareto_bucket")
        params["pareto_bucket"] = pareto_bucket
    if action_signal:
        filters.append("action_signal = :action_signal")
        params["action_signal"] = action_signal
    if product_status:
        filters.append("product_status = :product_status")
        params["product_status"] = product_status
    if revenue_velocity:
        filters.append("revenue_velocity = :revenue_velocity")
        params["revenue_velocity"] = revenue_velocity

    where = " AND ".join(filters)
    order_by = _build_order_by(
        sort_by, sort_dir, PRODUTOS_ML_SORT_COLUMNS, "gross_revenue", "DESC",
        ["brand", "item_id"],
    )

    total_row = db.execute(
        text(f"SELECT COUNT(*) AS n FROM marts.fact_ml_produto_ranking WHERE {where}"),
        params,
    ).fetchone()
    total = int(total_row.n) if total_row else 0

    rows = db.execute(
        text(f"""
            SELECT brand, item_id, seller_sku, title,
                   gross_revenue, units_sold, unique_buyers,
                   CASE WHEN units_sold > 0
                        THEN gross_revenue / units_sold ELSE NULL END AS avg_price,
                   cancel_rate_pct, pareto_bucket, revenue_velocity,
                   ad_roas, ad_acos_pct, ad_spend, ad_efficiency,
                   action_signal, estimated_margin, revenue_share_pct, product_status
            FROM marts.fact_ml_produto_ranking
            WHERE {where}
            {order_by}
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).fetchall()

    items = [
        {
            "brand":             r.brand,
            "item_id":           r.item_id,
            "seller_sku":        r.seller_sku,
            "title":             r.title,
            "gross_revenue":     _f(r.gross_revenue),
            "units_sold":        int(_f(r.units_sold)),
            "unique_buyers":     int(_f(r.unique_buyers)) if r.unique_buyers is not None else None,
            "avg_price":         round(_f(r.avg_price), 2) if r.avg_price is not None else None,
            "cancel_rate_pct":   round(_f(r.cancel_rate_pct), 2) if r.cancel_rate_pct is not None else None,
            "pareto_bucket":     r.pareto_bucket,
            "revenue_velocity":  r.revenue_velocity,
            "ad_roas":           round(_f(r.ad_roas), 2) if r.ad_roas is not None else None,
            "ad_acos_pct":       round(_f(r.ad_acos_pct), 2) if r.ad_acos_pct is not None else None,
            "ad_spend":          _f(r.ad_spend) if r.ad_spend is not None else None,
            "ad_efficiency":     r.ad_efficiency,
            "action_signal":     r.action_signal,
            "estimated_margin":  _f(r.estimated_margin) if r.estimated_margin is not None else None,
            "revenue_share_pct": round(_f(r.revenue_share_pct), 3) if r.revenue_share_pct is not None else None,
            "product_status":    r.product_status,
        }
        for r in rows
    ]

    return {"total": total, "limit": limit, "offset": offset, "items": items}


def get_produtos_ml_summary(db: Session, brand: str | None) -> dict:
    brands_tuple = tuple(sorted(_ML_BRANDS))
    filters = ["brand IN :brands", "pareto_bucket IS NOT NULL"]
    params: dict = {"brands": brands_tuple}

    if brand and brand in _ML_BRANDS:
        filters.append("brand = :brand")
        params["brand"] = brand

    where = " AND ".join(filters)

    rows = db.execute(
        text(f"""
            SELECT pareto_bucket,
                   COUNT(*)                        AS count,
                   COALESCE(SUM(gross_revenue), 0) AS gmv
            FROM marts.fact_ml_produto_ranking
            WHERE {where}
            GROUP BY pareto_bucket
        """),
        params,
    ).fetchall()

    total_gmv   = sum(_f(r.gmv) for r in rows)
    total_count = sum(int(_f(r.count)) for r in rows)
    row_map     = {r.pareto_bucket: r for r in rows}

    buckets = []
    for bk in _PARETO_ORDER:
        label, desc = _PARETO_META[bk]
        r   = row_map.get(bk)
        gmv = _f(r.gmv) if r else 0.0
        cnt = int(_f(r.count)) if r else 0
        buckets.append({
            "bucket":      bk,
            "label":       label,
            "description": desc,
            "gmv":         gmv,
            "count":       cnt,
            "gmv_pct":     round(gmv / total_gmv * 100, 1) if total_gmv > 0 else 0.0,
        })

    return {
        "total_gmv":   total_gmv,
        "total_count": total_count,
        "brand":       brand,
        "buckets":     buckets,
    }


def get_produtos_tiktok(
    db: Session,
    brand: str | None,
    year: int,
    month: int,
    limit: int = 25,
    offset: int = 0,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> dict:
    start, end = _month_bounds(year, month)
    brands_tuple = tuple(sorted(_TK_BRANDS))
    filters = ["brand IN :brands", "date BETWEEN :start AND :end"]
    params: dict = {"brands": brands_tuple, "start": start, "end": end, "limit": limit, "offset": offset}

    if brand and brand in _TK_BRANDS:
        filters.append("brand = :brand")
        params["brand"] = brand

    where = " AND ".join(filters)
    order_by = _build_order_by(
        sort_by, sort_dir, PRODUTOS_TIKTOK_SORT_COLUMNS, "gmv", "DESC",
        ["brand", "product_id"],
    )

    # Grao estavel do produto: (brand, product_id). product_name NAO entra no
    # GROUP BY — se o nome mudar durante o mes, isso geraria duas linhas para
    # o mesmo produto. Em vez disso, escolhemos o nome vigente de forma
    # deterministica (o mais recente por data) via ARRAY_AGG ORDER BY date DESC.
    total_row = db.execute(
        text(f"""
            SELECT COUNT(*) AS n FROM (
                SELECT brand, product_id
                FROM marts.fact_tiktok_product_daily
                WHERE {where}
                GROUP BY brand, product_id
                HAVING SUM(gmv) > 0
            ) t
        """),
        params,
    ).fetchone()
    total = int(total_row.n) if total_row else 0

    rows = db.execute(
        text(f"""
            SELECT brand, product_id,
                   (ARRAY_AGG(product_name ORDER BY date DESC))[1] AS product_name,
                   SUM(gmv)        AS gmv,
                   SUM(orders)     AS orders,
                   SUM(items_sold) AS items_sold,
                   CASE WHEN SUM(gmv) > 0
                        THEN SUM(gmv_video) / SUM(gmv) * 100 ELSE NULL END AS pct_gmv_video,
                   CASE WHEN SUM(gmv) > 0
                        THEN SUM(gmv_live) / SUM(gmv) * 100  ELSE NULL END AS pct_gmv_live,
                   CASE WHEN SUM(gmv) > 0
                        THEN SUM(gmv_product_card) / SUM(gmv) * 100 ELSE NULL END AS pct_gmv_card,
                   -- Media ponderada (peso=orders) do problem_rate diario pre-calculado pelo TikTok.
                   -- Counts (canceled/refunded/returned) sao NULL em alguns periodos; nao sao usados.
                   CASE WHEN SUM(orders) FILTER (WHERE problem_rate IS NOT NULL) > 0
                        THEN SUM(problem_rate * orders) FILTER (WHERE problem_rate IS NOT NULL)
                             / SUM(orders) FILTER (WHERE problem_rate IS NOT NULL)
                        ELSE NULL END AS problem_rate,
                   CASE WHEN SUM(total_ratings) > 0
                        THEN SUM(rating_avg * total_ratings) / SUM(total_ratings)
                        ELSE NULL END AS rating_avg,
                   SUM(total_ratings) AS total_ratings
            FROM marts.fact_tiktok_product_daily
            WHERE {where}
            GROUP BY brand, product_id
            HAVING SUM(gmv) > 0
            {order_by}
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).fetchall()

    items = [
        {
            "brand":        r.brand,
            "product_id":   r.product_id,
            "product_name": r.product_name,
            "gmv":          _f(r.gmv),
            "orders":       int(_f(r.orders)),
            "items_sold":   int(_f(r.items_sold)),
            "pct_gmv_video": round(_f(r.pct_gmv_video), 1) if r.pct_gmv_video is not None else None,
            "pct_gmv_live":  round(_f(r.pct_gmv_live), 1)  if r.pct_gmv_live  is not None else None,
            "pct_gmv_card":  round(_f(r.pct_gmv_card), 1)  if r.pct_gmv_card  is not None else None,
            "problem_rate":  round(_f(r.problem_rate), 2)  if r.problem_rate   is not None else None,
            "rating_avg":    round(_f(r.rating_avg), 1)    if r.rating_avg     is not None else None,
            "total_ratings": int(_f(r.total_ratings))      if r.total_ratings  is not None else None,
        }
        for r in rows
    ]

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "total":     total,
        "limit":     limit,
        "offset":    offset,
        "items":     items,
    }
