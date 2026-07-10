"""
Gate 6B — sync Data Mart -> Neon de gold.marketplace_region_daily (aplicada
e carregada no Gate 6A) para marts.fact_marketplace_region_daily (criada
pela migration alembic apps/api/alembic/versions/005_create_fact_marketplace_region_daily.py).

Fonte (somente leitura): Data Mart RDS, DATAMART_DATABASE_URL, gold.marketplace_region_daily.
Destino (escrita): Neon, DATABASE_URL, marts.fact_marketplace_region_daily.

Mesmo padrao de pipelines/reconciliation/diagnose_bug8_neon.py +
swap_bug8_neon.py, adaptado para sync cross-database (fonte e destino sao
servidores diferentes — sem EXCEPT SQL cross-database possivel para a
fonte, entao a comparacao fonte-vs-staging e' feita em Python via
agregados; o EXCEPT SQL real so' e' usado dentro do Neon, staging vs. real,
apos o INSERT):

  1. Le TODAS as linhas da fonte (Data Mart), conexao somente leitura.
  2. Abre UMA UNICA transacao no Neon: LOCK TABLE ... IN ACCESS EXCLUSIVE
     MODE, cria staging TEMP (ON COMMIT DROP, dropada automaticamente ao
     fim da transacao mesmo em rollback), insere as linhas da fonte nela.
  3. Valida staging == fonte: agregados (contagem + somas de todas as
     colunas numericas) batendo exatamente, zero duplicidade na chave
     (date, marketplace_id, loja_id, uf), zero nulos obrigatorios.
  4. Se a tabela real ja tiver linhas (re-sync), cria backup
     (marts.fact_marketplace_region_daily_backup_<tag>) antes de tocar nela.
  5. TRUNCATE da tabela real + INSERT a partir da staging validada.
  6. Valida real == staging apos o INSERT (EXCEPT bidirecional pelas
     colunas de negocio + agregados).
  7. So' commita se TODAS as validacoes passarem; qualquer excecao aciona
     ROLLBACK antes de propagar — sem nova tentativa automatica.
  8. Registra audit.source_sync_run (source_name='marketplace_region_daily'),
     numa conexao/transacao propria, independente do resultado do sync.

Nunca imprime host/URL/credencial/CPF/order_id/filename. Mensagens de erro
passam por sanitizacao antes de ir para audit.source_sync_run.error_message
ou para stdout.

Guardas obrigatorias e simultaneas para --sync (escrita real no Neon):
  - flag --sync explicita;
  - variavel de ambiente I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1;
  - DATABASE_URL e DATAMART_DATABASE_URL explicitas, sem fallback.

Sem --sync (--diagnose, tambem o padrao sem nenhuma flag): somente leitura
nos dois lados — le a fonte, verifica se o destino existe e (se existir)
seus agregados atuais, reporta se um sync e' necessario. Nunca escreve.

Uso:
    python -m pipelines.sync_region_daily              # diagnose (padrao)
    python -m pipelines.sync_region_daily --diagnose   # idem, explicito
    python -m pipelines.sync_region_daily --sync       # escreve no Neon (guardas obrigatorias)
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

# ---------------------------------------------------------------------------
# Contrato de colunas — espelha gold.marketplace_region_daily (Data Mart) e
# marts.fact_marketplace_region_daily (Neon). id/ingested_at sao gerados por
# cada lado, nunca copiados da fonte.
# ---------------------------------------------------------------------------
BUSINESS_COLUMNS = [
    "date", "marketplace_id", "loja_id", "uf",
    "gmv", "orders", "units_sold", "canceled_orders", "returned_orders",
    "seller_shipping_cost", "buyer_shipping_fee", "estimated_shipping_fee", "reverse_shipping_fee",
    "uf_known_orders", "uf_eligible_orders",
    "shipping_cost_covered_orders", "shipping_cost_eligible_orders",
    "source_updated_at",
]

SUM_COLUMNS = [
    "gmv", "orders", "units_sold", "canceled_orders", "returned_orders",
    "seller_shipping_cost", "buyer_shipping_fee", "estimated_shipping_fee", "reverse_shipping_fee",
    "uf_known_orders", "uf_eligible_orders",
    "shipping_cost_covered_orders", "shipping_cost_eligible_orders",
]

SOURCE_TABLE = "gold.marketplace_region_daily"
REAL_TABLE = "marts.fact_marketplace_region_daily"
STAGING_TABLE_NAME = "sync_region_daily_staging"
STAGING_TABLE_QUALIFIED = f"pg_temp.{STAGING_TABLE_NAME}"

# Identificador seguro: letra inicial, so' minusculas/numeros/underscore,
# tamanho compativel com o limite de identificador do Postgres (63 bytes).
# Aplicado a todo nome de tabela gerado internamente (backup) antes de
# entrar em qualquer f-string SQL.
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class SyncValidationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Conexoes
# ---------------------------------------------------------------------------
def _get_neon_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL (Neon) nao definido. Este script exige a variavel "
            "explicita, sem fallback, para nunca conectar a um banco nao pretendido."
        )
    return url


def _get_datamart_url() -> str:
    url = os.environ.get("DATAMART_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATAMART_DATABASE_URL nao definido. Este script exige a variavel "
            "explicita, sem fallback, para nunca conectar a um banco nao pretendido."
        )
    return url


def _datamart_readonly(url: str):
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)
    conn.set_session(readonly=True)
    return conn


def _neon_readonly(url: str):
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)
    conn.set_session(readonly=True)
    return conn


def _neon_writable(url: str):
    """Sem readonly=True — usada exclusivamente pelo modo --sync. Autocommit
    permanece desligado (padrao do psycopg2): toda a escrita roda numa unica
    transacao controlada explicitamente por do_sync (commit/rollback manuais)."""
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _validate_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"identificador gerado internamente falhou na validacao de seguranca: {name!r}")
    return name


def _sanitize_error_message(exc: Exception) -> str:
    """Nunca confia cegamente em str(exc): remove qualquer trecho no formato
    usuario:senha@ antes de logar/imprimir (defesa em profundidade caso uma
    excecao de baixo nivel do driver algum dia inclua a DSN na mensagem)."""
    return re.sub(r"//[^/\s@]+:[^/\s@]+@", "//<redacted>@", str(exc))[:500]


# ---------------------------------------------------------------------------
# Leitura da fonte e agregados — puros, testaveis com conexoes/listas falsas
# ---------------------------------------------------------------------------
def fetch_source_rows(datamart_conn) -> list[dict]:
    cur = datamart_conn.cursor()
    cur.execute(f"""
        SELECT {', '.join(BUSINESS_COLUMNS)} FROM {SOURCE_TABLE}
        ORDER BY date, marketplace_id, loja_id, uf
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def _num(x) -> float:
    return float(x) if x is not None else 0.0


