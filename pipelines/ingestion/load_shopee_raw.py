"""
Loader Fase Raw Shopee — inventário, contrato, leitura read-only e (Fase 2)
carga real em raw.shopee_*.

Uso:
    python -m pipelines.ingestion.load_shopee_raw --inventory
    python -m pipelines.ingestion.load_shopee_raw --dry-run --source orders
    python -m pipelines.ingestion.load_shopee_raw --dry-run --brand kokeshi

    # Fase 2 — requer .env.shopee-write.local (nunca .env, nunca os.environ):
    python -m pipelines.ingestion.load_shopee_raw --apply --create-schema
    python -m pipelines.ingestion.load_shopee_raw --apply --pilot
    python -m pipelines.ingestion.load_shopee_raw --apply --backfill

`--inventory`/`--dry-run` nunca leem o secret de escrita e nunca abrem
conexão de escrita. `--apply` só lê `.env.shopee-write.local` (nunca
`os.environ`), e só chega a conectar depois de todos os guardrails
estáticos (arquivo ignorado/não rastreado, exatamente as 2 chaves
esperadas, URL de escrita diferente da de leitura) passarem.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine

from pipelines.common.config import settings
from pipelines.connectors.shopee.connector import BRANDS_IN_SCOPE
from pipelines.connectors.shopee._numeric import ShopeeNumericParseError, parse_brl_float
from pipelines.ingestion.shopee_raw import ddl, inventory as inv, reconcile, write_conn, writer
from pipelines.ingestion.shopee_raw.pii import classify_headers, has_direct_pii

DEFAULT_WRITE_SECRET_PATH = Path(__file__).resolve().parents[2] / ".env.shopee-write.local"
REPO_ROOT = Path(__file__).resolve().parents[2]

_SOURCE_ALIASES = {
    "orders": inv.SOURCE_ORDERS,
    "shop-stats": inv.SOURCE_SHOP_STATS,
    "ads": inv.SOURCE_ADS,
}

_RECONCILE_FIELDS = {
    inv.SOURCE_ORDERS: ["Subtotal do produto", "Quantidade", "Total global"],
    inv.SOURCE_SHOP_STATS: ["Vendas (BRL)", "Pedidos", "Visitantes"],
    inv.SOURCE_ADS: ["Despesas", "Impressões", "Cliques", "GMV"],
}


def _filter_records(
    records: list[inv.FileInventoryRecord],
    source: str,
    brand: Optional[str],
    file_substr: Optional[str],
) -> list[inv.FileInventoryRecord]:
    result = records
    if source != "all":
        wanted = _SOURCE_ALIASES[source]
        result = [r for r in result if r.source_type == wanted]
    if brand:
        result = [r for r in result if r.brand == brand]
    if file_substr:
        result = [r for r in result if file_substr in r.relative_path]
    return result


def _print_inventory_report(records: list[inv.FileInventoryRecord]) -> None:
    summary = inv.build_summary(records)

    print(f"Total de arquivos: {summary['total_files']}")
    print(f"Tamanho total: {summary['total_size_bytes']:,} bytes".replace(",", "."))
    print(f"Linhas legíveis (soma): {summary['total_readable_rows']:,}".replace(",", "."))
    print(f"Por source_type: {summary['by_source_type']}")
    print(f"Por brand: {summary['by_brand']}")

    if summary["unreadable_files"]:
        print(f"\nArquivos ilegíveis/vazios ({len(summary['unreadable_files'])}):")
        for path in summary["unreadable_files"]:
            print(f"  - {path}")

    if summary["unknown_or_unlisted_files"]:
        print(f"\nArquivos desconhecidos ou fora das brands oficiais ({len(summary['unknown_or_unlisted_files'])}):")
        for path in summary["unknown_or_unlisted_files"]:
            print(f"  - {path}")

    if summary["duplicate_files_by_sha256"]:
        print("\nArquivos duplicados (mesmo SHA-256):")
        for sha, paths in summary["duplicate_files_by_sha256"].items():
            print(f"  - {sha[:12]}...: {paths}")

    if summary["schema_drift_by_source_type"]:
        print("\nDiferenças de template (schema drift) por source_type:")
        for source_type, fingerprints in summary["schema_drift_by_source_type"].items():
            print(f"  - {source_type}: {len(fingerprints)} schemas distintos")
            for fp, paths in fingerprints.items():
                print(f"      {fp[:12]}...: {len(paths)} arquivo(s), ex: {paths[0]}")

    if summary["overlapping_exports"]:
        print("\nExports com período sobreposto (preservados, não deduplicados):")
        for o in summary["overlapping_exports"]:
            print(
                f"  - {o['brand']}/{o['source_type']}: "
                f"{o['group_a']} {o['range_a']} sobrepõe {o['group_b']} {o['range_b']}"
            )

    print("\nDetalhe por arquivo:")
    for r in records:
        status = "OK" if r.is_readable else f"ERRO: {r.error_message}"
        print(
            f"  [{r.source_type:10s}] {r.relative_path:70s} brand={r.brand:10s} "
            f"linhas={r.source_row_count} rejeitadas={r.rejected_row_count} "
            f"sha256={r.file_sha256[:12] if r.file_sha256 else '-'}... {status}"
        )


def _print_pii_report(records: list[inv.FileInventoryRecord]) -> None:
    seen_fingerprints: set[str] = set()
    print("\n=== Auditoria de PII (fonte: orders) ===")
    any_direct = False
    for r in records:
        if r.source_type != inv.SOURCE_ORDERS or not r.headers or not r.schema_fingerprint:
            continue
        if r.schema_fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(r.schema_fingerprint)
        classifications = classify_headers(r.headers)
        print(f"\n-- Template visto em {r.relative_path} (brand={r.brand}) --")
        for c in classifications:
            print(f"  [{c['classification']:28s}] {c['header']}")
        if has_direct_pii(r.headers):
            any_direct = True
    if any_direct:
        print("\nATENÇÃO: PII direta confirmada nos exports de orders (nome, telefone, endereço, CEP, CPF).")
        print("Nenhum dado de PII foi enviado a nenhum destino nesta fase — apenas leitura local.")


def _reconcile_source(records: list[inv.FileInventoryRecord], data_path: Path) -> dict:
    """Diagnóstico read-only (dry-run). Uma célula numérica inválida NUNCA
    é silenciosamente excluída da soma: é contada em
    `totals["numeric_parse_errors"]`, que `_print_dry_run_report` usa para
    reprovar a reconciliação (nunca declarar "OK" ignorando o erro)."""
    per_file = []
    totals = {"physical_rows": 0, "parsed_rows": 0, "rejected_rows": 0, "numeric_parse_errors": 0}
    numeric_sums: dict[str, float] = {}

    for r in records:
        if not r.is_readable or r.source_type not in _RECONCILE_FIELDS:
            continue
        path = data_path / r.relative_path
        try:
            result = inv.read_source_file(path, r.source_type)
        except inv.SourceReadError as exc:
            per_file.append({"file": r.relative_path, "error": str(exc)})
            continue

        parsed = len(result.rows)
        rejected = len(result.rejects)
        physical = parsed + rejected
        totals["physical_rows"] += physical
        totals["parsed_rows"] += parsed
        totals["rejected_rows"] += rejected

        file_sums: dict[str, float] = {}
        file_numeric_errors = 0
        for row in result.rows:
            for field_name in _RECONCILE_FIELDS[r.source_type]:
                try:
                    v = parse_brl_float(row.raw_payload.get(field_name))
                except ShopeeNumericParseError:
                    file_numeric_errors += 1
                    totals["numeric_parse_errors"] += 1
                    continue
                if v is not None:
                    file_sums[field_name] = file_sums.get(field_name, 0.0) + v
                    numeric_sums[field_name] = numeric_sums.get(field_name, 0.0) + v

        per_file.append(
            {
                "file": r.relative_path,
                "physical_rows": physical,
                "parsed_rows": parsed,
                "rejected_rows": rejected,
                "numeric_sums": file_sums,
                "numeric_parse_errors": file_numeric_errors,
            }
        )

    return {"per_file": per_file, "totals": totals, "numeric_sums": numeric_sums}


def _print_dry_run_report(records: list[inv.FileInventoryRecord], data_path: Path) -> None:
    _print_inventory_report(records)
    _print_pii_report(records)

    print("\n=== Reconciliação (dry-run) ===")
    by_group: dict[tuple[str, str], list[inv.FileInventoryRecord]] = {}
    for r in records:
        by_group.setdefault((r.brand, r.source_type), []).append(r)

    grand_total_physical = 0
    grand_total_parsed = 0
    grand_total_rejected = 0
    grand_total_numeric_errors = 0

    for (brand, source_type), group_records in sorted(by_group.items()):
        if source_type not in _RECONCILE_FIELDS:
            continue
        recon = _reconcile_source(group_records, data_path)
        t = recon["totals"]
        grand_total_physical += t["physical_rows"]
        grand_total_parsed += t["parsed_rows"]
        grand_total_rejected += t["rejected_rows"]
        grand_total_numeric_errors += t["numeric_parse_errors"]
        print(f"\n-- {brand} / {source_type} --")
        print(f"  arquivos legíveis processados: {len([f for f in recon['per_file'] if 'error' not in f])}")
        print(f"  linhas físicas (parseadas + rejeitadas): {t['physical_rows']}")
        print(f"  linhas parseadas (seriam inseridas): {t['parsed_rows']}")
        print(f"  linhas rejeitadas (vazias): {t['rejected_rows']}")
        print(f"  células numéricas inválidas (não vazias, não interpretáveis): {t['numeric_parse_errors']}")
        for field_name, total in recon["numeric_sums"].items():
            print(f"  soma bruta '{field_name}': {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        for err in [f for f in recon["per_file"] if "error" in f]:
            print(f"  ERRO ao ler {err['file']}: {err['error']}")

    print(
        f"\nTOTAL GERAL — físicas={grand_total_physical} parseadas={grand_total_parsed} "
        f"rejeitadas={grand_total_rejected} erros_numericos={grand_total_numeric_errors}"
    )
    if grand_total_physical != grand_total_parsed + grand_total_rejected:
        print("INCONSISTÊNCIA: físicas != parseadas + rejeitadas — investigar antes de aprovar Gate 2.")
        raise SystemExit(1)
    if grand_total_numeric_errors > 0:
        print(
            f"INCONSISTÊNCIA: {grand_total_numeric_errors} célula(s) numérica(s) inválida(s) "
            "encontrada(s) — a reconciliação não pode ser declarada limpa ignorando-as."
        )
        raise SystemExit(1)
    print("Reconciliação OK: linhas físicas == parseadas + rejeitadas e zero células numéricas inválidas em 100% dos arquivos legíveis.")


def _load_secret_or_none(secret_path: Path, repo_root: Path) -> tuple[Optional[dict], Optional[str]]:
    try:
        return write_conn.load_write_secret(secret_path, repo_root), None
    except write_conn.SecretLoadError as exc:
        return None, str(exc)


def _print_write_preflight(report: write_conn.PreflightReport, label: str) -> None:
    print(f"\n=== Preflight de escrita ({label}) — nunca exibe host/IP/usuário/senha ===")
    for key, value in report.safe_summary.items():
        print(f"  {key}: {value}")
    for warning in report.warnings:
        print(f"  AVISO (não bloqueante): {warning}")
    if not report.ok:
        print("  BLOQUEADO:")
        for reason in report.blocking_reasons:
            print(f"    - {reason}")


def _resolve_write_url(secret_path: Path, repo_root: Path) -> tuple[Optional[str], Optional[str]]:
    """Carrega o secret e valida os guardrails estáticos. Retorna
    (write_url, None) em caso de sucesso, ou (None, motivo) se bloqueado —
    nunca levanta para o chamador nem imprime valores."""
    secret, err = _load_secret_or_none(secret_path, repo_root)
    if err:
        return None, err
    try:
        write_url = write_conn.validate_write_guardrails(secret, settings.datamart_url)
    except write_conn.SecretLoadError as exc:
        return None, str(exc)
    return write_url, None


def run_apply_create_schema(secret_path: Path = DEFAULT_WRITE_SECRET_PATH, repo_root: Path = REPO_ROOT) -> int:
    write_url, err = _resolve_write_url(secret_path, repo_root)
    if err:
        print(f"--apply --create-schema bloqueado: {err}", file=sys.stderr)
        return 2

    report = write_conn.run_preflight(write_url, settings.datamart_url, expect_tables_exist=False)
    _print_write_preflight(report, "antes do DDL — raw.shopee_* NÃO deve existir ainda")
    if not report.ok:
        print("DDL NÃO executado — preflight bloqueado.", file=sys.stderr)
        return 3

    try:
        statements = ddl.execute_ddl(write_url)
    except Exception as exc:  # noqa: BLE001
        print(f"DDL falhou, rollback completo: {write_conn.sanitize_error_message(exc)}", file=sys.stderr)
        return 4

    print(f"\nDDL executado com sucesso em uma única transação: {len(statements)} statements.")
    print("4 tabelas criadas: raw.shopee_ingestion_file, raw.shopee_order_item_export, "
          "raw.shopee_shop_stats_export, raw.shopee_ads_export.")
    return 0


def _select_pilot_files(records: list[inv.FileInventoryRecord]) -> dict[str, inv.FileInventoryRecord]:
    pilot: dict[str, inv.FileInventoryRecord] = {}
    for source_type in (inv.SOURCE_ORDERS, inv.SOURCE_SHOP_STATS, inv.SOURCE_ADS):
        candidates = [r for r in records if r.source_type == source_type and r.is_readable and r.brand_known]
        if candidates:
            pilot[source_type] = min(candidates, key=lambda r: r.size_bytes)
    return pilot


def _print_write_outcome(outcome: writer.FileWriteOutcome, label: str) -> None:
    line = f"  [{label}] {outcome.outcome:20s} rows={outcome.rows_inserted:6d} file={outcome.relative_path}"
    if outcome.error:
        line += f" ERRO={outcome.error}"
    print(line)


def run_apply_pilot(
    secret_path: Path = DEFAULT_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
    data_path: Optional[Path] = None,
) -> int:
    write_url, err = _resolve_write_url(secret_path, repo_root)
    if err:
        print(f"--apply --pilot bloqueado: {err}", file=sys.stderr)
        return 2

    report = write_conn.run_preflight(write_url, settings.datamart_url, expect_tables_exist=True)
    _print_write_preflight(report, "antes do piloto — raw.shopee_* deve existir")
    if not report.ok:
        print("PILOTO NÃO executado — preflight bloqueado.", file=sys.stderr)
        return 3

    data_path = data_path or Path(settings.shopee_data_path)
    records = inv.scan_directory(data_path)
    pilot_files = _select_pilot_files(records)
    if len(pilot_files) < 3:
        missing = sorted({inv.SOURCE_ORDERS, inv.SOURCE_SHOP_STATS, inv.SOURCE_ADS} - set(pilot_files))
        print(f"PILOTO abortado: sem arquivo válido para source_type(s) {missing}.", file=sys.stderr)
        return 5

    print("\nArquivos selecionados para o piloto (os menores válidos por source_type):")
    for source_type, record in pilot_files.items():
        print(f"  - {source_type}: {record.relative_path} ({record.size_bytes} bytes)")

    conn = write_conn.open_write_connection(write_url)
    try:
        if not write_conn.try_acquire_advisory_lock(conn):
            print("PILOTO abortado: advisory lock em uso por outra execução — sem retry automático.", file=sys.stderr)
            return 6
        try:
            batch_id = writer.new_batch_id()
            print(f"\n--- Passada 1 (inserção real) — batch_id={batch_id} ---")
            for source_type, record in pilot_files.items():
                outcome = writer.insert_file(conn, data_path, record, batch_id)
                _print_write_outcome(outcome, "pass1")
                if outcome.outcome != "inserted":
                    print(f"PILOTO FALHOU na 1a passada em {outcome.relative_path} — abortando sem continuar.", file=sys.stderr)
                    return 7

            print("\n--- Passada 2 (mesmos 3 arquivos — testa idempotência) ---")
            for source_type, record in pilot_files.items():
                outcome = writer.insert_file(conn, data_path, record, batch_id)
                _print_write_outcome(outcome, "pass2")
                if outcome.outcome != "skipped_idempotent":
                    print(
                        f"IDEMPOTÊNCIA FALHOU em {outcome.relative_path}: esperado 'skipped_idempotent', "
                        f"obtido '{outcome.outcome}'.",
                        file=sys.stderr,
                    )
                    return 8
        finally:
            write_conn.release_advisory_lock(conn)
    finally:
        conn.close()

    print("\nPILOTO OK: 3 arquivos inseridos na 1ª passada, 3 pulados por idempotência na 2ª passada.")
    return 0


def run_apply_backfill(
    secret_path: Path = DEFAULT_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
    data_path: Optional[Path] = None,
    source: str = "all",
    brand: Optional[str] = None,
) -> int:
    write_url, err = _resolve_write_url(secret_path, repo_root)
    if err:
        print(f"--apply --backfill bloqueado: {err}", file=sys.stderr)
        return 2

    report = write_conn.run_preflight(write_url, settings.datamart_url, expect_tables_exist=True)
    _print_write_preflight(report, "antes do backfill — raw.shopee_* deve existir")
    if not report.ok:
        print("BACKFILL NÃO executado — preflight bloqueado.", file=sys.stderr)
        return 3

    data_path = data_path or Path(settings.shopee_data_path)
    records = inv.scan_directory(data_path)
    eligible = [r for r in records if r.source_type in writer.SOURCE_TABLE_MAP and r.is_readable and r.brand_known]
    eligible = _filter_records(eligible, source, brand, None)
    if not eligible:
        print("Nenhum arquivo elegível para os filtros informados.")
        return 0

    print(f"\n{len(eligible)} arquivo(s) elegível(is) para o backfill (transação por arquivo, sem paralelismo, sem retry).")

    outcomes: list[writer.FileWriteOutcome] = []
    conn = write_conn.open_write_connection(write_url)
    try:
        if not write_conn.try_acquire_advisory_lock(conn):
            print("BACKFILL abortado: advisory lock em uso por outra execução — sem retry automático.", file=sys.stderr)
            return 6
        try:
            batch_id = writer.new_batch_id()
            print(f"batch_id={batch_id}")
            for record in eligible:
                outcome = writer.insert_file(conn, data_path, record, batch_id)
                outcomes.append(outcome)
                _print_write_outcome(outcome, "backfill")
        finally:
            write_conn.release_advisory_lock(conn)
    finally:
        conn.close()

    inserted = [o for o in outcomes if o.outcome == "inserted"]
    skipped = [o for o in outcomes if o.outcome == "skipped_idempotent"]
    failed = [o for o in outcomes if o.outcome == "failed"]
    print(
        f"\nBACKFILL: {len(inserted)} inseridos, {len(skipped)} pulados (idempotência), "
        f"{len(failed)} falharam, de {len(eligible)} elegíveis."
    )
    if failed:
        print("Arquivos com falha (transação individual revertida, nenhum dado parcial persistido):")
        for o in failed:
            print(f"  - {o.relative_path}: {o.error}")

    print("\n=== Reconciliação pós-carga (via PRIMARY, sessão explicitamente read-only) ===")
    # Reconciliação imediata após escrita usa a PRIMARY (a própria conexão de
    # escrita, em modo postgresql_readonly=True — nunca escreve, mas nunca
    # está sujeita a lag de réplica). DATAMART_DATABASE_URL é uma read
    # replica (achado desta fase: pg_is_in_recovery()=true) e serve para
    # conferências posteriores, não para a checagem logo após o backfill —
    # usar a réplica aqui já causou uma leitura incompleta que parecia um
    # problema real quando era só lag (ver docs/runbook_shopee_raw.md).
    engine = create_engine(write_url)
    recon = reconcile.run_reconciliation(engine)
    reconciled_files = sum(v["arquivos"] for v in recon.manifest_counts_by_source_brand.values())
    incomplete = reconciled_files != len(eligible)
    if incomplete:
        print(
            f"PROBLEMA: reconciliação na primary não reflete todos os arquivos processados "
            f"({reconciled_files} manifestos visíveis / {len(eligible)} elegíveis) — investigar antes de confiar nesta carga."
        )
    print(f"Total de linhas no manifesto: {recon.total_manifest_rows} | total de linhas-filhas: {recon.total_child_rows}")
    for key, counts in sorted(recon.manifest_counts_by_source_brand.items()):
        print(f"  {key}: {counts}")
    print(f"Tamanho ocupado (bytes): {recon.table_sizes_bytes}")
    print(f"PII (orders) — arquivos com headers de PII direta presentes: {recon.pii_headers_present_files} | ausentes: {recon.pii_headers_absent_files}")
    if recon.problems:
        print("PROBLEMAS DE RECONCILIAÇÃO:")
        for p in recon.problems:
            print(f"  - {p}")
    else:
        print("Reconciliação OK: nenhuma linha órfã, nenhuma duplicidade, manifesto == linhas-filhas.")

    return 0 if not failed and not recon.problems and not incomplete else 9


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Loader Fase Raw Shopee (inventário / dry-run / apply)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory", action="store_true", help="Somente inventário técnico dos arquivos.")
    mode.add_argument("--dry-run", action="store_true", help="Lê e valida tudo, sem conexão de escrita.")
    mode.add_argument("--apply", action="store_true", help="Requer --create-schema, --pilot ou --backfill.")

    apply_action = parser.add_mutually_exclusive_group()
    apply_action.add_argument("--create-schema", action="store_true", help="Executa o DDL (uma vez).")
    apply_action.add_argument("--pilot", action="store_true", help="Carrega os 3 menores arquivos + testa idempotência.")
    apply_action.add_argument("--backfill", action="store_true", help="Carrega todos os arquivos elegíveis.")

    parser.add_argument("--source", choices=["orders", "shop-stats", "ads", "all"], default="all")
    parser.add_argument("--brand", choices=BRANDS_IN_SCOPE, default=None)
    parser.add_argument("--file", default=None, help="Filtra por substring no caminho relativo.")
    parser.add_argument("--data-path", default=None, help="Sobrepõe SHOPEE_DATA_PATH.")

    args = parser.parse_args(argv)

    if args.apply:
        if sum([args.create_schema, args.pilot, args.backfill]) != 1:
            print("--apply exige exatamente uma de: --create-schema, --pilot, --backfill", file=sys.stderr)
            return 1
        data_path = Path(args.data_path) if args.data_path else None
        if args.create_schema:
            return run_apply_create_schema()
        if args.pilot:
            return run_apply_pilot(data_path=data_path)
        return run_apply_backfill(data_path=data_path, source=args.source, brand=args.brand)

    data_path = Path(args.data_path) if args.data_path else Path(settings.shopee_data_path)
    try:
        records = inv.scan_directory(data_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    records = _filter_records(records, args.source, args.brand, args.file)
    if not records:
        print("Nenhum arquivo encontrado para os filtros informados.")
        return 0

    batch_id = str(uuid.uuid4())
    print(f"Batch (somente para referência do relatório, não persistido): {batch_id}")

    if args.inventory:
        _print_inventory_report(records)
        return 0

    if args.dry_run:
        try:
            _print_dry_run_report(records, data_path)
        except SystemExit as exc:
            return int(exc.code or 1)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
