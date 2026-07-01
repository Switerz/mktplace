"""
Sync incremental idempotente das tabelas de produto para o Neon.

Uso:
    python -m pipelines.sync_produtos --source shopee
    python -m pipelines.sync_produtos --source ml
    python -m pipelines.sync_produtos --source tiktok
    python -m pipelines.sync_produtos --source all
    python -m pipelines.sync_produtos --source tiktok --days 14

Fontes (somente leitura):
    shopee  -> local PG localhost:5432/mktplace_control (marts.fact_shopee_product_monthly)
    ml      -> RDS gold.ml_produto_ranking  (snapshot — full refresh sempre)
    tiktok  -> RDS gold.tiktok_product_daily (incremental por date)

Destino (escrita):
    Neon marts.*  via ON CONFLICT DO UPDATE (idempotente)

Regras de seguranca:
    - Nao escreve nas fontes (RDS ou local PG)
    - Nao deleta dados do Neon (apenas upsert)
    - brands fora do escopo sao filtrados na leitura
"""
import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dotenv import load_dotenv

# Carregar .env ANTES de ler as variaveis de conexao abaixo — bug anterior:
# load_dotenv() so' era chamado dentro de main(), depois que NEON_URL/RDS_URL/
# LOCAL_URL ja tinham sido lidas do ambiente no import do modulo, entao o
# script so' funcionava se as variaveis já estivessem exportadas no shell.
load_dotenv(dotenv_path=str(Path(__file__).resolve().parent.parent / ".env"))
load_dotenv()

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
BRANDS_IN_SCOPE = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
DEFAULT_TIKTOK_DAYS = 7  # re-sync ultimos N dias (garante idempotencia em meses parciais)

# Abaixo deste percentual do total anterior no Neon, uma fonte que fez full
# refresh/backfill e' considerada suspeita e a carga e' abortada sem commit
# (protege contra fonte RDS/local retornando parcial por erro silencioso).
MIN_ROWS_RATIO = 0.5

NEON_URL  = os.environ.get("DATABASE_URL", "")
RDS_URL   = os.environ.get("DATAMART_DATABASE_URL", "")
LOCAL_URL = os.environ.get(
    "LOCAL_PG_URL",
    "postgresql://postgres:postgres@localhost:5432/mktplace_control",
)


def _assert_distinct_targets() -> None:
    """Guarda contra .env mal configurado apontando origem e destino para o mesmo host.

    Nao decodifica credenciais: compara apenas as strings de conexao completas.
    """
    urls = {"NEON (destino)": NEON_URL, "RDS (fonte)": RDS_URL, "LOCAL (fonte)": LOCAL_URL}
    if NEON_URL and NEON_URL in (RDS_URL, LOCAL_URL):
        raise RuntimeError(
            "DATABASE_URL (Neon/destino) e igual a uma das fontes (RDS/local). "
            "Sync abortado para evitar escrita no banco errado."
        )
    _ = urls  # mantido para depuracao futura sem expor valores em log


def _neon():
    if not NEON_URL:
        raise RuntimeError("DATABASE_URL nao definido")
    return psycopg2.connect(NEON_URL, connect_timeout=15)


def _rds():
    if not RDS_URL:
        raise RuntimeError("DATAMART_DATABASE_URL nao definido")
    return psycopg2.connect(RDS_URL, cursor_factory=RealDictCursor, connect_timeout=15)


def _local():
    return psycopg2.connect(LOCAL_URL, cursor_factory=RealDictCursor, connect_timeout=5)


def _brands_sql(brands=BRANDS_IN_SCOPE):
    return "(" + ",".join(f"'{b}'" for b in sorted(brands)) + ")"


def _active_brands(default=BRANDS_IN_SCOPE) -> set:
    """Le brands ativas de marts.dim_loja no Neon (fonte de verdade do projeto).

    Cai para o conjunto hardcoded se a leitura falhar (Neon indisponivel etc.),
    para nao travar o sync inteiro por um problema de conectividade pontual.
    """
    try:
        conn = _neon()
        cur = conn.cursor()
        cur.execute("SELECT brand_key FROM marts.dim_loja WHERE ativo = true")
        rows = {r[0] for r in cur.fetchall()}
        cur.close(); conn.close()
        if rows:
            return rows
    except Exception as e:
        print(f"[aviso] falha ao ler marts.dim_loja, usando lista hardcoded: {e}")
    return set(default)


