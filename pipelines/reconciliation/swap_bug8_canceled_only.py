"""
Gate 3 do Bug 8 (marts.fact_shopee_product_monthly) — SOMENTE PostgreSQL local.

Substitui a tabela real LOCAL a partir da staging já validada no Gate 2
(reconcile_bug8_canceled_only.py), dentro de uma unica transacao com
verificacao antes de qualquer COMMIT. Reusa a mesma guarda de host de
reconcile_bug8_canceled_only.py — nunca referencia DATABASE_URL (Neon) nem
DATAMART_DATABASE_URL (RDS), e recusa qualquer host fora de
localhost/127.0.0.1/::1.

Objetos usados (nomes fixos, definidos pelo Gate 2 já aprovado — este
script NAO descobre "o backup/staging mais recente" automaticamente):
    backup:  marts.fact_shopee_product_monthly_backup_bug8_20260702_150840
    staging: marts.fact_shopee_product_monthly_staging_bug8_20260702_150840
    real:    marts.fact_shopee_product_monthly

Fases:
  0. Pre-flight: os 3 objetos existem; host e' loopback; tabela real ==
     backup (EXCEPT nos dois sentidos, ausencia de drift desde o Gate 2);
     staging reconfere os numeros do Gate 2 (linhas, GMV, cancelados,
     duplicatas, nulos).
  1. Swap transacional: BEGIN; LOCK; TRUNCATE (so' da tabela real); INSERT
     com lista explicita de colunas a partir da staging; EXCEPT nos dois
     sentidos entre real e staging; COMMIT so' se tudo identico, senao
     ROLLBACK.
  2. Pos-swap: reconciliacao por brand x ref_month (real vs backup),
     confirma GMV/units/completed inalterados e +84 cancelados, confirma
     Pareto inalterado, smoke test read-only via performance_service
     contra uma engine SQLAlchemy que aponta EXPLICITAMENTE para o mesmo
     LOCAL_PG_URL validado (nunca para app.database/settings, que aponta
     para o Neon).

NAO apaga backup nem staging. NAO conecta a Neon/Data Mart em nenhum
momento. NAO faz nada no Neon.

Uso:
    python -m pipelines.reconciliation.swap_bug8_canceled_only
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from pipelines.reconciliation.reconcile_bug8_canceled_only import (  # noqa: E402
    _get_local_pg_url,
    _sanitize_url,
    ALLOWED_LOCAL_HOSTS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

REAL_TABLE = "fact_shopee_product_monthly"
BACKUP_TABLE = "fact_shopee_product_monthly_backup_bug8_20260702_150840"
STAGING_TABLE = "fact_shopee_product_monthly_staging_bug8_20260702_150840"

# Colunas de negocio — EXCLUI "id" (SERIAL, nunca deve ser comparado: cada
# tabela tem sua propria sequencia, ids divergem por construcao mesmo com
# dados de negocio identicos).
BUSINESS_COLS = (
    "ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name, "
    "gmv, units_sold, completed_orders, canceled_orders, cancel_rate_pct, "
    "unique_buyers, avg_price"
)

EXPECTED_STAGING_ROWS = 2471
EXPECTED_STAGING_GMV = 21174272.80
EXPECTED_STAGING_CANCELED = 53599


class PreflightError(RuntimeError):
    pass


class SwapValidationError(RuntimeError):
    pass


def _local(url: str):
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=10)


def _table_exists(cur, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'marts' AND table_name = %s",
        (table_name,),
    )
    return cur.fetchone() is not None


def _except_both_directions(cur, table_a: str, table_b: str, cols: str = BUSINESS_COLS) -> tuple[int, int]:
    """Retorna (linhas so' em A, linhas so' em B), comparando so' colunas de
    negocio (nunca 'id')."""
    cur.execute(f"SELECT COUNT(*) AS n FROM (SELECT {cols} FROM marts.{table_a} EXCEPT SELECT {cols} FROM marts.{table_b}) x")
    a_not_b = cur.fetchone()["n"]
    cur.execute(f"SELECT COUNT(*) AS n FROM (SELECT {cols} FROM marts.{table_b} EXCEPT SELECT {cols} FROM marts.{table_a}) x")
    b_not_a = cur.fetchone()["n"]
    return a_not_b, b_not_a


def _aggregates(cur, table: str) -> dict:
    cur.execute(f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(gmv), 0) AS gmv,
               COALESCE(SUM(units_sold), 0) AS units_sold,
               COALESCE(SUM(completed_orders), 0) AS completed_orders,
               COALESCE(SUM(canceled_orders), 0) AS canceled_orders
        FROM marts.{table}
    """)
    return dict(cur.fetchone())


