"""
Parsing e execução do DDL versionado (`db/sql/raw/shopee_raw_ddl.sql`) para
a Fase Raw Shopee 2.

O arquivo .sql é a fonte de verdade para revisão humana (inclui BEGIN/COMMIT
e comentários). Este módulo extrai só os statements executáveis e os roda
dentro de UMA transação Python explícita — controle de transação fica
inteiramente no código, não no texto do arquivo, para nunca depender de como
um driver interpreta um BEGIN/COMMIT embutido numa string.
"""
from __future__ import annotations

from pathlib import Path

import psycopg2

from pipelines.ingestion.shopee_raw.write_conn import (
    ADVISORY_LOCK_KEY,
    WritePreflightBlocked,
    release_advisory_lock,
    sanitize_error_message,
    try_acquire_advisory_lock,
)

DEFAULT_DDL_PATH = Path(__file__).resolve().parents[3] / "db" / "sql" / "raw" / "shopee_raw_ddl.sql"

_TRANSACTION_CONTROL_STATEMENTS = {"begin", "commit", "rollback"}


def _strip_line_comment(line: str, in_string: bool) -> tuple[str, bool]:
    """Remove um comentário '--' de fim de linha, mas só fora de uma string
    literal de aspas simples (senão um texto como '... — isso ...' dentro de
    um COMMENT ON ... IS '...' seria cortado no meio)."""
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'":
            in_string = not in_string
            out.append(ch)
            i += 1
            continue
        if not in_string and line[i : i + 2] == "--":
            break
        out.append(ch)
        i += 1
    return "".join(out), in_string


def parse_ddl_statements(sql_text: str) -> list[str]:
    """Remove comentários de linha (com consciência de string literal) e
    separa por ';' SÓ fora de strings — um ';' dentro de um
    `COMMENT ON ... IS '...(sem granularidade diaria);...'` não é um fim de
    statement. Descarta BEGIN/COMMIT/ROLLBACK (controlados pelo chamador) e
    fragmentos vazios."""
    lines_no_comments = []
    in_string = False
    for line in sql_text.splitlines():
        cleaned, in_string = _strip_line_comment(line, in_string)
        lines_no_comments.append(cleaned)
    joined = "\n".join(lines_no_comments)

    statements = []
    current: list[str] = []
    in_string = False
    for ch in joined:
        if ch == "'":
            in_string = not in_string
            current.append(ch)
            continue
        if ch == ";" and not in_string:
            stmt = "".join(current).strip()
            current = []
            if stmt and stmt.lower() not in _TRANSACTION_CONTROL_STATEMENTS:
                statements.append(stmt)
            continue
        current.append(ch)

    tail = "".join(current).strip()
    if tail and tail.lower() not in _TRANSACTION_CONTROL_STATEMENTS:
        statements.append(tail)

    return statements


def execute_ddl(write_url: str, ddl_path: Path = DEFAULT_DDL_PATH) -> list[str]:
    """Executa o DDL em uma única transação, sob advisory lock e timeouts
    locais. Levanta WritePreflightBlocked se o lock já estiver em uso por
    outra execução concorrente. Qualquer exceção durante os statements faz
    ROLLBACK integral — nada fica parcialmente criado.

    Retorna a lista de statements executados (para o relatório — nenhum
    contém dado de linha/PII, é só o schema)."""
    statements = parse_ddl_statements(ddl_path.read_text(encoding="utf-8"))

    conn = psycopg2.connect(write_url, connect_timeout=15)
    conn.autocommit = False
    try:
        if not try_acquire_advisory_lock(conn):
            raise WritePreflightBlocked(
                f"advisory lock {ADVISORY_LOCK_KEY} já está em uso — outra execução da "
                "ingestão Shopee raw pode estar em andamento. Abortando sem tentar novamente."
            )
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                cur.execute("SET LOCAL statement_timeout = '120s'")
                for stmt in statements:
                    cur.execute(stmt)
            conn.commit()
            return statements
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise RuntimeError(f"DDL falhou, rollback completo executado: {sanitize_error_message(exc)}") from exc
        finally:
            release_advisory_lock(conn)
    finally:
        conn.close()
