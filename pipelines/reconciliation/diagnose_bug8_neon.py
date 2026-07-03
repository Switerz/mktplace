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

  --prepare: cria backup + staging SOMENTE no Neon (Gate 4A.2), a partir
      da staging local ja validada no Gate 2 — nunca reprocessa os XLSX de
      novo. Exige simultaneamente: flag --prepare, variavel de ambiente
      I_UNDERSTAND_THIS_TOUCHES_NEON=1, e um diagnostico limpo executado
      imediatamente antes (sempre recalculado, nunca reaproveitado). Roda
      numa unica transacao no Neon: adquire um lock de leitura na tabela
      real (bloqueia escritores concorrentes sem bloquear leitores e sem
      ser, ele mesmo, uma escrita), cria o backup a partir da tabela real,
      cria a staging e a popula com as 13 colunas de negocio explicitas,
      reconcilia cada objeto criado (contagem, GMV, unidades, concluidos,
      cancelados, duplicidades, nulos), e so' commita se tudo passar —
      qualquer falha aciona rollback integral antes de propagar a excecao.
      Nomes de backup/staging sao gerados internamente com timestamp e
      validados como identificadores seguros antes de entrar em qualquer
      SQL; o modo recusa criar um objeto se o nome gerado ja existir (nunca
      sobrescreve). A tabela real do Neon nunca recebe nenhuma instrucao
      que a modifique — nenhuma instrucao SQL desse tipo existe neste
      arquivo, para nenhuma tabela.

Uso:
    python -m pipelines.reconciliation.diagnose_bug8_neon              # diagnose
    python -m pipelines.reconciliation.diagnose_bug8_neon --diagnose   # idem, explicito
    python -m pipelines.reconciliation.diagnose_bug8_neon --prepare    # cria backup/staging no Neon (guardas obrigatorias)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from pipelines.reconciliation.reconcile_bug8_canceled_only import (  # noqa: E402
    _get_local_pg_url,
    _sanitize_url,
)
from pipelines.reconciliation.swap_bug8_canceled_only import (  # noqa: E402
    BACKUP_TABLE,
    EXPECTED_STAGING_CANCELED,
    EXPECTED_STAGING_GMV,
    EXPECTED_STAGING_ROWS,
    REAL_TABLE,
    STAGING_TABLE as LOCAL_STAGING_TABLE,
    _table_exists,
)

BUSINESS_COLUMNS = [
    "ref_month", "brand", "sku_ref", "sku_ref_key", "product_name", "variation_name",
    "gmv", "units_sold", "completed_orders", "canceled_orders",
    "cancel_rate_pct", "unique_buyers", "avg_price",
]

# Identificador seguro: letra inicial, so' minusculas/numeros/underscore,
# tamanho compativel com o limite de identificador do Postgres (63 bytes).
# Aplicado a TODO nome de tabela gerado internamente antes de entrar em
# qualquer f-string SQL — defesa em profundidade mesmo sem entrada de
# usuario, contra um refactor futuro que introduza interpolacao insegura.
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class PrepareValidationError(RuntimeError):
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


def _neon_writable(url: str):
    """Conexao Neon SEM readonly=True — usada exclusivamente pelo modo
    --prepare, que precisa criar backup/staging. Autocommit permanece
    desligado (padrao do psycopg2): toda a operacao roda numa unica
    transacao controlada explicitamente por do_prepare_neon (commit/
    rollback manuais)."""
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _validate_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"identificador gerado internamente falhou na validacao de seguranca: {name!r}")
    return name


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
# Helpers de agregacao/reconciliacao no Neon (puros o suficiente para
# testar com conexoes falsas — recebem conn/cursor, nunca abrem conexao)
# ---------------------------------------------------------------------------
def _aggregates_from_rows(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "gmv": round(sum(float(r["gmv"] or 0) for r in rows), 2),
        "units_sold": sum(int(r["units_sold"] or 0) for r in rows),
        "completed_orders": sum(int(r["completed_orders"] or 0) for r in rows),
        "canceled_orders": sum(int(r["canceled_orders"] or 0) for r in rows),
    }


