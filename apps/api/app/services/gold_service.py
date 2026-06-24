"""
Queries diretas às gold tables do Data Mart via SQLAlchemy.

Substitui metabase_service.py eliminando o hop HTTP do Metabase.
A conexão é a mesma do engine principal (database_url no .env).
O schema gold.* deve ser acessível pelo usuario configurado.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import datamart_engine
BRANDS_IN_SCOPE = ("apice", "barbours", "kokeshi", "lescent", "rituaria")
ML_BRANDS = ("barbours", "kokeshi", "lescent")
SHOPEE_BRANDS = BRANDS_IN_SCOPE
SHOPEE_MARKETPLACE_ID = 3

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


def _uses_datamart(sql: str) -> bool:
    lowered = sql.lower()
    return any(token in lowered for token in (" gold.", "from gold.", "join gold.", " raw.", "from raw.", "join raw."))


def _query(db: Session, sql: str) -> list[dict]:
    if _uses_datamart(sql):
        if datamart_engine is None:
            raise RuntimeError("Data Mart indisponivel: configure DATAMART_DATABASE_URL ou DATAMART_*.")
        with datamart_engine.connect() as conn:
            return [dict(r) for r in conn.execute(text(sql)).mappings()]
    return [dict(r) for r in db.execute(text(sql)).mappings()]


def _float(v) -> float:
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


def _fmt_list(t: tuple) -> str:
    return "(" + ", ".join(f"'{b}'" for b in t) + ")"


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def _fetch_tiktok_kpis(db: Session, start: date, end: date) -> dict:
    sql = f"""
        SELECT
            COALESCE(SUM(gmv), 0)    AS gmv,
            COALESCE(SUM(orders), 0) AS orders,
            CASE WHEN SUM(orders)>0 THEN SUM(gmv)/SUM(orders) ELSE 0 END AS avg_ticket,
            COALESCE(SUM(CASE WHEN visitors > 0 THEN customers ELSE 0 END), 0) AS customers
        FROM gold.tiktok_brand_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date BETWEEN '{start}' AND '{end}'
    """
    return _query(db, sql)[0]


def _fetch_ml_kpis(db: Session, start: date, end: date) -> dict:
    sql = f"""
        SELECT
            COALESCE(SUM(gmv), 0)              AS gmv,
            COALESCE(SUM(paid_orders), 0)      AS orders,
            COALESCE(SUM(total_orders), 0)     AS total_orders,
            COALESCE(SUM(cancelled_orders), 0) AS cancelled_orders,
            COALESCE(SUM(ad_spend), 0)         AS ad_spend,
            COALESCE(SUM(ad_revenue), 0)       AS ad_revenue,
            CASE WHEN SUM(ad_spend) > 0
                 THEN SUM(ad_revenue) / SUM(ad_spend)
                 ELSE NULL END                  AS roas,
            CASE WHEN SUM(paid_orders)>0
                 THEN SUM(gmv)/SUM(paid_orders) ELSE 0 END AS avg_ticket
        FROM gold.ml_gestao_diaria
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND ref_date BETWEEN '{start}' AND '{end}'
    """
    return _query(db, sql)[0]


def _fetch_shopee_kpis(db: Session, start: date, end: date) -> dict:
    sql = f"""
        SELECT
            COALESCE(SUM(f.gmv), 0)             AS gmv,
            COALESCE(SUM(f.orders), 0)          AS orders,
            COALESCE(SUM(f.canceled_orders), 0) AS canceled_orders,
            COALESCE(SUM(f.ad_spend), 0)        AS ad_spend,
            COALESCE(SUM(f.ad_revenue), 0)      AS ad_revenue,
            COALESCE(SUM(f.unique_buyers), 0)   AS unique_buyers,
            CASE WHEN SUM(f.orders) > 0
                 THEN SUM(f.gmv) / SUM(f.orders)
                 ELSE 0 END                     AS avg_ticket,
            CASE WHEN SUM(f.ad_spend) > 0
                 THEN SUM(f.ad_revenue) / SUM(f.ad_spend)
                 ELSE NULL END                  AS roas
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.marketplace_id = {SHOPEE_MARKETPLACE_ID}
          AND l.brand_key IN {_fmt_list(SHOPEE_BRANDS)}
          AND f.date BETWEEN '{start}' AND '{end}'
    """
    try:
        return _query(db, sql)[0]
    except SQLAlchemyError:
        return {"gmv": 0, "orders": 0, "canceled_orders": 0, "ad_spend": 0, "ad_revenue": 0, "unique_buyers": 0, "avg_ticket": 0, "roas": None}


def get_overview(db: Session, marketplace: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)

    def aggregate(s, e):
        if marketplace == "tiktok":
            tk = _fetch_tiktok_kpis(db, s, e)
            tk_gmv = _float(tk["gmv"])
            return {
                "gmv": tk_gmv,
                "tiktok_gmv": tk_gmv or None,
                "ml_gmv": None,
                "orders": int(_float(tk["orders"])),
                "avg_ticket": _float(tk["avg_ticket"]),
                "ad_spend": None,
                "ml_roas": None,
                "ml_cancel_rate_pct": None,
                "tiktok_customers": int(_float(tk["customers"])) or None,
                "ml_unique_buyers": None,
            }
        if marketplace == "ml":
            ml = _fetch_ml_kpis(db, s, e)
            ml_gmv = _float(ml["gmv"])
            total_ord = int(_float(ml["total_orders"]))
            cancelled = int(_float(ml["cancelled_orders"]))
            roas = _float(ml.get("roas") or 0)
            return {
                "gmv": ml_gmv,
                "tiktok_gmv": None,
                "ml_gmv": ml_gmv or None,
                "shopee_gmv": None,
                "orders": int(_float(ml["orders"])),
                "avg_ticket": _float(ml["avg_ticket"]),
                "ad_spend": _float(ml["ad_spend"]) or None,
                "ml_roas": round(roas, 2) if roas > 0 else None,
                "ml_cancel_rate_pct": round(cancelled / total_ord * 100, 1) if total_ord > 0 else None,
                "tiktok_customers": None,
                "ml_unique_buyers": None,
                "shopee_unique_buyers": None,
            }
        if marketplace == "shopee":
            sh = _fetch_shopee_kpis(db, s, e)
            sh_gmv = _float(sh["gmv"])
            roas = _float(sh.get("roas") or 0)
            return {
                "gmv": sh_gmv,
                "tiktok_gmv": None,
                "ml_gmv": None,
                "shopee_gmv": sh_gmv or None,
                "orders": int(_float(sh["orders"])),
                "avg_ticket": _float(sh["avg_ticket"]),
                "ad_spend": _float(sh["ad_spend"]) or None,
                "ml_roas": None,
                "ml_cancel_rate_pct": None,
                "tiktok_customers": None,
                "ml_unique_buyers": None,
                "shopee_unique_buyers": int(_float(sh["unique_buyers"])) or None,
                "shopee_roas": round(roas, 2) if roas > 0 else None,
            }
        tk = _fetch_tiktok_kpis(db, s, e)
        ml = _fetch_ml_kpis(db, s, e)
        sh = _fetch_shopee_kpis(db, s, e)
        tk_gmv = _float(tk["gmv"])
        ml_gmv = _float(ml["gmv"])
        sh_gmv = _float(sh["gmv"])
        gmv = tk_gmv + ml_gmv + sh_gmv
        orders = int(_float(tk["orders"])) + int(_float(ml["orders"])) + int(_float(sh["orders"]))
        total_ord = int(_float(ml["total_orders"]))
        cancelled = int(_float(ml["cancelled_orders"]))
        roas = _float(ml.get("roas") or 0)
        sh_roas = _float(sh.get("roas") or 0)
        return {
            "gmv": gmv,
            "tiktok_gmv": tk_gmv or None,
            "ml_gmv": ml_gmv or None,
            "shopee_gmv": sh_gmv or None,
            "orders": orders,
            "avg_ticket": gmv / orders if orders > 0 else 0,
            "ad_spend": (_float(ml["ad_spend"]) + _float(sh["ad_spend"])) or None,
            "ml_roas": round(roas, 2) if roas > 0 else None,
            "shopee_roas": round(sh_roas, 2) if sh_roas > 0 else None,
            "ml_cancel_rate_pct": round(cancelled / total_ord * 100, 1) if total_ord > 0 else None,
            "tiktok_customers": int(_float(tk["customers"])) or None,
            "ml_unique_buyers": None,
            "shopee_unique_buyers": int(_float(sh["unique_buyers"])) or None,
        }

    cur = aggregate(start, end)
    prev = aggregate(pstart, pend)
    mom = ((cur["gmv"] - prev["gmv"]) / prev["gmv"] * 100) if prev["gmv"] > 0 else None

    # Compradores ML deduplicated from mensal (diaria overcounts repeat buyers)
    if marketplace in ("all", "ml"):
        sql_buyers = f"""
            SELECT COALESCE(SUM(unique_buyers), 0) AS ml_unique_buyers
            FROM gold.ml_gestao_mensal
            WHERE brand IN {_fmt_list(ML_BRANDS)}
              AND ref_month = '{year}-{month:02d}-01'
        """
        buyers_rows = _query(db, sql_buyers)
        ml_ub = int(_float(buyers_rows[0]["ml_unique_buyers"])) if buyers_rows else 0
        cur["ml_unique_buyers"] = ml_ub or None

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "current": cur,
        "previous": prev,
        "gmv_mom_pct": round(mom, 2) if mom else None,
    }


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------

def get_brands(db: Session, marketplace: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)

    def fetch_tk(s, e):
        sql = f"""
            SELECT brand,
                   COALESCE(SUM(gmv), 0)    AS gmv,
                   COALESCE(SUM(orders), 0) AS orders,
                   CASE WHEN SUM(gmv) > 0
                        THEN ABS(COALESCE(SUM(total_fees),0)) / SUM(gmv) * 100
                        ELSE NULL END        AS cos_pct,
                   CASE WHEN SUM(total_views) > 0
                        THEN SUM(gmv) / SUM(total_views) * 1000
                        ELSE NULL END        AS gpm,
                   CASE WHEN SUM(orders) > 0
                        THEN SUM(gmv) / SUM(orders)
                        ELSE NULL END        AS avg_ticket
            FROM gold.tiktok_brand_daily
            WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
              AND date BETWEEN '{s}' AND '{e}'
            GROUP BY brand
        """
        return {r["brand"]: r for r in _query(db, sql)}

    def fetch_ml(s, e):
        sql = f"""
            SELECT brand,
                   COALESCE(SUM(gmv), 0)              AS gmv,
                   COALESCE(SUM(paid_orders), 0)      AS orders,
                   COALESCE(SUM(total_orders), 0)     AS total_orders,
                   COALESCE(SUM(cancelled_orders), 0) AS cancelled_orders,
                   CASE WHEN SUM(paid_orders) > 0
                        THEN SUM(gmv) / SUM(paid_orders)
                        ELSE NULL END                  AS avg_ticket,
                   CASE WHEN SUM(ad_spend) > 0
                        THEN SUM(ad_revenue) / SUM(ad_spend)
                        ELSE NULL END                  AS roas
            FROM gold.ml_gestao_diaria
            WHERE brand IN {_fmt_list(ML_BRANDS)}
              AND ref_date BETWEEN '{s}' AND '{e}'
            GROUP BY brand
        """
        return {r["brand"]: r for r in _query(db, sql)}

    def fetch_shopee(s, e):
        sql = f"""
            SELECT l.brand_key AS brand,
                   COALESCE(SUM(f.gmv), 0)             AS gmv,
                   COALESCE(SUM(f.orders), 0)          AS orders,
                   COALESCE(SUM(f.canceled_orders), 0) AS canceled_orders,
                   CASE WHEN SUM(f.orders) > 0
                        THEN SUM(f.gmv) / SUM(f.orders)
                        ELSE NULL END                  AS avg_ticket,
                   CASE WHEN SUM(f.ad_spend) > 0
                        THEN SUM(f.ad_revenue) / SUM(f.ad_spend)
                        ELSE NULL END                  AS roas
            FROM marts.fact_marketplace_daily_performance f
            JOIN marts.dim_loja l ON l.loja_id = f.loja_id
            WHERE f.marketplace_id = {SHOPEE_MARKETPLACE_ID}
              AND l.brand_key IN {_fmt_list(SHOPEE_BRANDS)}
              AND f.date BETWEEN '{s}' AND '{e}'
            GROUP BY l.brand_key
        """
        try:
            return {r["brand"]: r for r in _query(db, sql)}
        except SQLAlchemyError:
            return {}

    if marketplace == "tiktok":
        cur_tk, cur_ml, cur_sh = fetch_tk(start, end), {}, {}
        prev_tk, prev_ml, prev_sh = fetch_tk(pstart, pend), {}, {}
        brands_set = BRANDS_IN_SCOPE
    elif marketplace == "ml":
        cur_tk, cur_ml, cur_sh = {}, fetch_ml(start, end), {}
        prev_tk, prev_ml, prev_sh = {}, fetch_ml(pstart, pend), {}
        brands_set = ML_BRANDS
    elif marketplace == "shopee":
        cur_tk, cur_ml, cur_sh = {}, {}, fetch_shopee(start, end)
        prev_tk, prev_ml, prev_sh = {}, {}, fetch_shopee(pstart, pend)
        brands_set = SHOPEE_BRANDS
    else:
        cur_tk, cur_ml, cur_sh = fetch_tk(start, end), fetch_ml(start, end), fetch_shopee(start, end)
        prev_tk, prev_ml, prev_sh = fetch_tk(pstart, pend), fetch_ml(pstart, pend), fetch_shopee(pstart, pend)
        brands_set = BRANDS_IN_SCOPE

    result = []
    for brand in brands_set:
        tk_gmv = _float(cur_tk.get(brand, {}).get("gmv", 0))
        ml_gmv = _float(cur_ml.get(brand, {}).get("gmv", 0))
        shopee_gmv = _float(cur_sh.get(brand, {}).get("gmv", 0))
        total = tk_gmv + ml_gmv + shopee_gmv
        if total == 0:
            continue

        prev_tk_gmv = _float(prev_tk.get(brand, {}).get("gmv", 0))
        prev_ml_gmv = _float(prev_ml.get(brand, {}).get("gmv", 0))
        prev_shopee_gmv = _float(prev_sh.get(brand, {}).get("gmv", 0))
        total_prev = prev_tk_gmv + prev_ml_gmv + prev_shopee_gmv

        orders = (int(_float(cur_tk.get(brand, {}).get("orders", 0))) +
                  int(_float(cur_ml.get(brand, {}).get("orders", 0))) +
                  int(_float(cur_sh.get(brand, {}).get("orders", 0))))
        mom = ((total - total_prev) / total_prev * 100) if total_prev > 0 else None

        tk_cur = cur_tk.get(brand, {})
        ml_cur = cur_ml.get(brand, {})
        sh_cur = cur_sh.get(brand, {})
        ml_total_ord = int(_float(ml_cur.get("total_orders") or 0))
        ml_cancelled = int(_float(ml_cur.get("cancelled_orders") or 0))
        ml_roas_raw = _float(ml_cur.get("roas") or 0)
        result.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_gmv": tk_gmv or None,
            "ml_gmv": ml_gmv or None,
            "shopee_gmv": shopee_gmv or None,
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": total / orders if orders > 0 else None,
            "tiktok_avg_ticket": round(_float(tk_cur.get("avg_ticket") or 0), 2) or None,
            "ml_avg_ticket": round(_float(ml_cur.get("avg_ticket") or 0), 2) or None,
            "shopee_avg_ticket": round(_float(sh_cur.get("avg_ticket") or 0), 2) or None,
            "tiktok_gmv_prev": prev_tk_gmv or None,
            "ml_gmv_prev": prev_ml_gmv or None,
            "shopee_gmv_prev": prev_shopee_gmv or None,
            "total_gmv_prev": total_prev,
            "mom_pct": round(mom, 2) if mom else None,
            "cos_pct": round(_float(tk_cur.get("cos_pct") or 0), 2) or None,
            "gpm": round(_float(tk_cur.get("gpm") or 0), 2) or None,
            "ml_roas": round(ml_roas_raw, 2) if ml_roas_raw > 0 else None,
            "ml_cancel_rate_pct": round(ml_cancelled / ml_total_ord * 100, 1) if ml_total_ord > 0 else None,
        })

    result.sort(key=lambda r: -r["total_gmv"])
    return {"ref_month": f"{year:04d}-{month:02d}", "brands": result}


# ---------------------------------------------------------------------------
# Monthly
# ---------------------------------------------------------------------------

def get_monthly(db: Session, marketplace: str, months_back: int = 6) -> dict:
    today = date.today()
    year, month = today.year, today.month
    for _ in range(months_back):
        year, month = _prev_month(year, month)
    start = date(year, month, 1)

    tk_rows: list[dict] = []
    if marketplace in ("all", "tiktok"):
        sql_tk = f"""
            SELECT DATE_TRUNC('month', date)::date AS mes,
                   brand,
                   COALESCE(SUM(gmv), 0) AS gmv
            FROM gold.tiktok_brand_daily
            WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
              AND date >= '{start}'
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        tk_rows = _query(db, sql_tk)

    ml_rows: list[dict] = []
    if marketplace in ("all", "ml"):
        sql_ml = f"""
            SELECT DATE_TRUNC('month', ref_date)::date AS mes,
                   brand,
                   COALESCE(SUM(gmv), 0) AS gmv
            FROM gold.ml_gestao_diaria
            WHERE brand IN {_fmt_list(ML_BRANDS)}
              AND ref_date >= '{start}'
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        ml_rows = _query(db, sql_ml)

    shopee_rows: list[dict] = []
    if marketplace in ("all", "shopee"):
        sql_shopee = f"""
            SELECT DATE_TRUNC('month', f.date)::date AS mes,
                   l.brand_key AS brand,
                   COALESCE(SUM(f.gmv), 0) AS gmv
            FROM marts.fact_marketplace_daily_performance f
            JOIN marts.dim_loja l ON l.loja_id = f.loja_id
            WHERE f.marketplace_id = {SHOPEE_MARKETPLACE_ID}
              AND l.brand_key IN {_fmt_list(SHOPEE_BRANDS)}
              AND f.date >= '{start}'
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        try:
            shopee_rows = _query(db, sql_shopee)
        except SQLAlchemyError:
            shopee_rows = []

    months: dict[str, dict] = {}

    def add_row(r):
        mes_val = r["mes"]
        if isinstance(mes_val, str):
            mes_val = mes_val[:10]
        key = str(mes_val)[:7]
        m_num = int(key[5:7])
        yr = key[:4]
        label = f"{MES_LABELS[m_num]}/{yr[2:]}"
        if key not in months:
            months[key] = {"mes": key, "mes_label": label,
                           "barbours": 0, "kokeshi": 0, "apice": 0,
                           "lescent": 0, "rituaria": 0}
        brand = r["brand"]
        if brand in months[key]:
            months[key][brand] = months[key][brand] + _float(r["gmv"])

    for r in tk_rows:
        add_row(r)
    for r in ml_rows:
        add_row(r)
    for r in shopee_rows:
        add_row(r)

    return {"data": sorted(months.values(), key=lambda x: x["mes"])}


# ---------------------------------------------------------------------------
# Daily
# ---------------------------------------------------------------------------