# ---------------------------------------------------------------------------
# Auditoria (audit.source_sync_run) — mesmo contrato usado por
# pipelines/ingestion/daily_performance.py, para manter um unico historico
# de execucoes consultavel via docs/runbook_sync_produtos.md.
# ---------------------------------------------------------------------------
def _audit_start(conn, source_name: str, marketplace_id: int) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit.source_sync_run (source_name, marketplace_id, status, started_at)
        VALUES (%s, %s, 'running', NOW())
        RETURNING sync_run_id
        """,
        (source_name, marketplace_id),
    )
    run_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return run_id


def _audit_finish(conn, run_id: int, status: str, extracted: int, loaded: int,
                   min_d=None, max_d=None, error: str | None = None) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE audit.source_sync_run SET
            finished_at = NOW(), status = %s, rows_extracted = %s, rows_loaded = %s,
            source_min_date = %s, source_max_date = %s, error_message = %s
        WHERE sync_run_id = %s
        """,
        (status, extracted, loaded, min_d, max_d, error, run_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Shopee: local PG -> Neon
# Estrategia: sincroniza ref_months onde Neon tem menos linhas que a fonte,
#             mais o mes corrente (para capturar atualizacoes recentes).
# ---------------------------------------------------------------------------
def sync_shopee(full: bool = False, brands: set = None) -> dict:
    brands = brands or BRANDS_IN_SCOPE
    audit_conn = _neon()
    run_id = _audit_start(audit_conn, "shopee_product_monthly", marketplace_id=3)

    try:
        print("[shopee] lendo fonte (local PG)...")
        src = _local()
        sc = src.cursor(cursor_factory=RealDictCursor)

        if full:
            sc.execute(
                "SELECT * FROM marts.fact_shopee_product_monthly WHERE brand = ANY(%s)",
                (list(brands),),
            )
        else:
            # Descobrir quais ref_months precisam de sync:
            # 1) mes atual e anterior (dados podem ter mudado)
            today = date.today()
            first_this  = date(today.year, today.month, 1)
            first_prev  = (first_this - timedelta(days=1)).replace(day=1)
            sc.execute(
                """
                SELECT * FROM marts.fact_shopee_product_monthly
                WHERE brand = ANY(%s)
                  AND ref_month >= %s
                """,
                (list(brands), first_prev),
            )

        rows = sc.fetchall()
        sc.close(); src.close()
        print(f"[shopee] fonte: {len(rows)} linhas")

        if not rows:
            print("[shopee] nada a sincronizar")
            _audit_finish(audit_conn, run_id, "success", 0, 0)
            audit_conn.close()
            return {"source": 0, "upserted": 0}

        dst = _neon()
        try:
            dc = dst.cursor()

            UPSERT = """
                INSERT INTO marts.fact_shopee_product_monthly
                    (ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
                     gmv, units_sold, completed_orders, canceled_orders,
                     cancel_rate_pct, unique_buyers, avg_price)
                VALUES %s
                ON CONFLICT (ref_month, brand, sku_ref_key, product_name)
                DO UPDATE SET
                    sku_ref          = EXCLUDED.sku_ref,
                    variation_name   = EXCLUDED.variation_name,
                    gmv              = EXCLUDED.gmv,
                    units_sold       = EXCLUDED.units_sold,
                    completed_orders = EXCLUDED.completed_orders,
                    canceled_orders  = EXCLUDED.canceled_orders,
                    cancel_rate_pct  = EXCLUDED.cancel_rate_pct,
                    unique_buyers    = EXCLUDED.unique_buyers,
                    avg_price        = EXCLUDED.avg_price,
                    ingested_at      = NOW()
            """

            batch = [
                (
                    r["ref_month"], r["brand"], r["sku_ref"], r["sku_ref_key"],
                    r["product_name"], r["variation_name"],
                    r["gmv"], r["units_sold"], r["completed_orders"], r["canceled_orders"],
                    r["cancel_rate_pct"], r["unique_buyers"], r["avg_price"],
                )
                for r in rows
            ]
            execute_values(dc, UPSERT, batch, page_size=500)
            dst.commit()
            dc.close()
        except Exception:
            dst.rollback()
            raise
        finally:
            dst.close()

        print(f"[shopee] Neon: {len(batch)} linhas upserted")
        ref_months = [r["ref_month"] for r in rows]
        _audit_finish(
            audit_conn, run_id, "success", len(rows), len(batch),
            min(ref_months), max(ref_months),
        )
        audit_conn.close()
        return {"source": len(rows), "upserted": len(batch)}

    except Exception as exc:
        _audit_finish(audit_conn, run_id, "failed", 0, 0, error=str(exc)[:500])
        audit_conn.close()
        raise


# ---------------------------------------------------------------------------
# ML: RDS gold.ml_produto_ranking -> Neon
# Estrategia: full refresh sempre (snapshot sem dimensao temporal, 1326 linhas)
#             Deduplicar por (brand, item_id) mantendo maior gross_revenue.
# ---------------------------------------------------------------------------
def sync_ml(brands: set = None) -> dict:
    brands = brands or BRANDS_IN_SCOPE
    audit_conn = _neon()
    run_id = _audit_start(audit_conn, "ml_produto_ranking", marketplace_id=2)

    try:
        cur = audit_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM marts.fact_ml_produto_ranking")
        prev_count = cur.fetchone()[0]
        cur.close()

        print("[ml] lendo fonte (RDS gold.ml_produto_ranking)...")
        src = _rds()
        sc = src.cursor(cursor_factory=RealDictCursor)

        sc.execute(f"""
            SELECT DISTINCT ON (brand, item_id)
                   brand, item_id, seller_sku, title,
                   gross_revenue, units_sold, unique_buyers, units_per_buyer,
                   cancel_rate_pct, ad_spend, ad_roas, ad_acos_pct, days_advertised,
                   revenue_share_pct, cumulative_revenue_pct, estimated_margin,
                   price_spread_pct, pareto_bucket, revenue_velocity,
                   ad_efficiency, action_signal, product_status,
                   first_sale, last_sale
            FROM gold.ml_produto_ranking
            WHERE brand IN {_brands_sql(brands)}
            ORDER BY brand, item_id, gross_revenue DESC NULLS LAST
        """)
        rows = sc.fetchall()
        sc.close(); src.close()
        print(f"[ml] fonte (pos-dedup): {len(rows)} linhas")

        if prev_count > 0 and len(rows) < prev_count * MIN_ROWS_RATIO:
            raise RuntimeError(
                f"[ml] queda suspeita de linhas: fonte={len(rows)} vs Neon atual={prev_count} "
                f"(limite={MIN_ROWS_RATIO:.0%}). Carga abortada sem commit — investigar RDS antes de repetir."
            )

        dst = _neon()
        now = datetime.now(timezone.utc)

        UPSERT = """
        INSERT INTO marts.fact_ml_produto_ranking
            (brand, item_id, seller_sku, title,
             gross_revenue, units_sold, unique_buyers, units_per_buyer,
             cancel_rate_pct, ad_spend, ad_roas, ad_acos_pct, days_advertised,
             revenue_share_pct, cumulative_revenue_pct, estimated_margin,
             price_spread_pct, pareto_bucket, revenue_velocity,
             ad_efficiency, action_signal, product_status,
             first_sale, last_sale, refreshed_at)
        VALUES %s
        ON CONFLICT (brand, item_id)
        DO UPDATE SET
            seller_sku             = EXCLUDED.seller_sku,
            title                  = EXCLUDED.title,
            gross_revenue          = EXCLUDED.gross_revenue,
            units_sold             = EXCLUDED.units_sold,
            unique_buyers          = EXCLUDED.unique_buyers,
            units_per_buyer        = EXCLUDED.units_per_buyer,
            cancel_rate_pct        = EXCLUDED.cancel_rate_pct,
            ad_spend               = EXCLUDED.ad_spend,
            ad_roas                = EXCLUDED.ad_roas,
            ad_acos_pct            = EXCLUDED.ad_acos_pct,
            days_advertised        = EXCLUDED.days_advertised,
            revenue_share_pct      = EXCLUDED.revenue_share_pct,
            cumulative_revenue_pct = EXCLUDED.cumulative_revenue_pct,
            estimated_margin       = EXCLUDED.estimated_margin,
            price_spread_pct       = EXCLUDED.price_spread_pct,
            pareto_bucket          = EXCLUDED.pareto_bucket,
            revenue_velocity       = EXCLUDED.revenue_velocity,
            ad_efficiency          = EXCLUDED.ad_efficiency,
            action_signal          = EXCLUDED.action_signal,
            product_status         = EXCLUDED.product_status,
            first_sale             = EXCLUDED.first_sale,
            last_sale              = EXCLUDED.last_sale,
            refreshed_at           = NOW(),
            ingested_at            = NOW()
        """

        batch = [
            (
                r["brand"], r["item_id"], r["seller_sku"], r["title"],
                r["gross_revenue"], r["units_sold"], r["unique_buyers"], r["units_per_buyer"],
                r["cancel_rate_pct"], r["ad_spend"], r["ad_roas"], r["ad_acos_pct"],
                r["days_advertised"], r["revenue_share_pct"], r["cumulative_revenue_pct"],
                r["estimated_margin"], r["price_spread_pct"],
                r["pareto_bucket"], r["revenue_velocity"], r["ad_efficiency"],
                r["action_signal"], r["product_status"],
                r["first_sale"], r["last_sale"], now,
            )
            for r in rows
        ]

        try:
            dc = dst.cursor()
            execute_values(dc, UPSERT, batch, page_size=500)
            dst.commit()
            dc.close()
        except Exception:
            dst.rollback()
            raise
        finally:
            dst.close()

        print(f"[ml] Neon: {len(batch)} linhas upserted")
        sales_dates = [r["last_sale"] for r in rows if r.get("last_sale")]
        _audit_finish(
            audit_conn, run_id, "success", len(rows), len(batch),
            min(sales_dates) if sales_dates else None,
            max(sales_dates) if sales_dates else None,
        )
        audit_conn.close()
        return {"source": len(rows), "upserted": len(batch)}

    except Exception as exc:
        _audit_finish(audit_conn, run_id, "failed", 0, 0, error=str(exc)[:500])
        audit_conn.close()
        raise


# ---------------------------------------------------------------------------
# TikTok: RDS gold.tiktok_product_daily -> Neon
# Estrategia: incremental por date. Sincroniza ultimos `days` dias + hoje.
#             Re-sincronizar semana garante idempotencia se fonte for corrigida.
# ---------------------------------------------------------------------------
def sync_tiktok(days: int = DEFAULT_TIKTOK_DAYS, full: bool = False, brands: set = None) -> dict:
    brands = brands or BRANDS_IN_SCOPE
    audit_conn = _neon()
    run_id = _audit_start(audit_conn, "tiktok_product_daily", marketplace_id=1)

    try:
        ac = audit_conn.cursor()
        if full:
            start_date = date(2025, 10, 1)
            print(f"[tiktok] full backfill desde {start_date}...")
        else:
            # Determinar data inicial: max(date) no Neon - days (ou 2025-10-01 se vazio)
            ac.execute("SELECT MAX(date) AS max_d FROM marts.fact_tiktok_product_daily")
            r = ac.fetchone()
            max_neon = r[0] if r and r[0] else date(2025, 9, 30)
            start_date = max_neon - timedelta(days=days)
            print(f"[tiktok] incremental desde {start_date} (Neon max={max_neon}, lookback={days}d)...")
        ac.close()

        src = _rds()
        sc = src.cursor(cursor_factory=RealDictCursor)

        sc.execute(f"""
            SELECT date, brand, product_id, product_name,
                   gmv, orders, items_sold,
                   gmv_video, gmv_live, gmv_product_card,
                   items_sold_video, items_sold_live, items_sold_product_card,
                   pct_gmv_video, pct_gmv_live, pct_gmv_card,
                   canceled, refunded, returned, problem_rate,
                   rating_avg, total_ratings
            FROM gold.tiktok_product_daily
            WHERE brand IN {_brands_sql(brands)}
              AND date >= %s
            ORDER BY date, product_id
        """, (start_date,))
        rows = sc.fetchall()
        sc.close(); src.close()
        print(f"[tiktok] fonte: {len(rows)} linhas")

        if not rows:
            print("[tiktok] nada a sincronizar")
            _audit_finish(audit_conn, run_id, "success", 0, 0, start_date, start_date)
            audit_conn.close()
            return {"source": 0, "upserted": 0}

        if full and len(rows) < 1000:
            raise RuntimeError(
                f"[tiktok] full backfill retornou apenas {len(rows)} linhas desde {start_date} "
                "— abaixo do esperado para um historico completo. Carga abortada sem commit."
            )

        dst = _neon()
        UPSERT = """
            INSERT INTO marts.fact_tiktok_product_daily
                (date, brand, product_id, product_name,
                 gmv, orders, items_sold,
                 gmv_video, gmv_live, gmv_product_card,
                 items_sold_video, items_sold_live, items_sold_product_card,
                 pct_gmv_video, pct_gmv_live, pct_gmv_card,
                 canceled, refunded, returned, problem_rate,
                 rating_avg, total_ratings)
            VALUES %s
            ON CONFLICT (date, product_id)
            DO UPDATE SET
                brand                   = EXCLUDED.brand,
                product_name            = EXCLUDED.product_name,
                gmv                     = EXCLUDED.gmv,
                orders                  = EXCLUDED.orders,
                items_sold              = EXCLUDED.items_sold,
                gmv_video               = EXCLUDED.gmv_video,
                gmv_live                = EXCLUDED.gmv_live,
                gmv_product_card        = EXCLUDED.gmv_product_card,
                items_sold_video        = EXCLUDED.items_sold_video,
                items_sold_live         = EXCLUDED.items_sold_live,
                items_sold_product_card = EXCLUDED.items_sold_product_card,
                pct_gmv_video           = EXCLUDED.pct_gmv_video,
                pct_gmv_live            = EXCLUDED.pct_gmv_live,
                pct_gmv_card            = EXCLUDED.pct_gmv_card,
                canceled                = EXCLUDED.canceled,
                refunded                = EXCLUDED.refunded,
                returned                = EXCLUDED.returned,
                problem_rate            = EXCLUDED.problem_rate,
                rating_avg              = EXCLUDED.rating_avg,
                total_ratings           = EXCLUDED.total_ratings,
                ingested_at             = NOW()
        """

        BATCH_SIZE = 1000
        inserted = 0
        try:
            dc = dst.cursor()
            for i in range(0, len(rows), BATCH_SIZE):
                chunk = [
                    (
                        r["date"], r["brand"], r["product_id"], r["product_name"],
                        r["gmv"], r["orders"], r["items_sold"],
                        r["gmv_video"], r["gmv_live"], r["gmv_product_card"],
                        r["items_sold_video"], r["items_sold_live"], r["items_sold_product_card"],
                        r["pct_gmv_video"], r["pct_gmv_live"], r["pct_gmv_card"],
                        r["canceled"], r["refunded"], r["returned"], r["problem_rate"],
                        r["rating_avg"], r["total_ratings"],
                    )
                    for r in rows[i : i + BATCH_SIZE]
                ]
                execute_values(dc, UPSERT, chunk, page_size=500)
                inserted += len(chunk)
            dst.commit()
            dc.close()
        except Exception:
            dst.rollback()
            raise
        finally:
            dst.close()

        print(f"[tiktok] Neon: {inserted} linhas upserted")
        dates = [r["date"] for r in rows]
        _audit_finish(audit_conn, run_id, "success", len(rows), inserted, min(dates), max(dates))
        audit_conn.close()
        return {"source": len(rows), "upserted": inserted}

    except Exception as exc:
        _audit_finish(audit_conn, run_id, "failed", 0, 0, error=str(exc)[:500])
        audit_conn.close()
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Sync produtos para Neon")
    parser.add_argument("--source", choices=["shopee", "ml", "tiktok", "all"], default="all")
    parser.add_argument("--days",   type=int, default=DEFAULT_TIKTOK_DAYS,
                        help="Lookback em dias para TikTok incremental (default: %(default)s)")
    parser.add_argument("--full",   action="store_true",
                        help="Forcear full backfill (Shopee e TikTok)")
    args = parser.parse_args()

    _assert_distinct_targets()
    brands = _active_brands()
    print(f"[main] brands ativas (marts.dim_loja): {sorted(brands)}")

    t0 = time.time()
    results = {}
    failures = {}

    sources = []
    if args.source in ("shopee", "all"):
        sources.append(("shopee", lambda: sync_shopee(full=args.full, brands=brands)))
    if args.source in ("ml", "all"):
        sources.append(("ml", lambda: sync_ml(brands=brands)))
    if args.source in ("tiktok", "all"):
        sources.append(("tiktok", lambda: sync_tiktok(days=args.days, full=args.full, brands=brands)))

    for name, fn in sources:
        try:
            results[name] = fn()
        except Exception as exc:
            print(f"[ERRO] [{name}] sync falhou: {exc}")
            failures[name] = str(exc)

    elapsed = time.time() - t0
    print(f"\n[DONE] {elapsed:.1f}s | sucesso={results} | falhas={failures}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