def _duplicates_and_nulls(cur, table: str) -> tuple[int, int]:
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM (
            SELECT ref_month, brand, sku_ref_key, product_name
            FROM marts.{table}
            GROUP BY ref_month, brand, sku_ref_key, product_name
            HAVING COUNT(*) > 1
        ) d
    """)
    dupes = cur.fetchone()["n"]
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM marts.{table}
        WHERE ref_month IS NULL OR brand IS NULL OR product_name IS NULL
    """)
    nulls = cur.fetchone()["n"]
    return dupes, nulls


def _by_brand_month(cur, table: str) -> dict[tuple, dict]:
    cur.execute(f"""
        SELECT brand, ref_month, COUNT(*) AS n,
               COALESCE(SUM(gmv), 0) AS gmv,
               COALESCE(SUM(units_sold), 0) AS units_sold,
               COALESCE(SUM(completed_orders), 0) AS completed_orders,
               COALESCE(SUM(canceled_orders), 0) AS canceled_orders
        FROM marts.{table}
        GROUP BY brand, ref_month
        ORDER BY brand, ref_month
    """)
    return {(r["brand"], r["ref_month"]): dict(r) for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Fase 0 — pre-flight
# ---------------------------------------------------------------------------
def _preflight(conn, local_url: str) -> None:
    print("\n[fase 0] pre-flight")

    parsed_host = (urlsplit(local_url).hostname or "").lower()
    if parsed_host not in ALLOWED_LOCAL_HOSTS:
        raise PreflightError(f"host inesperado apos validacao: {parsed_host!r}")
    print(f"  1. LOCAL_PG_URL valida, host loopback confirmado: {_sanitize_url(local_url)}")

    cur = conn.cursor()
    for name in (BACKUP_TABLE, STAGING_TABLE, REAL_TABLE):
        if not _table_exists(cur, name):
            raise PreflightError(f"objeto nao encontrado: marts.{name}")
    print(f"  2. Os 3 objetos existem: marts.{BACKUP_TABLE}, marts.{STAGING_TABLE}, marts.{REAL_TABLE}")

    real_not_backup, backup_not_real = _except_both_directions(cur, REAL_TABLE, BACKUP_TABLE)
    if real_not_backup or backup_not_real:
        raise PreflightError(
            f"tabela real DIVERGE do backup do Gate 2 (real_not_backup={real_not_backup}, "
            f"backup_not_real={backup_not_real}) — algo alterou a tabela real desde o Gate 2. Abortando."
        )
    print("  3. Tabela real == backup (EXCEPT nos dois sentidos, 0 diferencas) — sem drift desde o Gate 2")

    staging_agg = _aggregates(cur, STAGING_TABLE)
    staging_dupes, staging_nulls = _duplicates_and_nulls(cur, STAGING_TABLE)
    checks = {
        "linhas": (int(staging_agg["n"]), EXPECTED_STAGING_ROWS),
        "gmv": (round(float(staging_agg["gmv"]), 2), EXPECTED_STAGING_GMV),
        "canceled_orders": (int(staging_agg["canceled_orders"]), EXPECTED_STAGING_CANCELED),
    }
    for label, (actual, expected) in checks.items():
        if actual != expected:
            raise PreflightError(f"staging {label}={actual}, esperado {expected} — reconciliacao do Gate 2 pode estar desatualizada")
    if staging_dupes:
        raise PreflightError(f"staging com {staging_dupes} chaves duplicadas")
    if staging_nulls:
        raise PreflightError(f"staging com {staging_nulls} linhas com nulos obrigatorios")
    print(f"  4/5. Staging reconfere: {staging_agg['n']} linhas, GMV={staging_agg['gmv']}, "
          f"canceled_orders={staging_agg['canceled_orders']}, duplicatas=0, nulos=0")
    cur.close()


# ---------------------------------------------------------------------------
# Fase 1 — swap transacional
# ---------------------------------------------------------------------------
def _swap(conn) -> dict:
    print("\n[fase 1] swap transacional (BEGIN implicito por psycopg2)")
    cur = conn.cursor()
    try:
        cur.execute(f"LOCK TABLE marts.{REAL_TABLE} IN ACCESS EXCLUSIVE MODE")
        print(f"  LOCK adquirido em marts.{REAL_TABLE}")

        cur.execute(f"TRUNCATE TABLE marts.{REAL_TABLE}")
        print(f"  TRUNCATE em marts.{REAL_TABLE} (staging/backup nao tocados)")

        cur.execute(f"""
            INSERT INTO marts.{REAL_TABLE}
                (ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
                 gmv, units_sold, completed_orders, canceled_orders,
                 cancel_rate_pct, unique_buyers, avg_price)
            SELECT ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
                   gmv, units_sold, completed_orders, canceled_orders,
                   cancel_rate_pct, unique_buyers, avg_price
            FROM marts.{STAGING_TABLE}
        """)
        print(f"  INSERT a partir de marts.{STAGING_TABLE} (colunas explicitas)")

        real_not_staging, staging_not_real = _except_both_directions(cur, REAL_TABLE, STAGING_TABLE)
        if real_not_staging or staging_not_real:
            raise SwapValidationError(
                f"real != staging apos INSERT (real_not_staging={real_not_staging}, "
                f"staging_not_real={staging_not_real})"
            )

        real_agg = _aggregates(cur, REAL_TABLE)
        staging_agg = _aggregates(cur, STAGING_TABLE)
        for key in ("n", "gmv", "units_sold", "completed_orders", "canceled_orders"):
            if round(float(real_agg[key]), 2) != round(float(staging_agg[key]), 2):
                raise SwapValidationError(f"agregado {key} diverge: real={real_agg[key]} staging={staging_agg[key]}")

        print(f"  EXCEPT nos dois sentidos = 0/0; agregados identicos "
              f"(n={real_agg['n']}, gmv={real_agg['gmv']}, canceled_orders={real_agg['canceled_orders']})")

        conn.commit()
        print("  COMMIT — swap concluido com sucesso")
        cur.close()
        return real_agg
    except Exception:
        conn.rollback()
        cur.close()
        print("  ROLLBACK — nenhuma alteracao foi persistida na tabela real")
        raise


# ---------------------------------------------------------------------------
# Fase 2 — pos-swap: reconciliacao + smoke test read-only
# ---------------------------------------------------------------------------
def _post_swap_report(conn) -> list[str]:
    print("\n[fase 2] reconciliacao pos-swap (real vs backup, por brand x mes)")
    cur = conn.cursor()
    before = _by_brand_month(cur, BACKUP_TABLE)
    after = _by_brand_month(cur, REAL_TABLE)
    keys = sorted(set(before) | set(after))

    problems = []
    total_before_canc = sum(v["canceled_orders"] for v in before.values())
    total_after_canc = sum(v["canceled_orders"] for v in after.values())

    print(f"\n{'brand':<10} {'mes':<12} {'n_antes':>8} {'n_depois':>9} {'canc_antes':>11} {'canc_depois':>12} {'d_gmv':>10}")
    for key in keys:
        b = before.get(key, {"n": 0, "gmv": 0, "units_sold": 0, "completed_orders": 0, "canceled_orders": 0})
        a = after.get(key, {"n": 0, "gmv": 0, "units_sold": 0, "completed_orders": 0, "canceled_orders": 0})
        gmv_diff = float(a["gmv"]) - float(b["gmv"])
        if abs(gmv_diff) > 0.005 or int(a["units_sold"]) != int(b["units_sold"]) or int(a["completed_orders"]) != int(b["completed_orders"]):
            problems.append(f"{key[0]} {key[1]}: GMV/units/completed mudaram inesperadamente")
        print(f"{key[0]:<10} {str(key[1]):<12} {b['n']:>8} {a['n']:>9} {b['canceled_orders']:>11} {a['canceled_orders']:>12} {gmv_diff:>10.2f}")

    delta_canc = total_after_canc - total_before_canc
    print(f"\nTOTAL canceled_orders: antes={total_before_canc} depois={total_after_canc} (delta={delta_canc})")
    if delta_canc != 84:
        problems.append(f"delta de canceled_orders = {delta_canc}, esperado +84")

    # Pareto: toda linha nova/alterada (ausente do backup pela chave real)
    # deve ter gmv=0 -> Pareto matematicamente inalterado.
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM marts.{REAL_TABLE} r
        LEFT JOIN marts.{BACKUP_TABLE} b
          ON b.ref_month = r.ref_month AND b.brand = r.brand
         AND b.sku_ref_key = r.sku_ref_key AND b.product_name = r.product_name
        WHERE b.ref_month IS NULL AND r.gmv <> 0
    """)
    bad_gmv_rows = cur.fetchone()["n"]
    if bad_gmv_rows:
        problems.append(f"{bad_gmv_rows} linha(s) nova(s) com gmv != 0 — Pareto poderia ter mudado")
    print(f"Linhas novas com gmv != 0 (deveria ser 0): {bad_gmv_rows} -> Pareto {'INALTERADO' if not bad_gmv_rows else 'EM RISCO'}")

    cur.close()
    return problems


def _smoke_test(local_url: str) -> list[str]:
    """Smoke test SOMENTE LEITURA dos endpoints Shopee via performance_service,
    usando uma engine SQLAlchemy propria apontando para LOCAL_PG_URL — nunca
    para app.database/settings (que aponta para o Neon)."""
    print("\n[fase 2b] smoke test read-only (performance_service.get_produtos_shopee*)")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.services import performance_service as perf_svc

    problems = []
    engine = create_engine(local_url, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            combos = db.execute(text(f"""
                SELECT DISTINCT brand, EXTRACT(YEAR FROM ref_month)::int AS y, EXTRACT(MONTH FROM ref_month)::int AS m
                FROM marts.{REAL_TABLE} ORDER BY 1, 2, 3 LIMIT 3
            """)).fetchall()

            for brand, year, month in combos:
                page = perf_svc.get_produtos_shopee(db, brand=brand, year=year, month=month, limit=10, offset=0)
                summary = perf_svc.get_produtos_shopee_summary(db, brand=brand, year=year, month=month)
                print(f"  {brand} {year}-{month:02d}: tabela total={page['total']} itens_pagina={len(page['items'])} | "
                      f"summary eligible={summary['eligible_count']} total_gmv={summary['total_gmv']}")
                if page["total"] != summary["eligible_count"]:
                    problems.append(f"{brand} {year}-{month:02d}: total da tabela ({page['total']}) != eligible_count do summary ({summary['eligible_count']})")
                bucket_sum_count = sum(b["count"] for b in summary["buckets"])
                if bucket_sum_count != summary["eligible_count"]:
                    problems.append(f"{brand} {year}-{month:02d}: soma dos buckets ({bucket_sum_count}) != eligible_count ({summary['eligible_count']})")
    finally:
        engine.dispose()
    return problems


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    local_url = _get_local_pg_url()
    conn = _local(local_url)
    try:
        _preflight(conn, local_url)

        agg = _swap(conn)

        problems = _post_swap_report(conn)
        problems += _smoke_test(local_url)

        if problems:
            print(f"\n!!! {len(problems)} PROBLEMA(S) POS-SWAP (swap ja foi commitado, dados NAO foram revertidos automaticamente):")
            for p in problems:
                print(f"    - {p}")
            return 1

        print("\n=== GATE 3 CONCLUIDO COM SUCESSO ===")
        print(f"Tabela real marts.{REAL_TABLE}: {agg['n']} linhas, GMV={agg['gmv']}, canceled_orders={agg['canceled_orders']}")
        print(f"Backup preservado: marts.{BACKUP_TABLE}")
        print(f"Staging preservada: marts.{STAGING_TABLE}")
        print("Nenhuma conexao com Neon ou Data Mart foi aberta nesta execucao.")
        return 0
    except (PreflightError, SwapValidationError) as e:
        print(f"\n!!! ABORTADO: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
