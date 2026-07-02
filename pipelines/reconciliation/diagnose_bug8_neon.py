"""
Gate 4A do Bug 8 (marts.fact_shopee_product_monthly) — diagnostico Neon.

Este e' o PRIMEIRO script desta serie autorizado a abrir uma conexao com
DATABASE_URL (Neon). Ele nunca referencia DATAMART_DATABASE_URL (Data
Mart/RDS) em nenhum lugar.

Dois modos:
  --diagnose (padrao, sem flag necessaria): SOMENTE LEITURA. Abre o Neon
      numa transacao explicitamente read-only, le a tabela real do Neon e
      o backup local pre-fix (marts.fact_shopee_product_monthly_backup_bug8_20260702_150840,
      criado no Gate 2 antes de qualquer alteracao), compara as 13 colunas
      de negocio em Python (equivalente a um EXCEPT logico nos dois
      sentidos, ja que Neon e local sao servidores diferentes — nao ha
      EXCEPT SQL cross-database), agrega por marca x mes, e reporta se ha
      dados novos no Neon (chaves que nao existem no backup) ou drift
      (mesma chave, valores diferentes).

  --prepare: escrita de backup/staging no Neon. NAO IMPLEMENTADO nesta
      versao — por decisao explicita de escopo (Gate 4A.2, nao autorizado
      ainda), este modo sempre recusa a execucao apos validar suas
      proprias guardas (flag + variavel de ambiente + diagnostico limpo),
      levantando NotImplementedError. Nao existe nenhuma instrucao SQL de
      escrita (criar tabela, inserir linha, ou qualquer operacao que
      remova/sobrescreva dados existentes) em nenhum lugar deste arquivo.

Uso:
    python -m pipelines.reconciliation.diagnose_bug8_neon              # diagnose
    python -m pipelines.reconciliation.diagnose_bug8_neon --diagnose   # idem, explicito
    python -m pipelines.reconciliation.diagnose_bug8_neon --prepare    # sempre recusa nesta versao
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from pipelines.reconciliation.reconcile_bug8_canceled_only import (  # noqa: E402
    _get_local_pg_url,
    _sanitize_url,
)
from pipelines.reconciliation.swap_bug8_canceled_only import (  # noqa: E402
    BACKUP_TABLE,
    REAL_TABLE,
)

BUSINESS_COLUMNS = [
    "ref_month", "brand", "sku_ref", "sku_ref_key", "product_name", "variation_name",
    "gmv", "units_sold", "completed_orders", "canceled_orders",
    "cancel_rate_pct", "unique_buyers", "avg_price",
]


class PrepareNotAuthorizedError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Conexoes
# ---------------------------------------------------------------------------
def _get_neon_url() -> str:
    """Le DATABASE_URL (Neon) sem fallback silencioso. Esta funcao e' a
    UNICA deste modulo que le DATABASE_URL — nunca DATAMART_DATABASE_URL."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL (Neon) nao definido. Este script exige a variavel "
            "explicita, sem fallback, para nunca conectar a um banco nao "
            "pretendido."
        )
    return url


def _neon_readonly(url: str):
    """Conecta ao Neon com a transacao explicitamente somente leitura —
    qualquer tentativa de escrita falharia no proprio servidor, defesa em
    profundidade alem de este modulo nunca emitir SQL de escrita."""
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)
    conn.set_session(readonly=True)
    return conn


def _local_readonly(url: str):
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=10)
    conn.set_session(readonly=True)
    return conn


# ---------------------------------------------------------------------------
# Leitura e normalizacao (puras — testaveis com conexoes falsas)
# ---------------------------------------------------------------------------
def _fetch_business_rows(conn, table: str) -> list[dict]:
    cur = conn.cursor()
    cur.execute(f"SELECT {', '.join(BUSINESS_COLUMNS)} FROM marts.{table}")
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


def _num(x):
    return round(float(x), 4) if x is not None else None


def _row_key(row: dict) -> tuple:
    ref_month = row["ref_month"]
    ref_month_str = ref_month.isoformat() if hasattr(ref_month, "isoformat") else ref_month
    return (ref_month_str, row["brand"], row["sku_ref_key"], row["product_name"])


def _row_tuple(row: dict) -> tuple:
    ref_month = row["ref_month"]
    ref_month_str = ref_month.isoformat() if hasattr(ref_month, "isoformat") else ref_month
    return (
        ref_month_str, row["brand"], row["sku_ref"], row["sku_ref_key"],
        row["product_name"], row["variation_name"],
        _num(row["gmv"]), row["units_sold"], row["completed_orders"], row["canceled_orders"],
        _num(row["cancel_rate_pct"]), row["unique_buyers"], _num(row["avg_price"]),
    )


