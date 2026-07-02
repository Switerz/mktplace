"""
Gate 2 do Bug 8 (marts.fact_shopee_product_monthly) — SOMENTE PostgreSQL local.

Reconcilia a correcao do merge left->outer em apps/api/etl/load_shopee_products.py
(_aggregate) contra os dados reais do PostgreSQL local, SEM alterar a tabela real
e SEM tocar em Neon/Data Mart em nenhum momento — este script nunca abre conexao
com DATABASE_URL (Neon) nem DATAMART_DATABASE_URL (RDS); usa exclusivamente
LOCAL_PG_URL.

Fases:
  1. Snapshot ANTES (tabela real, por brand x ref_month)
  2. Backup timestampado da tabela real (nunca apaga backups anteriores)
  3. Staging timestampada, populada com o ETL corrigido (nunca apaga staging anterior)
  4. Reconciliacao (aborta no primeiro check estrito que falhar)
  5. Relatorio antes/depois por brand x ref_month

NAO faz TRUNCATE, DROP ou UPDATE na tabela real. NAO faz swap. NAO conecta a
Neon nem ao Data Mart.

Uso (dry-run e' o unico modo — nao ha flag de substituicao neste script):
    cd apps/api  # necessario para 'from etl.load_shopee_products import ...' resolver
    python -m pipelines.reconciliation.reconcile_bug8_canceled_only
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGDIR = REPO_ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)

REAL_TABLE = "fact_shopee_product_monthly"

# Hosts aceitos por este script — nunca um host remoto (Neon, RDS ou
# qualquer outro). "::1" e' o loopback IPv6, equivalente a 127.0.0.1.
ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeDatabaseHostError(RuntimeError):
    """Levantado quando LOCAL_PG_URL aponta para um host fora da allowlist local."""


def _sanitize_url(url: str) -> str:
    """host:porta/database, NUNCA usuario/senha — seguro para print()/logs/
    mensagens de excecao. Usar SEMPRE em vez da URL bruta em qualquer saida."""
    parsed = urlsplit(url)
    host = parsed.hostname or "?"
    port = parsed.port if parsed.port is not None else "?"
    db = parsed.path.lstrip("/") or "?"
    return f"{host}:{port}/{db}"


def _get_local_pg_url() -> str:
    """Le LOCAL_PG_URL (SEM fallback silencioso) e garante que o host e'
    localhost/127.0.0.1/::1. Esta funcao nunca le DATABASE_URL (Neon) nem
    DATAMART_DATABASE_URL (RDS) — essas variaveis nao sao referenciadas em
    nenhum lugar deste modulo, por design, para tornar estruturalmente
    impossivel este script se conectar a Neon/RDS mesmo por engano."""
    url = os.environ.get("LOCAL_PG_URL")
    if not url:
        raise RuntimeError(
            "LOCAL_PG_URL nao definido. Este script exige a variavel explicita "
            "(sem fallback para nenhuma connection string default) para nunca "
            "conectar acidentalmente a um banco nao pretendido."
        )
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_LOCAL_HOSTS:
        raise UnsafeDatabaseHostError(
            f"Host nao permitido em LOCAL_PG_URL: {_sanitize_url(url)!r} — "
            f"so' localhost/127.0.0.1/::1 sao aceitos por este script."
        )
    return url


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _local(url: str):
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=10)


def _by_brand_month(conn, table: str) -> dict[tuple, dict]:
    cur = conn.cursor()
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
    rows = cur.fetchall()
    cur.close()
    return {(r["brand"], r["ref_month"]): dict(r) for r in rows}


def _backup(conn, tag: str) -> str:
    name = f"{REAL_TABLE}_backup_bug8_{tag}"
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE marts.{name} AS SELECT * FROM marts.{REAL_TABLE}")
    conn.commit()
    cur.execute(f"SELECT COUNT(*) AS n FROM marts.{name}")
    n = cur.fetchone()["n"]
    cur.close()
    print(f"  backup criado: marts.{name} ({n} linhas)")
    return name


def _build_staging(conn, tag: str) -> str:
    """Reprocessa os XLSX com o ETL corrigido (outer merge) e grava numa
    tabela staging NOVA e unicamente identificada por tag — nunca reusa nem
    apaga uma staging table de execucao anterior."""
    from etl.load_shopee_products import BRANDS, DDL, _aggregate, _load_brand

    name = f"{REAL_TABLE}_staging_bug8_{tag}"
    cur = conn.cursor()
    cur.execute(DDL.replace(REAL_TABLE, name))
    conn.commit()
    cur.close()

    UPSERT_STAGING = f"""
        INSERT INTO marts.{name}
            (ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
             gmv, units_sold, completed_orders, canceled_orders,
             cancel_rate_pct, unique_buyers, avg_price)
        VALUES %s
    """

    total_rows = 0
    total_collisions = 0
    for brand in BRANDS:
        df = _load_brand(brand)
        if df is None:
            print(f"  [{brand}] nenhum arquivo encontrado — ignorado")
            continue
        agg = _aggregate(df)

        # Mesma logica de colisao de variation_name usada em
        # pipelines/reconciliation/fix_shopee_product_dates.py::_rebuild_staging
        # (a chave unica real e' ref_month+brand+sku_ref_key+product_name, sem
        # variation_name — nao e' o Bug 8, e' um pre-requisito para gravar
        # numa tabela com essa UNIQUE constraint sem perder linhas por
        # colisao de chave).
        rows_by_key: dict[tuple, dict] = {}
        for _, row in agg.iterrows():
            ref_month_val = row["ref_month"]
            if ref_month_val is None or pd.isna(ref_month_val):
                continue
            key = (ref_month_val.date().isoformat(), brand, row["sku_ref_key"], row["product_name"])
            variation = row["variation_name"] if pd.notna(row.get("variation_name")) else None
            gmv = float(row["gmv"])
            units = int(row["units_sold"])
            completed = int(row["completed_orders"])
            canceled = int(row["canceled_orders"])
            buyers = int(row["unique_buyers"])
            sku_ref = row["sku_ref"] if pd.notna(row["sku_ref"]) else None

            if key not in rows_by_key:
                rows_by_key[key] = {
                    "sku_ref": sku_ref, "variations": [variation] if variation else [],
                    "gmv": gmv, "units_sold": units, "completed_orders": completed,
                    "canceled_orders": canceled, "unique_buyers": buyers,
                }
            else:
                total_collisions += 1
                acc = rows_by_key[key]
                acc["sku_ref"] = acc["sku_ref"] or sku_ref
                if variation:
                    acc["variations"].append(variation)
                acc["gmv"] += gmv
                acc["units_sold"] += units
                acc["completed_orders"] += completed
                acc["canceled_orders"] += canceled
                acc["unique_buyers"] += buyers

        batch = []
        for key, acc in rows_by_key.items():
            total_orders = acc["completed_orders"] + acc["canceled_orders"]
            cancel_rate = round(acc["canceled_orders"] / total_orders * 100, 4) if total_orders > 0 else None
            avg_price = round(acc["gmv"] / acc["units_sold"], 2) if acc["units_sold"] > 0 else None
            variation_label = "; ".join(dict.fromkeys(acc["variations"])) or None
            batch.append((
                key[0], key[1], acc["sku_ref"], key[2], key[3], variation_label,
                acc["gmv"], acc["units_sold"], acc["completed_orders"], acc["canceled_orders"],
                cancel_rate, acc["unique_buyers"], avg_price,
            ))
        if not batch:
            continue
        dc = conn.cursor()
        execute_values(dc, UPSERT_STAGING, batch, page_size=500)
        conn.commit()
        dc.close()
        print(f"  [{brand}] {len(batch)} linhas na staging")
        total_rows += len(batch)

    if total_collisions:
        print(f"  aviso: {total_collisions} colisoes de chave (variation_name distinta, mesma chave unica) somadas na staging")

    return name


def _duplicates_and_nulls(conn, table: str) -> tuple[int, int]:
    cur = conn.cursor()
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
    cur.close()
    return dupes, nulls


def _cancel_only_groups(conn, staging_table: str) -> int:
    """Linhas da staging com completed_orders=0 e canceled_orders>0 — sao
    exatamente os grupos que o left merge antigo descartava (Bug 8)."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM marts.{staging_table}
        WHERE completed_orders = 0 AND canceled_orders > 0
    """)
    n = cur.fetchone()["n"]
    cur.close()
    return n


def _new_rows_have_zero_gmv(conn, backup_table: str, staging_table: str) -> tuple[bool, int]:
    """Verifica que toda linha presente na staging mas ausente do backup
    (pela chave unica real) tem gmv=0 — o que garante que o Pareto (que so
    considera WHERE gmv > 0) e' matematicamente inalterado pelo fix."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT COUNT(*) AS n
        FROM marts.{staging_table} s
        LEFT JOIN marts.{backup_table} b
          ON b.ref_month = s.ref_month AND b.brand = s.brand
         AND b.sku_ref_key = s.sku_ref_key AND b.product_name = s.product_name
        WHERE b.ref_month IS NULL AND s.gmv <> 0
    """)
    bad = cur.fetchone()["n"]
    cur.execute(f"""
        SELECT COUNT(*) AS n
        FROM marts.{staging_table} s
        LEFT JOIN marts.{backup_table} b
          ON b.ref_month = s.ref_month AND b.brand = s.brand
         AND b.sku_ref_key = s.sku_ref_key AND b.product_name = s.product_name
        WHERE b.ref_month IS NULL
    """)
    new_rows = cur.fetchone()["n"]
    cur.close()
    return bad == 0, new_rows


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    # Guarda estrutural: este script nunca le DATABASE_URL nem
    # DATAMART_DATABASE_URL — nao ha como ele se conectar a Neon/RDS mesmo
    # por engano, porque essas variaveis nunca sao referenciadas neste
    # modulo. _get_local_pg_url() tambem recusa qualquer host que nao seja
    # localhost/127.0.0.1/::1, mesmo que fosse passado via LOCAL_PG_URL.
    local_url = _get_local_pg_url()

    tag = _now_tag()
    print(f"=== reconcile_bug8_canceled_only — tag={tag} (SOMENTE LOCAL) ===")
    print(f"  conectando a: {_sanitize_url(local_url)}")

    conn = _local(local_url)
    problems: list[str] = []

    try:
        print("\n[fase 1] snapshot ANTES (tabela real)")
        before = _by_brand_month(conn, REAL_TABLE)
        total_before_canc = sum(v["canceled_orders"] for v in before.values())
        total_before_gmv = sum(float(v["gmv"]) for v in before.values())
        print(f"  {len(before)} combinacoes brand x mes | canceled_orders={total_before_canc} | gmv={total_before_gmv:.2f}")

        print("\n[fase 2] backup")
        backup_table = _backup(conn, tag)

        print("\n[fase 3] staging (ETL corrigido)")
        staging_table = _build_staging(conn, tag)

        print("\n[fase 4] reconciliacao")
        after = _by_brand_month(conn, staging_table)

        keys = sorted(set(before) | set(after))
        report_rows = []
        for key in keys:
            b = before.get(key, {"n": 0, "gmv": 0, "units_sold": 0, "completed_orders": 0, "canceled_orders": 0})
            a = after.get(key, {"n": 0, "gmv": 0, "units_sold": 0, "completed_orders": 0, "canceled_orders": 0})

            gmv_diff = float(a["gmv"]) - float(b["gmv"])
            units_diff = int(a["units_sold"]) - int(b["units_sold"])
            completed_diff = int(a["completed_orders"]) - int(b["completed_orders"])
            canc_diff = int(a["canceled_orders"]) - int(b["canceled_orders"])
            row_diff = int(a["n"]) - int(b["n"])

            if abs(gmv_diff) > 0.005:
                problems.append(f"{key[0]} {key[1]}: GMV mudou ({b['gmv']} -> {a['gmv']}, diff={gmv_diff:.2f}) — deveria ser 0")
            if units_diff != 0:
                problems.append(f"{key[0]} {key[1]}: units_sold mudou (diff={units_diff}) — deveria ser 0")
            if completed_diff != 0:
                problems.append(f"{key[0]} {key[1]}: completed_orders mudou (diff={completed_diff}) — deveria ser 0")
            if canc_diff < 0:
                problems.append(f"{key[0]} {key[1]}: canceled_orders DIMINUIU (diff={canc_diff}) — inesperado, fix so deveria recuperar, nunca perder")
            if row_diff < 0:
                problems.append(f"{key[0]} {key[1]}: numero de linhas DIMINUIU (diff={row_diff}) — inesperado")

            report_rows.append((key[0], key[1], b["n"], a["n"], row_diff, b["canceled_orders"], a["canceled_orders"], canc_diff, gmv_diff))

        dupes, nulls = _duplicates_and_nulls(conn, staging_table)
        if dupes:
            problems.append(f"staging com {dupes} chaves duplicadas (ref_month, brand, sku_ref_key, product_name)")
        if nulls:
            problems.append(f"staging com {nulls} linhas com NULL em ref_month/brand/product_name")

        cancel_only_n = _cancel_only_groups(conn, staging_table)
        zero_gmv_ok, new_rows_n = _new_rows_have_zero_gmv(conn, backup_table, staging_table)
        if not zero_gmv_ok:
            problems.append("existem linhas novas (ausentes do backup) com gmv != 0 — Pareto poderia mudar, isso NUNCA deveria acontecer neste fix")

        # ---- Relatorio ----
        print(f"\n{'brand':<10} {'mes':<12} {'n_antes':>8} {'n_depois':>9} {'d_linhas':>9} {'canc_antes':>11} {'canc_depois':>12} {'d_canc':>7} {'d_gmv':>10}")
        for r in report_rows:
            brand, ym, n_b, n_a, dn, cb, ca, dc, dg = r
            if dn != 0 or dc != 0 or abs(dg) > 0.005:
                print(f"{brand:<10} {str(ym):<12} {n_b:>8} {n_a:>9} {dn:>9} {cb:>11} {ca:>12} {dc:>7} {dg:>10.2f}")

        total_after_canc = sum(v["canceled_orders"] for v in after.values())
        total_after_gmv = sum(float(v["gmv"]) for v in after.values())
        print(f"\nTOTAIS: canceled_orders antes={total_before_canc} depois={total_after_canc} (delta={total_after_canc - total_before_canc})")
        print(f"        GMV antes={total_before_gmv:.2f} depois={total_after_gmv:.2f} (delta={total_after_gmv - total_before_gmv:.2f})")
        print(f"        linhas novas (so-cancelado, ausentes do backup)={new_rows_n}")
        print(f"        grupos com completed_orders=0 e canceled_orders>0 na staging={cancel_only_n}")
        print(f"        chaves duplicadas na staging={dupes} | nulos obrigatorios na staging={nulls}")
        print(f"        todas as linhas novas tem gmv=0 (Pareto inalterado)? {zero_gmv_ok}")

        if problems:
            print(f"\n!!! {len(problems)} PROBLEMA(S) DE RECONCILIACAO — abortando, nenhuma tabela real foi tocada:")
            for p in problems:
                print(f"    - {p}")
            print(f"\nBackup preservado: marts.{backup_table}")
            print(f"Staging preservada: marts.{staging_table}")
            return 1

        print("\nTODOS OS CHECKS PASSARAM.")
        print(f"Backup preservado: marts.{backup_table}")
        print(f"Staging preservada: marts.{staging_table}")
        print("Tabela real marts.fact_shopee_product_monthly NAO foi alterada.")
        print("Nenhuma conexao com Neon ou Data Mart foi aberta nesta execucao.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