def _int(x) -> int:
    return int(x) if x is not None else 0


def aggregates_from_rows(rows: list[dict]) -> dict:
    agg: dict = {"n": len(rows)}
    for col in SUM_COLUMNS:
        if col in ("orders", "units_sold", "canceled_orders", "returned_orders",
                    "uf_known_orders", "uf_eligible_orders",
                    "shipping_cost_covered_orders", "shipping_cost_eligible_orders"):
            agg[col] = sum(_int(r[col]) for r in rows)
        else:
            agg[col] = round(sum(_num(r[col]) for r in rows), 2)
    return agg


def rows_with_numerator_over_denominator(rows: list[dict]) -> int:
    return sum(
        1 for r in rows
        if _int(r["uf_known_orders"]) > _int(r["uf_eligible_orders"])
        or _int(r["shipping_cost_covered_orders"]) > _int(r["shipping_cost_eligible_orders"])
    )


# ---------------------------------------------------------------------------
# Helpers Neon (recebem conn/cursor, nunca abrem conexao — testaveis com
# conexoes falsas)
# ---------------------------------------------------------------------------
def table_exists(cur, schema: str, table: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s) AS exists",
        (schema, table),
    )
    return bool(cur.fetchone()["exists"])


def aggregates_from_table(conn, schema_table: str) -> dict:
    cur = conn.cursor()
    select_cols = ", ".join(f"COALESCE(SUM({c}),0) AS {c}" for c in SUM_COLUMNS)
    cur.execute(f"SELECT COUNT(*) AS n, {select_cols} FROM {schema_table}")
    row = dict(cur.fetchone())
    cur.close()
    agg: dict = {"n": int(row["n"])}
    for col in SUM_COLUMNS:
        if col in ("orders", "units_sold", "canceled_orders", "returned_orders",
                    "uf_known_orders", "uf_eligible_orders",
                    "shipping_cost_covered_orders", "shipping_cost_eligible_orders"):
            agg[col] = int(row[col])
        else:
            agg[col] = round(float(row[col]), 2)
    return agg


def duplicates_and_nulls(conn, schema_table: str) -> tuple[int, int]:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM (
            SELECT date, marketplace_id, loja_id, uf FROM {schema_table}
            GROUP BY date, marketplace_id, loja_id, uf HAVING COUNT(*) > 1
        ) d
    """)
    dupes = cur.fetchone()["n"]
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM {schema_table}
        WHERE date IS NULL OR marketplace_id IS NULL OR loja_id IS NULL OR uf IS NULL
    """)
    nulls = cur.fetchone()["n"]
    cur.close()
    return int(dupes), int(nulls)


