"""
Reconciliação pós-carga — Fase Raw Shopee 2.

Recebe uma `engine` SQLAlchemy já pronta. Quem decide qual credencial usar
é o chamador — a função em si é agnóstica, mas as duas engines possíveis
têm papéis diferentes:

- **Reconciliação imediatamente após uma escrita** (ex: dentro de
  `pipelines/ingestion/load_shopee_raw.py::run_apply_backfill`) usa a
  PRIMARY — a própria credencial de escrita (`DATAMART_SHOPEE_WRITE_URL`),
  aberta com `execution_options(postgresql_readonly=True)` (nunca escreve,
  ver `run_reconciliation` abaixo). É a primary que acabou de commitar,
  então não há lag de replicação a considerar.
- **Verificações posteriores/rotineiras** podem usar a credencial de
  LEITURA existente (`DATAMART_DATABASE_URL`, a mesma usada por TikTok/ML).
  Achado operacional desta fase: essa credencial é uma READ REPLICA
  (`pg_is_in_recovery() = true`), não a primary — está sujeita a lag de
  replicação. Uma contagem incompleta vinda da réplica não deve ser
  tratada como sucesso silencioso; se usada para conferência logo após
  uma escrita, uma contagem abaixo do esperado deve reprovar claramente,
  nunca ser mascarada.

Nunca seleciona `raw_payload` inteiro — só contagens, hashes, tamanhos e
presença de headers (para a checagem de PII), nunca valores de célula.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

_CHILD_TABLES = {
    "orders": "shopee_order_item_export",
    "shop_stats": "shopee_shop_stats_export",
    "ads": "shopee_ads_export",
}

_PII_ORDERS_HEADERS = [
    "Nome do destinatário",
    "Telefone",
    "Endereço de entrega",
    "CEP",
    "CPF do Comprador",
    "Nome de usuário (comprador)",
]


@dataclass
class ReconciliationReport:
    manifest_counts_by_source_brand: dict = field(default_factory=dict)
    row_counts_by_table: dict = field(default_factory=dict)
    orphan_children: dict = field(default_factory=dict)
    manifests_without_children: int = 0
    duplicate_file_row: dict = field(default_factory=dict)
    duplicate_manifest_key: int = 0
    schema_fingerprints_by_source: dict = field(default_factory=dict)
    pii_headers_present_files: int = 0
    pii_headers_absent_files: int = 0
    table_sizes_bytes: dict = field(default_factory=dict)
    total_manifest_rows: int = 0
    total_child_rows: int = 0
    problems: list[str] = field(default_factory=list)


def run_reconciliation(engine) -> ReconciliationReport:
    """`execution_options(postgresql_readonly=True)` instrui o driver
    (psycopg2) a chamar `set_session(readonly=True)` ANTES de qualquer
    `execute()`, o que o Postgres aplica à sessão inteira — uma tentativa
    de escrita seria rejeitada pelo servidor, não apenas "evitada" pelo
    código. Isso acontece antes da primeira query abaixo."""
    report = ReconciliationReport()
    with engine.connect().execution_options(postgresql_readonly=True) as conn:
        rows = conn.execute(text(
            "SELECT source_type, brand, count(*) AS n, sum(source_row_count) AS rows "
            "FROM raw.shopee_ingestion_file GROUP BY 1, 2 ORDER BY 1, 2"
        )).fetchall()
        for r in rows:
            report.manifest_counts_by_source_brand[f"{r.brand}/{r.source_type}"] = {
                "arquivos": r.n, "linhas_no_manifesto": r.rows or 0,
            }
            report.total_manifest_rows += r.rows or 0

        for source_type, table in _CHILD_TABLES.items():
            n = conn.execute(text(f"SELECT count(*) FROM raw.{table}")).scalar()
            report.row_counts_by_table[table] = n
            report.total_child_rows += n

            orphans = conn.execute(text(
                f"SELECT count(*) FROM raw.{table} c "
                f"LEFT JOIN raw.shopee_ingestion_file f ON f.file_id = c.file_id "
                f"WHERE f.file_id IS NULL"
            )).scalar()
            report.orphan_children[table] = orphans
            if orphans:
                report.problems.append(f"{table}: {orphans} linha(s) órfã(s) sem manifesto correspondente")

            dupes = conn.execute(text(
                f"SELECT count(*) FROM (SELECT file_id, source_row_number FROM raw.{table} "
                f"GROUP BY 1, 2 HAVING count(*) > 1) d"
            )).scalar()
            report.duplicate_file_row[table] = dupes
            if dupes:
                report.problems.append(f"{table}: {dupes} duplicidade(s) de (file_id, source_row_number)")

            size = conn.execute(text(f"SELECT pg_total_relation_size('raw.{table}')")).scalar()
            report.table_sizes_bytes[table] = size

        report.table_sizes_bytes["shopee_ingestion_file"] = conn.execute(
            text("SELECT pg_total_relation_size('raw.shopee_ingestion_file')")
        ).scalar()

        without_children = conn.execute(text(
            "SELECT count(*) FROM raw.shopee_ingestion_file f WHERE f.source_row_count > 0 AND NOT EXISTS ("
            "  SELECT 1 FROM raw.shopee_order_item_export c WHERE c.file_id = f.file_id "
            "  UNION ALL SELECT 1 FROM raw.shopee_shop_stats_export c WHERE c.file_id = f.file_id "
            "  UNION ALL SELECT 1 FROM raw.shopee_ads_export c WHERE c.file_id = f.file_id"
            ")"
        )).scalar()
        report.manifests_without_children = without_children
        if without_children:
            report.problems.append(f"{without_children} manifesto(s) com source_row_count>0 sem nenhuma linha-filha")

        dup_manifest = conn.execute(text(
            "SELECT count(*) FROM (SELECT file_sha256, sheet_name FROM raw.shopee_ingestion_file "
            "GROUP BY 1, 2 HAVING count(*) > 1) d"
        )).scalar()
        report.duplicate_manifest_key = dup_manifest
        if dup_manifest:
            report.problems.append(f"{dup_manifest} duplicidade(s) de (file_sha256, sheet_name) em shopee_ingestion_file")

        fps = conn.execute(text(
            "SELECT source_type, count(DISTINCT schema_fingerprint) AS n "
            "FROM raw.shopee_ingestion_file GROUP BY 1"
        )).fetchall()
        report.schema_fingerprints_by_source = {r.source_type: r.n for r in fps}

        pii_present = conn.execute(text(
            "SELECT count(*) FROM raw.shopee_ingestion_file f "
            "WHERE f.source_type = 'orders' AND EXISTS ("
            "  SELECT 1 FROM jsonb_array_elements_text(f.headers_json) h(header) "
            "  WHERE h.header = ANY(:headers)"
            ")"
        ), {"headers": _PII_ORDERS_HEADERS}).scalar()
        pii_total_orders = conn.execute(text(
            "SELECT count(*) FROM raw.shopee_ingestion_file WHERE source_type = 'orders'"
        )).scalar()
        report.pii_headers_present_files = pii_present
        report.pii_headers_absent_files = pii_total_orders - pii_present

    if report.total_manifest_rows != report.total_child_rows:
        report.problems.append(
            f"total de linhas no manifesto ({report.total_manifest_rows}) difere do total de "
            f"linhas-filhas ({report.total_child_rows})"
        )

    return report