def get_daily(db: Session, brand: str, marketplace: str, days_back: int = 60) -> dict:
    date_from = date.today() - timedelta(days=days_back)
    rows = []

    if marketplace in ("all", "tiktok") and brand in BRANDS_IN_SCOPE:
        sql = f"""
            SELECT date, gmv, orders,
                   avg_ticket, NULL AS ad_spend
            FROM gold.tiktok_brand_daily
            WHERE brand = '{brand}'
              AND date >= '{date_from}'
            ORDER BY date
        """
        for r in _query(db, sql):
            rows.append({
                "date": str(r["date"])[:10],
                "tiktok_gmv": _float(r["gmv"]) or None,
                "ml_gmv": None,
                "shopee_gmv": None,
                "orders_tk": int(_float(r["orders"])),
                "orders_shopee": 0,
                "ad_spend": None,
            })

    ml_data: dict[str, dict] = {}
    if marketplace in ("all", "ml") and brand in ML_BRANDS:
        sql = f"""
            SELECT ref_date AS date, gmv,
                   paid_orders AS orders, avg_ticket, ad_spend
            FROM gold.ml_gestao_diaria
            WHERE brand = '{brand}'
              AND ref_date >= '{date_from}'
            ORDER BY ref_date
        """
        for r in _query(db, sql):
            ml_data[str(r["date"])[:10]] = r

    shopee_data: dict[str, dict] = {}
    if marketplace in ("all", "shopee") and brand in SHOPEE_BRANDS:
        sql = f"""
            SELECT f.date, f.gmv, f.orders, f.avg_ticket, f.ad_spend
            FROM marts.fact_marketplace_daily_performance f
            JOIN marts.dim_loja l ON l.loja_id = f.loja_id
            WHERE f.marketplace_id = {SHOPEE_MARKETPLACE_ID}
              AND l.brand_key = '{brand}'
              AND f.date >= '{date_from}'
            ORDER BY f.date
        """
        try:
            for r in _query(db, sql):
                shopee_data[str(r["date"])[:10]] = r
        except SQLAlchemyError:
            shopee_data = {}

    merged: dict[str, dict] = {}
    for r in rows:
        d = r["date"]
        merged[d] = r
    for d, r in ml_data.items():
        if d not in merged:
            merged[d] = {"date": d, "tiktok_gmv": None, "ml_gmv": None, "shopee_gmv": None, "orders_tk": 0, "orders_shopee": 0, "ad_spend": None}
        merged[d]["ml_gmv"] = _float(r["gmv"]) or None
        merged[d]["ad_spend"] = _float(r.get("ad_spend") or 0) or merged[d].get("ad_spend")
    for d, r in shopee_data.items():
        if d not in merged:
            merged[d] = {"date": d, "tiktok_gmv": None, "ml_gmv": None, "shopee_gmv": None, "orders_tk": 0, "orders_shopee": 0, "ad_spend": None}
        merged[d]["shopee_gmv"] = _float(r["gmv"]) or None
        merged[d]["orders_shopee"] = int(_float(r.get("orders") or 0))
        merged[d]["ad_spend"] = (_float(merged[d].get("ad_spend") or 0) + _float(r.get("ad_spend") or 0)) or None

    result = []
    for d in sorted(merged.keys()):
        v = merged[d]
        total = (v.get("tiktok_gmv") or 0) + (v.get("ml_gmv") or 0) + (v.get("shopee_gmv") or 0)
        orders = v.get("orders_tk", 0) + v.get("orders_shopee", 0)
        result.append({
            "date": d,
            "tiktok_gmv": v.get("tiktok_gmv"),
            "ml_gmv": v.get("ml_gmv"),
            "shopee_gmv": v.get("shopee_gmv"),
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": total / orders if orders > 0 else None,
            "ad_spend": v.get("ad_spend"),
        })

    return {"brand": brand, "marketplace": marketplace, "data": result}

# ---------------------------------------------------------------------------
# Produtos ML
# ---------------------------------------------------------------------------

ML_PRODUTO_BRANDS = ML_BRANDS
TK_PRODUTO_BRANDS = BRANDS_IN_SCOPE

_BUCKET_META = {
    "A_top50":  ("A", "Top 50% GMV"),
    "B_next30": ("B", "Next 30%"),
    "C_next15": ("C", "Next 15%"),
    "D_tail":   ("D", "Cauda"),
}


def get_produtos_ml(
    db: Session,
    brand: str | None,
    pareto_bucket: str | None,
    action_signal: str | None,
    product_status: str | None,
    revenue_velocity: str | None,
    limit: int,
    offset: int,
) -> dict:
    filters = [f"brand IN {_fmt_list(ML_PRODUTO_BRANDS)}"]
    if brand and brand in ML_PRODUTO_BRANDS:
        filters.append(f"brand = '{brand}'")
    if pareto_bucket:
        filters.append(f"pareto_bucket = '{pareto_bucket}'")
    if action_signal:
        filters.append(f"action_signal = '{action_signal}'")
    if product_status:
        filters.append(f"product_status = '{product_status}'")
    if revenue_velocity:
        filters.append(f"revenue_velocity = '{revenue_velocity}'")
    where = " AND ".join(filters)

    count_sql = f"SELECT COUNT(*) AS n FROM gold.ml_produto_ranking WHERE {where}"
    total = int(_float(_query(db, count_sql)[0]["n"]))

    data_sql = f"""
        SELECT brand, item_id, seller_sku, title,
               gross_revenue, units_sold, unique_buyers,
               CASE WHEN units_sold > 0 THEN gross_revenue / units_sold ELSE NULL END AS avg_price,
               cancel_rate_pct, pareto_bucket, revenue_velocity,
               ad_roas, ad_acos_pct, ad_spend, ad_efficiency, action_signal,
               estimated_margin, revenue_share_pct, product_status
        FROM gold.ml_produto_ranking
        WHERE {where}
        ORDER BY gross_revenue DESC
        LIMIT {limit} OFFSET {offset}
    """
    rows = _query(db, data_sql)

    items = []
    for r in rows:
        items.append({
            "brand": r["brand"],
            "item_id": r["item_id"],
            "seller_sku": r.get("seller_sku"),
            "title": r["title"],
            "gross_revenue": _float(r["gross_revenue"]),
            "units_sold": int(_float(r["units_sold"])),
            "unique_buyers": int(_float(r["unique_buyers"])) if r.get("unique_buyers") else None,
            "avg_price": round(_float(r["avg_price"]), 2) if r.get("avg_price") else None,
            "cancel_rate_pct": round(_float(r["cancel_rate_pct"]), 2) if r.get("cancel_rate_pct") else None,
            "pareto_bucket": r.get("pareto_bucket"),
            "revenue_velocity": r.get("revenue_velocity"),
            "ad_roas": round(_float(r["ad_roas"]), 2) if r.get("ad_roas") else None,
            "ad_acos_pct": round(_float(r["ad_acos_pct"]), 2) if r.get("ad_acos_pct") else None,
            "ad_spend": _float(r["ad_spend"]) if r.get("ad_spend") else None,
            "ad_efficiency": r.get("ad_efficiency"),
            "action_signal": r.get("action_signal"),
            "estimated_margin": _float(r["estimated_margin"]) if r.get("estimated_margin") else None,
            "revenue_share_pct": round(_float(r["revenue_share_pct"]), 3) if r.get("revenue_share_pct") else None,
            "product_status": r.get("product_status"),
        })

    return {"total": total, "limit": limit, "offset": offset, "items": items}


def get_produtos_ml_summary(db: Session, brand: str | None) -> dict:
    filters = [f"brand IN {_fmt_list(ML_PRODUTO_BRANDS)}"]
    if brand and brand in ML_PRODUTO_BRANDS:
        filters.append(f"brand = '{brand}'")
    where = " AND ".join(filters)

    sql = f"""
        SELECT pareto_bucket,
               COUNT(*)              AS count,
               COALESCE(SUM(gross_revenue), 0) AS gmv
        FROM gold.ml_produto_ranking
        WHERE {where}
          AND pareto_bucket IS NOT NULL
        GROUP BY pareto_bucket
        ORDER BY pareto_bucket
    """
    rows = _query(db, sql)

    total_gmv = sum(_float(r["gmv"]) for r in rows)
    total_count = sum(int(_float(r["count"])) for r in rows)

    buckets = []
    for bk in ("A_top50", "B_next30", "C_next15", "D_tail"):
        row = next((r for r in rows if r["pareto_bucket"] == bk), None)
        label, desc = _BUCKET_META[bk]
        gmv = _float(row["gmv"]) if row else 0.0
        cnt = int(_float(row["count"])) if row else 0
        buckets.append({
            "bucket": bk,
            "label": label,
            "description": desc,
            "gmv": gmv,
            "count": cnt,
            "gmv_pct": round(gmv / total_gmv * 100, 1) if total_gmv > 0 else 0.0,
        })

    return {
        "total_gmv": total_gmv,
        "total_count": total_count,
        "brand": brand,
        "buckets": buckets,
    }


# ---------------------------------------------------------------------------
# Produtos TikTok
# ---------------------------------------------------------------------------

def get_produtos_tiktok(
    db: Session,
    brand: str | None,
    year: int,
    month: int,
    limit: int,
    offset: int,
) -> dict:
    start, end = _month_bounds(year, month)
    filters = [
        f"brand IN {_fmt_list(TK_PRODUTO_BRANDS)}",
        f"date BETWEEN '{start}' AND '{end}'",
    ]
    if brand and brand in TK_PRODUTO_BRANDS:
        filters.append(f"brand = '{brand}'")
    where = " AND ".join(filters)

    count_sql = f"""
        SELECT COUNT(DISTINCT product_id) AS n
        FROM gold.tiktok_product_daily
        WHERE {where}
    """
    total = int(_float(_query(db, count_sql)[0]["n"]))

    data_sql = f"""
        SELECT brand, product_id, product_name,
               SUM(gmv)        AS gmv,
               SUM(orders)     AS orders,
               SUM(items_sold) AS items_sold,
               CASE WHEN SUM(gmv) > 0
                    THEN SUM(gmv_video) / SUM(gmv) * 100 ELSE NULL END AS pct_gmv_video,
               CASE WHEN SUM(gmv) > 0
                    THEN SUM(gmv_live) / SUM(gmv) * 100  ELSE NULL END AS pct_gmv_live,
               CASE WHEN SUM(gmv) > 0
                    THEN SUM(gmv_product_card) / SUM(gmv) * 100 ELSE NULL END AS pct_gmv_card,
               CASE WHEN SUM(orders + canceled + refunded + returned) > 0
                    THEN (SUM(canceled) + SUM(refunded) + SUM(returned))
                         * 100.0 / SUM(orders + canceled + refunded + returned)
                    ELSE NULL END AS problem_rate,
               CASE WHEN SUM(total_ratings) > 0
                    THEN SUM(rating_avg * total_ratings) / SUM(total_ratings)
                    ELSE NULL END AS rating_avg,
               SUM(total_ratings) AS total_ratings
        FROM gold.tiktok_product_daily
        WHERE {where}
        GROUP BY brand, product_id, product_name
        ORDER BY SUM(gmv) DESC
        LIMIT {limit} OFFSET {offset}
    """
    rows = _query(db, data_sql)

    items = []
    for r in rows:
        gmv = _float(r["gmv"])
        if gmv == 0:
            continue
        items.append({
            "brand": r["brand"],
            "product_id": r["product_id"],
            "product_name": r["product_name"],
            "gmv": gmv,
            "orders": int(_float(r["orders"])),
            "items_sold": int(_float(r["items_sold"])),
            "pct_gmv_video": round(_float(r["pct_gmv_video"]), 1) if r.get("pct_gmv_video") else None,
            "pct_gmv_live": round(_float(r["pct_gmv_live"]), 1) if r.get("pct_gmv_live") else None,
            "pct_gmv_card": round(_float(r["pct_gmv_card"]), 1) if r.get("pct_gmv_card") else None,
            "problem_rate": round(_float(r["problem_rate"]), 2) if r.get("problem_rate") else None,
            "rating_avg": round(_float(r["rating_avg"]), 1) if r.get("rating_avg") else None,
            "total_ratings": int(_float(r["total_ratings"])) if r.get("total_ratings") else None,
        })

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Canais
# ---------------------------------------------------------------------------

def get_canais(db: Session, marketplace: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)

    tk_rows: list[dict] = []
    if marketplace in ("all", "tiktok"):
        sql = f"""
            SELECT brand,
                   COALESCE(SUM(gmv), 0)       AS gmv,
                   COALESCE(SUM(gmv_video), 0) AS gmv_video,
                   COALESCE(SUM(gmv_live), 0)  AS gmv_live,
                   COALESCE(SUM(gmv_card), 0)  AS gmv_card,
                   COALESCE(SUM(CASE WHEN visitors > 0 THEN visitors ELSE 0 END), 0) AS visitors,
                   COALESCE(SUM(CASE WHEN visitors > 0 THEN customers ELSE 0 END), 0) AS customers
            FROM gold.tiktok_brand_daily
            WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
              AND date BETWEEN '{start}' AND '{end}'
            GROUP BY brand
        """
        tk_rows = _query(db, sql)

    ctr_rows: list[dict] = []
    if marketplace in ("all", "tiktok"):
        sql_ctr = f"""
            SELECT brand,
                   SUM(impressions) AS impressions,
                   SUM(page_views)  AS page_views,
                   CASE WHEN SUM(impressions) > 0
                        THEN SUM(page_views)::numeric / SUM(impressions) * 100
                        ELSE NULL END AS ctr_pct
            FROM gold.v_channel_efficiency
            WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
              AND date BETWEEN '{start}' AND '{end}'
            GROUP BY brand
        """
        ctr_rows = _query(db, sql_ctr)

    ml_rows: list[dict] = []
    if marketplace in ("all", "ml"):
        sql = f"""
            SELECT brand,
                   COALESCE(gmv, 0)           AS gmv,
                   COALESCE(unique_buyers, 0)  AS unique_buyers,
                   COALESCE(new_buyers, 0)     AS new_buyers,
                   COALESCE(repeat_buyers, 0)  AS repeat_buyers,
                   repeat_buyer_rate_pct,
                   gmv_per_buyer
            FROM gold.ml_gestao_mensal
            WHERE brand IN {_fmt_list(ML_BRANDS)}
              AND ref_month = '{year}-{month:02d}-01'
        """
        ml_rows = _query(db, sql)

    sh_rows: list[dict] = []
    if marketplace in ("all", "shopee"):
        sql = f"""
            SELECT brand_key                          AS brand,
                   SUM(gmv)                           AS gmv,
                   SUM(unique_buyers)                 AS unique_buyers,
                   SUM(new_buyers)                    AS new_buyers,
                   SUM(repeat_buyers)                 AS repeat_buyers,
                   SUM(canceled_orders)               AS canceled_orders,
                   SUM(orders)                        AS orders,
                   SUM(visitors)                      AS visitors
            FROM marts.fact_marketplace_daily_performance
            JOIN marts.dim_loja USING (loja_id)
            WHERE marketplace_id = {SHOPEE_MARKETPLACE_ID}
              AND brand_key IN {_fmt_list(SHOPEE_BRANDS)}
              AND date BETWEEN '{start}' AND '{end}'
            GROUP BY brand_key
        """
        try:
            sh_rows = _query(db, sql)
        except Exception:
            sh_rows = []

    tk_by_brand = {r["brand"]: r for r in tk_rows}
    ctr_by_brand = {r["brand"]: r for r in ctr_rows}
    ml_by_brand = {r["brand"]: r for r in ml_rows}
    sh_by_brand = {r["brand"]: r for r in sh_rows}

    if marketplace in ("all", "tiktok"):
        brands_set = BRANDS_IN_SCOPE
    elif marketplace == "shopee":
        brands_set = SHOPEE_BRANDS
    else:
        brands_set = ML_BRANDS

    def _tk_pct(part, total):
        return round(part / total * 100, 1) if total > 0 else None

    brand_rows = []
    for brand in brands_set:
        tk = tk_by_brand.get(brand, {})
        ml = ml_by_brand.get(brand, {})
        sh = sh_by_brand.get(brand, {})
        if not tk and not ml and not sh:
            continue

        row: dict = {"brand": brand, "label": BRAND_LABELS.get(brand, brand.upper())}

        if tk:
            gmv = _float(tk["gmv"])
            vid = _float(tk["gmv_video"])
            live = _float(tk["gmv_live"])
            card = _float(tk["gmv_card"])
            visitors = int(_float(tk["visitors"]))
            customers = int(_float(tk["customers"]))
            ctr = ctr_by_brand.get(brand, {})
            row.update({
                "tiktok_gmv": gmv,
                "tiktok_gmv_video": vid,
                "tiktok_gmv_live": live,
                "tiktok_gmv_card": card,
                "tiktok_video_pct": _tk_pct(vid, gmv),
                "tiktok_live_pct": _tk_pct(live, gmv),
                "tiktok_card_pct": _tk_pct(card, gmv),
                "tiktok_visitors": visitors or None,
                "tiktok_customers": customers or None,
                "tiktok_conversion_rate": _tk_pct(customers, visitors) if visitors > 0 else None,
                "tiktok_impressions": int(_float(ctr["impressions"])) if ctr else None,
                "tiktok_page_views": int(_float(ctr["page_views"])) if ctr else None,
                "tiktok_ctr_pct": round(_float(ctr["ctr_pct"]), 2) if ctr and ctr.get("ctr_pct") is not None else None,
            })

        if ml:
            gmv = _float(ml["gmv"])
            buyers = int(_float(ml["unique_buyers"]))
            new_b = int(_float(ml["new_buyers"]))
            repeat_b = int(_float(ml["repeat_buyers"]))
            row.update({
                "ml_gmv": gmv,
                "ml_unique_buyers": buyers or None,
                "ml_new_buyers": new_b or None,
                "ml_repeat_buyers": repeat_b or None,
                "ml_repeat_buyer_rate_pct": round(_float(ml.get("repeat_buyer_rate_pct") or 0), 1) or None,
                "ml_gmv_per_buyer": round(_float(ml.get("gmv_per_buyer") or 0), 2) or None,
            })

        if sh:
            sh_gmv = _float(sh["gmv"])
            sh_unique = int(_float(sh["unique_buyers"]))
            sh_new = int(_float(sh["new_buyers"]))
            sh_repeat = int(_float(sh["repeat_buyers"]))
            sh_canceled = int(_float(sh["canceled_orders"]))
            sh_orders = int(_float(sh["orders"]))
            sh_visitors = int(_float(sh.get("visitors") or 0))
            row.update({
                "shopee_gmv": sh_gmv,
                "shopee_unique_buyers": sh_unique or None,
                "shopee_new_buyers": sh_new or None,
                "shopee_repeat_buyers": sh_repeat or None,
                "shopee_new_buyer_pct": round(sh_new / sh_unique * 100, 1) if sh_unique > 0 else None,
                "shopee_repeat_buyer_rate_pct": round(sh_repeat / sh_unique * 100, 1) if sh_unique > 0 else None,
                "shopee_gmv_per_buyer": round(sh_gmv / sh_unique, 2) if sh_unique > 0 else None,
                "shopee_cancel_rate_pct": round(sh_canceled / (sh_orders + sh_canceled) * 100, 2) if (sh_orders + sh_canceled) > 0 else None,
                "shopee_visitors": sh_visitors or None,
                "shopee_conversion_rate": round(sh_unique / sh_visitors * 100, 2) if sh_visitors > 0 else None,
            })

        brand_rows.append(row)

    tk_gmv_total = sum(_float(r.get("tiktok_gmv") or 0) for r in brand_rows)
    tk_vid_total = sum(_float(r.get("tiktok_gmv_video") or 0) for r in brand_rows)
    tk_live_total = sum(_float(r.get("tiktok_gmv_live") or 0) for r in brand_rows)
    tk_card_total = sum(_float(r.get("tiktok_gmv_card") or 0) for r in brand_rows)
    tk_visitors = sum(r.get("tiktok_visitors") or 0 for r in brand_rows)
    tk_customers = sum(r.get("tiktok_customers") or 0 for r in brand_rows)
    tk_impressions = sum(r.get("tiktok_impressions") or 0 for r in brand_rows)
    tk_page_views = sum(r.get("tiktok_page_views") or 0 for r in brand_rows)

    ml_buyers = sum(r.get("ml_unique_buyers") or 0 for r in brand_rows)
    ml_new = sum(r.get("ml_new_buyers") or 0 for r in brand_rows)
    ml_repeat = sum(r.get("ml_repeat_buyers") or 0 for r in brand_rows)
    ml_gmv_total = sum(_float(r.get("ml_gmv") or 0) for r in brand_rows)

    sh_gmv_total = sum(_float(r.get("shopee_gmv") or 0) for r in brand_rows)
    sh_buyers_total = sum(r.get("shopee_unique_buyers") or 0 for r in brand_rows)
    sh_new_total = sum(r.get("shopee_new_buyers") or 0 for r in brand_rows)
    sh_repeat_total = sum(r.get("shopee_repeat_buyers") or 0 for r in brand_rows)
    sh_visitors_total = sum(r.get("shopee_visitors") or 0 for r in brand_rows)

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_gmv": tk_gmv_total or None,
            "tiktok_gmv_video": tk_vid_total or None,
            "tiktok_gmv_live": tk_live_total or None,
            "tiktok_gmv_card": tk_card_total or None,
            "tiktok_video_pct": _tk_pct(tk_vid_total, tk_gmv_total),
            "tiktok_live_pct": _tk_pct(tk_live_total, tk_gmv_total),
            "tiktok_card_pct": _tk_pct(tk_card_total, tk_gmv_total),
            "tiktok_visitors": tk_visitors or None,
            "tiktok_customers": tk_customers or None,
            "tiktok_conversion_rate": _tk_pct(tk_customers, tk_visitors) if tk_visitors > 0 else None,
            "tiktok_impressions": tk_impressions or None,
            "tiktok_page_views": tk_page_views or None,
            "tiktok_ctr_pct": round(tk_page_views / tk_impressions * 100, 2) if tk_impressions > 0 else None,
            "ml_unique_buyers": ml_buyers or None,
            "ml_new_buyers": ml_new or None,
            "ml_repeat_buyers": ml_repeat or None,
            "ml_new_buyer_pct": _tk_pct(ml_new, ml_buyers),
            "ml_repeat_buyer_rate_pct": round(ml_repeat / ml_buyers * 100, 1) if ml_buyers > 0 else None,
            "ml_gmv_per_buyer": round(ml_gmv_total / ml_buyers, 2) if ml_buyers > 0 else None,
            "shopee_gmv": sh_gmv_total or None,
            "shopee_unique_buyers": sh_buyers_total or None,
            "shopee_new_buyers": sh_new_total or None,
            "shopee_repeat_buyers": sh_repeat_total or None,
            "shopee_new_buyer_pct": round(sh_new_total / sh_buyers_total * 100, 1) if sh_buyers_total > 0 else None,
            "shopee_repeat_buyer_rate_pct": round(sh_repeat_total / sh_buyers_total * 100, 1) if sh_buyers_total > 0 else None,
            "shopee_gmv_per_buyer": round(sh_gmv_total / sh_buyers_total, 2) if sh_buyers_total > 0 else None,
            "shopee_visitors": sh_visitors_total or None,
            "shopee_conversion_rate": round(sh_buyers_total / sh_visitors_total * 100, 2) if sh_visitors_total > 0 else None,
        },
        "brands": brand_rows,
    }