def _aggregates_from_table(conn, table: str) -> dict:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(gmv), 0) AS gmv,
               COALESCE(SUM(units_sold), 0) AS units_sold,
               COALESCE(SUM(completed_orders), 0) AS completed_orders,
               COALESCE(SUM(canceled_orders), 0) AS canceled_orders
        FROM marts.{table}
    """)
    row = dict(cur.fetchone())
    cur.close()
    return {
        "n": int(row["n"]), "gmv": round(float(row["gmv"]), 2),
        "units_sold": int(row["units_sold"]), "completed_orders": int(row["completed_orders"]),
        "canceled_orders": int(row["canceled_orders"]),
    }


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


def _except_both_directions(conn, table_a: str, table_b: str) -> tuple[int, int]:
    cols = ", ".join(BUSINESS_COLUMNS)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) AS n FROM (SELECT {cols} FROM marts.{table_a} EXCEPT SELECT {cols} FROM marts.{table_b}) x")
    a_not_b = cur.fetchone()["n"]
    cur.execute(f"SELECT COUNT(*) AS n FROM (SELECT {cols} FROM marts.{table_b} EXCEPT SELECT {cols} FROM marts.{table_a}) x")
    b_not_a = cur.fetchone()["n"]
    cur.close()
    return a_not_b, b_not_a


# ---------------------------------------------------------------------------
# Gate 4A.2 — cria backup + staging SOMENTE no Neon (implementado, mas so'
# executado sob as guardas de run_prepare; nao chamado com conexoes reais
# nesta sessao).
# ---------------------------------------------------------------------------
def do_prepare_neon(neon_conn, local_conn, tag: str) -> dict:
    """Dentro de uma UNICA transacao no Neon: valida que os nomes gerados
    nao colidem com objetos existentes, adquire um lock de leitura
    (SHARE MODE — bloqueia escritores concorrentes sem bloquear leitores e
    sem constituir, ele mesmo, uma escrita), REVALIDA sob o lock que o Neon
    ainda e' identico ao backup local pre-fix (fecha a janela de corrida
    entre o diagnostico inicial de run_prepare, que roda ANTES desta
    funcao/do lock, e o momento em que de fato vamos escrever), cria o
    backup a partir da tabela real, cria a staging e a popula com as
    linhas da staging local ja validada (13 colunas explicitas), reconcilia
    cada objeto criado, e so' faz commit se TODAS as reconciliacoes
    passarem. Qualquer excecao aciona rollback antes de propagar. NUNCA
    emite uma instrucao que modifique a tabela real."""
    backup_name = _validate_identifier(f"{REAL_TABLE}_backup_bug8_neon_{tag}")
    staging_name = _validate_identifier(f"{REAL_TABLE}_staging_bug8_neon_{tag}")

    cur = neon_conn.cursor()
    try:
        if _table_exists(cur, backup_name):
            raise PrepareValidationError(f"objeto ja existe, recusando sobrescrever: marts.{backup_name}")
        if _table_exists(cur, staging_name):
            raise PrepareValidationError(f"objeto ja existe, recusando sobrescrever: marts.{staging_name}")

        cur.execute(f"LOCK TABLE marts.{REAL_TABLE} IN SHARE MODE")

        # Revalidacao SOB O LOCK, antes de qualquer escrita: reusa a mesma
        # comparacao de run_diagnose (13 colunas de negocio nos dois
        # sentidos + chaves + agregados por marca x mes) contra o Neon
        # (agora protegido pelo lock) e o backup local pre-fix. Se algo
        # mudou entre o diagnostico inicial (fora desta transacao) e agora,
        # aborta antes do CREATE TABLE do backup Neon.
        revalidation = run_diagnose(neon_conn, local_conn)
        if revalidation["problems"]:
            raise PrepareValidationError(
                "revalidacao sob lock encontrou divergencia entre o Neon e o backup local "
                "pre-fix — o Neon mudou entre o diagnostico inicial e a aquisicao do lock, "
                "abortando antes de qualquer escrita: " + "; ".join(revalidation["problems"])
            )

        cur.execute(f"CREATE TABLE marts.{backup_name} AS SELECT * FROM marts.{REAL_TABLE}")

        real_agg = _aggregates_from_table(neon_conn, REAL_TABLE)
        backup_agg = _aggregates_from_table(neon_conn, backup_name)
        if real_agg != backup_agg:
            raise PrepareValidationError(f"backup diverge da tabela real imediatamente apos a criacao: real={real_agg} backup={backup_agg}")
        real_not_backup, backup_not_real = _except_both_directions(neon_conn, REAL_TABLE, backup_name)
        if real_not_backup or backup_not_real:
            raise PrepareValidationError(f"backup diverge da tabela real (EXCEPT nao-zero: real_not_backup={real_not_backup} backup_not_real={backup_not_real})")

        from etl.load_shopee_products import DDL as _SHOPEE_TABLE_DDL
        cur.execute(_SHOPEE_TABLE_DDL.replace(REAL_TABLE, staging_name))

        local_rows = _fetch_business_rows(local_conn, LOCAL_STAGING_TABLE)
        if not local_rows:
            raise PrepareValidationError("staging local esta vazia — nada para copiar ao Neon")

        insert_sql = f"INSERT INTO marts.{staging_name} ({', '.join(BUSINESS_COLUMNS)}) VALUES %s"
        batch = [tuple(r[c] for c in BUSINESS_COLUMNS) for r in local_rows]
        execute_values(cur, insert_sql, batch, page_size=500)

        staging_agg = _aggregates_from_table(neon_conn, staging_name)
        local_agg = _aggregates_from_rows(local_rows)
        for key in ("n", "gmv", "units_sold", "completed_orders", "canceled_orders"):
            if staging_agg[key] != local_agg[key]:
                raise PrepareValidationError(f"staging Neon diverge da staging local em {key}: neon={staging_agg[key]} local={local_agg[key]}")

        expected = {"n": EXPECTED_STAGING_ROWS, "gmv": EXPECTED_STAGING_GMV, "canceled_orders": EXPECTED_STAGING_CANCELED}
        for key, exp in expected.items():
            if staging_agg[key] != exp:
                raise PrepareValidationError(f"staging Neon nao confere com os numeros esperados do Gate 2 em {key}: {staging_agg[key]} != {exp}")

        dupes, nulls = _duplicates_and_nulls(neon_conn, staging_name)
        if dupes:
            raise PrepareValidationError(f"staging Neon com {dupes} chaves duplicadas")
        if nulls:
            raise PrepareValidationError(f"staging Neon com {nulls} linhas com nulos obrigatorios")

        neon_conn.commit()
        cur.close()
        return {
            "backup_table": backup_name, "staging_table": staging_name,
            "backup_agg": backup_agg, "staging_agg": staging_agg,
        }
    except Exception:
        neon_conn.rollback()
        cur.close()
        raise


# ---------------------------------------------------------------------------
# --prepare — orquestracao gated
# ---------------------------------------------------------------------------
def run_prepare(args, diagnose_fn=None, connect_fn=None, tag_fn=None) -> dict:
    """Guardas obrigatorias e simultaneas: flag --prepare, variavel de
    ambiente I_UNDERSTAND_THIS_TOUCHES_NEON=1, e diagnostico limpo
    executado IMEDIATAMENTE antes (sempre chamado de novo aqui, nunca
    reaproveitado de uma execucao anterior). So' depois disso abre conexao
    de escrita com o Neon e delega a do_prepare_neon."""
    if not args.prepare:
        raise RuntimeError("modo prepare requer a flag --prepare explicita")
    if os.environ.get("I_UNDERSTAND_THIS_TOUCHES_NEON") != "1":
        raise RuntimeError(
            "modo prepare requer a variavel de ambiente "
            "I_UNDERSTAND_THIS_TOUCHES_NEON=1 explicitamente definida"
        )

    neon_url = _get_neon_url()
    print(f"Neon (prepare): {_sanitize_url(neon_url)}")

    diagnose_fn = diagnose_fn or _run_diagnose_from_env
    report = diagnose_fn()
    if report["problems"]:
        raise RuntimeError(
            f"diagnostico encontrou {len(report['problems'])} problema(s) — modo prepare recusado: "
            + "; ".join(report["problems"])
        )
    print("Diagnostico limpo, executado agora — prosseguindo com backup/staging no Neon.")

    tag_fn = tag_fn or _now_tag
    tag = tag_fn()

    if connect_fn is not None:
        neon_conn, local_conn = connect_fn()
    else:
        local_url = _get_local_pg_url()
        neon_conn = _neon_writable(neon_url)
        local_conn = _local_readonly(local_url)

    try:
        result = do_prepare_neon(neon_conn, local_conn, tag)
    finally:
        neon_conn.close()
        local_conn.close()

    print(f"Backup Neon criado: marts.{result['backup_table']}")
    print(f"Staging Neon criada: marts.{result['staging_table']}")
    return result


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
    parser.add_argument("--prepare", action="store_true", help="Cria backup/staging no Neon — exige guardas explicitas, ver docstring do modulo")
    args = parser.parse_args()

    if args.prepare:
        try:
            run_prepare(args)
        except RuntimeError as e:
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