def except_both_directions(conn, table_a: str, table_b: str) -> tuple[int, int]:
    cols = ", ".join(BUSINESS_COLUMNS)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) AS n FROM (SELECT {cols} FROM {table_a} EXCEPT SELECT {cols} FROM {table_b}) x")
    a_not_b = cur.fetchone()["n"]
    cur.execute(f"SELECT COUNT(*) AS n FROM (SELECT {cols} FROM {table_b} EXCEPT SELECT {cols} FROM {table_a}) x")
    b_not_a = cur.fetchone()["n"]
    cur.close()
    return int(a_not_b), int(b_not_a)


def create_staging_table(cur) -> None:
    cur.execute(f"""
        CREATE TEMP TABLE {STAGING_TABLE_NAME} (LIKE {REAL_TABLE} INCLUDING DEFAULTS)
        ON COMMIT DROP
    """)


def insert_into_staging(cur, rows: list[dict]) -> None:
    if not rows:
        return
    sql = f"INSERT INTO {STAGING_TABLE_QUALIFIED} ({', '.join(BUSINESS_COLUMNS)}) VALUES %s"
    batch = [tuple(r[c] for c in BUSINESS_COLUMNS) for r in rows]
    execute_values(cur, sql, batch, page_size=500)


def backup_real_table_if_present(conn, tag: str) -> str | None:
    current = aggregates_from_table(conn, REAL_TABLE)
    if current["n"] == 0:
        return None
    backup_name = _validate_identifier(f"fact_marketplace_region_daily_backup_{tag}")
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE marts.{backup_name} AS SELECT * FROM {REAL_TABLE}")
    cur.close()
    return backup_name


# ---------------------------------------------------------------------------
# Gate 6B.3 — sync real no Neon (implementado, so' executado sob as guardas
# de run_sync; nao chamado com conexoes reais nesta sessao/Gate 6B.1).
# ---------------------------------------------------------------------------
def do_sync(source_rows: list[dict], neon_conn, tag: str | None = None) -> dict:
    """UNICA transacao: timeouts locais -> ACCESS EXCLUSIVE LOCK na tabela
    real -> staging TEMP -> validacao staging vs fonte (agregados + zero
    duplicidade + zero nulos) -> backup da real se ja tiver linhas ->
    TRUNCATE so' da real -> INSERT a partir da staging -> validacao real vs
    staging pos-INSERT (EXCEPT bidirecional + agregados) -> COMMIT. Qualquer
    excecao aciona ROLLBACK antes de propagar, sem nova tentativa automatica."""
    if not source_rows:
        raise SyncValidationError("fonte (Data Mart) retornou 0 linhas — sync recusado para nao truncar o destino sem motivo")

    cur = neon_conn.cursor()
    try:
        cur.execute("SET LOCAL lock_timeout = '10s'")
        cur.execute("SET LOCAL statement_timeout = '120s'")
        cur.execute(f"LOCK TABLE {REAL_TABLE} IN ACCESS EXCLUSIVE MODE")

        bad = rows_with_numerator_over_denominator(source_rows)
        if bad:
            raise SyncValidationError(f"{bad} linha(s) da fonte com numerador > denominador — sync recusado")

        source_agg = aggregates_from_rows(source_rows)

        create_staging_table(cur)
        insert_into_staging(cur, source_rows)

        staging_agg = aggregates_from_table(neon_conn, STAGING_TABLE_QUALIFIED)
        if staging_agg != source_agg:
            raise SyncValidationError(f"staging diverge da fonte: staging={staging_agg} fonte={source_agg}")

        dupes, nulls = duplicates_and_nulls(neon_conn, STAGING_TABLE_QUALIFIED)
        if dupes:
            raise SyncValidationError(f"staging com {dupes} chave(s) duplicada(s)")
        if nulls:
            raise SyncValidationError(f"staging com {nulls} linha(s) com nulos obrigatorios")

        backup_name = backup_real_table_if_present(neon_conn, tag or _now_tag())

        cur.execute(f"TRUNCATE TABLE {REAL_TABLE}")
        cur.execute(f"""
            INSERT INTO {REAL_TABLE} ({', '.join(BUSINESS_COLUMNS)})
            SELECT {', '.join(BUSINESS_COLUMNS)} FROM {STAGING_TABLE_QUALIFIED}
        """)

        real_not_staging, staging_not_real = except_both_directions(neon_conn, REAL_TABLE, STAGING_TABLE_QUALIFIED)
        if real_not_staging or staging_not_real:
            raise SyncValidationError(
                f"tabela real diverge da staging apos o INSERT: "
                f"real_not_staging={real_not_staging} staging_not_real={staging_not_real}"
            )

        real_after_agg = aggregates_from_table(neon_conn, REAL_TABLE)
        if real_after_agg != staging_agg:
            raise SyncValidationError(f"agregados divergem apos o INSERT: real={real_after_agg} staging={staging_agg}")

        neon_conn.commit()
        cur.close()
        return {"backup_table": backup_name, "source_agg": source_agg, "real_agg_after": real_after_agg}
    except Exception:
        neon_conn.rollback()
        cur.close()
        raise