# ---------------------------------------------------------------------------
# Produtos Shopee
# ---------------------------------------------------------------------------

def get_produtos_shopee(
    db,
    brand: str | None,
    year: int,
    month: int,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    ref_month = f"{year}-{month:02d}-01"
    conditions = [f"ref_month = '{ref_month}'", "gmv > 0"]
    if brand:
        conditions.append(f"brand = '{brand}'")
    where = " AND ".join(conditions)

    count_row = _query(db, f"SELECT COUNT(*) AS n FROM marts.fact_shopee_product_monthly WHERE {where}")
    total = int(count_row[0]["n"]) if count_row else 0

    rows = _query(db, f"""
        SELECT brand, sku_ref, product_name, variation_name,
               gmv, units_sold, completed_orders, canceled_orders,
               cancel_rate_pct, unique_buyers, avg_price
        FROM marts.fact_shopee_product_monthly
        WHERE {where}
        ORDER BY gmv DESC
        LIMIT {limit} OFFSET {offset}
    """)

    items = [
        {
            "brand": r["brand"],
            "sku_ref": r.get("sku_ref"),
            "product_name": r["product_name"],
            "variation_name": r.get("variation_name"),
            "gmv": _float(r.get("gmv", 0)),
            "units_sold": int(_float(r.get("units_sold", 0))),
            "orders": int(_float(r.get("completed_orders", 0))),
            "canceled_orders": int(_float(r.get("canceled_orders", 0))),
            "cancel_rate_pct": round(_float(r["cancel_rate_pct"]), 2) if r.get("cancel_rate_pct") else None,
            "unique_buyers": int(_float(r["unique_buyers"])) if r.get("unique_buyers") else None,
            "avg_price": round(_float(r["avg_price"]), 2) if r.get("avg_price") else None,
        }
        for r in rows
    ]

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Financeiro
# ---------------------------------------------------------------------------

def get_financeiro(db: Session, marketplace: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)

    tk_rows: list[dict] = []
    if marketplace in ("all", "tiktok"):
        sql = f"""
            SELECT brand,
                   COALESCE(SUM(gmv), 0)                       AS gmv,
                   COALESCE(SUM(total_settlement), 0)          AS total_settlement,
                   ABS(COALESCE(SUM(total_fees), 0))           AS total_fees,
                   CASE WHEN SUM(gmv) > 0
                        THEN ABS(COALESCE(SUM(total_fees),0)) / SUM(gmv) * 100
                        ELSE NULL END                           AS avg_fee_pct,
                   CASE WHEN SUM(gmv) > 0
                        THEN SUM(total_settlement) / SUM(gmv) * 100
                        ELSE NULL END                           AS avg_settlement_pct
            FROM gold.tiktok_brand_daily
            WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
              AND date BETWEEN '{start}' AND '{end}'
            GROUP BY brand
        """
        tk_rows = _query(db, sql)

    ml_rows: list[dict] = []
    if marketplace in ("all", "ml"):
        sql = f"""
            SELECT brand,
                   COALESCE(SUM(gmv), 0)                  AS gmv,
                   COALESCE(SUM(ad_spend), 0)             AS ad_spend,
                   COALESCE(SUM(ad_revenue), 0)           AS ad_revenue,
                   CASE WHEN SUM(ad_spend) > 0
                        THEN SUM(ad_revenue) / SUM(ad_spend)
                        ELSE NULL END                      AS roas,
                   CASE WHEN SUM(ad_revenue) > 0
                        THEN SUM(ad_spend) / SUM(ad_revenue) * 100
                        ELSE NULL END                      AS acos_pct,
                   COALESCE(SUM(ad_clicks), 0)            AS ad_clicks,
                   COALESCE(SUM(ad_impressions), 0)       AS ad_impressions,
                   CASE WHEN SUM(ad_clicks) > 0
                        THEN SUM(ad_spend) / SUM(ad_clicks)
                        ELSE NULL END                      AS cpc,
                   CASE WHEN SUM(ad_impressions) > 0
                        THEN SUM(ad_clicks) * 100.0 / SUM(ad_impressions)
                        ELSE NULL END                      AS ctr_pct,
                   COALESCE(SUM(seller_shipping_cost), 0) AS seller_shipping_cost,
                   CASE WHEN SUM(gmv) > 0
                        THEN SUM(seller_shipping_cost) / SUM(gmv) * 100
                        ELSE NULL END                      AS shipping_pct_of_gmv,
                   CASE WHEN SUM(gmv) > 0
                        THEN (COALESCE(SUM(ad_spend),0) + COALESCE(SUM(seller_shipping_cost),0)) / SUM(gmv) * 100
                        ELSE NULL END                      AS total_cost_pct_of_gmv
            FROM gold.ml_gestao_diaria
            WHERE brand IN {_fmt_list(ML_BRANDS)}
              AND ref_date BETWEEN '{start}' AND '{end}'
            GROUP BY brand
        """
        ml_rows = _query(db, sql)

    sh_rows: list[dict] = []
    if marketplace in ("all", "shopee"):
        sql = f"""
            SELECT brand_key                                                          AS brand,
                   SUM(gmv)                                                           AS gmv,
                   SUM(total_settlement)                                              AS total_settlement,
                   SUM(total_fees)                                                    AS total_fees,
                   CASE WHEN SUM(gmv) > 0
                        THEN SUM(total_fees)::numeric / SUM(gmv) * 100
                        ELSE NULL END                                                 AS avg_fee_pct,
                   CASE WHEN SUM(gmv) > 0
                        THEN SUM(total_settlement)::numeric / SUM(gmv) * 100
                        ELSE NULL END                                                 AS avg_settlement_pct,
                   SUM(ad_spend)                                                      AS ad_spend,
                   SUM(ad_revenue)                                                    AS ad_revenue,
                   CASE WHEN SUM(ad_spend) > 0
                        THEN SUM(ad_revenue)::numeric / SUM(ad_spend)
                        ELSE NULL END                                                 AS roas,
                   SUM(seller_shipping_cost)                                          AS seller_shipping_cost,
                   CASE WHEN SUM(gmv) > 0
                        THEN SUM(seller_shipping_cost)::numeric / SUM(gmv) * 100
                        ELSE NULL END                                                 AS shipping_pct_of_gmv
            FROM marts.fact_marketplace_daily_performance
            JOIN marts.dim_loja USING (loja_id)
            WHERE marketplace_id = {SHOPEE_MARKETPLACE_ID}
              AND brand_key IN {_fmt_list(SHOPEE_BRANDS)}
              AND date BETWEEN '{start}' AND '{end}'
            GROUP BY brand_key
        """
        try:
            sh_rows = _query(db, sql)
        except Exception:
            sh_rows = []

    tk_by_brand = {r["brand"]: r for r in tk_rows}
    ml_by_brand = {r["brand"]: r for r in ml_rows}
    sh_by_brand = {r["brand"]: r for r in sh_rows}

    if marketplace in ("all", "tiktok"):
        brands_set = BRANDS_IN_SCOPE
    elif marketplace == "shopee":
        brands_set = SHOPEE_BRANDS
    else:
        brands_set = ML_BRANDS

    brand_rows = []
    for brand in brands_set:
        tk = tk_by_brand.get(brand, {})
        ml = ml_by_brand.get(brand, {})
        sh = sh_by_brand.get(brand, {})
        if not tk and not ml and not sh:
            continue

        row: dict = {"brand": brand, "label": BRAND_LABELS.get(brand, brand.upper())}

        if tk:
            row.update({
                "tiktok_gmv": _float(tk.get("gmv")),
                "tiktok_settlement": _float(tk.get("total_settlement")),
                "tiktok_fees": _float(tk.get("total_fees")),
                "tiktok_avg_fee_pct": round(_float(tk.get("avg_fee_pct") or 0), 2) or None,
                "tiktok_avg_settlement_pct": round(_float(tk.get("avg_settlement_pct") or 0), 2) or None,
            })

        if ml:
            row.update({
                "ml_gmv": _float(ml.get("gmv")),
                "ml_ad_spend": _float(ml.get("ad_spend")),
                "ml_ad_revenue": _float(ml.get("ad_revenue")),
                "ml_roas": round(_float(ml.get("roas") or 0), 2) or None,
                "ml_acos_pct": round(_float(ml.get("acos_pct") or 0), 2) or None,
                "ml_cpc": round(_float(ml.get("cpc") or 0), 4) or None,
                "ml_ctr_pct": round(_float(ml.get("ctr_pct") or 0), 3) or None,
                "ml_ad_clicks": int(_float(ml.get("ad_clicks", 0))),
                "ml_ad_impressions": int(_float(ml.get("ad_impressions", 0))),
                "ml_seller_shipping_cost": _float(ml.get("seller_shipping_cost")),
                "ml_shipping_pct_of_gmv": round(_float(ml.get("shipping_pct_of_gmv") or 0), 2) or None,
                "ml_total_cost_pct": round(_float(ml.get("total_cost_pct_of_gmv") or 0), 2) or None,
            })

        if sh:
            row.update({
                "shopee_gmv": _float(sh.get("gmv")),
                "shopee_settlement": _float(sh.get("total_settlement")),
                "shopee_fees": _float(sh.get("total_fees")),
                "shopee_avg_fee_pct": round(_float(sh.get("avg_fee_pct") or 0), 2) or None,
                "shopee_avg_settlement_pct": round(_float(sh.get("avg_settlement_pct") or 0), 2) or None,
                "shopee_ad_spend": _float(sh.get("ad_spend")),
                "shopee_ad_revenue": _float(sh.get("ad_revenue")),
                "shopee_roas": round(_float(sh.get("roas") or 0), 2) or None,
                "shopee_shipping_cost": _float(sh.get("seller_shipping_cost")),
                "shopee_shipping_pct_of_gmv": round(_float(sh.get("shipping_pct_of_gmv") or 0), 2) or None,
            })

        brand_rows.append(row)

    total_tk_gmv = sum(_float(r.get("tiktok_gmv") or 0) for r in brand_rows)
    total_tk_settlement = sum(_float(r.get("tiktok_settlement") or 0) for r in brand_rows)
    total_tk_fees = sum(_float(r.get("tiktok_fees") or 0) for r in brand_rows)
    kpi_tk_fee_pct = round(total_tk_fees / total_tk_gmv * 100, 2) if total_tk_gmv > 0 else None
    kpi_tk_settlement_pct = round(total_tk_settlement / total_tk_gmv * 100, 2) if total_tk_gmv > 0 else None

    total_ml_gmv = sum(_float(r.get("ml_gmv") or 0) for r in brand_rows)
    total_ml_ad_spend = sum(_float(r.get("ml_ad_spend") or 0) for r in brand_rows)
    total_ml_ad_revenue = sum(_float(r.get("ml_ad_revenue") or 0) for r in brand_rows)
    total_ml_shipping = sum(_float(r.get("ml_seller_shipping_cost") or 0) for r in brand_rows)
    kpi_ml_roas = round(total_ml_ad_revenue / total_ml_ad_spend, 2) if total_ml_ad_spend > 0 else None
    kpi_ml_acos = round(total_ml_ad_spend / total_ml_ad_revenue * 100, 2) if total_ml_ad_revenue > 0 else None
    total_ml_clicks = sum(r.get("ml_ad_clicks") or 0 for r in brand_rows)
    kpi_ml_cpc = round(total_ml_ad_spend / total_ml_clicks, 4) if total_ml_clicks > 0 else None
    kpi_ml_total_cost_pct = round((total_ml_ad_spend + total_ml_shipping) / total_ml_gmv * 100, 2) if total_ml_gmv > 0 else None

    total_sh_gmv = sum(_float(r.get("shopee_gmv") or 0) for r in brand_rows)
    total_sh_settlement = sum(_float(r.get("shopee_settlement") or 0) for r in brand_rows)
    total_sh_fees = sum(_float(r.get("shopee_fees") or 0) for r in brand_rows)
    total_sh_ad_spend = sum(_float(r.get("shopee_ad_spend") or 0) for r in brand_rows)
    total_sh_ad_revenue = sum(_float(r.get("shopee_ad_revenue") or 0) for r in brand_rows)
    kpi_sh_fee_pct = round(total_sh_fees / total_sh_gmv * 100, 2) if total_sh_gmv > 0 else None
    kpi_sh_settlement_pct = round(total_sh_settlement / total_sh_gmv * 100, 2) if total_sh_gmv > 0 else None
    kpi_sh_roas = round(total_sh_ad_revenue / total_sh_ad_spend, 2) if total_sh_ad_spend > 0 else None

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_gmv": total_tk_gmv or None,
            "tiktok_settlement": total_tk_settlement or None,
            "tiktok_fees": total_tk_fees or None,
            "tiktok_avg_fee_pct": kpi_tk_fee_pct,
            "tiktok_avg_settlement_pct": kpi_tk_settlement_pct,
            "ml_gmv": total_ml_gmv or None,
            "ml_ad_spend": total_ml_ad_spend or None,
            "ml_ad_revenue": total_ml_ad_revenue or None,
            "ml_roas": kpi_ml_roas,
            "ml_acos_pct": kpi_ml_acos,
            "ml_cpc": kpi_ml_cpc,
            "ml_total_cost_pct": kpi_ml_total_cost_pct,
            "shopee_gmv": total_sh_gmv or None,
            "shopee_settlement": total_sh_settlement or None,
            "shopee_fees": total_sh_fees or None,
            "shopee_avg_fee_pct": kpi_sh_fee_pct,
            "shopee_avg_settlement_pct": kpi_sh_settlement_pct,
            "shopee_ad_spend": total_sh_ad_spend or None,
            "shopee_roas": kpi_sh_roas,
        },
        "brands": brand_rows,
    }


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

