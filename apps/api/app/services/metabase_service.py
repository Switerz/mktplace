"""
Queries diretas às gold tables do Data Mart via Metabase REST API.
Mesmas assinaturas do performance_service.py — usadas como fallback
quando o banco local não está disponível.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app import metabase_client as mb

BRANDS_IN_SCOPE = ("apice", "barbours", "kokeshi", "lescent", "rituaria")
ML_BRANDS = ("barbours", "kokeshi", "lescent")

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

_MKT_FILTER = {"all": None, "tiktok": "tiktok", "ml": "ml"}


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


def _brands_list(mkt: str) -> tuple[tuple, str]:
    if mkt == "tiktok":
        return BRANDS_IN_SCOPE, "gold.tiktok_brand_daily"
    if mkt == "ml":
        return ML_BRANDS, "gold.ml_gestao_diaria"
    return BRANDS_IN_SCOPE, ""  # "all" trata as duas separado


def _fmt_list(t: tuple) -> str:
    return "(" + ", ".join(f"'{b}'" for b in t) + ")"


def _fetch_tiktok_kpis(start: date, end: date) -> dict:
    sql = f"""
        SELECT
            COALESCE(SUM(gmv), 0)    AS gmv,
            COALESCE(SUM(orders), 0) AS orders,
            CASE WHEN SUM(orders)>0 THEN SUM(gmv)/SUM(orders) ELSE 0 END AS avg_ticket
        FROM gold.tiktok_brand_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)}
          AND date BETWEEN '{start}' AND '{end}'
    """
    return mb.query(sql)[0]


def _fetch_ml_kpis(start: date, end: date) -> dict:
    sql = f"""
        SELECT
            COALESCE(SUM(gmv), 0)         AS gmv,
            COALESCE(SUM(paid_orders), 0) AS orders,
            COALESCE(SUM(ad_spend), 0)    AS ad_spend,
            CASE WHEN SUM(paid_orders)>0
                 THEN SUM(gmv)/SUM(paid_orders) ELSE 0 END AS avg_ticket
        FROM gold.ml_gestao_diaria
        WHERE brand IN {_fmt_list(ML_BRANDS)}
          AND ref_date BETWEEN '{start}' AND '{end}'
    """
    return mb.query(sql)[0]


def get_overview(marketplace: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)

    def aggregate(s, e):
        if marketplace == "tiktok":
            tk = _fetch_tiktok_kpis(s, e)
            tk_gmv = _float(tk["gmv"])
            return {
                "gmv": tk_gmv,
                "tiktok_gmv": tk_gmv or None,
                "ml_gmv": None,
                "orders": int(_float(tk["orders"])),
                "avg_ticket": _float(tk["avg_ticket"]),
                "ad_spend": None,
            }
        if marketplace == "ml":
            ml = _fetch_ml_kpis(s, e)
            ml_gmv = _float(ml["gmv"])
            return {
                "gmv": ml_gmv,
                "tiktok_gmv": None,
                "ml_gmv": ml_gmv or None,
                "orders": int(_float(ml["orders"])),
                "avg_ticket": _float(ml["avg_ticket"]),
                "ad_spend": _float(ml["ad_spend"]) or None,
            }
        # all
        tk = _fetch_tiktok_kpis(s, e)
        ml = _fetch_ml_kpis(s, e)
        tk_gmv = _float(tk["gmv"])
        ml_gmv = _float(ml["gmv"])
        gmv = tk_gmv + ml_gmv
        orders = int(_float(tk["orders"])) + int(_float(ml["orders"]))
        return {
            "gmv": gmv,
            "tiktok_gmv": tk_gmv or None,
            "ml_gmv": ml_gmv or None,
            "orders": orders,
            "avg_ticket": gmv / orders if orders > 0 else 0,
            "ad_spend": _float(ml["ad_spend"]) or None,
        }

    cur = aggregate(start, end)
    prev = aggregate(pstart, pend)
    mom = ((cur["gmv"] - prev["gmv"]) / prev["gmv"] * 100) if prev["gmv"] > 0 else None

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "current": cur,
        "previous": prev,
        "gmv_mom_pct": round(mom, 2) if mom else None,
    }


def get_brands(marketplace: str, year: int, month: int) -> dict:
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
        return {r["brand"]: r for r in mb.query(sql)}

    def fetch_ml(s, e):
        sql = f"""
            SELECT brand,
                   COALESCE(SUM(gmv), 0)         AS gmv,
                   COALESCE(SUM(paid_orders), 0) AS orders,
                   CASE WHEN SUM(paid_orders) > 0
                        THEN SUM(gmv) / SUM(paid_orders)
                        ELSE NULL END             AS avg_ticket
            FROM gold.ml_gestao_diaria
            WHERE brand IN {_fmt_list(ML_BRANDS)}
              AND ref_date BETWEEN '{s}' AND '{e}'
            GROUP BY brand
        """
        return {r["brand"]: r for r in mb.query(sql)}

    if marketplace == "tiktok":
        cur_tk, cur_ml = fetch_tk(start, end), {}
        prev_tk, prev_ml = fetch_tk(pstart, pend), {}
        brands_set = BRANDS_IN_SCOPE
    elif marketplace == "ml":
        cur_tk, cur_ml = {}, fetch_ml(start, end)
        prev_tk, prev_ml = {}, fetch_ml(pstart, pend)
        brands_set = ML_BRANDS
    else:
        cur_tk, cur_ml = fetch_tk(start, end), fetch_ml(start, end)
        prev_tk, prev_ml = fetch_tk(pstart, pend), fetch_ml(pstart, pend)
        brands_set = BRANDS_IN_SCOPE

    result = []
    for brand in brands_set:
        tk_gmv = _float(cur_tk.get(brand, {}).get("gmv", 0))
        ml_gmv = _float(cur_ml.get(brand, {}).get("gmv", 0))
        total = tk_gmv + ml_gmv
        if total == 0:
            continue

        prev_tk_gmv = _float(prev_tk.get(brand, {}).get("gmv", 0))
        prev_ml_gmv = _float(prev_ml.get(brand, {}).get("gmv", 0))
        total_prev = prev_tk_gmv + prev_ml_gmv

        orders = int(_float(cur_tk.get(brand, {}).get("orders", 0))) + \
                 int(_float(cur_ml.get(brand, {}).get("orders", 0)))
        mom = ((total - total_prev) / total_prev * 100) if total_prev > 0 else None

        tk_cur = cur_tk.get(brand, {})
        ml_cur = cur_ml.get(brand, {})
        tk_orders = int(_float(tk_cur.get("orders", 0)))
        ml_orders = int(_float(ml_cur.get("orders", 0)))
        result.append({
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
            "tiktok_gmv": tk_gmv or None,
            "ml_gmv": ml_gmv or None,
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": total / orders if orders > 0 else None,
            "tiktok_avg_ticket": round(_float(tk_cur.get("avg_ticket") or 0), 2) or None,
            "ml_avg_ticket": round(_float(ml_cur.get("avg_ticket") or 0), 2) or None,
            "tiktok_gmv_prev": prev_tk_gmv or None,
            "ml_gmv_prev": prev_ml_gmv or None,
            "total_gmv_prev": total_prev,
            "mom_pct": round(mom, 2) if mom else None,
            "cos_pct": round(_float(tk_cur.get("cos_pct") or 0), 2) or None,
            "gpm": round(_float(tk_cur.get("gpm") or 0), 2) or None,
        })

    result.sort(key=lambda r: -r["total_gmv"])
    return {"ref_month": f"{year:04d}-{month:02d}", "brands": result}


def get_monthly(marketplace: str, months_back: int = 6) -> dict:
    today = date.today()
    # começa no primeiro dia do mês (months_back) atrás
    year, month = today.year, today.month
    for _ in range(months_back):
        year, month = _prev_month(year, month)
    start = date(year, month, 1)

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
        tk_rows = mb.query(sql_tk)
    else:
        tk_rows = []

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
        ml_rows = mb.query(sql_ml)
    else:
        ml_rows = []

    months: dict[str, dict] = {}

    def add_row(r):
        # Metabase retorna o campo como string ou date
        mes_val = r["mes"]
        if isinstance(mes_val, str):
            mes_val = mes_val[:10]  # "2026-05-01"
        key = str(mes_val)[:7]   # "2026-05"
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

    return {"data": sorted(months.values(), key=lambda x: x["mes"])}


ML_PRODUTO_BRANDS = ML_BRANDS  # barbours, kokeshi, lescent
TK_PRODUTO_BRANDS = BRANDS_IN_SCOPE


def get_produtos_ml(
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
    total = int(_float(mb.query(count_sql)[0]["n"]))

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
    rows = mb.query(data_sql)

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


def get_produtos_tiktok(
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
    total = int(_float(mb.query(count_sql)[0]["n"]))

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
    rows = mb.query(data_sql)

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


_BUCKET_META = {
    "A_top50":  ("A", "Top 50% GMV"),
    "B_next30": ("B", "Next 30%"),
    "C_next15": ("C", "Next 15%"),
    "D_tail":   ("D", "Cauda"),
}


def get_produtos_ml_summary(brand: str | None) -> dict:
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
    rows = mb.query(sql)

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


def get_canais(marketplace: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)

    tk_rows: list[dict] = []
    if marketplace in ("all", "tiktok"):
        # visitors é populado em pouquíssimos dias por mês (cobertura estrutural da API TikTok).
        # Usar SUM incondicional divide clientes de 31 dias por visitantes de 1 dia → taxas >100%.
        # Solução: alinhar numerador e denominador apenas nos dias onde visitors > 0.
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
        tk_rows = mb.query(sql)

    ml_rows: list[dict] = []
    if marketplace in ("all", "ml"):
        # ml_gestao_mensal deduplica compradores únicos no mês corretamente.
        # Somar ml_gestao_diaria infla repeat_buyers 30-74% (mesmo comprador conta várias vezes).
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
        ml_rows = mb.query(sql)

    tk_by_brand = {r["brand"]: r for r in tk_rows}
    ml_by_brand = {r["brand"]: r for r in ml_rows}

    brands_set = BRANDS_IN_SCOPE if marketplace in ("all", "tiktok") else ML_BRANDS

    def _tk_pct(part, total):
        return round(part / total * 100, 1) if total > 0 else None

    brand_rows = []
    for brand in brands_set:
        tk = tk_by_brand.get(brand, {})
        ml = ml_by_brand.get(brand, {})
        if not tk and not ml:
            continue

        row: dict = {"brand": brand, "label": BRAND_LABELS.get(brand, brand.upper())}

        if tk:
            gmv = _float(tk["gmv"])
            vid = _float(tk["gmv_video"])
            live = _float(tk["gmv_live"])
            card = _float(tk["gmv_card"])
            visitors = int(_float(tk["visitors"]))
            customers = int(_float(tk["customers"]))
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

        brand_rows.append(row)

    # KPIs agregados
    tk_gmv_total = sum(_float(r.get("tiktok_gmv") or 0) for r in brand_rows)
    tk_vid_total = sum(_float(r.get("tiktok_gmv_video") or 0) for r in brand_rows)
    tk_live_total = sum(_float(r.get("tiktok_gmv_live") or 0) for r in brand_rows)
    tk_card_total = sum(_float(r.get("tiktok_gmv_card") or 0) for r in brand_rows)
    tk_visitors = sum(r.get("tiktok_visitors") or 0 for r in brand_rows)
    tk_customers = sum(r.get("tiktok_customers") or 0 for r in brand_rows)

    ml_buyers = sum(r.get("ml_unique_buyers") or 0 for r in brand_rows)
    ml_new = sum(r.get("ml_new_buyers") or 0 for r in brand_rows)
    ml_repeat = sum(r.get("ml_repeat_buyers") or 0 for r in brand_rows)
    ml_gmv_total = sum(_float(r.get("ml_gmv") or 0) for r in brand_rows)

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
            "ml_unique_buyers": ml_buyers or None,
            "ml_new_buyers": ml_new or None,
            "ml_repeat_buyers": ml_repeat or None,
            "ml_new_buyer_pct": _tk_pct(ml_new, ml_buyers),
            "ml_repeat_buyer_rate_pct": round(ml_repeat / ml_buyers * 100, 1) if ml_buyers > 0 else None,
            "ml_gmv_per_buyer": round(ml_gmv_total / ml_buyers, 2) if ml_buyers > 0 else None,
        },
        "brands": brand_rows,
    }


def get_financeiro(marketplace: str, year: int, month: int) -> dict:
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
        tk_rows = mb.query(sql)

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
        ml_rows = mb.query(sql)

    tk_by_brand = {r["brand"]: r for r in tk_rows}
    ml_by_brand = {r["brand"]: r for r in ml_rows}

    brands_set = BRANDS_IN_SCOPE if marketplace in ("all", "tiktok") else ML_BRANDS

    brand_rows = []
    for brand in brands_set:
        tk = tk_by_brand.get(brand, {})
        ml = ml_by_brand.get(brand, {})

        if not tk and not ml:
            continue

        row: dict = {
            "brand": brand,
            "label": BRAND_LABELS.get(brand, brand.upper()),
        }

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

        brand_rows.append(row)

    # KPIs agregados
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
        },
        "brands": brand_rows,
    }


def get_quality(marketplace: str, year: int, month: int) -> dict:
    start, end = _month_bounds(year, month)

    tk_rows: list[dict] = []
    if marketplace in ("all", "tiktok"):
        # canceled/refunded/returned são sempre zero na tabela — usar problem_rate e avg_delivery_hours
        # que já são colunas pré-computadas corretas.
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
        tk_rows = mb.query(sql)

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
        ml_rows = mb.query(sql)

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
        ml_mensal_rows = mb.query(sql_mensal)
    ml_mensal_by_brand = {r["brand"]: r for r in ml_mensal_rows}

    tk_by_brand = {r["brand"]: r for r in tk_rows}
    ml_by_brand = {r["brand"]: r for r in ml_rows}

    brands_set = BRANDS_IN_SCOPE if marketplace in ("all", "tiktok") else ML_BRANDS

    brand_rows = []
    for brand in brands_set:
        tk = tk_by_brand.get(brand, {})
        ml = ml_by_brand.get(brand, {})
        mm = ml_mensal_by_brand.get(brand, {})

        if not tk and not ml:
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
        })

    # KPIs agregados — TikTok (problem_rate é média ponderada por pedidos; cancel_rate indisponível)
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
    kpi_tk_cancel_rate = None

    tk_del_pairs = [(r["tiktok_avg_delivery_days"], r["tiktok_orders"] or 0)
                    for r in brand_rows if r.get("tiktok_avg_delivery_days") is not None]
    kpi_tk_avg_del = None
    if tk_del_pairs:
        total_w = sum(w for _, w in tk_del_pairs)
        kpi_tk_avg_del = round(sum(d * w for d, w in tk_del_pairs) / total_w, 1) if total_w > 0 else None

    # KPIs agregados — ML
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

    return {
        "ref_month": f"{year:04d}-{month:02d}",
        "marketplace": marketplace,
        "kpis": {
            "tiktok_problem_rate": kpi_tk_problem_rate,
            "tiktok_cancel_rate": kpi_tk_cancel_rate,
            "tiktok_avg_delivery_days": kpi_tk_avg_del,
            "ml_cancel_rate_pct": kpi_ml_cancel,
            "ml_not_delivered_rate_pct": kpi_ml_nd,
            "ml_avg_delivery_days": kpi_ml_avg_del,
        },
        "brands": brand_rows,
    }


def get_daily(brand: str, marketplace: str, days_back: int = 60) -> dict:
    date_from = date.today() - timedelta(days=days_back)
    rows = []

    if marketplace in ("all", "tiktok") and brand in BRANDS_IN_SCOPE:
        sql = f"""
            SELECT date, gmv, orders AS orders,
                   avg_ticket, NULL AS ad_spend
            FROM gold.tiktok_brand_daily
            WHERE brand = '{brand}'
              AND date >= '{date_from}'
            ORDER BY date
        """
        for r in mb.query(sql):
            rows.append({
                "date": str(r["date"])[:10],
                "tiktok_gmv": _float(r["gmv"]) or None,
                "ml_gmv": None,
                "orders_tk": int(_float(r["orders"])),
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
        for r in mb.query(sql):
            ml_data[str(r["date"])[:10]] = r

    # Merge TikTok + ML by date
    merged: dict[str, dict] = {}
    for r in rows:
        d = r["date"]
        merged[d] = r
    for d, r in ml_data.items():
        if d not in merged:
            merged[d] = {"date": d, "tiktok_gmv": None, "ml_gmv": None, "orders_tk": 0, "ad_spend": None}
        merged[d]["ml_gmv"] = _float(r["gmv"]) or None
        merged[d]["ad_spend"] = _float(r.get("ad_spend") or 0) or None

    result = []
    for d in sorted(merged.keys()):
        v = merged[d]
        total = (v.get("tiktok_gmv") or 0) + (v.get("ml_gmv") or 0)
        orders = v.get("orders_tk", 0)
        result.append({
            "date": d,
            "tiktok_gmv": v.get("tiktok_gmv"),
            "ml_gmv": v.get("ml_gmv"),
            "total_gmv": total,
            "orders": orders,
            "avg_ticket": total / orders if orders > 0 else None,
            "ad_spend": v.get("ad_spend"),
        })

    return {"brand": brand, "marketplace": marketplace, "data": result}


# ---------------------------------------------------------------------------
# Tempo Real — hourly GMV from gold.tiktok_shop_hourly
# ---------------------------------------------------------------------------

def get_tempo_real() -> dict:
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

    today_rows = mb.query(sql_today)
    avg_rows   = mb.query(sql_avg7)

    avg_idx: dict[tuple, float] = {}
    for r in avg_rows:
        avg_idx[(r["brand"], int(_float(r["hour_brt"])))] = _float(r["gmv_avg7d"])

    # gold.tiktok_shop_hourly armazena gmv_acumulado/prior como total do dia em todas as linhas
    # (não é running sum). Recalculamos aqui a partir de gmv_hour.
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

    # Compute real running cumulative sums per brand
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
        # ultima hora ativa = última hora com gmv > 0
        active_hours = [h for h in hours if h["gmv_hour"] > 0]
        last_active = active_hours[-1] if active_hours else hours[-1]
        latest = hours[-1]
        gmv_hoje  = latest["gmv_acumulado"]
        gmv_ontem_cumul = latest.get("gmv_acumulado_prior")
        delta_pct = round((gmv_hoje - gmv_ontem_cumul) / gmv_ontem_cumul * 100, 1) if gmv_ontem_cumul else None
        # ritmo projetado: gmv/hora_ativa * 24
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

    total_hoje  = sum(b["gmv_hoje"] for b in brands_result)
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
# Brand Detail — deep drill-down for /brand/[brand] page
# ---------------------------------------------------------------------------

def get_brand_detail(brand: str, year: int, month: int) -> dict:
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
                 ELSE NULL END AS viewers_pct_55_plus
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

    monthly_rows = mb.query(sql_monthly)
    daily_rows   = mb.query(sql_daily)
    creators     = mb.query(sql_creators)
    products     = mb.query(sql_products)

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
