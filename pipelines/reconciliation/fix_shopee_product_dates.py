"""
Corrige transacionalmente o ref_month de marts.fact_shopee_product_monthly
(Bug 3 — docs/sections/produtos_audit.md): ~42% das linhas foram gravadas com
ref_month errado porque apps/api/etl/load_shopee_products.py usava
pd.to_datetime(order_date, dayfirst=True) sobre datas ISO — já corrigido no
código (format="%Y-%m-%d %H:%M" explícito). Este script reprocessa os
arquivos-fonte com o parser corrigido e substitui os dados, com backup e
validação em cada etapa.

NÃO toca gold.*/raw.* (Data Mart). Só escreve em marts.fact_shopee_product_monthly
no PostgreSQL local e no Neon.

Uso:
    python -m pipelines.reconciliation.fix_shopee_product_dates --dry-run
    python -m pipelines.reconciliation.fix_shopee_product_dates

--dry-run executa as fases 0-4 (snapshot, backup, staging, validação) e
para antes de qualquer substituição. Sem --dry-run, executa até o fim.

Fases:
  0. Guardas de segurança (origem != destino)
  1. Snapshot ANTES (local + Neon) -> logs/shopee_fix_<ts>_before.json
  2. Backup timestamped das tabelas afetadas (local + Neon)
  3. Reprocessa os arquivos XLSX com o parser corrigido -> tabela staging local
  4. Valida staging (GMV total conservado, sem ref_month futuro, sem dup/nulos)
  5. Substitui a tabela local dentro de transação (TRUNCATE + INSERT FROM staging)
  6. Valida local pós-swap
  7. Substitui a tabela no Neon a partir do local corrigido, dentro de transação
  8. Valida Neon pós-swap, confirma local == Neon
  9. Snapshot DEPOIS -> logs/shopee_fix_<ts>_after.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGDIR = REPO_ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)

TOLERANCE_GMV = 1.0  # tolerancia absoluta em R$ para conservacao de GMV total (arredondamento)


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _neon(url: str):
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)


def _local(url: str):
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=10)


def _snapshot(conn, table: str = "marts.fact_shopee_product_monthly") -> dict:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT COUNT(*) AS n, MIN(ref_month) AS min_m, MAX(ref_month) AS max_m,
               SUM(gmv) AS gmv, SUM(units_sold) AS units_sold
        FROM {table}
    """)
    total = dict(cur.fetchone())
    cur.execute(f"""
        SELECT brand, ref_month, COUNT(*) AS n, SUM(gmv) AS gmv
        FROM {table} GROUP BY brand, ref_month ORDER BY brand, ref_month
    """)
    by_brand_month = [dict(r) for r in cur.fetchall()]
    cur.close()
    return {"total": total, "by_brand_month": by_brand_month}


def _to_jsonable(obj):
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "__float__"):
        return float(obj)
    return obj


def _write_snapshot(tag: str, phase: str, local_snap: dict, neon_snap: dict) -> Path:
    path = LOGDIR / f"shopee_fix_{tag}_{phase}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_to_jsonable({"local": local_snap, "neon": neon_snap}), f, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# Fase 2 — backup
# ---------------------------------------------------------------------------
def _backup_table(conn, tag: str, table: str = "fact_shopee_product_monthly") -> str:
    backup_name = f"{table}_backup_{tag}"
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE marts.{backup_name} AS SELECT * FROM marts.{table}")
    conn.commit()
    cur.execute(f"SELECT COUNT(*) AS n FROM marts.{backup_name}")
    n = cur.fetchone()["n"]
    cur.close()
    print(f"  backup criado: marts.{backup_name} ({n} linhas)")
    return backup_name