def get_quality(db: Session, marketplace: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)

    tk_rows: list[dict] = []
    if marketplace in ("all", "tiktok"):
        sql = f"""
            SELECT brand,
                   COALESCE(SUM(orders), 0)           AS orders,
                   CASE WHEN SUM(orders) > 0
                        THEN SUM(problem_rate * orders) / SUM(orders)
                        ELSE NULL END                  AS problem_rate,
                   CASE WHEN SUM(orders) > 0
                        THEN SUM(avg_delivery_hours * orders) / SUM(orders)
                        ELSE NULL END                  AS avg_delivery_hours
            FROM gold.tiktok_brand_daily
            WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
              AND date BETWEEN '{start}' AND '{end}'
            GROUP BY brand
        """
        tk_rows = _query(db, sql)

    ml_rows: list[dict] = []
    if marketplace in ("all", "ml"):
        sql = f"""
            SELECT brand,
                   COALESCE(SUM(cancelled_orders), 0)                              AS cancelled_orders,
                   COALESCE(SUM(total_orders), 0)                                  AS total_orders,
                   COALESCE(SUM(total_shipments), 0)                               AS total_shipments,
                   COALESCE(SUM(delivered_shipments), 0)                           AS delivered_shipments,
                   GREATEST(COALESCE(SUM(total_shipments), 0)
                            - COALESCE(SUM(delivered_shipments), 0), 0)            AS not_delivered_shipments,
                   CASE WHEN SUM(delivered_shipments) > 0
                        THEN SUM(avg_delivery_days * delivered_shipments) / SUM(delivered_shipments)
                        ELSE NULL END                                               AS avg_delivery_days
            FROM gold.ml_gestao_diaria
            WHERE brand IN {_fmt_list(ML_BRANDS)}
              AND ref_date BETWEEN '{start}' AND '{end}'
            GROUP BY brand
        """
        ml_rows = _query(db, sql)

    ml_mensal_rows: list[dict] = []
    if marketplace in ("all", "ml"):
        sql_mensal = f"""
            SELECT brand,
                   repeat_buyer_rate_pct,
                   gmv_per_buyer,
                   gmv_mom_pct,
                   COALESCE(new_buyers, 0)    AS new_buyers,
                   COALESCE(unique_buyers, 0) AS unique_buyers,
                   shipping_pct_of_gmv
            FROM gold.ml_gestao_mensal
            WHERE brand IN {_fmt_list(ML_BRANDS)}
              AND ref_month = '{year}-{month:02d}-01'
        """
        ml_mensal_rows = _query(db, sql_mensal)
    sh_rows: list[dict] = []
    if marketplace in ("all", "shopee"):
        sql = f"""
            SELECT brand_key                                                     AS brand,
                   SUM(orders)                                                   AS orders,
                   SUM(canceled_orders)                                          AS canceled_orders,
                   SUM(returned_orders)                                          AS returned_orders,
                   CASE WHEN SUM(orders) + SUM(canceled_orders) > 0
                        THEN SUM(canceled_orders)::numeric / (SUM(orders) + SUM(canceled_orders)) * 100
                        ELSE NULL END                                            AS cancel_rate_pct,
                   CASE WHEN SUM(orders) > 0
                        THEN SUM(returned_orders)::numeric / SUM(orders) * 100
                        ELSE NULL END                                            AS return_rate_pct
            FROM marts.fact_marketplace_daily_performance
            JOIN marts.dim_loja USING (loja_id)
            WHERE marketplace_id = {SHOPEE_MARKETPLACE_ID}
              AND brand_key IN {_fmt_list(SHOPEE_BRANDS)}
              AND date BETWEEN '{start}' AND '{end}'
            GROUP BY brand_key
        """
        try:
            sh_rows = _query(db, sql)
        except Exception:
            sh_rows = []

    ml_mensal_by_brand = {r["brand"]: r for r in ml_mensal_rows}

    tk_by_brand = {r["brand"]: r for r in tk_rows}
    ml_by_brand = {r["brand"]: r for r in ml_rows}
    sh_by_brand = {r["brand"]: r for r in sh_rows}

    if marketplace in ("all", "tiktok"):
        brands_set = BRANDS_IN_SCOPE
    elif marketplace == "shopee":
        brands_set = SHOPEE_BRANDS
    else:
        brands_set = ML_BRANDS

    brand_rows = []
    for brand in brands_set:
        tk = tk_by_brand.get(brand, {})
        ml = ml_by_brand.get(brand, {})
        mm = ml_mensal_by_brand.get(brand, {})
        sh = sh_by_brand.get(brand, {})

        if not tk and not ml and not sh:
            continue

        tk_orders = int(_float(tk.get("orders", 0)))
        tk_problem_rate = round(_float(tk.get("problem_rate") or 0), 2) or None
        tk_avg_hours = _float(tk.get("avg_delivery_hours") or 0) or None
        tk_avg_days = round(tk_avg_hours / 24, 1) if tk_avg_hours else None

        ml_cancelled = int(_float(ml.get("cancelled_orders", 0)))
        ml_total = int(_float(ml.get("total_orders", 0)))
        ml_total_ship = int(_float(ml.get("total_shipments", 0)))
        ml_not_delivered = int(_float(ml.get("not_delivered_shipments", 0)))
        ml_cancel_rate = round(ml_cancelled / ml_total * 100, 2) if ml_total > 0 else None
        ml_nd_rate = round(ml_not_delivered / ml_total_ship * 100, 2) if ml_total_ship > 0 else None
        ml_avg_del = _float(ml.get("avg_delivery_days") or 0) or None

        sh_orders_n = int(_float(sh.get("orders", 0)))
        sh_canceled_n = int(_float(sh.get("canceled_orders", 0)))
        sh_returned_n = int(_float(sh.get("returned_orders", 0)))

        brand_rows.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_orders": tk_orders if tk else None,
            "tiktok_canceled": None,
            "tiktok_refunded": None,
            "tiktok_returned": None,
            "tiktok_problem_rate": tk_problem_rate if tk else None,
            "tiktok_cancel_rate": None,
            "tiktok_avg_delivery_days": tk_avg_days if tk else None,
            "ml_cancel_rate_pct": ml_cancel_rate if ml else None,
            "ml_not_delivered_rate_pct": ml_nd_rate if ml else None,
            "ml_cancelled_orders": ml_cancelled if ml else None,
            "ml_total_orders": ml_total if ml else None,
            "ml_not_delivered_shipments": ml_not_delivered if ml else None,
            "ml_avg_delivery_days": round(ml_avg_del, 1) if ml_avg_del else None,
            "ml_repeat_buyer_rate_pct": round(_float(mm.get("repeat_buyer_rate_pct") or 0), 1) or None,
            "ml_gmv_per_buyer": round(_float(mm.get("gmv_per_buyer") or 0), 2) or None,
            "ml_gmv_mom_pct": round(_float(mm.get("gmv_mom_pct") or 0), 1) or None,
            "ml_new_buyers": int(_float(mm.get("new_buyers", 0))) or None,
            "ml_unique_buyers": int(_float(mm.get("unique_buyers", 0))) or None,
            "ml_shipping_pct_of_gmv": round(_float(mm.get("shipping_pct_of_gmv") or 0), 1) or None,
            "shopee_orders": sh_orders_n if sh else None,
            "shopee_canceled_orders": sh_canceled_n if sh else None,
            "shopee_returned_orders": sh_returned_n if sh else None,
            "shopee_cancel_rate_pct": round(_float(sh.get("cancel_rate_pct") or 0), 2) or None if sh else None,
            "shopee_return_rate_pct": round(_float(sh.get("return_rate_pct") or 0), 2) or None if sh else None,
        })

    all_tk_orders = sum(r["tiktok_orders"] or 0 for r in brand_rows if r.get("tiktok_orders") is not None)
    tk_rate_pairs = [
        (r["tiktok_problem_rate"], r["tiktok_orders"] or 0)
        for r in brand_rows
        if r.get("tiktok_problem_rate") is not None
    ]
    if tk_rate_pairs:
        total_w = sum(w for _, w in tk_rate_pairs)
        kpi_tk_problem_rate = round(sum(rate * w for rate, w in tk_rate_pairs) / total_w, 2) if total_w > 0 else None
    else:
        kpi_tk_problem_rate = None

    tk_del_pairs = [(r["tiktok_avg_delivery_days"], r["tiktok_orders"] or 0)
                    for r in brand_rows if r.get("tiktok_avg_delivery_days") is not None]
    kpi_tk_avg_del = None
    if tk_del_pairs:
        total_w = sum(w for _, w in tk_del_pairs)
        kpi_tk_avg_del = round(sum(d * w for d, w in tk_del_pairs) / total_w, 1) if total_w > 0 else None

    all_ml_cancelled = sum(r["ml_cancelled_orders"] or 0 for r in brand_rows if r.get("ml_cancelled_orders") is not None)
    all_ml_total = sum(r["ml_total_orders"] or 0 for r in brand_rows if r.get("ml_total_orders") is not None)
    kpi_ml_cancel = round(all_ml_cancelled / all_ml_total * 100, 2) if all_ml_total > 0 else None

    all_ml_not_del = sum(r["ml_not_delivered_shipments"] or 0 for r in brand_rows if r.get("ml_not_delivered_shipments") is not None)
    all_ml_total_ship = sum(int(_float(r.get("total_shipments", 0))) for r in ml_rows)
    kpi_ml_nd = round(all_ml_not_del / all_ml_total_ship * 100, 2) if all_ml_total_ship > 0 else None

    ml_del_pairs = [(r["ml_avg_delivery_days"], r["ml_total_orders"] or 0)
                    for r in brand_rows if r.get("ml_avg_delivery_days") is not None]
    kpi_ml_avg_del = None
    if ml_del_pairs:
        total_w = sum(w for _, w in ml_del_pairs)
        kpi_ml_avg_del = round(sum(d * w for d, w in ml_del_pairs) / total_w, 1) if total_w > 0 else None

    sh_total_orders = sum(r.get("shopee_orders") or 0 for r in brand_rows if r.get("shopee_orders") is not None)
    sh_total_canceled = sum(r.get("shopee_canceled_orders") or 0 for r in brand_rows if r.get("shopee_canceled_orders") is not None)
    sh_total_returned = sum(r.get("shopee_returned_orders") or 0 for r in brand_rows if r.get("shopee_returned_orders") is not None)
    kpi_sh_cancel = round(sh_total_canceled / (sh_total_orders + sh_total_canceled) * 100, 2) if (sh_total_orders + sh_total_canceled) > 0 else None
    kpi_sh_return = round(sh_total_returned / sh_total_orders * 100, 2) if sh_total_orders > 0 else None

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_problem_rate": kpi_tk_problem_rate,
            "tiktok_cancel_rate": None,
            "tiktok_avg_delivery_days": kpi_tk_avg_del,
            "ml_cancel_rate_pct": kpi_ml_cancel,
            "ml_not_delivered_rate_pct": kpi_ml_nd,
            "ml_avg_delivery_days": kpi_ml_avg_del,
            "shopee_cancel_rate_pct": kpi_sh_cancel,
            "shopee_return_rate_pct": kpi_sh_return,
        },
        "brands": brand_rows,
    }


# ---------------------------------------------------------------------------
# Tempo Real
# ---------------------------------------------------------------------------

def get_tempo_real(db: Session) -> dict:
    sql_today = f"""
        SELECT brand, hour_brt,
               COALESCE(gmv_hour, 0)            AS gmv_hour,
               COALESCE(gmv_acumulado, 0)        AS gmv_acumulado,
               gmv_hour_prior,
               gmv_acumulado_prior,
               COALESCE(customers_hour, 0)       AS customers_hour,
               COALESCE(customers_acumulado, 0)  AS customers_acumulado,
               conversion_hour,
               ticket_medio
        FROM gold.tiktok_shop_hourly
        WHERE date_brt = CURRENT_DATE
          AND brand IN {_fmt_list(BRANDS_IN_SCOPE)}
        ORDER BY brand, hour_brt
    """
    sql_avg7 = f"""
        SELECT brand, hour_brt,
               ROUND(AVG(gmv_hour)::numeric, 2) AS gmv_avg7d
        FROM gold.tiktok_shop_hourly
        WHERE date_brt BETWEEN CURRENT_DATE - 7 AND CURRENT_DATE - 1
          AND brand IN {_fmt_list(BRANDS_IN_SCOPE)}
        GROUP BY brand, hour_brt
        ORDER BY brand, hour_brt
    """

    today_rows = _query(db, sql_today)
    avg_rows = _query(db, sql_avg7)

    avg_idx: dict[tuple, float] = {}
    for r in avg_rows:
        avg_idx[(r["brand"], int(_float(r["hour_brt"])))] = _float(r["gmv_avg7d"])

    by_brand: dict[str, list] = {}
    for r in today_rows:
        b = r["brand"]
        if b not in by_brand:
            by_brand[b] = []
        hour = int(_float(r["hour_brt"]))
        by_brand[b].append({
            "hour": hour,
            "gmv_hour": _float(r["gmv_hour"]),
            "gmv_hour_prior": _float(r["gmv_hour_prior"]) if r.get("gmv_hour_prior") is not None else None,
            "gmv_avg7d": avg_idx.get((b, hour)),
            "customers_hour": int(_float(r["customers_hour"])),
            "customers_acumulado": int(_float(r["customers_acumulado"])),
            "conversion_hour": round(_float(r["conversion_hour"]) * 100, 1) if r.get("conversion_hour") is not None else None,
            "ticket_medio": round(_float(r["ticket_medio"]), 2) if r.get("ticket_medio") is not None else None,
        })

    for brand, rows in by_brand.items():
        rows.sort(key=lambda x: x["hour"])
        cumul, cumul_prior = 0.0, 0.0
        for row in rows:
            cumul += row["gmv_hour"]
            prior = row.get("gmv_hour_prior") or 0.0
            cumul_prior += prior
            row["gmv_acumulado"] = round(cumul, 2)
            row["gmv_acumulado_prior"] = round(cumul_prior, 2) if cumul_prior > 0 else None

    brands_result = []
    for brand in BRANDS_IN_SCOPE:
        hours = by_brand.get(brand, [])
        if not hours:
            continue
        active_hours = [h for h in hours if h["gmv_hour"] > 0]
        last_active = active_hours[-1] if active_hours else hours[-1]
        latest = hours[-1]
        gmv_hoje = latest["gmv_acumulado"]
        gmv_ontem_cumul = latest.get("gmv_acumulado_prior")
        delta_pct = round((gmv_hoje - gmv_ontem_cumul) / gmv_ontem_cumul * 100, 1) if gmv_ontem_cumul else None
        n_active = len(active_hours)
        ritmo_projetado = round(gmv_hoje / n_active * 24, 0) if n_active > 0 else None
        brands_result.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "gmv_hoje": gmv_hoje,
            "gmv_ontem": gmv_ontem_cumul,
            "delta_pct": delta_pct,
            "ritmo_projetado": ritmo_projetado,
            "clientes_hoje": latest["customers_acumulado"],
            "ultima_hora": last_active["hour"],
            "conversion_hora": last_active.get("conversion_hour"),
            "ticket_medio": last_active.get("ticket_medio"),
            "hours": hours,
        })

    total_hoje = sum(b["gmv_hoje"] for b in brands_result)
    total_ontem = sum((b["gmv_ontem"] or 0) for b in brands_result)
    total_delta = round((total_hoje - total_ontem) / total_ontem * 100, 1) if total_ontem else None
    total_ritmo = sum(b["ritmo_projetado"] or 0 for b in brands_result) or None

    return {
        "total_gmv_hoje": total_hoje,
        "total_gmv_ontem": total_ontem or None,
        "total_delta_pct": total_delta,
        "total_ritmo_projetado": total_ritmo,
        "brands": brands_result,
    }


# ---------------------------------------------------------------------------
# Diagnóstico — raw.tiktok_shop_orders vs gold.tiktok_shop_hourly
# ---------------------------------------------------------------------------

def diagnose_raw_tempo_real(db: Session) -> dict:
    # 1. Columns of raw.tiktok_shop_orders
    sql_schema = """
        SELECT column_name, data_type, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = 'raw'
          AND table_name   = 'tiktok_shop_orders'
        ORDER BY ordinal_position
    """
    columns = _query(db, sql_schema)

    # 2. Sample row to see actual values
    sql_sample = """
        SELECT * FROM raw.tiktok_shop_orders LIMIT 1
    """
    try:
        sample = _query(db, sql_sample)
        sample_row = {k: str(v) for k, v in (sample[0] if sample else {}).items()}
    except Exception as e:
        sample_row = {"error": str(e)}

    # 3. Freshness of gold.tiktok_shop_hourly (latest hour we have today)
    sql_gold_fresh = f"""
        SELECT brand,
               MAX(hour_brt) AS max_hour,
               MAX(date_brt) AS max_date_brt
        FROM gold.tiktok_shop_hourly
        WHERE date_brt >= CURRENT_DATE - 1
          AND brand IN {_fmt_list(BRANDS_IN_SCOPE)}
        GROUP BY brand
        ORDER BY brand
    """
    gold_freshness = _query(db, sql_gold_fresh)

    # 4. Guess timestamp columns in raw table and check max value
    ts_candidates = [c["column_name"] for c in columns
                     if any(kw in c["column_name"].lower()
                            for kw in ("time", "date", "created", "updated", "at"))]
    ts_max_results = {}
    for col in ts_candidates[:5]:  # check at most 5 candidates
        try:
            row = _query(db, f"""
                SELECT MAX("{col}") AS max_val
                FROM raw.tiktok_shop_orders
                WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
            """)
            ts_max_results[col] = str(row[0]["max_val"]) if row else None
        except Exception as e:
            ts_max_results[col] = f"error: {e}"

    return {
        "raw_tiktok_shop_orders": {
            "columns": [{"name": c["column_name"], "type": c["data_type"]} for c in columns],
            "sample_row": sample_row,
            "timestamp_cols_max": ts_max_results,
        },
        "gold_tiktok_shop_hourly_freshness": [
            {"brand": r["brand"], "max_hour_today": r["max_hour"], "max_date": str(r["max_date_brt"])}
            for r in gold_freshness
        ],
    }


# ---------------------------------------------------------------------------
# Brand Detail
# ---------------------------------------------------------------------------