# ---------------------------------------------------------------------------
# audit.source_sync_run — mesmo contrato de pipelines/ingestion/daily_performance.py
# e pipelines/sync_produtos.py. Conexao propria, sempre commitada
# independente do resultado do sync principal.
# ---------------------------------------------------------------------------
def _audit_start(conn, source_name: str) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit.source_sync_run (source_name, status, started_at)
        VALUES (%s, 'running', NOW())
        RETURNING sync_run_id
        """,
        (source_name,),
    )
    run_id = cur.fetchone()["sync_run_id"]
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
# --sync — orquestracao gated
# ---------------------------------------------------------------------------
def run_sync(args, connect_fn=None, tag_fn=None) -> dict:
    """Guardas obrigatorias e simultaneas: flag --sync, variavel de ambiente
    I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1. So' depois disso le a
    fonte e delega a do_sync."""
    if not getattr(args, "sync", False):
        raise RuntimeError("Gate 6B requer a flag --sync explicita")
    if os.environ.get("I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY") != "1":
        raise RuntimeError(
            "Gate 6B requer a variavel de ambiente "
            "I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1 explicitamente definida"
        )

    tag_fn = tag_fn or _now_tag
    tag = tag_fn()

    if connect_fn is not None:
        datamart_conn, neon_conn, audit_conn = connect_fn()
    else:
        datamart_conn = _datamart_readonly(_get_datamart_url())
        neon_conn = _neon_writable(_get_neon_url())
        audit_conn = _neon_writable(_get_neon_url())

    run_id = _audit_start(audit_conn, "marketplace_region_daily")
    try:
        source_rows = fetch_source_rows(datamart_conn)
        result = do_sync(source_rows, neon_conn, tag=tag)

        dates = [r["date"] for r in source_rows]
        _audit_finish(
            audit_conn, run_id, "success",
            len(source_rows), result["real_agg_after"]["n"],
            min(dates), max(dates),
        )
        return result
    except Exception as exc:
        _audit_finish(audit_conn, run_id, "failed", 0, 0, error=_sanitize_error_message(exc))
        raise
    finally:
        datamart_conn.close()
        neon_conn.close()
        audit_conn.close()


# ---------------------------------------------------------------------------
# --diagnose (padrao) — somente leitura nos dois lados
# ---------------------------------------------------------------------------
def run_diagnose(connect_fn=None) -> dict:
    if connect_fn is not None:
        datamart_conn, neon_conn = connect_fn()
    else:
        datamart_conn = _datamart_readonly(_get_datamart_url())
        neon_conn = _neon_readonly(_get_neon_url())
    try:
        source_rows = fetch_source_rows(datamart_conn)
        source_agg = aggregates_from_rows(source_rows)

        cur = neon_conn.cursor()
        target_exists = table_exists(cur, "marts", "fact_marketplace_region_daily")
        cur.close()

        target_agg = aggregates_from_table(neon_conn, REAL_TABLE) if target_exists else None
        return {
            "source_agg": source_agg,
            "target_exists": target_exists,
            "target_agg": target_agg,
            "needs_sync": target_agg != source_agg,
        }
    finally:
        datamart_conn.close()
        neon_conn.close()


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(Path(__file__).resolve().parent.parent / ".env"))

    parser = argparse.ArgumentParser(description="Gate 6B — sync Data Mart -> Neon de gold.marketplace_region_daily")
    parser.add_argument("--diagnose", action="store_true", help="Somente leitura (padrao)")
    parser.add_argument("--sync", action="store_true", help="Escreve no Neon — exige guardas explicitas, ver docstring do modulo")
    args = parser.parse_args()

    if args.sync:
        try:
            result = run_sync(args)
        except RuntimeError as e:
            print(f"!!! RECUSADO/ABORTADO: {e}")
            return 1
        print(f"SYNC CONCLUIDO: {result['real_agg_after']['n']} linhas no Neon, gmv={result['real_agg_after']['gmv']}")
        print(f"Backup preservado: marts.{result['backup_table']}" if result["backup_table"] else "Backup: nao aplicavel (tabela estava vazia)")
        return 0

    report = run_diagnose()
    print(f"Fonte (Data Mart) — linhas: {report['source_agg']['n']}, gmv: {report['source_agg']['gmv']}")
    print(f"Destino (Neon) existe: {report['target_exists']}")
    if report["target_agg"] is not None:
        print(f"Destino (Neon) — linhas: {report['target_agg']['n']}, gmv: {report['target_agg']['gmv']}")
    print(f"Precisa sincronizar: {report['needs_sync']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