# ---------------------------------------------------------------------------
# Fase 3 — reprocessa XLSX com o parser corrigido -> staging local
# ---------------------------------------------------------------------------
def _rebuild_staging(local_conn) -> int:
    from etl.load_shopee_products import BRANDS, DDL, _aggregate, _load_brand

    cur = local_conn.cursor()
    cur.execute("DROP TABLE IF EXISTS marts.fact_shopee_product_monthly_staging")
    cur.execute(DDL.replace("fact_shopee_product_monthly", "fact_shopee_product_monthly_staging"))
    local_conn.commit()
    cur.close()

    UPSERT_STAGING = """
        INSERT INTO marts.fact_shopee_product_monthly_staging
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

        # A chave unica da tabela e' (ref_month, brand, sku_ref_key, product_name) —
        # NAO inclui variation_name, mas o groupby de _aggregate inclui.
        # Quando o mesmo sku_ref_key+product_name tem mais de uma
        # variation_name no mesmo mes (comum: mesma "SKU principal" para
        # cores/tamanhos diferentes), essas linhas colidem na chave unica.
        #
        # ACHADO (2026-07-01): o script original (etl/load_shopee_products.py)
        # resolve a colisao fazendo upsert linha a linha (ON CONFLICT DO
        # UPDATE) — a ultima linha da ordem do DataFrame sobrescreve as
        # anteriores, DESCARTANDO o gmv/units das variacoes anteriores. Esse
        # bug ja existia em produção, mas ficava mascarado porque o bug de
        # data (dayfirst=True) espalhava as variacoes de um mesmo produto por
        # meses errados diferentes, reduzindo a chance de colidirem no mesmo
        # mes. Corrigido o parsing de data, mais variacoes passam a cair
        # corretamente no mesmo mes e a colisao se torna maior (ex.: lescent
        # perdia ~36% do GMV real so' com "last wins"). Em vez de replicar o
        # bug de sobrescrita, SOMAMOS as linhas colidentes (gmv, units_sold,
        # completed_orders, canceled_orders, unique_buyers) e recalculamos
        # cancel_rate_pct/avg_price a partir dos totais somados — preserva
        # 100% do GMV/unidades real. variation_name passa a listar as
        # variacoes combinadas para nao perder a informacao silenciosamente.
        rows_by_key: dict[tuple, dict] = {}
        for _, row in agg.iterrows():
            ref_month_val = row["ref_month"]
            if ref_month_val is None:
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
                acc["unique_buyers"] += buyers  # aproximacao: pode contar 2x um comprador de >1 variacao

        batch = []
        for key, acc in rows_by_key.items():
            total_orders = acc["completed_orders"] + acc["canceled_orders"]
            cancel_rate = round(acc["canceled_orders"] / total_orders * 100, 4) if total_orders > 0 else None
            avg_price = round(acc["gmv"] / acc["units_sold"], 2) if acc["units_sold"] > 0 else None
            variation_label = "; ".join(dict.fromkeys(acc["variations"])) or None  # dedup preservando ordem
            batch.append((
                key[0], key[1], acc["sku_ref"], key[2], key[3], variation_label,
                acc["gmv"], acc["units_sold"], acc["completed_orders"], acc["canceled_orders"],
                cancel_rate, acc["unique_buyers"], avg_price,
            ))
        if not batch:
            continue
        dc = local_conn.cursor()
        execute_values(dc, UPSERT_STAGING, batch, page_size=500)
        local_conn.commit()
        dc.close()
        print(f"  [{brand}] {len(batch)} linhas na staging")
        total_rows += len(batch)

    if total_collisions:
        print(f"  aviso: {total_collisions} colisoes de chave (variation_name distinta, mesma chave unica) — GMV/units somados, nao descartados (ver comentario no codigo)")

    return total_rows


# ---------------------------------------------------------------------------
# Fase 4 — validação da staging
# ---------------------------------------------------------------------------
def _validate_staging(local_conn, neon_conn, backup_snapshot: dict) -> list[str]:
    problems = []
    cur = local_conn.cursor()

    cur.execute("""
        SELECT COUNT(*) AS n, SUM(gmv) AS gmv, MAX(ref_month) AS max_m, MIN(ref_month) AS min_m
        FROM marts.fact_shopee_product_monthly_staging
    """)
    staging = dict(cur.fetchone())

    if not staging["n"]:
        problems.append("staging vazia — reprocessamento nao gerou nenhuma linha")
        return problems

    plausible_max = date.today().replace(day=1)
    if staging["max_m"] and staging["max_m"] > plausible_max:
        problems.append(f"staging ainda contem ref_month futuro: max={staging['max_m']}")

    # NAO comparamos staging vs backup diretamente: o backup e' o dado com bug
    # (dayfirst=True descartava 14.2% das linhas como NaT — ver logs desta
    # sessao), entao GMV "conservado" seria o resultado ERRADO. A referencia
    # correta e' marts.fact_marketplace_daily_performance (marketplace_id=3),
    # que sempre usou o parser correto (pipelines/connectors/shopee/_parser.py).
    # Tolerancia ampla (nao estrita) porque as duas tabelas tem grão e status
    # considerados ligeiramente diferentes (produtos = so' 'Concluído';
    # diario = todos os status exceto 'Cancelado') — nao sao a mesma metrica,
    # mas devem ficar na mesma ordem de grandeza por brand/periodo.
    ncur = neon_conn.cursor()
    ncur.execute("""
        SELECT COALESCE(SUM(gmv), 0) AS gmv
        FROM marts.fact_marketplace_daily_performance
        WHERE marketplace_id = 3 AND date >= %s AND date < (%s + INTERVAL '1 month')
    """, (staging["min_m"], staging["max_m"]))
    daily_gmv = float(ncur.fetchone()["gmv"] or 0)
    ncur.close()

    new_gmv = float(staging["gmv"] or 0)
    old_gmv = float(backup_snapshot["total"]["gmv"] or 0)
    print(f"  cross-check: staging_gmv={new_gmv:.2f} | backup_gmv(com bug)={old_gmv:.2f} | daily_fact_gmv(referencia independente)={daily_gmv:.2f}")

    if daily_gmv > 0:
        ratio = new_gmv / daily_gmv
        if ratio > 1.10 or ratio < 0.40:
            problems.append(
                f"staging GMV ({new_gmv:.2f}) fora da faixa plausivel vs fact_marketplace_daily_performance "
                f"({daily_gmv:.2f}, mesmo periodo) — razao={ratio:.2f} (esperado ~0.6-1.0, produtos so' conta "
                f"pedidos 'Concluído'; diario conta todos exceto 'Cancelado')"
            )

    cur.execute("""
        SELECT ref_month, brand, sku_ref_key, product_name, COUNT(*) AS n
        FROM marts.fact_shopee_product_monthly_staging
        GROUP BY ref_month, brand, sku_ref_key, product_name
        HAVING COUNT(*) > 1
    """)
    dupes = cur.fetchall()
    if dupes:
        problems.append(f"staging com {len(dupes)} chaves duplicadas")

    cur.execute("""
        SELECT COUNT(*) AS n FROM marts.fact_shopee_product_monthly_staging
        WHERE ref_month IS NULL OR brand IS NULL OR product_name IS NULL
    """)
    nulls = cur.fetchone()["n"]
    if nulls:
        problems.append(f"staging com {nulls} linhas com nulos em chave obrigatoria")

    cur.close()
    print(f"  staging: {staging['n']} linhas, GMV={staging['gmv']}, ref_month {staging['min_m']}..{staging['max_m']}")
    return problems


# ---------------------------------------------------------------------------
# Fase 5/6 — swap local (transacional)
# ---------------------------------------------------------------------------
def _swap_local(local_conn) -> int:
    cur = local_conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS n FROM marts.fact_shopee_product_monthly_staging")
        staging_n = cur.fetchone()["n"]

        cur.execute("TRUNCATE TABLE marts.fact_shopee_product_monthly")
        cur.execute("""
            INSERT INTO marts.fact_shopee_product_monthly
                (ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
                 gmv, units_sold, completed_orders, canceled_orders,
                 cancel_rate_pct, unique_buyers, avg_price)
            SELECT ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
                   gmv, units_sold, completed_orders, canceled_orders,
                   cancel_rate_pct, unique_buyers, avg_price
            FROM marts.fact_shopee_product_monthly_staging
        """)
        cur.execute("SELECT COUNT(*) AS n FROM marts.fact_shopee_product_monthly")
        after_n = cur.fetchone()["n"]

        if after_n != staging_n:
            local_conn.rollback()
            raise RuntimeError(f"swap local: contagem pos-insert ({after_n}) != staging ({staging_n}) — rollback")

        local_conn.commit()
        print(f"  swap local OK: {after_n} linhas")
        return after_n
    except Exception:
        local_conn.rollback()
        raise
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Fase 7 — swap Neon (a partir do local ja corrigido)
# ---------------------------------------------------------------------------
def _swap_neon(local_conn, neon_conn) -> int:
    cur = local_conn.cursor()
    cur.execute("""
        SELECT ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
               gmv, units_sold, completed_orders, canceled_orders,
               cancel_rate_pct, unique_buyers, avg_price
        FROM marts.fact_shopee_product_monthly
    """)
    rows = cur.fetchall()
    cur.close()

    nc = neon_conn.cursor()
    try:
        nc.execute("TRUNCATE TABLE marts.fact_shopee_product_monthly")
        batch = [
            (
                r["ref_month"], r["brand"], r["sku_ref"], r["sku_ref_key"], r["product_name"],
                r["variation_name"], r["gmv"], r["units_sold"], r["completed_orders"],
                r["canceled_orders"], r["cancel_rate_pct"], r["unique_buyers"], r["avg_price"],
            )
            for r in rows
        ]
        execute_values(nc, """
            INSERT INTO marts.fact_shopee_product_monthly
                (ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
                 gmv, units_sold, completed_orders, canceled_orders,
                 cancel_rate_pct, unique_buyers, avg_price)
            VALUES %s
        """, batch, page_size=500)

        nc.execute("SELECT COUNT(*) AS n FROM marts.fact_shopee_product_monthly")
        after_n = nc.fetchone()["n"]
        if after_n != len(rows):
            neon_conn.rollback()
            raise RuntimeError(f"swap Neon: contagem pos-insert ({after_n}) != local ({len(rows)}) — rollback")

        neon_conn.commit()
        print(f"  swap Neon OK: {after_n} linhas")
        return after_n
    except Exception:
        neon_conn.rollback()
        raise
    finally:
        nc.close()


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(description="Corrige ref_month em fact_shopee_product_monthly")
    parser.add_argument("--dry-run", action="store_true", help="Roda so' snapshot/backup/staging/validacao, sem substituir dados")
    args = parser.parse_args()

    neon_url = os.environ["DATABASE_URL"]
    local_url = os.environ.get("LOCAL_PG_URL", "postgresql://postgres:postgres@localhost:5432/mktplace_control")
    rds_url = os.environ.get("DATAMART_DATABASE_URL", "")

    if neon_url in (local_url, rds_url):
        print("ERRO: DATABASE_URL (Neon) coincide com outra connection string — abortando.")
        return 1

    tag = _now_tag()
    print(f"=== fix_shopee_product_dates — tag={tag} dry_run={args.dry_run} ===")

    local_conn = _local(local_url)
    neon_conn = _neon(neon_url)

    try:
        print("\n[fase 1] snapshot ANTES")
        local_before = _snapshot(local_conn)
        neon_before = _snapshot(neon_conn)
        path = _write_snapshot(tag, "before", local_before, neon_before)
        print(f"  salvo em {path}")
        print(f"  local:  n={local_before['total']['n']} gmv={local_before['total']['gmv']}")
        print(f"  neon:   n={neon_before['total']['n']} gmv={neon_before['total']['gmv']}")

        print("\n[fase 2] backup")
        local_backup = _backup_table(local_conn, tag)
        neon_backup = _backup_table(neon_conn, tag)

        print("\n[fase 3] reprocessando arquivos XLSX com parser corrigido -> staging local")
        staging_rows = _rebuild_staging(local_conn)
        print(f"  total staging: {staging_rows} linhas")

        print("\n[fase 4] validando staging")
        problems = _validate_staging(local_conn, neon_conn, local_before)
        if problems:
            print("  PROBLEMAS ENCONTRADOS — abortando antes de qualquer substituicao:")
            for p in problems:
                print(f"    - {p}")
            print(f"\n  Backups preservados: marts.{local_backup} (local), marts.{neon_backup} (Neon)")
            print("  Nenhuma tabela de produção foi alterada.")
            return 1
        print("  staging validada OK")

        if args.dry_run:
            print("\n--dry-run: parando antes da substituicao. Staging e backups preservados para inspecao manual.")
            return 0

        print("\n[fase 5/6] substituindo tabela local (transacional)")
        local_after_n = _swap_local(local_conn)

        print("\n[fase 7] substituindo tabela Neon a partir do local corrigido (transacional)")
        neon_after_n = _swap_neon(local_conn, neon_conn)

        print("\n[fase 8] validacao pos-swap")
        local_after = _snapshot(local_conn)
        neon_after = _snapshot(neon_conn)
        path = _write_snapshot(tag, "after", local_after, neon_after)
        print(f"  salvo em {path}")

        if local_after_n != neon_after_n:
            print(f"  ATENCAO: local ({local_after_n}) != Neon ({neon_after_n}) apos o swap")
            return 1

        future = [r for r in local_after["by_brand_month"] if r["ref_month"] > date.today().replace(day=1)]
        print(f"  linhas com ref_month futuro apos correcao: {len(future)}")
        print(f"  GMV antes: {local_before['total']['gmv']} | GMV depois: {local_after['total']['gmv']}")
        print(f"  linhas antes: {local_before['total']['n']} | linhas depois: {local_after['total']['n']}")
        print(f"\nBackups preservados: marts.{local_backup} (local), marts.{neon_backup} (Neon)")
        print("Concluido com sucesso — local e Neon identicos.")
        return 0

    finally:
        local_conn.close()
        neon_conn.close()


if __name__ == "__main__":
    sys.exit(main())