def get_brand_detail(db: Session, brand: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)

    sql_monthly = f"""
        SELECT
            COALESCE(SUM(gmv), 0)    AS gmv,
            COALESCE(SUM(orders), 0) AS orders,
            COALESCE(SUM(CASE WHEN visitors > 0 THEN customers END), 0) AS customers,
            CASE WHEN SUM(CASE WHEN visitors > 0 THEN visitors END) > 0
                 THEN SUM(CASE WHEN visitors > 0 THEN customers END) * 100.0
                      / SUM(CASE WHEN visitors > 0 THEN visitors END)
                 ELSE NULL END                        AS cvr_pct,
            CASE WHEN SUM(gmv) > 0
                 THEN ABS(SUM(total_fees)) / SUM(gmv) * 100
                 ELSE NULL END                        AS cos_pct,
            CASE WHEN SUM(gmv) > 0
                 THEN SUM(gmv_video) / SUM(gmv) * 100 ELSE NULL END AS pct_video,
            CASE WHEN SUM(gmv) > 0
                 THEN SUM(gmv_live)  / SUM(gmv) * 100 ELSE NULL END AS pct_live,
            CASE WHEN SUM(gmv) > 0
                 THEN SUM(gmv_card)  / SUM(gmv) * 100 ELSE NULL END AS pct_card,
            COALESCE(SUM(active_videos), 0)          AS active_videos,
            COALESCE(SUM(new_videos_posted), 0)      AS new_videos_posted,
            COALESCE(SUM(active_video_creators), 0)  AS active_video_creators,
            COALESCE(SUM(total_views), 0)            AS total_views,
            COALESCE(SUM(total_lives), 0)            AS total_lives,
            COALESCE(SUM(live_creators), 0)          AS live_creators,
            CASE WHEN SUM(total_views) > 0
                 THEN SUM(gmv) / SUM(total_views) * 1000 ELSE NULL END AS gpm,
            CASE WHEN SUM(new_videos_posted) > 0
                 THEN SUM(gmv_video) / SUM(new_videos_posted) ELSE NULL END AS gmv_per_video,
            CASE WHEN SUM(active_video_creators) > 0
                 THEN SUM(gmv_video) / SUM(active_video_creators) ELSE NULL END AS gmv_per_creator,
            CASE WHEN SUM(total_lives) > 0
                 THEN SUM(gmv_live)  / SUM(total_lives)  ELSE NULL END AS gmv_per_live,
            CASE WHEN SUM(active_video_creators) > 0
                 THEN SUM(active_videos) * 1.0 / SUM(active_video_creators) ELSE NULL END AS videos_per_creator,
            COALESCE(SUM(fresh_videos), 0)           AS fresh_videos,
            COALESCE(SUM(evergreen_videos), 0)       AS evergreen_videos,
            COALESCE(SUM(gmv_fresh), 0)              AS gmv_fresh,
            COALESCE(SUM(gmv_evergreen), 0)          AS gmv_evergreen,
            CASE WHEN SUM(gmv) > 0
                 THEN SUM(gmv_fresh) / SUM(gmv) * 100 ELSE NULL END AS pct_gmv_fresh,
            CASE WHEN SUM(viewers_views_weighted) > 0
                 THEN SUM(viewers_pct_female * viewers_views_weighted) / SUM(viewers_views_weighted)
                 ELSE NULL END AS viewers_pct_female,
            CASE WHEN SUM(viewers_views_weighted) > 0
                 THEN SUM(viewers_pct_male * viewers_views_weighted) / SUM(viewers_views_weighted)
                 ELSE NULL END AS viewers_pct_male,
            CASE WHEN SUM(viewers_views_weighted) > 0
                 THEN SUM(viewers_pct_age_18_24 * viewers_views_weighted) / SUM(viewers_views_weighted)
                 ELSE NULL END AS viewers_pct_18_24,
            CASE WHEN SUM(viewers_views_weighted) > 0
                 THEN SUM(viewers_pct_age_25_34 * viewers_views_weighted) / SUM(viewers_views_weighted)
                 ELSE NULL END AS viewers_pct_25_34,
            CASE WHEN SUM(viewers_views_weighted) > 0
                 THEN SUM(viewers_pct_age_35_44 * viewers_views_weighted) / SUM(viewers_views_weighted)
                 ELSE NULL END AS viewers_pct_35_44,
            CASE WHEN SUM(viewers_views_weighted) > 0
                 THEN SUM(viewers_pct_age_45_54 * viewers_views_weighted) / SUM(viewers_views_weighted)
                 ELSE NULL END AS viewers_pct_45_54,
            CASE WHEN SUM(viewers_views_weighted) > 0
                 THEN SUM(viewers_pct_age_55_plus * viewers_views_weighted) / SUM(viewers_views_weighted)
                 ELSE NULL END AS viewers_pct_55_plus,
            CASE WHEN SUM(followers_views_weighted) > 0
                 THEN SUM(followers_pct_female * followers_views_weighted) / SUM(followers_views_weighted)
                 ELSE NULL END AS followers_pct_female,
            CASE WHEN SUM(followers_views_weighted) > 0
                 THEN SUM(followers_pct_male * followers_views_weighted) / SUM(followers_views_weighted)
                 ELSE NULL END AS followers_pct_male,
            CASE WHEN SUM(followers_views_weighted) > 0
                 THEN SUM(followers_pct_age_18_24 * followers_views_weighted) / SUM(followers_views_weighted)
                 ELSE NULL END AS followers_pct_18_24,
            CASE WHEN SUM(followers_views_weighted) > 0
                 THEN SUM(followers_pct_age_25_34 * followers_views_weighted) / SUM(followers_views_weighted)
                 ELSE NULL END AS followers_pct_25_34,
            CASE WHEN SUM(followers_views_weighted) > 0
                 THEN SUM(followers_pct_age_35_44 * followers_views_weighted) / SUM(followers_views_weighted)
                 ELSE NULL END AS followers_pct_35_44,
            CASE WHEN SUM(followers_views_weighted) > 0
                 THEN SUM(followers_pct_age_45_54 * followers_views_weighted) / SUM(followers_views_weighted)
                 ELSE NULL END AS followers_pct_45_54,
            CASE WHEN SUM(followers_views_weighted) > 0
                 THEN SUM(followers_pct_age_55_plus * followers_views_weighted) / SUM(followers_views_weighted)
                 ELSE NULL END AS followers_pct_55_plus
        FROM gold.tiktok_brand_daily
        WHERE brand = '{brand}'
          AND date BETWEEN '{start}' AND '{end}'
    """

    sql_daily = f"""
        SELECT date::text                        AS date,
               COALESCE(gmv, 0)                 AS gmv,
               COALESCE(gmv_video, 0)           AS gmv_video,
               COALESCE(gmv_live, 0)            AS gmv_live,
               COALESCE(gmv_card, 0)            AS gmv_card,
               COALESCE(new_videos_posted, 0)   AS new_videos_posted
        FROM gold.tiktok_brand_daily
        WHERE brand = '{brand}'
          AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date
    """

    sql_creators = f"""
        SELECT creator,
               SUM(gmv_total)    AS gmv,
               SUM(videos_count) AS videos,
               SUM(lives_count)  AS lives
        FROM gold.tiktok_creator_daily
        WHERE brand = '{brand}'
          AND date BETWEEN '{start}' AND '{end}'
        GROUP BY creator
        ORDER BY SUM(gmv_total) DESC
        LIMIT 5
    """

    sql_products = f"""
        SELECT product_id, product_name,
               SUM(gmv)           AS gmv,
               SUM(orders)        AS orders,
               SUM(active_videos) AS videos,
               CASE WHEN SUM(video_views) > 0
                    THEN SUM(gmv) / SUM(video_views) * 1000
                    ELSE NULL END  AS gpm
        FROM gold.tiktok_product_daily
        WHERE brand = '{brand}'
          AND date BETWEEN '{start}' AND '{end}'
        GROUP BY product_id, product_name
        ORDER BY SUM(gmv) DESC
        LIMIT 5
    """

    sql_channel_funnel = f"""
        SELECT channel,
               SUM(impressions)  AS impressions,
               SUM(page_views)   AS page_views,
               SUM(items_sold)   AS items_sold,
               SUM(gmv)          AS gmv,
               CASE WHEN SUM(impressions) > 0
                    THEN SUM(page_views)::numeric / SUM(impressions) * 100
                    ELSE NULL END AS ctr_pct,
               CASE WHEN SUM(page_views) > 0
                    THEN SUM(items_sold)::numeric / SUM(page_views) * 100
                    ELSE NULL END AS cvr_pct
        FROM gold.v_channel_efficiency
        WHERE brand = '{brand}'
          AND date BETWEEN '{start}' AND '{end}'
        GROUP BY channel
        ORDER BY channel
    """

    monthly_rows = _query(db, sql_monthly)
    daily_rows = _query(db, sql_daily)
    creators = _query(db, sql_creators)
    products = _query(db, sql_products)
    channel_funnel_rows = _query(db, sql_channel_funnel)

    m = monthly_rows[0] if monthly_rows else {}

    def _r(v, decimals=2):
        f = _float(v)
        return round(f, decimals) if v is not None and f != 0 else None

    return {
        "brand": brand,
        "label": BRAND_LABELS.get(brand, brand.upper()),
        "ref_month": f"{year:04d}-{month:02d}",
        "gmv": _float(m.get("gmv", 0)),
        "orders": int(_float(m.get("orders", 0))),
        "customers": int(_float(m.get("customers", 0))),
        "cvr_pct": _r(m.get("cvr_pct")),
        "cos_pct": _r(m.get("cos_pct")),
        "pct_video": _r(m.get("pct_video"), 1),
        "pct_live": _r(m.get("pct_live"), 1),
        "pct_card": _r(m.get("pct_card"), 1),
        "active_videos": int(_float(m.get("active_videos", 0))),
        "new_videos_posted": int(_float(m.get("new_videos_posted", 0))),
        "active_video_creators": int(_float(m.get("active_video_creators", 0))),
        "total_views": int(_float(m.get("total_views", 0))),
        "total_lives": int(_float(m.get("total_lives", 0))),
        "live_creators": int(_float(m.get("live_creators", 0))),
        "gpm": _r(m.get("gpm")),
        "gmv_per_video": _r(m.get("gmv_per_video")),
        "gmv_per_creator": _r(m.get("gmv_per_creator")),
        "gmv_per_live": _r(m.get("gmv_per_live")),
        "videos_per_creator": _r(m.get("videos_per_creator"), 1),
        "fresh_videos": int(_float(m.get("fresh_videos", 0))),
        "evergreen_videos": int(_float(m.get("evergreen_videos", 0))),
        "gmv_fresh": _float(m.get("gmv_fresh", 0)),
        "gmv_evergreen": _float(m.get("gmv_evergreen", 0)),
        "pct_gmv_fresh": _r(m.get("pct_gmv_fresh"), 1),
        "viewers_pct_female": _r(m.get("viewers_pct_female"), 1),
        "viewers_pct_male": _r(m.get("viewers_pct_male"), 1),
        "viewers_pct_18_24": _r(m.get("viewers_pct_18_24"), 1),
        "viewers_pct_25_34": _r(m.get("viewers_pct_25_34"), 1),
        "viewers_pct_35_44": _r(m.get("viewers_pct_35_44"), 1),
        "viewers_pct_45_54": _r(m.get("viewers_pct_45_54"), 1),
        "viewers_pct_55_plus": _r(m.get("viewers_pct_55_plus"), 1),
        "followers_pct_female": _r(m.get("followers_pct_female"), 1),
        "followers_pct_male": _r(m.get("followers_pct_male"), 1),
        "followers_pct_18_24": _r(m.get("followers_pct_18_24"), 1),
        "followers_pct_25_34": _r(m.get("followers_pct_25_34"), 1),
        "followers_pct_35_44": _r(m.get("followers_pct_35_44"), 1),
        "followers_pct_45_54": _r(m.get("followers_pct_45_54"), 1),
        "followers_pct_55_plus": _r(m.get("followers_pct_55_plus"), 1),
        "channel_funnel": [
            {
                "channel": r["channel"],
                "label": {"VIDEO": "Video", "LIVE": "Live", "PRODUCT_CARD": "Card"}.get(
                    str(r["channel"]), str(r["channel"])
                ),
                "impressions": int(_float(r["impressions"])),
                "page_views": int(_float(r["page_views"])),
                "items_sold": int(_float(r["items_sold"])),
                "gmv": _float(r["gmv"]),
                "ctr_pct": _r(r.get("ctr_pct")),
                "cvr_pct": _r(r.get("cvr_pct")),
            }
            for r in channel_funnel_rows
        ],
        "daily": [
            {
                "date": str(r["date"])[:10],
                "gmv": _float(r["gmv"]) or None,
                "gmv_video": _float(r["gmv_video"]) or None,
                "gmv_live": _float(r["gmv_live"]) or None,
                "gmv_card": _float(r["gmv_card"]) or None,
                "new_videos_posted": int(_float(r["new_videos_posted"])) or None,
            }
            for r in daily_rows
        ],
        "top_creators": [
            {
                "creator": r.get("creator") or "—",
                "gmv": _float(r["gmv"]),
                "videos": int(_float(r.get("videos", 0))),
                "lives": int(_float(r.get("lives", 0))),
            }
            for r in creators
        ],
        "top_produtos": [
            {
                "product_id": str(r.get("product_id") or ""),
                "product_name": r.get("product_name") or "—",
                "gmv": _float(r["gmv"]),
                "orders": int(_float(r["orders"])),
                "videos": int(_float(r.get("videos", 0))),
                "gpm": _r(r.get("gpm")),
            }
            for r in products
        ],
    }


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------

def get_pedidos(db: Session, days_back: int = 30) -> dict:
    end = date.today()
    start = end - timedelta(days=days_back - 1)

    sql_tk_kpis = f"""
        SELECT
            COALESCE(SUM(orders), 0)            AS orders,
            COALESCE(SUM(canceled), 0)          AS canceled,
            COALESCE(SUM(delivered_orders), 0)  AS delivered,
            COALESCE(SUM(gmv), 0)               AS gmv
        FROM gold.tiktok_brand_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date BETWEEN '{start}' AND '{end}'
    """

    sql_ml_kpis = f"""
        SELECT
            COALESCE(SUM(total_orders), 0)        AS total_orders,
            COALESCE(SUM(cancelled_orders), 0)    AS cancelled_orders,
            COALESCE(SUM(delivered_shipments), 0) AS delivered,
            COALESCE(SUM(gmv), 0)                 AS gmv
        FROM gold.ml_gestao_diaria
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND ref_date BETWEEN '{start}' AND '{end}'
    """

    sql_tk_daily = f"""
        SELECT date::text                          AS date,
               COALESCE(SUM(orders), 0)           AS orders,
               COALESCE(SUM(canceled), 0)         AS canceled,
               COALESCE(SUM(gmv), 0)              AS gmv
        FROM gold.tiktok_brand_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date BETWEEN '{start}' AND '{end}'
        GROUP BY date ORDER BY date
    """

    sql_ml_daily = f"""
        SELECT ref_date::text                         AS date,
               COALESCE(SUM(paid_orders), 0)         AS orders,
               COALESCE(SUM(cancelled_orders), 0)    AS canceled,
               COALESCE(SUM(gmv), 0)                 AS gmv
        FROM gold.ml_gestao_diaria
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND ref_date BETWEEN '{start}' AND '{end}'
        GROUP BY ref_date ORDER BY ref_date
    """

    sql_tk_brand = f"""
        SELECT brand,
               COALESCE(SUM(orders), 0)   AS orders,
               COALESCE(SUM(canceled), 0) AS canceled,
               COALESCE(SUM(gmv), 0)      AS gmv
        FROM gold.tiktok_brand_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date BETWEEN '{start}' AND '{end}'
        GROUP BY brand
    """

    sql_ml_brand = f"""
        SELECT brand,
               COALESCE(SUM(total_orders), 0)     AS total_orders,
               COALESCE(SUM(cancelled_orders), 0) AS canceled,
               COALESCE(SUM(gmv), 0)              AS gmv
        FROM gold.ml_gestao_diaria
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND ref_date BETWEEN '{start}' AND '{end}'
        GROUP BY brand
    """

    tk_kpis = _query(db, sql_tk_kpis)[0]
    ml_kpis = _query(db, sql_ml_kpis)[0]
    tk_daily = _query(db, sql_tk_daily)
    ml_daily = _query(db, sql_ml_daily)
    tk_brand = {r["brand"]: r for r in _query(db, sql_tk_brand)}
    ml_brand = {r["brand"]: r for r in _query(db, sql_ml_brand)}

    tk_orders = int(_float(tk_kpis["orders"]))
    tk_canceled = int(_float(tk_kpis["canceled"]))
    tk_gmv = _float(tk_kpis["gmv"])
    tk_cancel_rate = round(tk_canceled / tk_orders * 100, 2) if tk_orders > 0 else None

    ml_orders = int(_float(ml_kpis["total_orders"]))
    ml_canceled = int(_float(ml_kpis["cancelled_orders"]))
    ml_gmv = _float(ml_kpis["gmv"])
    ml_cancel_rate = round(ml_canceled / ml_orders * 100, 2) if ml_orders > 0 else None

    total_orders = tk_orders + ml_orders
    total_canceled = tk_canceled + ml_canceled
    total_gmv = tk_gmv + ml_gmv
    avg_ticket = round(total_gmv / total_orders, 2) if total_orders > 0 else 0.0
    total_cancel_rate = round(total_canceled / total_orders * 100, 2) if total_orders > 0 else None

    tk_daily_map = {r["date"][:10]: r for r in tk_daily}
    ml_daily_map = {r["date"][:10]: r for r in ml_daily}
    all_dates = sorted(set(list(tk_daily_map.keys()) + list(ml_daily_map.keys())))

    daily_rows = []
    for d in all_dates:
        tk = tk_daily_map.get(d, {})
        ml = ml_daily_map.get(d, {})
        tk_o = int(_float(tk.get("orders", 0)))
        tk_c = int(_float(tk.get("canceled", 0)))
        ml_o = int(_float(ml.get("orders", 0)))
        ml_c = int(_float(ml.get("canceled", 0)))
        daily_rows.append({
            "date": d,
            "tiktok_orders": tk_o,
            "tiktok_canceled": tk_c,
            "ml_orders": ml_o,
            "ml_canceled": ml_c,
            "total_orders": tk_o + ml_o,
            "total_gmv": round(_float(tk.get("gmv", 0)) + _float(ml.get("gmv", 0)), 2),
        })

    brand_rows = []
    for brand in BRANDS_IN_SCOPE:
        tk = tk_brand.get(brand, {})
        ml = ml_brand.get(brand, {}) if brand in ML_BRANDS else {}

        tk_o = int(_float(tk.get("orders", 0))) if tk else None
        tk_c = int(_float(tk.get("canceled", 0))) if tk else None
        tk_g = round(_float(tk.get("gmv", 0)), 2) if tk else None
        tk_cr = round(tk_c / tk_o * 100, 2) if (tk_o and tk_c is not None and tk_o > 0) else None

        ml_o = int(_float(ml.get("total_orders", 0))) if ml else None
        ml_c = int(_float(ml.get("canceled", 0))) if ml else None
        ml_g = round(_float(ml.get("gmv", 0)), 2) if ml else None
        ml_cr = round(ml_c / ml_o * 100, 2) if (ml_o and ml_c is not None and ml_o > 0) else None

        brand_rows.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_orders": tk_o,
            "tiktok_canceled": tk_c,
            "tiktok_cancel_rate_pct": tk_cr,
            "tiktok_gmv": tk_g,
            "ml_orders": ml_o,
            "ml_canceled": ml_c,
            "ml_cancel_rate_pct": ml_cr,
            "ml_gmv": ml_g,
            "total_orders": (tk_o or 0) + (ml_o or 0),
            "total_gmv": round((tk_g or 0) + (ml_g or 0), 2),
        })

    return {
        "days_back": days_back,
        "kpis": {
            "total_orders": total_orders,
            "total_gmv": round(total_gmv, 2),
            "avg_ticket": avg_ticket,
            "cancel_rate_pct": total_cancel_rate,
        },
        "tiktok": {
            "orders": tk_orders,
            "canceled": tk_canceled,
            "gmv": round(tk_gmv, 2),
            "cancel_rate_pct": tk_cancel_rate,
            "delivered": int(_float(tk_kpis.get("delivered", 0))) or None,
        },
        "ml": {
            "orders": ml_orders,
            "canceled": ml_canceled,
            "gmv": round(ml_gmv, 2),
            "cancel_rate_pct": ml_cancel_rate,
            "delivered": int(_float(ml_kpis.get("delivered", 0))) or None,
        },
        "daily": daily_rows,
        "by_brand": brand_rows,
    }


# ---------------------------------------------------------------------------
# Inteligencia
# ---------------------------------------------------------------------------

