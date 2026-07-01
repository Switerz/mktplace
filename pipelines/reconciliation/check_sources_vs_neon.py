"""
Reconciliacao somente leitura: fonte (RDS/local PG) vs Neon (marts.*).

Nao escreve em nenhum banco. Compara, por marketplace/brand/mes:
  - contagem de registros
  - datas minima/maxima
  - GMV
  - pedidos
  - gasto com anuncios (quando disponivel na fonte)

Tambem verifica, direto no Neon:
  - duplicidade pela chave de grain de cada tabela
  - nulos em chaves obrigatorias
  - datas futuras (> hoje + 1 dia) ou fora do range plausivel dos arquivos-fonte

Uso:
    python -m pipelines.reconciliation.check_sources_vs_neon
    python -m pipelines.reconciliation.check_sources_vs_neon --only tiktok
    python -m pipelines.reconciliation.check_sources_vs_neon --only shopee-produtos

Nota: TikTok/ML "orders" no gold RDS ja' equivale ao "orders" do mart apos o
transform (paid_orders no ML, orders no TikTok) — nao comparamos metricas
semanticamente diferentes (ex: total_orders do ML vs orders pagos).
"""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor

BRANDS_IN_SCOPE = ("apice", "barbours", "kokeshi", "lescent", "rituaria")


def _connect(url: str, connect_timeout: int = 15, **kw):
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=connect_timeout, **kw)


def _fetch(conn, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def _fmt_list(t) -> str:
    return "(" + ", ".join(f"'{b}'" for b in t) + ")"


def _print_table(title: str, rows: list[dict]) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("(sem linhas)")
        return
    cols = list(rows[0].keys())
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(r[c]) for c in cols))


# ---------------------------------------------------------------------------
# TikTok / ML: RDS gold vs Neon fact_marketplace_daily_performance
# ---------------------------------------------------------------------------
def check_daily_fact(rds_url: str, neon_url: str, months_back: int = 6) -> None:
    since = date.today().replace(day=1)
    for _ in range(months_back):
        since = (since - timedelta(days=1)).replace(day=1)

    rds = _connect(rds_url)
    neon = _connect(neon_url)

    tiktok_src = _fetch(rds, f"""
        SELECT brand, DATE_TRUNC('month', date)::date AS mes,
               COUNT(*) AS n, MIN(date) AS min_d, MAX(date) AS max_d,
               SUM(gmv) AS gmv, SUM(orders) AS orders
        FROM gold.tiktok_brand_daily
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)} AND date >= %s
        GROUP BY brand, DATE_TRUNC('month', date)
        ORDER BY brand, mes
    """, (since,))
    _print_table("RDS gold.tiktok_brand_daily por brand/mes (fonte)", tiktok_src)

    tiktok_neon = _fetch(neon, """
        SELECT l.brand_key AS brand, DATE_TRUNC('month', f.date)::date AS mes,
               COUNT(*) AS n, MIN(f.date) AS min_d, MAX(f.date) AS max_d,
               SUM(f.gmv) AS gmv, SUM(f.orders) AS orders
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.marketplace_id = 1 AND f.date >= %s
        GROUP BY l.brand_key, DATE_TRUNC('month', f.date)
        ORDER BY brand, mes
    """, (since,))
    _print_table("Neon marts.fact_marketplace_daily_performance (TikTok) por brand/mes", tiktok_neon)

    ml_src = _fetch(rds, f"""
        SELECT brand, DATE_TRUNC('month', ref_date)::date AS mes,
               COUNT(*) AS n, MIN(ref_date) AS min_d, MAX(ref_date) AS max_d,
               SUM(gmv) AS gmv, SUM(paid_orders) AS orders, SUM(ad_spend) AS ad_spend
        FROM gold.ml_gestao_diaria
        WHERE brand IN {_fmt_list(BRANDS_IN_SCOPE)} AND ref_date >= %s
        GROUP BY brand, DATE_TRUNC('month', ref_date)
        ORDER BY brand, mes
    """, (since,))
    _print_table("RDS gold.ml_gestao_diaria por brand/mes (fonte)", ml_src)

    ml_neon = _fetch(neon, """
        SELECT l.brand_key AS brand, DATE_TRUNC('month', f.date)::date AS mes,
               COUNT(*) AS n, MIN(f.date) AS min_d, MAX(f.date) AS max_d,
               SUM(f.gmv) AS gmv, SUM(f.orders) AS orders, SUM(f.ad_spend) AS ad_spend
        FROM marts.fact_marketplace_daily_performance f
        JOIN marts.dim_loja l ON l.loja_id = f.loja_id
        WHERE f.marketplace_id = 2 AND f.date >= %s
        GROUP BY l.brand_key, DATE_TRUNC('month', f.date)
        ORDER BY brand, mes
    """, (since,))
    _print_table("Neon marts.fact_marketplace_daily_performance (ML) por brand/mes", ml_neon)

    # Diff resumido: meses presentes na fonte mas ausentes/desatualizados no Neon
    def _key(r):
        return (r["brand"], r["mes"])

    for label, src, neon_rows in (("TikTok", tiktok_src, tiktok_neon), ("ML", ml_src, ml_neon)):
        src_map = {_key(r): r for r in src}
        neon_map = {_key(r): r for r in neon_rows}
        missing = sorted(set(src_map) - set(neon_map))
        stale = [
            k for k in set(src_map) & set(neon_map)
            if src_map[k]["max_d"] > neon_map[k]["max_d"]
        ]
        print(f"\n[{label}] meses com dados na fonte e ausentes no Neon: {missing}")
        print(f"[{label}] meses onde Neon esta desatualizado vs fonte (max_d menor): {stale}")

    rds.close(); neon.close()