def _agg_by_brand_month(rows: list[dict]) -> dict[tuple, dict]:
    agg: dict[tuple, dict] = {}
    for r in rows:
        ref_month = r["ref_month"]
        ref_month_key = ref_month.isoformat() if hasattr(ref_month, "isoformat") else ref_month
        key = (r["brand"], ref_month_key)
        a = agg.setdefault(key, {"n": 0, "gmv": 0.0, "units_sold": 0, "completed_orders": 0, "canceled_orders": 0})
        a["n"] += 1
        a["gmv"] += float(r["gmv"] or 0)
        a["units_sold"] += int(r["units_sold"] or 0)
        a["completed_orders"] += int(r["completed_orders"] or 0)
        a["canceled_orders"] += int(r["canceled_orders"] or 0)
    return agg


# ---------------------------------------------------------------------------
# Diagnostico — funcao pura, testavel com conexoes falsas
# ---------------------------------------------------------------------------
def run_diagnose(neon_conn, local_conn) -> dict:
    """SOMENTE LEITURA: le a tabela real do Neon e o backup local pre-fix,
    compara pelas 13 colunas de negocio, e classifica diferencas em 'dados
    novos no Neon' (chave ausente do backup) vs 'drift' (mesma chave,
    valores diferentes). Nao executa nenhuma escrita em nenhum dos dois
    bancos."""
    neon_rows = _fetch_business_rows(neon_conn, REAL_TABLE)
    local_rows = _fetch_business_rows(local_conn, BACKUP_TABLE)

    neon_by_key = {_row_key(r): _row_tuple(r) for r in neon_rows}
    local_by_key = {_row_key(r): _row_tuple(r) for r in local_rows}

    neon_keys = set(neon_by_key)
    local_keys = set(local_by_key)

    new_in_neon = sorted(neon_keys - local_keys)
    missing_from_neon = sorted(local_keys - neon_keys)
    drifted = sorted(k for k in (neon_keys & local_keys) if neon_by_key[k] != local_by_key[k])

    # EXCEPT logico nos dois sentidos pelas 13 colunas de negocio completas
    # (equivalente a "SELECT ... EXCEPT SELECT ..." — nao ha EXCEPT SQL
    # cross-database porque Neon e local sao servidores diferentes).
    neon_full = set(neon_by_key.values())
    local_full = set(local_by_key.values())
    except_neon_not_local = len(neon_full - local_full)
    except_local_not_neon = len(local_full - neon_full)

    neon_agg = _agg_by_brand_month(neon_rows)
    local_agg = _agg_by_brand_month(local_rows)
    brand_month_keys = sorted(set(neon_agg) | set(local_agg))
    by_brand_month = []
    for key in brand_month_keys:
        n = neon_agg.get(key, {"n": 0, "gmv": 0.0, "units_sold": 0, "completed_orders": 0, "canceled_orders": 0})
        l = local_agg.get(key, {"n": 0, "gmv": 0.0, "units_sold": 0, "completed_orders": 0, "canceled_orders": 0})
        by_brand_month.append({
            "brand": key[0], "ref_month": key[1],
            "n_neon": n["n"], "n_local": l["n"],
            "gmv_neon": round(n["gmv"], 2), "gmv_local": round(l["gmv"], 2),
            "units_neon": n["units_sold"], "units_local": l["units_sold"],
            "completed_neon": n["completed_orders"], "completed_local": l["completed_orders"],
            "canceled_neon": n["canceled_orders"], "canceled_local": l["canceled_orders"],
        })

    problems = []
    if new_in_neon:
        problems.append(f"{len(new_in_neon)} chave(s) existem no Neon mas NAO no backup local pre-fix — dados novos chegaram ao Neon desde o Gate 2")
    if missing_from_neon:
        problems.append(f"{len(missing_from_neon)} chave(s) existem no backup local mas NAO no Neon")
    if drifted:
        problems.append(f"{len(drifted)} chave(s) com os MESMOS identificadores mas valores diferentes entre Neon e backup local (drift)")

    return {
        "problems": problems,
        "neon_row_count": len(neon_rows),
        "local_row_count": len(local_rows),
        "new_in_neon_count": len(new_in_neon),
        "missing_from_neon_count": len(missing_from_neon),
        "drifted_count": len(drifted),
        "except_neon_not_local": except_neon_not_local,
        "except_local_not_neon": except_local_not_neon,
        "by_brand_month": by_brand_month,
        "new_in_neon_sample": new_in_neon[:10],
        "missing_from_neon_sample": missing_from_neon[:10],
        "drifted_sample": drifted[:10],
    }