def get_inteligencia(db: Session) -> dict:
    signals_sql = f"""
        SELECT product_status,
               COUNT(*)                       AS n_products,
               COALESCE(SUM(gross_revenue), 0) AS gmv,
               COALESCE(SUM(ad_spend), 0)      AS ad_spend,
               AVG(CASE WHEN ad_roas > 0 THEN ad_roas END) AS avg_roas
        FROM gold.ml_produto_ranking
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND product_status IS NOT NULL
        GROUP BY product_status
        ORDER BY gmv DESC
    """
    signals_rows = _query(db, signals_sql)
    signals = [
        {
            "product_status": r["product_status"],
            "n_products": int(_float(r["n_products"])),
            "gmv": _float(r["gmv"]),
            "ad_spend": _float(r["ad_spend"]),
            "avg_roas": round(_float(r["avg_roas"]), 2) if r.get("avg_roas") else None,
        }
        for r in signals_rows
    ]

    urgent_sql = f"""
        SELECT brand, title, pareto_bucket, revenue_velocity,
               COALESCE(gross_revenue, 0) AS gmv,
               COALESCE(ad_spend, 0)      AS ad_spend,
               ad_roas, ad_acos_pct, cancel_rate_pct,
               revenue_share_pct, units_sold, days_advertised, ad_efficiency
        FROM gold.ml_produto_ranking
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND product_status = 'ad_spend_no_sales'
        ORDER BY ad_spend DESC
        LIMIT 30
    """
    urgent = [
        {
            "brand": r["brand"],
            "title": r["title"],
            "pareto_bucket": r.get("pareto_bucket"),
            "revenue_velocity": r.get("revenue_velocity"),
            "gmv": _float(r["gmv"]),
            "ad_spend": _float(r["ad_spend"]),
            "ad_roas": round(_float(r["ad_roas"]), 2) if r.get("ad_roas") else None,
            "ad_acos_pct": round(_float(r["ad_acos_pct"]), 2) if r.get("ad_acos_pct") else None,
            "cancel_rate_pct": round(_float(r["cancel_rate_pct"]), 2) if r.get("cancel_rate_pct") else None,
            "revenue_share_pct": round(_float(r["revenue_share_pct"]), 3) if r.get("revenue_share_pct") else None,
            "units_sold": int(_float(r["units_sold"])) if r.get("units_sold") else None,
            "days_advertised": int(_float(r["days_advertised"])) if r.get("days_advertised") else None,
            "ad_efficiency": r.get("ad_efficiency"),
        }
        for r in _query(db, urgent_sql)
    ]

    scale_sql = f"""
        SELECT brand, title, pareto_bucket, revenue_velocity,
               COALESCE(gross_revenue, 0) AS gmv,
               COALESCE(ad_spend, 0)      AS ad_spend,
               ad_roas, ad_acos_pct, cancel_rate_pct,
               revenue_share_pct, units_sold, days_advertised, ad_efficiency
        FROM gold.ml_produto_ranking
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND product_status = 'sells+advertised'
          AND ad_roas >= 8
        ORDER BY ad_roas DESC
        LIMIT 20
    """
    scale = [
        {
            "brand": r["brand"],
            "title": r["title"],
            "pareto_bucket": r.get("pareto_bucket"),
            "revenue_velocity": r.get("revenue_velocity"),
            "gmv": _float(r["gmv"]),
            "ad_spend": _float(r["ad_spend"]),
            "ad_roas": round(_float(r["ad_roas"]), 2) if r.get("ad_roas") else None,
            "ad_acos_pct": round(_float(r["ad_acos_pct"]), 2) if r.get("ad_acos_pct") else None,
            "cancel_rate_pct": round(_float(r["cancel_rate_pct"]), 2) if r.get("cancel_rate_pct") else None,
            "revenue_share_pct": round(_float(r["revenue_share_pct"]), 3) if r.get("revenue_share_pct") else None,
            "units_sold": int(_float(r["units_sold"])) if r.get("units_sold") else None,
            "days_advertised": int(_float(r["days_advertised"])) if r.get("days_advertised") else None,
            "ad_efficiency": r.get("ad_efficiency"),
        }
        for r in _query(db, scale_sql)
    ]

    organic_sql = f"""
        SELECT brand, title, pareto_bucket, revenue_velocity,
               COALESCE(gross_revenue, 0) AS gmv,
               COALESCE(ad_spend, 0)      AS ad_spend,
               ad_roas, ad_acos_pct, cancel_rate_pct,
               revenue_share_pct, units_sold, days_advertised, ad_efficiency,
               unique_buyers
        FROM gold.ml_produto_ranking
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND product_status = 'sells_organic_only'
        ORDER BY gross_revenue DESC
        LIMIT 20
    """
    organic = [
        {
            "brand": r["brand"],
            "title": r["title"],
            "pareto_bucket": r.get("pareto_bucket"),
            "revenue_velocity": r.get("revenue_velocity"),
            "gmv": _float(r["gmv"]),
            "ad_spend": _float(r["ad_spend"]),
            "ad_roas": round(_float(r["ad_roas"]), 2) if r.get("ad_roas") else None,
            "ad_acos_pct": round(_float(r["ad_acos_pct"]), 2) if r.get("ad_acos_pct") else None,
            "cancel_rate_pct": round(_float(r["cancel_rate_pct"]), 2) if r.get("cancel_rate_pct") else None,
            "revenue_share_pct": round(_float(r["revenue_share_pct"]), 3) if r.get("revenue_share_pct") else None,
            "units_sold": int(_float(r["units_sold"])) if r.get("units_sold") else None,
            "days_advertised": int(_float(r["days_advertised"])) if r.get("days_advertised") else None,
            "ad_efficiency": r.get("ad_efficiency"),
        }
        for r in _query(db, organic_sql)
    ]

    pareto_sql = f"""
        SELECT brand, pareto_bucket,
               COUNT(*)                        AS n_products,
               COALESCE(SUM(gross_revenue), 0) AS gmv,
               COALESCE(SUM(ad_spend), 0)      AS ad_spend
        FROM gold.ml_produto_ranking
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND pareto_bucket IS NOT NULL
        GROUP BY brand, pareto_bucket
        ORDER BY brand, pareto_bucket
    """
    pareto = [
        {
            "brand": r["brand"],
            "pareto_bucket": r["pareto_bucket"],
            "n_products": int(_float(r["n_products"])),
            "gmv": _float(r["gmv"]),
            "ad_spend": _float(r["ad_spend"]),
        }
        for r in _query(db, pareto_sql)
    ]

    ltv: list[dict] = []
    try:
        ltv_sql = f"""
            SELECT brand, total_buyers, repeat_buyers, repeat_rate_pct,
                   avg_customer_ltv, vip_buyers, one_and_done_buyers,
                   at_risk_or_churned, overall_roas
            FROM gold.ml_cross_company_summary
            WHERE brand IN {_fmt_list(ML_BRANDS)}
            ORDER BY brand
        """
        ltv = [
            {
                "brand": r["brand"],
                "total_buyers": int(_float(r["total_buyers"])),
                "repeat_buyers": int(_float(r["repeat_buyers"])),
                "repeat_rate_pct": round(_float(r["repeat_rate_pct"]), 2) if r.get("repeat_rate_pct") else None,
                "avg_customer_ltv": round(_float(r["avg_customer_ltv"]), 2) if r.get("avg_customer_ltv") else None,
                "vip_buyers": int(_float(r["vip_buyers"])) if r.get("vip_buyers") else None,
                "one_and_done_buyers": int(_float(r["one_and_done_buyers"])) if r.get("one_and_done_buyers") else None,
                "at_risk_or_churned": int(_float(r["at_risk_or_churned"])) if r.get("at_risk_or_churned") else None,
                "overall_roas": round(_float(r["overall_roas"]), 2) if r.get("overall_roas") else None,
            }
            for r in _query(db, ltv_sql)
        ]
    except Exception:
        ltv = []

    today = date.today()
    tk30_start = today - timedelta(days=30)
    tk_products_sql = f"""
        SELECT brand, product_name,
               SUM(gmv)    AS gmv,
               SUM(orders) AS orders,
               CASE WHEN SUM(gmv) > 0
                    THEN AVG(pct_gmv_video) ELSE NULL END AS avg_pct_video,
               CASE WHEN SUM(gmv) > 0
                    THEN AVG(pct_gmv_live)  ELSE NULL END AS avg_pct_live,
               CASE WHEN SUM(gmv) > 0
                    THEN AVG(pct_gmv_card)  ELSE NULL END AS avg_pct_card,
               AVG(NULLIF(rating_avg, 0)) AS avg_rating
        FROM gold.tiktok_product_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date >= '{tk30_start}'
          AND gmv > 0
        GROUP BY brand, product_name
        ORDER BY SUM(gmv) DESC
        LIMIT 25
    """
    tk_products = [
        {
            "brand": r["brand"],
            "product_name": r["product_name"],
            "gmv": _float(r["gmv"]),
            "orders": int(_float(r["orders"])),
            "avg_pct_video": round(_float(r["avg_pct_video"]), 1) if r.get("avg_pct_video") else None,
            "avg_pct_live": round(_float(r["avg_pct_live"]), 1) if r.get("avg_pct_live") else None,
            "avg_pct_card": round(_float(r["avg_pct_card"]), 1) if r.get("avg_pct_card") else None,
            "avg_rating": round(_float(r["avg_rating"]), 1) if r.get("avg_rating") else None,
        }
        for r in _query(db, tk_products_sql)
    ]

    return {
        "signals": signals,
        "urgent": urgent,
        "scale": scale,
        "organic": organic,
        "pareto": pareto,
        "ltv": ltv,
        "tk_products": tk_products,
    }


# ---------------------------------------------------------------------------
# Operacoes
# ---------------------------------------------------------------------------

def get_operacoes(db: Session) -> dict:
    today = date.today()
    d7_start = today - timedelta(days=7)
    d14_start = today - timedelta(days=14)
    d30_start = today - timedelta(days=30)

    gestao_sql = f"""
        SELECT brand, ref_date,
               COALESCE(ad_spend, 0) AS ad_spend,
               COALESCE(gmv, 0)      AS gmv,
               COALESCE(roas, 0)     AS roas
        FROM gold.ml_gestao_diaria
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND ref_date >= '{d7_start}'
        ORDER BY ref_date DESC
    """
    gestao_rows = _query(db, gestao_sql)

    alertas: list[dict] = []
    for r in gestao_rows:
        spend = _float(r["ad_spend"])
        gmv = _float(r["gmv"])
        roas = _float(r["roas"])
        brand_label = BRAND_LABELS.get(r["brand"], r["brand"])
        ref = str(r["ref_date"])
        if spend > 0 and gmv == 0:
            alertas.append({
                "tipo": "ad_sem_gmv",
                "severidade": "critico",
                "brand": r["brand"],
                "mensagem": f"{brand_label} teve R${spend:,.0f} em ads sem nenhuma venda em {ref}",
                "ad_spend": spend,
                "gmv": gmv,
            })
        elif roas < 3 and spend > 500:
            alertas.append({
                "tipo": "roas_baixo",
                "severidade": "atencao",
                "brand": r["brand"],
                "mensagem": f"{brand_label} com ROAS {roas:.1f}x em {ref} (abaixo de 3x, investimento R${spend:,.0f})",
                "ad_spend": spend,
                "gmv": gmv,
                "roas": round(roas, 2),
            })

    velocity_sql = f"""
        SELECT brand,
               COALESCE(SUM(ad_spend), 0)  AS ad_spend_7d,
               COALESCE(SUM(gmv), 0)        AS gmv_7d,
               COALESCE(SUM(paid_orders), 0) AS orders_7d,
               CASE WHEN SUM(ad_spend) > 0
                    THEN SUM(ad_revenue) / SUM(ad_spend)
                    ELSE NULL END            AS roas_7d
        FROM gold.ml_gestao_diaria
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND ref_date >= '{d7_start}'
        GROUP BY brand
        ORDER BY brand
    """
    ml_velocity = [
        {
            "brand": r["brand"],
            "ad_spend_7d": _float(r["ad_spend_7d"]),
            "gmv_7d": _float(r["gmv_7d"]),
            "orders_7d": int(_float(r["orders_7d"])),
            "roas_7d": round(_float(r["roas_7d"]), 2) if r.get("roas_7d") else None,
        }
        for r in _query(db, velocity_sql)
    ]

    creators_sql = f"""
        SELECT brand, creator,
               COALESCE(SUM(gmv_total), 0)   AS gmv,
               COALESCE(SUM(views_video), 0)  AS views,
               COALESCE(SUM(videos_count), 0) AS videos,
               COALESCE(SUM(lives_count), 0)  AS lives,
               COALESCE(SUM(gmv_video), 0)    AS gmv_video,
               COALESCE(SUM(gmv_live), 0)     AS gmv_live,
               CASE WHEN SUM(views_video) > 0
                    THEN SUM(gmv_video) / SUM(views_video) * 1000
                    ELSE NULL END              AS gpm_video
        FROM gold.tiktok_creator_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date >= '{d7_start}'
        GROUP BY brand, creator
        ORDER BY SUM(gmv_total) DESC
        LIMIT 30
    """
    creators = [
        {
            "brand": r["brand"],
            "creator": r["creator"],
            "gmv": _float(r["gmv"]),
            "views": int(_float(r["views"])),
            "videos": int(_float(r["videos"])),
            "lives": int(_float(r["lives"])),
            "gmv_video": _float(r["gmv_video"]),
            "gmv_live": _float(r["gmv_live"]),
            "gpm_video": round(_float(r["gpm_video"]), 2) if r.get("gpm_video") else None,
        }
        for r in _query(db, creators_sql)
    ]

    lives_sql = f"""
        SELECT brand,
               COUNT(DISTINCT date)           AS days_with_lives,
               SUM(total_lives)               AS total_lives,
               SUM(total_live_minutes)        AS total_minutes,
               COALESCE(SUM(gmv_live), 0)     AS live_gmv,
               COALESCE(SUM(gmv), 0)          AS total_gmv,
               CASE WHEN SUM(gmv) > 0
                    THEN SUM(gmv_live) / SUM(gmv) * 100
                    ELSE NULL END              AS pct_live,
               CASE WHEN SUM(total_lives) > 0
                    THEN SUM(gmv_live) / SUM(total_lives)
                    ELSE NULL END              AS gmv_per_live,
               CASE WHEN SUM(total_live_minutes) > 0
                    THEN SUM(gmv_live) / SUM(total_live_minutes)
                    ELSE NULL END              AS gmv_per_minute
        FROM gold.tiktok_brand_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date >= '{d30_start}'
        GROUP BY brand
        HAVING SUM(total_lives) > 0
        ORDER BY SUM(gmv_live) DESC
    """
    lives = [
        {
            "brand": r["brand"],
            "days_with_lives": int(_float(r["days_with_lives"])),
            "total_lives": int(_float(r["total_lives"])),
            "total_minutes": int(_float(r["total_minutes"])),
            "live_gmv": _float(r["live_gmv"]),
            "total_gmv": _float(r["total_gmv"]),
            "pct_live": round(_float(r["pct_live"]), 1) if r.get("pct_live") else None,
            "gmv_per_live": round(_float(r["gmv_per_live"]), 2) if r.get("gmv_per_live") else None,
            "gmv_per_minute": round(_float(r["gmv_per_minute"]), 2) if r.get("gmv_per_minute") else None,
        }
        for r in _query(db, lives_sql)
    ]

    tk_daily_sql = f"""
        SELECT brand,
               date AS ref_date,
               COALESCE(SUM(gmv), 0)    AS gmv,
               COALESCE(SUM(orders), 0) AS orders
        FROM gold.tiktok_brand_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date >= '{d14_start}'
        GROUP BY brand, date
        ORDER BY date, brand
    """
    tk_daily = [
        {
            "brand": r["brand"],
            "ref_date": str(r["ref_date"]),
            "gmv": _float(r["gmv"]),
            "orders": int(_float(r["orders"])),
        }
        for r in _query(db, tk_daily_sql)
    ]

    return {
        "alertas": alertas,
        "ml_velocity": ml_velocity,
        "creators": creators,
        "lives": lives,
        "tk_daily": tk_daily,
    }










# ---------------------------------------------------------------------------
# Neon runtime overrides
# ---------------------------------------------------------------------------
# Production Render cannot access the VPN-only Data Mart. The public API must
# serve dashboard pages from Neon/Postgres marts populated by the sync job.

_MKT_FILTER_FACT = {
    "all": None,
    "tiktok": 1,
    "ml": 2,
    "shopee": 3,
}


def _fact_mkt_clause(alias: str = "f") -> str:
    return f"(:mkt_id IS NULL OR {alias}.marketplace_id = :mkt_id)"


def _brand_metric(row: dict, key: str) -> float:
    return _float(row.get(key, 0))


def get_overview(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)

    sql = text("""
        SELECT
            COALESCE(SUM(gmv), 0) AS gmv,
            COALESCE(SUM(CASE WHEN marketplace_id = 1 THEN gmv ELSE 0 END), 0) AS tiktok_gmv,
            COALESCE(SUM(CASE WHEN marketplace_id = 2 THEN gmv ELSE 0 END), 0) AS ml_gmv,
            COALESCE(SUM(CASE WHEN marketplace_id = 3 THEN gmv ELSE 0 END), 0) AS shopee_gmv,
            COALESCE(SUM(orders), 0) AS orders,
            CASE WHEN SUM(orders) > 0 THEN SUM(gmv) / SUM(orders) ELSE 0 END AS avg_ticket,
            COALESCE(SUM(ad_spend), 0) AS ad_spend,
            COALESCE(SUM(CASE WHEN marketplace_id = 2 THEN ad_spend ELSE 0 END), 0) AS ml_ad_spend,
            COALESCE(SUM(CASE WHEN marketplace_id = 2 THEN ad_revenue ELSE 0 END), 0) AS ml_ad_revenue,
            COALESCE(SUM(CASE WHEN marketplace_id = 2 THEN canceled_orders ELSE 0 END), 0) AS ml_canceled,
            COALESCE(SUM(CASE WHEN marketplace_id = 2 THEN orders ELSE 0 END), 0) AS ml_orders,
            COALESCE(SUM(CASE WHEN marketplace_id = 1 THEN unique_buyers ELSE 0 END), 0) AS tiktok_customers,
            COALESCE(SUM(CASE WHEN marketplace_id = 2 THEN unique_buyers ELSE 0 END), 0) AS ml_unique_buyers,
            COALESCE(SUM(CASE WHEN marketplace_id = 3 THEN unique_buyers ELSE 0 END), 0) AS shopee_unique_buyers,
            COALESCE(SUM(CASE WHEN marketplace_id = 3 THEN ad_spend ELSE 0 END), 0) AS shopee_ad_spend,
            COALESCE(SUM(CASE WHEN marketplace_id = 3 THEN ad_revenue ELSE 0 END), 0) AS shopee_ad_revenue
        FROM marts.fact_marketplace_daily_performance
        WHERE date BETWEEN :start AND :end
          AND (:mkt_id IS NULL OR marketplace_id = :mkt_id)
    """)

    def run(s, e):
        r = dict(db.execute(sql, {"start": s, "end": e, "mkt_id": mkt_id}).mappings().one())
        gmv = _float(r["gmv"])
        orders = int(_float(r["orders"]))
        ml_spend = _float(r["ml_ad_spend"])
        ml_revenue = _float(r["ml_ad_revenue"])
        ml_orders = _float(r["ml_orders"])
        shopee_spend = _float(r["shopee_ad_spend"])
        shopee_revenue = _float(r["shopee_ad_revenue"])
        return {
            "gmv": gmv,
            "tiktok_gmv": _float(r["tiktok_gmv"]) or None,
            "ml_gmv": _float(r["ml_gmv"]) or None,
            "shopee_gmv": _float(r["shopee_gmv"]) or None,
            "orders": orders,
            "avg_ticket": _float(r["avg_ticket"]),
            "ad_spend": _float(r["ad_spend"]) or None,
            "ml_roas": round(ml_revenue / ml_spend, 2) if ml_spend > 0 else None,
            "ml_cancel_rate_pct": round(_float(r["ml_canceled"]) / ml_orders * 100, 1) if ml_orders > 0 else None,
            "tiktok_customers": int(_float(r["tiktok_customers"])) or None,
            "ml_unique_buyers": int(_float(r["ml_unique_buyers"])) or None,
            "shopee_unique_buyers": int(_float(r["shopee_unique_buyers"])) or None,
            "shopee_roas": round(shopee_revenue / shopee_spend, 2) if shopee_spend > 0 else None,
        }

    cur = run(start, end)
    prev = run(pstart, pend)
    mom = ((cur["gmv"] - prev["gmv"]) / prev["gmv"] * 100) if prev["gmv"] > 0 else None
    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "current": cur,
        "previous": prev,
        "gmv_mom_pct": round(mom, 2) if mom is not None else None,
    }


def get_brands(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)

    sql = text("""
        SELECT
            l.brand_key AS brand,
            f.marketplace_id,
            COALESCE(SUM(f.gmv), 0) AS gmv,
            COALESCE(SUM(f.orders), 0) AS orders,
            COALESCE(SUM(f.ad_spend), 0) AS ad_spend,
            COALESCE(SUM(f.ad_revenue), 0) AS ad_revenue,
            COALESCE(SUM(f.canceled_orders), 0) AS canceled_orders,
            COALESCE(SUM(f.total_fees), 0) AS total_fees,
            COALESCE(SUM(f.gmv_video), 0) AS gmv_video,
            COALESCE(SUM(f.gmv_live), 0) AS gmv_live,
            COALESCE(SUM(f.gmv_card), 0) AS gmv_card
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND (:mkt_id IS NULL OR f.marketplace_id = :mkt_id)
        GROUP BY l.brand_key, f.marketplace_id
    """)

    def run(s, e):
        rows = db.execute(sql, {"start": s, "end": e, "mkt_id": mkt_id}).mappings().all()
        result: dict[str, dict] = {}
        for r in rows:
            brand = r["brand"]
            item = result.setdefault(brand, {"orders": 0})
            mid = int(r["marketplace_id"])
            prefix = {1: "tiktok", 2: "ml", 3: "shopee"}.get(mid)
            if not prefix:
                continue
            item[f"{prefix}_gmv"] = _float(r["gmv"])
            item[f"{prefix}_orders"] = int(_float(r["orders"]))
            item["orders"] += int(_float(r["orders"]))
            if mid == 1:
                item["cos_pct"] = round(abs(_float(r["total_fees"])) / _float(r["gmv"]) * 100, 2) if _float(r["gmv"]) > 0 else None
            if mid == 2:
                spend = _float(r["ad_spend"])
                item["ml_roas"] = round(_float(r["ad_revenue"]) / spend, 2) if spend > 0 else None
                item["ml_cancel_rate_pct"] = round(_float(r["canceled_orders"]) / _float(r["orders"]) * 100, 1) if _float(r["orders"]) > 0 else None
        return result

    cur = run(start, end)
    prev = run(pstart, pend)
    result = []
    for brand, c in cur.items():
        tk = _float(c.get("tiktok_gmv"))
        ml = _float(c.get("ml_gmv"))
        sh = _float(c.get("shopee_gmv"))
        total = tk + ml + sh
        if total == 0:
            continue
        p = prev.get(brand, {})
        ptk = _float(p.get("tiktok_gmv"))
        pml = _float(p.get("ml_gmv"))
        psh = _float(p.get("shopee_gmv"))
        total_prev = ptk + pml + psh
        orders = int(c.get("orders", 0))
        mom = ((total - total_prev) / total_prev * 100) if total_prev > 0 else None
        result.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_gmv": tk or None,
            "ml_gmv": ml or None,
            "shopee_gmv": sh or None,
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": total / orders if orders > 0 else None,
            "tiktok_gmv_prev": ptk or None,
            "ml_gmv_prev": pml or None,
            "shopee_gmv_prev": psh or None,
            "total_gmv_prev": total_prev,
            "mom_pct": round(mom, 2) if mom is not None else None,
            "cos_pct": c.get("cos_pct"),
            "gpm": None,
            "ml_roas": c.get("ml_roas"),
            "ml_cancel_rate_pct": c.get("ml_cancel_rate_pct"),
        })
    result.sort(key=lambda r: -r["total_gmv"])
    return {"ref_month": f"{year:04d}-{month:02d}", "brands": result}