# ---------------------------------------------------------------------------
# Produtos Shopee: local PG vs Neon
# ---------------------------------------------------------------------------
def check_shopee_produtos(local_url: str, neon_url: str) -> None:
    local = _connect(local_url, connect_timeout=5)
    neon = _connect(neon_url)

    local_rows = _fetch(local, """
        SELECT brand, ref_month, COUNT(*) AS n, SUM(gmv) AS gmv
        FROM marts.fact_shopee_product_monthly
        GROUP BY brand, ref_month ORDER BY brand, ref_month
    """)
    neon_rows = _fetch(neon, """
        SELECT brand, ref_month, COUNT(*) AS n, SUM(gmv) AS gmv
        FROM marts.fact_shopee_product_monthly
        GROUP BY brand, ref_month ORDER BY brand, ref_month
    """)

    local_map = {(r["brand"], r["ref_month"]): r for r in local_rows}
    neon_map = {(r["brand"], r["ref_month"]): r for r in neon_rows}
    diffs = []
    for k in sorted(set(local_map) | set(neon_map)):
        l, n = local_map.get(k), neon_map.get(k)
        if l is None or n is None or l["n"] != n["n"] or l["gmv"] != n["gmv"]:
            diffs.append({"brand": k[0], "ref_month": k[1], "local": l, "neon": n})
    print(f"\n=== Shopee produtos: divergencias local PG vs Neon ({len(diffs)}) ===")
    for d in diffs[:20]:
        print(d)
    if not diffs:
        print("Nenhuma divergencia — Neon reflete exatamente o PostgreSQL local.")

    # Deteccao de datas fora do range plausivel dos exports (jan/2026-jun/2026,
    # ajustar conforme novos arquivos forem adicionados em shopee/{brand}/).
    plausible_max = date.today().replace(day=1)
    future = _fetch(neon, """
        SELECT brand, ref_month, COUNT(*) AS n, SUM(gmv) AS gmv
        FROM marts.fact_shopee_product_monthly
        WHERE ref_month > %s
        GROUP BY brand, ref_month ORDER BY ref_month
    """, (plausible_max,))
    print(f"\n=== Neon fact_shopee_product_monthly: ref_month > mes atual ({len(future)} grupos) ===")
    for r in future:
        print(r)
    if future:
        print(
            "ATENCAO: ref_month no futuro indica corrupcao de data na origem "
            "(ver docs/sections/produtos_audit.md) — nao sincronizar para Neon "
            "sem primeiro corrigir/re-rodar apps/api/etl/load_shopee_products.py "
            "no PostgreSQL local com a correcao de parsing de data."
        )

    local.close(); neon.close()


# ---------------------------------------------------------------------------
# Neon: duplicidade e nulos em chaves obrigatorias (qualquer tabela de grain)
# ---------------------------------------------------------------------------
def check_neon_integrity(neon_url: str) -> None:
    neon = _connect(neon_url)

    checks = [
        ("fact_marketplace_daily_performance", "date, loja_id, marketplace_id",
         "date IS NULL OR loja_id IS NULL OR marketplace_id IS NULL",
         "date > CURRENT_DATE + 1"),
        ("fact_shopee_product_monthly", "ref_month, brand, sku_ref_key, product_name",
         "ref_month IS NULL OR brand IS NULL",
         "ref_month > DATE_TRUNC('month', CURRENT_DATE)"),
        ("fact_ml_produto_ranking", "brand, item_id",
         "brand IS NULL OR item_id IS NULL",
         None),
        ("fact_tiktok_product_daily", "date, product_id",
         "date IS NULL OR product_id IS NULL",
         "date > CURRENT_DATE + 1"),
    ]

    for table, grain, null_pred, future_pred in checks:
        dup = _fetch(neon, f"""
            SELECT {grain}, COUNT(*) AS n
            FROM marts.{table}
            GROUP BY {grain}
            HAVING COUNT(*) > 1
            LIMIT 5
        """)
        nulls = _fetch(neon, f"SELECT COUNT(*) AS n FROM marts.{table} WHERE {null_pred}")
        print(f"\n=== marts.{table}: duplicidade por ({grain}) ===")
        print(dup or "nenhuma duplicidade")
        print(f"marts.{table}: linhas com nulos em chave obrigatoria = {nulls[0]['n']}")
        if future_pred:
            future = _fetch(neon, f"SELECT COUNT(*) AS n FROM marts.{table} WHERE {future_pred}")
            print(f"marts.{table}: linhas com data futura/implausivel = {future[0]['n']}")

    neon.close()


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    load_dotenv()

    parser = argparse.ArgumentParser(description="Reconciliacao somente leitura: fontes vs Neon")
    parser.add_argument("--only", choices=["daily", "shopee-produtos", "integrity", "all"], default="all")
    args = parser.parse_args()

    neon_url = os.environ["DATABASE_URL"]
    rds_url = os.environ.get("DATAMART_DATABASE_URL", "")
    local_url = os.environ.get("LOCAL_PG_URL", "postgresql://postgres:postgres@localhost:5432/mktplace_control")

    if args.only in ("daily", "all") and rds_url:
        check_daily_fact(rds_url, neon_url)
    if args.only in ("shopee-produtos", "all"):
        check_shopee_produtos(local_url, neon_url)
    if args.only in ("integrity", "all"):
        check_neon_integrity(neon_url)


if __name__ == "__main__":
    main()