def _print_report(report: dict, neon_url: str, local_url: str) -> None:
    print(f"Neon:  {_sanitize_url(neon_url)}")
    print(f"Local (backup pre-fix): {_sanitize_url(local_url)}")
    print(f"\nLinhas — Neon: {report['neon_row_count']} | backup local: {report['local_row_count']}")
    print(f"EXCEPT logico (13 colunas de negocio): neon_not_local={report['except_neon_not_local']} | local_not_neon={report['except_local_not_neon']}")
    print(f"Chaves novas no Neon (ausentes do backup): {report['new_in_neon_count']}")
    print(f"Chaves ausentes do Neon (presentes so no backup): {report['missing_from_neon_count']}")
    print(f"Chaves com drift (mesma identidade, valores diferentes): {report['drifted_count']}")

    print(f"\n{'brand':<10} {'mes':<12} {'n_neon':>7} {'n_local':>8} {'gmv_neon':>13} {'gmv_local':>13} {'canc_neon':>10} {'canc_local':>11}")
    for r in report["by_brand_month"]:
        marker = "" if (r["n_neon"] == r["n_local"] and abs(r["gmv_neon"] - r["gmv_local"]) < 0.01
                         and r["canceled_neon"] == r["canceled_local"]) else "  <-- DIFERENCA"
        print(f"{r['brand']:<10} {str(r['ref_month']):<12} {r['n_neon']:>7} {r['n_local']:>8} "
              f"{r['gmv_neon']:>13.2f} {r['gmv_local']:>13.2f} {r['canceled_neon']:>10} {r['canceled_local']:>11}{marker}")

    if report["problems"]:
        print(f"\n!!! {len(report['problems'])} PROBLEMA(S) — Neon NAO esta pronto para o Gate 4A.2:")
        for p in report["problems"]:
            print(f"    - {p}")
    else:
        print("\nNenhum problema encontrado: Neon == backup local pre-fix (sem dados novos, sem drift).")


# ---------------------------------------------------------------------------
# --prepare — gated, NAO IMPLEMENTADO (Gate 4A.2)
# ---------------------------------------------------------------------------
def run_prepare(args, diagnose_fn=None) -> None:
    """Sempre recusa a execucao nesta versao do script. Valida as guardas
    (flag + variavel de ambiente + diagnostico limpo) antes de recusar, para
    que a recusa em si ja sirva de teste dessas guardas quando o Gate 4A.2
    for autorizado e a criacao de backup/staging for implementada aqui."""
    if not args.prepare:
        raise RuntimeError("modo prepare requer a flag --prepare explicita")
    if os.environ.get("I_UNDERSTAND_THIS_TOUCHES_NEON") != "1":
        raise RuntimeError(
            "modo prepare requer a variavel de ambiente "
            "I_UNDERSTAND_THIS_TOUCHES_NEON=1 explicitamente definida"
        )

    diagnose_fn = diagnose_fn or _run_diagnose_from_env
    report = diagnose_fn()
    if report["problems"]:
        raise RuntimeError(
            f"diagnostico encontrou {len(report['problems'])} problema(s) — modo prepare recusado: "
            + "; ".join(report["problems"])
        )

    raise PrepareNotAuthorizedError(
        "Gate 4A.2 nao autorizado nesta sessao: a criacao de backup/staging no "
        "Neon nao esta implementada nesta versao do script, por decisao "
        "explicita de escopo. Nenhuma instrucao SQL de escrita para o Neon "
        "existe neste arquivo."
    )


def _run_diagnose_from_env() -> dict:
    neon_url = _get_neon_url()
    local_url = _get_local_pg_url()
    neon_conn = _neon_readonly(neon_url)
    local_conn = _local_readonly(local_url)
    try:
        return run_diagnose(neon_conn, local_conn)
    finally:
        neon_conn.close()
        local_conn.close()


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[2] / ".env"))

    parser = argparse.ArgumentParser(description="Gate 4A — diagnostico/preparo Neon do Bug 8")
    parser.add_argument("--diagnose", action="store_true", help="Somente leitura (padrao)")
    parser.add_argument("--prepare", action="store_true", help="Escrita de backup/staging no Neon — nao implementado nesta versao")
    args = parser.parse_args()

    if args.prepare:
        try:
            run_prepare(args)
        except (RuntimeError, PrepareNotAuthorizedError) as e:
            print(f"!!! RECUSADO: {e}")
            return 1
        return 0

    neon_url = _get_neon_url()
    local_url = _get_local_pg_url()
    neon_conn = _neon_readonly(neon_url)
    local_conn = _local_readonly(local_url)
    try:
        report = run_diagnose(neon_conn, local_conn)
    finally:
        neon_conn.close()
        local_conn.close()

    _print_report(report, neon_url, local_url)
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