def get_monthly(db: Session, marketplace: str, months_back: int = 6) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    cutoff = date.today().replace(day=1) - timedelta(days=1)
    year = cutoff.year
    month = cutoff.month
    for _ in range(months_back - 1):
        year, month = _prev_month(year, month)
    start = date(year, month, 1)

    rows = db.execute(text("""
        SELECT DATE_TRUNC('month', f.date)::date AS mes,
               l.brand_key AS brand,
               COALESCE(SUM(f.gmv), 0) AS gmv
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date >= :start
          AND (:mkt_id IS NULL OR f.marketplace_id = :mkt_id)
        GROUP BY 1, 2
        ORDER BY 1, 2
    """), {"start": start, "mkt_id": mkt_id}).mappings().all()

    months: dict[str, dict] = {}
    for r in rows:
        mes_dt = r["mes"]
        key = f"{mes_dt.year:04d}-{mes_dt.month:02d}"
        if key not in months:
            months[key] = {"mes": key, "mes_label": f"{MES_LABELS[mes_dt.month]}/{str(mes_dt.year)[2:]}", "barbours": 0, "kokeshi": 0, "apice": 0, "lescent": 0, "rituaria": 0}
        if r["brand"] in months[key]:
            months[key][r["brand"]] = _float(r["gmv"])
    return {"data": list(months.values())}

# ---------------------------------------------------------------------------
# More Neon runtime overrides for dashboard tabs
# ---------------------------------------------------------------------------

