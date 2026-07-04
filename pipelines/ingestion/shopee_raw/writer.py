"""
Carga append-only success-only para as tabelas raw.shopee_* — Fase Raw
Shopee 2. Cada arquivo é UMA transação: as linhas-filhas entram primeiro
(usando um file_id reservado via nextval(), com FK DEFERRABLE INITIALLY
DEFERRED — ver db/sql/raw/shopee_raw_ddl.sql), e o INSERT de
raw.shopee_ingestion_file é a última instrução, funcionando como marca de
commit. Sem UPDATE, sem DELETE, sem retry automático.

Nunca loga `raw_payload` nem qualquer valor de célula — só contagens,
hashes e metadados técnicos.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg2.extras

from pipelines.ingestion.shopee_raw import inventory as inv
from pipelines.ingestion.shopee_raw.hashing import json_safe, sha256_file

SOURCE_TABLE_MAP = {
    inv.SOURCE_ORDERS: "shopee_order_item_export",
    inv.SOURCE_SHOP_STATS: "shopee_shop_stats_export",
    inv.SOURCE_ADS: "shopee_ads_export",
}


class FileChangedDuringReadError(RuntimeError):
    """O SHA-256 do arquivo mudou entre o inventário e a leitura de linhas."""


@dataclass
class FileWriteOutcome:
    relative_path: str
    outcome: str  # "inserted" | "skipped_idempotent" | "failed"
    file_id: Optional[int] = None
    rows_inserted: int = 0
    error: Optional[str] = None


def is_already_ingested(cur, file_sha256: str, sheet_name: Optional[str]) -> Optional[int]:
    cur.execute(
        "SELECT file_id FROM raw.shopee_ingestion_file "
        "WHERE file_sha256 = %s AND sheet_name IS NOT DISTINCT FROM %s",
        (file_sha256, sheet_name),
    )
    row = cur.fetchone()
    return row[0] if row else None


def insert_file(
    conn,
    data_path: Path,
    record: inv.FileInventoryRecord,
    batch_id: str,
) -> FileWriteOutcome:
    """Processa UM arquivo em UMA transação. `conn` já deve estar com
    autocommit=False. Nunca lança para fora sem antes garantir rollback."""
    table = SOURCE_TABLE_MAP.get(record.source_type)
    if table is None:
        return FileWriteOutcome(record.relative_path, outcome="failed", error=f"source_type não suportado para carga: {record.source_type}")

    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SET LOCAL statement_timeout = '300s'")

        existing_file_id = is_already_ingested(cur, record.file_sha256, record.sheet_name)
        if existing_file_id is not None:
            conn.rollback()
            return FileWriteOutcome(record.relative_path, outcome="skipped_idempotent", file_id=existing_file_id)

        path = data_path / record.relative_path
        sha_before = record.file_sha256
        result = inv.read_source_file(path, record.source_type)
        sha_after = sha256_file(path)
        if sha_after != sha_before:
            conn.rollback()
            raise FileChangedDuringReadError(
                f"{record.relative_path}: SHA-256 mudou durante a leitura (arquivo alterado durante a carga)"
            )

        cur.execute("SELECT nextval('raw.shopee_ingestion_file_file_id_seq')")
        (file_id,) = cur.fetchone()

        rows_payload = [
            (
                file_id,
                record.brand,
                row.source_row_number,
                psycopg2.extras.Json(_json_safe_payload(row.raw_payload)),
                row.row_sha256,
            )
            for row in result.rows
        ]

        if rows_payload:
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO raw.{table} (file_id, brand, source_row_number, raw_payload, row_sha256) VALUES %s",
                rows_payload,
                page_size=1000,
            )

        cur.execute(
            """
            INSERT INTO raw.shopee_ingestion_file (
                file_id, batch_id, source_type, brand, source_filename,
                file_sha256, file_size_bytes, source_modified_at, sheet_name,
                source_row_count, headers_json, schema_fingerprint, ingestion_status
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, 'success'
            )
            """,
            (
                file_id,
                batch_id,
                record.source_type,
                record.brand,
                record.relative_path,
                record.file_sha256,
                record.size_bytes,
                record.source_modified_at,
                record.sheet_name,
                len(result.rows),
                psycopg2.extras.Json(record.headers or []),
                record.schema_fingerprint,
            ),
        )

        conn.commit()
        return FileWriteOutcome(record.relative_path, outcome="inserted", file_id=file_id, rows_inserted=len(result.rows))
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return FileWriteOutcome(record.relative_path, outcome="failed", error=_safe_error_summary(exc, record.relative_path))
    finally:
        cur.close()


def _safe_error_summary(exc: BaseException, relative_path: str) -> str:
    """Nunca usa `str(exc)` (nem `.pgerror`, nem `.diag.message_detail`):
    uma falha de INSERT pode vir com DETAIL/statement/parâmetros do
    servidor, que podem conter valores de linha — inclusive PII. Só
    metadados técnicos seguros: tipo da exceção, `pgcode` e
    `constraint_name` (quando existirem, via `psycopg2.Error.diag`), e o
    caminho relativo do arquivo. Nunca payload, nunca SQL, nunca célula."""
    parts = [type(exc).__name__]
    pgcode = getattr(exc, "pgcode", None)
    if pgcode:
        parts.append(f"pgcode={pgcode}")
    diag = getattr(exc, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None) if diag is not None else None
    if constraint_name:
        parts.append(f"constraint={constraint_name}")
    parts.append(f"file={relative_path}")
    return " ".join(parts)


def _json_safe_payload(payload: dict) -> dict:
    return json_safe(payload)


def new_batch_id() -> str:
    return str(uuid.uuid4())