def _fact_brand_rows(db: Session, start: date, end: date, mkt_id: int | None = None) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT
            l.brand_key AS brand,
            f.marketplace_id,
            COALESCE(SUM(f.gmv), 0) AS gmv,
            COALESCE(SUM(f.orders), 0) AS orders,
            COALESCE(SUM(f.units_sold), 0) AS units_sold,
            COALESCE(SUM(f.unique_buyers), 0) AS unique_buyers,
            COALESCE(SUM(f.new_buyers), 0) AS new_buyers,
            COALESCE(SUM(f.repeat_buyers), 0) AS repeat_buyers,
            AVG(f.repeat_buyer_rate_pct) AS repeat_buyer_rate_pct,
            COALESCE(SUM(f.visitors), 0) AS visitors,
            AVG(f.conversion_rate) AS conversion_rate,
            COALESCE(SUM(f.canceled_orders), 0) AS canceled_orders,
            COALESCE(SUM(f.returned_orders), 0) AS returned_orders,
            COALESCE(SUM(f.refunded_orders), 0) AS refunded_orders,
            AVG(f.problem_rate) AS problem_rate,
            AVG(f.cancel_rate_pct) AS cancel_rate_pct,
            COALESCE(SUM(f.delivered_orders), 0) AS delivered_orders,
            AVG(f.avg_delivery_hours) AS avg_delivery_hours,
            AVG(f.avg_delivery_days) AS avg_delivery_days,
            COALESCE(SUM(f.ad_spend), 0) AS ad_spend,
            COALESCE(SUM(f.ad_revenue), 0) AS ad_revenue,
            COALESCE(SUM(f.ad_impressions), 0) AS ad_impressions,
            COALESCE(SUM(f.ad_clicks), 0) AS ad_clicks,
            AVG(f.roas) AS avg_roas,
            AVG(f.acos_pct) AS acos_pct,
            AVG(f.ctr_pct) AS ctr_pct,
            AVG(f.cpc) AS cpc,
            COALESCE(SUM(f.gmv_video), 0) AS gmv_video,
            COALESCE(SUM(f.gmv_live), 0) AS gmv_live,
            COALESCE(SUM(f.gmv_card), 0) AS gmv_card,
            COALESCE(SUM(f.total_settlement), 0) AS total_settlement,
            COALESCE(SUM(f.total_fees), 0) AS total_fees,
            AVG(f.avg_fee_pct) AS avg_fee_pct,
            AVG(f.avg_settlement_pct) AS avg_settlement_pct,
            COALESCE(SUM(f.seller_shipping_cost), 0) AS seller_shipping_cost,
            AVG(f.shipping_pct_of_gmv) AS shipping_pct_of_gmv
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date BETWEEN :start AND :end
          AND (:mkt_id IS NULL OR f.marketplace_id = :mkt_id)
        GROUP BY l.brand_key, f.marketplace_id
        ORDER BY l.brand_key, f.marketplace_id
    """), {"start": start, "end": end, "mkt_id": mkt_id}).mappings()]


def _by_brand_market(rows: list[dict]) -> dict[str, dict[int, dict]]:
    result: dict[str, dict[int, dict]] = {}
    for r in rows:
        result.setdefault(r["brand"], {})[int(r["marketplace_id"])] = r
    return result


def _sum(rows: list[dict], marketplace_id: int, key: str) -> float:
    return sum(_float(r.get(key)) for r in rows if int(r.get("marketplace_id", 0)) == marketplace_id)


def _avg_nonzero(values: list[float]) -> float | None:
    vals = [v for v in values if v]
    return sum(vals) / len(vals) if vals else None


def get_canais(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    start, end = _month_bounds(year, month)
    rows = _fact_brand_rows(db, start, end, mkt_id)
    grouped = _by_brand_market(rows)

    tk_gmv = _sum(rows, 1, "gmv")
    tk_video = _sum(rows, 1, "gmv_video")
    tk_live = _sum(rows, 1, "gmv_live")
    tk_card = _sum(rows, 1, "gmv_card")
    tk_visitors = int(_sum(rows, 1, "visitors"))
    tk_customers = int(_sum(rows, 1, "unique_buyers"))
    ml_gmv = _sum(rows, 2, "gmv")
    ml_unique = int(_sum(rows, 2, "unique_buyers"))
    ml_new = int(_sum(rows, 2, "new_buyers"))
    ml_repeat = int(_sum(rows, 2, "repeat_buyers"))
    sh_gmv = _sum(rows, 3, "gmv")
    sh_unique = int(_sum(rows, 3, "unique_buyers"))
    sh_new = int(_sum(rows, 3, "new_buyers"))
    sh_repeat = int(_sum(rows, 3, "repeat_buyers"))
    sh_visitors = int(_sum(rows, 3, "visitors"))

    brands = []
    for brand in sorted(grouped.keys()):
        tk = grouped[brand].get(1, {})
        ml = grouped[brand].get(2, {})
        sh = grouped[brand].get(3, {})
        tk_brand_gmv = _float(tk.get("gmv"))
        ml_brand_unique = int(_float(ml.get("unique_buyers")))
        sh_brand_unique = int(_float(sh.get("unique_buyers")))
        brands.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_gmv": tk_brand_gmv or None,
            "tiktok_gmv_video": _float(tk.get("gmv_video")) or None,
            "tiktok_gmv_live": _float(tk.get("gmv_live")) or None,
            "tiktok_gmv_card": _float(tk.get("gmv_card")) or None,
            "tiktok_video_pct": round(_float(tk.get("gmv_video")) / tk_brand_gmv * 100, 1) if tk_brand_gmv else None,
            "tiktok_live_pct": round(_float(tk.get("gmv_live")) / tk_brand_gmv * 100, 1) if tk_brand_gmv else None,
            "tiktok_card_pct": round(_float(tk.get("gmv_card")) / tk_brand_gmv * 100, 1) if tk_brand_gmv else None,
            "tiktok_visitors": int(_float(tk.get("visitors"))) or None,
            "tiktok_customers": int(_float(tk.get("unique_buyers"))) or None,
            "tiktok_conversion_rate": round(_float(tk.get("unique_buyers")) / _float(tk.get("visitors")) * 100, 2) if _float(tk.get("visitors")) else None,
            "ml_gmv": _float(ml.get("gmv")) or None,
            "ml_unique_buyers": ml_brand_unique or None,
            "ml_new_buyers": int(_float(ml.get("new_buyers"))) or None,
            "ml_repeat_buyers": int(_float(ml.get("repeat_buyers"))) or None,
            "ml_repeat_buyer_rate_pct": _float(ml.get("repeat_buyer_rate_pct")) or None,
            "ml_gmv_per_buyer": round(_float(ml.get("gmv")) / ml_brand_unique, 2) if ml_brand_unique else None,
            "shopee_gmv": _float(sh.get("gmv")) or None,
            "shopee_unique_buyers": sh_brand_unique or None,
            "shopee_new_buyers": int(_float(sh.get("new_buyers"))) or None,
            "shopee_repeat_buyers": int(_float(sh.get("repeat_buyers"))) or None,
            "shopee_new_buyer_pct": round(_float(sh.get("new_buyers")) / sh_brand_unique * 100, 1) if sh_brand_unique else None,
            "shopee_repeat_buyer_rate_pct": _float(sh.get("repeat_buyer_rate_pct")) or None,
            "shopee_gmv_per_buyer": round(_float(sh.get("gmv")) / sh_brand_unique, 2) if sh_brand_unique else None,
            "shopee_cancel_rate_pct": _float(sh.get("cancel_rate_pct")) or None,
            "shopee_visitors": int(_float(sh.get("visitors"))) or None,
            "shopee_conversion_rate": _float(sh.get("conversion_rate")) or None,
        })

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_gmv": tk_gmv or None,
            "tiktok_gmv_video": tk_video or None,
            "tiktok_gmv_live": tk_live or None,
            "tiktok_gmv_card": tk_card or None,
            "tiktok_video_pct": round(tk_video / tk_gmv * 100, 1) if tk_gmv else None,
            "tiktok_live_pct": round(tk_live / tk_gmv * 100, 1) if tk_gmv else None,
            "tiktok_card_pct": round(tk_card / tk_gmv * 100, 1) if tk_gmv else None,
            "tiktok_visitors": tk_visitors or None,
            "tiktok_customers": tk_customers or None,
            "tiktok_conversion_rate": round(tk_customers / tk_visitors * 100, 2) if tk_visitors else None,
            "ml_unique_buyers": ml_unique or None,
            "ml_new_buyers": ml_new or None,
            "ml_repeat_buyers": ml_repeat or None,
            "ml_new_buyer_pct": round(ml_new / ml_unique * 100, 1) if ml_unique else None,
            "ml_repeat_buyer_rate_pct": round(ml_repeat / ml_unique * 100, 1) if ml_unique else None,
            "ml_gmv_per_buyer": round(ml_gmv / ml_unique, 2) if ml_unique else None,
            "shopee_gmv": sh_gmv or None,
            "shopee_unique_buyers": sh_unique or None,
            "shopee_new_buyers": sh_new or None,
            "shopee_repeat_buyers": sh_repeat or None,
            "shopee_new_buyer_pct": round(sh_new / sh_unique * 100, 1) if sh_unique else None,
            "shopee_repeat_buyer_rate_pct": round(sh_repeat / sh_unique * 100, 1) if sh_unique else None,
            "shopee_gmv_per_buyer": round(sh_gmv / sh_unique, 2) if sh_unique else None,
            "shopee_visitors": sh_visitors or None,
            "shopee_conversion_rate": round(sh_unique / sh_visitors * 100, 2) if sh_visitors else None,
        },
        "brands": brands,
    }


def get_financeiro(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    start, end = _month_bounds(year, month)
    rows = _fact_brand_rows(db, start, end, mkt_id)
    grouped = _by_brand_market(rows)

    brands = []
    for brand in sorted(grouped.keys()):
        tk = grouped[brand].get(1, {})
        ml = grouped[brand].get(2, {})
        sh = grouped[brand].get(3, {})
        ml_spend = _float(ml.get("ad_spend"))
        sh_spend = _float(sh.get("ad_spend"))
        brands.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_gmv": _float(tk.get("gmv")) or None,
            "tiktok_settlement": _float(tk.get("total_settlement")) or None,
            "tiktok_fees": _float(tk.get("total_fees")) or None,
            "tiktok_avg_fee_pct": _float(tk.get("avg_fee_pct")) or None,
            "tiktok_avg_settlement_pct": _float(tk.get("avg_settlement_pct")) or None,
            "ml_gmv": _float(ml.get("gmv")) or None,
            "ml_ad_spend": ml_spend or None,
            "ml_ad_revenue": _float(ml.get("ad_revenue")) or None,
            "ml_roas": round(_float(ml.get("ad_revenue")) / ml_spend, 2) if ml_spend else None,
            "ml_acos_pct": _float(ml.get("acos_pct")) or None,
            "ml_cpc": _float(ml.get("cpc")) or None,
            "ml_ctr_pct": _float(ml.get("ctr_pct")) or None,
            "ml_ad_clicks": int(_float(ml.get("ad_clicks"))) or None,
            "ml_ad_impressions": int(_float(ml.get("ad_impressions"))) or None,
            "ml_seller_shipping_cost": _float(ml.get("seller_shipping_cost")) or None,
            "ml_shipping_pct_of_gmv": _float(ml.get("shipping_pct_of_gmv")) or None,
            "shopee_gmv": _float(sh.get("gmv")) or None,
            "shopee_settlement": _float(sh.get("total_settlement")) or None,
            "shopee_fees": _float(sh.get("total_fees")) or None,
            "shopee_avg_fee_pct": _float(sh.get("avg_fee_pct")) or None,
            "shopee_avg_settlement_pct": _float(sh.get("avg_settlement_pct")) or None,
            "shopee_ad_spend": sh_spend or None,
            "shopee_ad_revenue": _float(sh.get("ad_revenue")) or None,
            "shopee_roas": round(_float(sh.get("ad_revenue")) / sh_spend, 2) if sh_spend else None,
            "shopee_shipping_cost": _float(sh.get("seller_shipping_cost")) or None,
            "shopee_shipping_pct_of_gmv": _float(sh.get("shipping_pct_of_gmv")) or None,
        })

    tk_gmv = _sum(rows, 1, "gmv")
    tk_settlement = _sum(rows, 1, "total_settlement")
    tk_fees = _sum(rows, 1, "total_fees")
    ml_spend = _sum(rows, 2, "ad_spend")
    ml_revenue = _sum(rows, 2, "ad_revenue")
    ml_clicks = _sum(rows, 2, "ad_clicks")
    sh_gmv = _sum(rows, 3, "gmv")
    sh_spend = _sum(rows, 3, "ad_spend")
    sh_revenue = _sum(rows, 3, "ad_revenue")
    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_gmv": tk_gmv or None,
            "tiktok_settlement": tk_settlement or None,
            "tiktok_fees": tk_fees or None,
            "tiktok_avg_fee_pct": round(abs(tk_fees) / tk_gmv * 100, 2) if tk_gmv else None,
            "tiktok_avg_settlement_pct": round(tk_settlement / tk_gmv * 100, 2) if tk_gmv else None,
            "ml_ad_spend": ml_spend or None,
            "ml_ad_revenue": ml_revenue or None,
            "ml_roas": round(ml_revenue / ml_spend, 2) if ml_spend else None,
            "ml_acos_pct": round(ml_spend / ml_revenue * 100, 2) if ml_revenue else None,
            "ml_cpc": round(ml_spend / ml_clicks, 4) if ml_clicks else None,
            "shopee_gmv": sh_gmv or None,
            "shopee_ad_spend": sh_spend or None,
            "shopee_roas": round(sh_revenue / sh_spend, 2) if sh_spend else None,
        },
        "brands": brands,
    }


def get_quality(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    start, end = _month_bounds(year, month)
    rows = _fact_brand_rows(db, start, end, mkt_id)
    grouped = _by_brand_market(rows)
    brands = []
    for brand in sorted(grouped.keys()):
        tk = grouped[brand].get(1, {})
        ml = grouped[brand].get(2, {})
        sh = grouped[brand].get(3, {})
        tk_orders = int(_float(tk.get("orders")))
        ml_orders = int(_float(ml.get("orders")))
        sh_orders = int(_float(sh.get("orders")))
        brands.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_orders": tk_orders or None,
            "tiktok_canceled": int(_float(tk.get("canceled_orders"))) or None,
            "tiktok_refunded": int(_float(tk.get("refunded_orders"))) or None,
            "tiktok_returned": int(_float(tk.get("returned_orders"))) or None,
            "tiktok_problem_rate": _float(tk.get("problem_rate")) or None,
            "tiktok_cancel_rate": round(_float(tk.get("canceled_orders")) / tk_orders * 100, 2) if tk_orders else None,
            "tiktok_avg_delivery_days": round(_float(tk.get("avg_delivery_hours")) / 24, 2) if _float(tk.get("avg_delivery_hours")) else None,
            "ml_cancel_rate_pct": _float(ml.get("cancel_rate_pct")) or None,
            "ml_cancelled_orders": int(_float(ml.get("canceled_orders"))) or None,
            "ml_total_orders": ml_orders or None,
            "ml_avg_delivery_days": _float(ml.get("avg_delivery_days")) or None,
            "ml_repeat_buyer_rate_pct": _float(ml.get("repeat_buyer_rate_pct")) or None,
            "ml_gmv_per_buyer": round(_float(ml.get("gmv")) / _float(ml.get("unique_buyers")), 2) if _float(ml.get("unique_buyers")) else None,
            "ml_new_buyers": int(_float(ml.get("new_buyers"))) or None,
            "ml_unique_buyers": int(_float(ml.get("unique_buyers"))) or None,
            "ml_shipping_pct_of_gmv": _float(ml.get("shipping_pct_of_gmv")) or None,
            "shopee_orders": sh_orders or None,
            "shopee_canceled_orders": int(_float(sh.get("canceled_orders"))) or None,
            "shopee_returned_orders": int(_float(sh.get("returned_orders"))) or None,
            "shopee_cancel_rate_pct": _float(sh.get("cancel_rate_pct")) or None,
        })
    tk_orders_total = _sum(rows, 1, "orders")
    ml_orders_total = _sum(rows, 2, "orders")
    sh_orders_total = _sum(rows, 3, "orders")
    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_problem_rate": _avg_nonzero([_float(r.get("problem_rate")) for r in rows if int(r.get("marketplace_id", 0)) == 1]),
            "tiktok_cancel_rate": round(_sum(rows, 1, "canceled_orders") / tk_orders_total * 100, 2) if tk_orders_total else None,
            "tiktok_avg_delivery_days": _avg_nonzero([_float(r.get("avg_delivery_hours")) / 24 for r in rows if int(r.get("marketplace_id", 0)) == 1]),
            "ml_cancel_rate_pct": round(_sum(rows, 2, "canceled_orders") / ml_orders_total * 100, 2) if ml_orders_total else None,
            "ml_avg_delivery_days": _avg_nonzero([_float(r.get("avg_delivery_days")) for r in rows if int(r.get("marketplace_id", 0)) == 2]),
            "shopee_cancel_rate_pct": round(_sum(rows, 3, "canceled_orders") / sh_orders_total * 100, 2) if sh_orders_total else None,
            "shopee_return_rate_pct": round(_sum(rows, 3, "returned_orders") / sh_orders_total * 100, 2) if sh_orders_total else None,
        },
        "brands": brands,
    }


def get_pedidos(db: Session, days_back: int = 30) -> dict:
    date_from = date.today() - timedelta(days=days_back)
    rows = [dict(r) for r in db.execute(text("""
        SELECT f.date, l.brand_key AS brand, f.marketplace_id,
               COALESCE(f.orders, 0) AS orders,
               COALESCE(f.canceled_orders, 0) AS canceled_orders,
               COALESCE(f.delivered_orders, 0) AS delivered_orders,
               COALESCE(f.gmv, 0) AS gmv
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.date >= :date_from
          AND f.marketplace_id IN (1, 2)
        ORDER BY f.date, l.brand_key, f.marketplace_id
    """), {"date_from": date_from}).mappings()]

    tk_orders = sum(int(_float(r["orders"])) for r in rows if int(r["marketplace_id"]) == 1)
    ml_orders = sum(int(_float(r["orders"])) for r in rows if int(r["marketplace_id"]) == 2)
    tk_canceled = sum(int(_float(r["canceled_orders"])) for r in rows if int(r["marketplace_id"]) == 1)
    ml_canceled = sum(int(_float(r["canceled_orders"])) for r in rows if int(r["marketplace_id"]) == 2)
    tk_gmv = sum(_float(r["gmv"]) for r in rows if int(r["marketplace_id"]) == 1)
    ml_gmv = sum(_float(r["gmv"]) for r in rows if int(r["marketplace_id"]) == 2)
    total_orders = tk_orders + ml_orders
    total_gmv = tk_gmv + ml_gmv

    by_day: dict[str, dict] = {}
    by_brand: dict[str, dict] = {}
    for r in rows:
        d = str(r["date"])
        day = by_day.setdefault(d, {"date": d, "tiktok_orders": 0, "tiktok_canceled": 0, "ml_orders": 0, "ml_canceled": 0, "total_orders": 0, "total_gmv": 0.0})
        brand = r["brand"]
        b = by_brand.setdefault(brand, {"brand": brand, "label": BRAND_LABELS.get(brand, brand.upper()), "total_orders": 0, "total_gmv": 0.0})
        orders = int(_float(r["orders"]))
        canceled = int(_float(r["canceled_orders"]))
        gmv = _float(r["gmv"])
        mid = int(r["marketplace_id"])
        if mid == 1:
            day["tiktok_orders"] += orders
            day["tiktok_canceled"] += canceled
            b["tiktok_orders"] = (b.get("tiktok_orders") or 0) + orders
            b["tiktok_canceled"] = (b.get("tiktok_canceled") or 0) + canceled
            b["tiktok_gmv"] = (b.get("tiktok_gmv") or 0) + gmv
        elif mid == 2:
            day["ml_orders"] += orders
            day["ml_canceled"] += canceled
            b["ml_orders"] = (b.get("ml_orders") or 0) + orders
            b["ml_canceled"] = (b.get("ml_canceled") or 0) + canceled
            b["ml_gmv"] = (b.get("ml_gmv") or 0) + gmv
        day["total_orders"] += orders
        day["total_gmv"] += gmv
        b["total_orders"] += orders
        b["total_gmv"] += gmv

    for b in by_brand.values():
        if b.get("tiktok_orders"):
            b["tiktok_cancel_rate_pct"] = round((b.get("tiktok_canceled") or 0) / b["tiktok_orders"] * 100, 2)
        if b.get("ml_orders"):
            b["ml_cancel_rate_pct"] = round((b.get("ml_canceled") or 0) / b["ml_orders"] * 100, 2)

    return {
        "days_back": days_back,
        "kpis": {
            "total_orders": total_orders,
            "total_gmv": round(total_gmv, 2),
            "avg_ticket": round(total_gmv / total_orders, 2) if total_orders else 0.0,
            "cancel_rate_pct": round((tk_canceled + ml_canceled) / total_orders * 100, 2) if total_orders else None,
        },
        "tiktok": {
            "orders": tk_orders,
            "canceled": tk_canceled,
            "gmv": round(tk_gmv, 2),
            "cancel_rate_pct": round(tk_canceled / tk_orders * 100, 2) if tk_orders else None,
            "delivered": sum(int(_float(r["delivered_orders"])) for r in rows if int(r["marketplace_id"]) == 1) or None,
        },
        "ml": {
            "orders": ml_orders,
            "canceled": ml_canceled,
            "gmv": round(ml_gmv, 2),
            "cancel_rate_pct": round(ml_canceled / ml_orders * 100, 2) if ml_orders else None,
            "delivered": sum(int(_float(r["delivered_orders"])) for r in rows if int(r["marketplace_id"]) == 2) or None,
        },
        "daily": list(by_day.values()),
        "by_brand": sorted(by_brand.values(), key=lambda r: -r["total_gmv"]),
    }

# Metric-quality overrides after Neon recheck
# ---------------------------------------------------------------------------

def _pct_from_source(value) -> float | None:
    v = _float(value)
    if not v:
        return None
    return round(v * 100, 2) if abs(v) <= 1 else round(v, 2)


def _ratio_pct(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _safe_div(numerator: float, denominator: float, digits: int = 2) -> float | None:
    return round(numerator / denominator, digits) if denominator else None


def _market_rows(rows: list[dict], marketplace_id: int) -> list[dict]:
    return [r for r in rows if int(r.get("marketplace_id", 0)) == marketplace_id]


def _market_avg_pct(rows: list[dict], marketplace_id: int, key: str) -> float | None:
    vals = [_pct_from_source(r.get(key)) for r in _market_rows(rows, marketplace_id)]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def get_canais(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    start, end = _month_bounds(year, month)
    rows = _fact_brand_rows(db, start, end, mkt_id)
    grouped = _by_brand_market(rows)

    tk_gmv = _sum(rows, 1, "gmv")
    tk_video = _sum(rows, 1, "gmv_video")
    tk_live = _sum(rows, 1, "gmv_live")
    tk_card = _sum(rows, 1, "gmv_card")
    tk_visitors = int(_sum(rows, 1, "visitors"))
    tk_customers = int(_sum(rows, 1, "unique_buyers"))
    ml_gmv = _sum(rows, 2, "gmv")
    ml_unique = int(_sum(rows, 2, "unique_buyers"))
    ml_new = int(_sum(rows, 2, "new_buyers"))
    ml_repeat = int(_sum(rows, 2, "repeat_buyers"))
    sh_gmv = _sum(rows, 3, "gmv")
    sh_unique = int(_sum(rows, 3, "unique_buyers"))
    sh_new = int(_sum(rows, 3, "new_buyers"))
    sh_repeat = int(_sum(rows, 3, "repeat_buyers"))
    sh_visitors = int(_sum(rows, 3, "visitors"))

    brands = []
    for brand in sorted(grouped.keys()):
        tk = grouped[brand].get(1, {})
        ml = grouped[brand].get(2, {})
        sh = grouped[brand].get(3, {})
        tk_brand_gmv = _float(tk.get("gmv"))
        ml_brand_unique = int(_float(ml.get("unique_buyers")))
        sh_brand_unique = int(_float(sh.get("unique_buyers")))
        brands.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_gmv": tk_brand_gmv or None,
            "tiktok_gmv_video": _float(tk.get("gmv_video")) or None,
            "tiktok_gmv_live": _float(tk.get("gmv_live")) or None,
            "tiktok_gmv_card": _float(tk.get("gmv_card")) or None,
            "tiktok_video_pct": _ratio_pct(_float(tk.get("gmv_video")), tk_brand_gmv),
            "tiktok_live_pct": _ratio_pct(_float(tk.get("gmv_live")), tk_brand_gmv),
            "tiktok_card_pct": _ratio_pct(_float(tk.get("gmv_card")), tk_brand_gmv),
            "tiktok_visitors": int(_float(tk.get("visitors"))) or None,
            "tiktok_customers": int(_float(tk.get("unique_buyers"))) or None,
            "tiktok_conversion_rate": _pct_from_source(tk.get("conversion_rate")),
            "ml_gmv": _float(ml.get("gmv")) or None,
            "ml_unique_buyers": ml_brand_unique or None,
            "ml_new_buyers": int(_float(ml.get("new_buyers"))) or None,
            "ml_repeat_buyers": int(_float(ml.get("repeat_buyers"))) or None,
            "ml_repeat_buyer_rate_pct": _pct_from_source(ml.get("repeat_buyer_rate_pct")),
            "ml_gmv_per_buyer": _safe_div(_float(ml.get("gmv")), ml_brand_unique),
            "shopee_gmv": _float(sh.get("gmv")) or None,
            "shopee_unique_buyers": sh_brand_unique or None,
            "shopee_new_buyers": int(_float(sh.get("new_buyers"))) or None,
            "shopee_repeat_buyers": int(_float(sh.get("repeat_buyers"))) or None,
            "shopee_new_buyer_pct": _ratio_pct(_float(sh.get("new_buyers")), sh_brand_unique),
            "shopee_repeat_buyer_rate_pct": _pct_from_source(sh.get("repeat_buyer_rate_pct")),
            "shopee_gmv_per_buyer": _safe_div(_float(sh.get("gmv")), sh_brand_unique),
            "shopee_cancel_rate_pct": _pct_from_source(sh.get("cancel_rate_pct")),
            "shopee_visitors": int(_float(sh.get("visitors"))) or None,
            "shopee_conversion_rate": _ratio_pct(sh_brand_unique, _float(sh.get("visitors"))),
        })

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_gmv": tk_gmv or None,
            "tiktok_gmv_video": tk_video or None,
            "tiktok_gmv_live": tk_live or None,
            "tiktok_gmv_card": tk_card or None,
            "tiktok_video_pct": _ratio_pct(tk_video, tk_gmv),
            "tiktok_live_pct": _ratio_pct(tk_live, tk_gmv),
            "tiktok_card_pct": _ratio_pct(tk_card, tk_gmv),
            "tiktok_visitors": tk_visitors or None,
            "tiktok_customers": tk_customers or None,
            "tiktok_conversion_rate": _market_avg_pct(rows, 1, "conversion_rate"),
            "ml_unique_buyers": ml_unique or None,
            "ml_new_buyers": ml_new or None,
            "ml_repeat_buyers": ml_repeat or None,
            "ml_new_buyer_pct": _ratio_pct(ml_new, ml_unique),
            "ml_repeat_buyer_rate_pct": _ratio_pct(ml_repeat, ml_unique),
            "ml_gmv_per_buyer": _safe_div(ml_gmv, ml_unique),
            "shopee_gmv": sh_gmv or None,
            "shopee_unique_buyers": sh_unique or None,
            "shopee_new_buyers": sh_new or None,
            "shopee_repeat_buyers": sh_repeat or None,
            "shopee_new_buyer_pct": _ratio_pct(sh_new, sh_unique),
            "shopee_repeat_buyer_rate_pct": _ratio_pct(sh_repeat, sh_unique),
            "shopee_gmv_per_buyer": _safe_div(sh_gmv, sh_unique),
            "shopee_visitors": sh_visitors or None,
            "shopee_conversion_rate": _ratio_pct(sh_unique, sh_visitors),
        },
        "brands": brands,
    }


def get_financeiro(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    start, end = _month_bounds(year, month)
    rows = _fact_brand_rows(db, start, end, mkt_id)
    grouped = _by_brand_market(rows)

    brands = []
    for brand in sorted(grouped.keys()):
        tk = grouped[brand].get(1, {})
        ml = grouped[brand].get(2, {})
        sh = grouped[brand].get(3, {})
        tk_gmv = _float(tk.get("gmv"))
        ml_spend = _float(ml.get("ad_spend"))
        ml_revenue = _float(ml.get("ad_revenue"))
        ml_clicks = _float(ml.get("ad_clicks"))
        ml_impressions = _float(ml.get("ad_impressions"))
        sh_gmv = _float(sh.get("gmv"))
        sh_spend = _float(sh.get("ad_spend"))
        sh_revenue = _float(sh.get("ad_revenue"))
        brands.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_gmv": tk_gmv or None,
            "tiktok_settlement": _float(tk.get("total_settlement")) or None,
            "tiktok_fees": _float(tk.get("total_fees")) or None,
            "tiktok_avg_fee_pct": _ratio_pct(abs(_float(tk.get("total_fees"))), tk_gmv),
            "tiktok_avg_settlement_pct": _ratio_pct(_float(tk.get("total_settlement")), tk_gmv),
            "ml_gmv": _float(ml.get("gmv")) or None,
            "ml_ad_spend": ml_spend or None,
            "ml_ad_revenue": ml_revenue or None,
            "ml_roas": _safe_div(ml_revenue, ml_spend),
            "ml_acos_pct": _ratio_pct(ml_spend, ml_revenue),
            "ml_cpc": _safe_div(ml_spend, ml_clicks, 4),
            "ml_ctr_pct": _ratio_pct(ml_clicks, ml_impressions),
            "ml_ad_clicks": int(ml_clicks) or None,
            "ml_ad_impressions": int(ml_impressions) or None,
            "ml_seller_shipping_cost": _float(ml.get("seller_shipping_cost")) or None,
            "ml_shipping_pct_of_gmv": _ratio_pct(_float(ml.get("seller_shipping_cost")), _float(ml.get("gmv"))),
            "ml_total_cost_pct": _ratio_pct(ml_spend + _float(ml.get("seller_shipping_cost")), _float(ml.get("gmv"))),
            "shopee_gmv": sh_gmv or None,
            "shopee_settlement": _float(sh.get("total_settlement")) or None,
            "shopee_fees": _float(sh.get("total_fees")) or None,
            "shopee_avg_fee_pct": _ratio_pct(abs(_float(sh.get("total_fees"))), sh_gmv),
            "shopee_avg_settlement_pct": _ratio_pct(_float(sh.get("total_settlement")), sh_gmv),
            "shopee_ad_spend": sh_spend or None,
            "shopee_ad_revenue": sh_revenue or None,
            "shopee_roas": _safe_div(sh_revenue, sh_spend),
            "shopee_shipping_cost": _float(sh.get("seller_shipping_cost")) or None,
            "shopee_shipping_pct_of_gmv": _ratio_pct(_float(sh.get("seller_shipping_cost")), sh_gmv),
        })

    tk_gmv = _sum(rows, 1, "gmv")
    tk_settlement = _sum(rows, 1, "total_settlement")
    tk_fees = _sum(rows, 1, "total_fees")
    ml_gmv = _sum(rows, 2, "gmv")
    ml_spend = _sum(rows, 2, "ad_spend")
    ml_revenue = _sum(rows, 2, "ad_revenue")
    ml_clicks = _sum(rows, 2, "ad_clicks")
    ml_shipping = _sum(rows, 2, "seller_shipping_cost")
    sh_gmv = _sum(rows, 3, "gmv")
    sh_settlement = _sum(rows, 3, "total_settlement")
    sh_fees = _sum(rows, 3, "total_fees")
    sh_spend = _sum(rows, 3, "ad_spend")
    sh_revenue = _sum(rows, 3, "ad_revenue")
    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_gmv": tk_gmv or None,
            "tiktok_settlement": tk_settlement or None,
            "tiktok_fees": tk_fees or None,
            "tiktok_avg_fee_pct": _ratio_pct(abs(tk_fees), tk_gmv),
            "tiktok_avg_settlement_pct": _ratio_pct(tk_settlement, tk_gmv),
            "ml_ad_spend": ml_spend or None,
            "ml_ad_revenue": ml_revenue or None,
            "ml_gmv": ml_gmv or None,
            "ml_roas": _safe_div(ml_revenue, ml_spend),
            "ml_acos_pct": _ratio_pct(ml_spend, ml_revenue),
            "ml_cpc": _safe_div(ml_spend, ml_clicks, 4),
            "ml_total_cost_pct": _ratio_pct(ml_spend + ml_shipping, ml_gmv),
            "shopee_gmv": sh_gmv or None,
            "shopee_settlement": sh_settlement or None,
            "shopee_fees": sh_fees or None,
            "shopee_avg_fee_pct": _ratio_pct(abs(sh_fees), sh_gmv),
            "shopee_avg_settlement_pct": _ratio_pct(sh_settlement, sh_gmv),
            "shopee_ad_spend": sh_spend or None,
            "shopee_roas": _safe_div(sh_revenue, sh_spend),
        },
        "brands": brands,
    }


def get_quality(db: Session, marketplace: str, year: int, month: int) -> dict:
    mkt_id = _MKT_FILTER_FACT.get(marketplace)
    start, end = _month_bounds(year, month)
    rows = _fact_brand_rows(db, start, end, mkt_id)
    grouped = _by_brand_market(rows)
    brands = []
    for brand in sorted(grouped.keys()):
        tk = grouped[brand].get(1, {})
        ml = grouped[brand].get(2, {})
        sh = grouped[brand].get(3, {})
        tk_orders = int(_float(tk.get("orders")))
        ml_orders = int(_float(ml.get("orders")))
        sh_orders = int(_float(sh.get("orders")))
        brands.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_orders": tk_orders or None,
            "tiktok_canceled": None,
            "tiktok_refunded": None,
            "tiktok_returned": None,
            "tiktok_problem_rate": None,
            "tiktok_cancel_rate": None,
            "tiktok_avg_delivery_days": _safe_div(_float(tk.get("avg_delivery_hours")), 24),
            "ml_cancel_rate_pct": _ratio_pct(_float(ml.get("canceled_orders")), ml_orders),
            "ml_cancelled_orders": int(_float(ml.get("canceled_orders"))) or None,
            "ml_total_orders": ml_orders or None,
            "ml_avg_delivery_days": _float(ml.get("avg_delivery_days")) or None,
            "ml_repeat_buyer_rate_pct": _pct_from_source(ml.get("repeat_buyer_rate_pct")),
            "ml_gmv_per_buyer": _safe_div(_float(ml.get("gmv")), _float(ml.get("unique_buyers"))),
            "ml_new_buyers": int(_float(ml.get("new_buyers"))) or None,
            "ml_unique_buyers": int(_float(ml.get("unique_buyers"))) or None,
            "ml_shipping_pct_of_gmv": _ratio_pct(_float(ml.get("seller_shipping_cost")), _float(ml.get("gmv"))),
            "shopee_orders": sh_orders or None,
            "shopee_canceled_orders": int(_float(sh.get("canceled_orders"))) or None,
            "shopee_returned_orders": int(_float(sh.get("returned_orders"))) or None,
            "shopee_cancel_rate_pct": _ratio_pct(_float(sh.get("canceled_orders")), sh_orders),
            "shopee_return_rate_pct": _ratio_pct(_float(sh.get("returned_orders")), sh_orders),
        })
    ml_orders_total = _sum(rows, 2, "orders")
    sh_orders_total = _sum(rows, 3, "orders")
    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_problem_rate": None,
            "tiktok_cancel_rate": None,
            "tiktok_avg_delivery_days": _avg_nonzero([_float(r.get("avg_delivery_hours")) / 24 for r in _market_rows(rows, 1)]),
            "ml_cancel_rate_pct": _ratio_pct(_sum(rows, 2, "canceled_orders"), ml_orders_total),
            "ml_avg_delivery_days": _avg_nonzero([_float(r.get("avg_delivery_days")) for r in _market_rows(rows, 2)]),
            "shopee_cancel_rate_pct": _ratio_pct(_sum(rows, 3, "canceled_orders"), sh_orders_total),
            "shopee_return_rate_pct": _ratio_pct(_sum(rows, 3, "returned_orders"), sh_orders_total),
        },
        "brands": brands,
    }
