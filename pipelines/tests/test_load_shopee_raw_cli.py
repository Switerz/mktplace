import json
from pathlib import Path

import openpyxl
import pytest

from pipelines.ingestion import load_shopee_raw as cli
from pipelines.ingestion.shopee_raw import inventory as inv
from pipelines.ingestion.shopee_raw import writer

ORDERS_HEADER = [
    "ID do pedido", "Status do pedido", "Data de criação do pedido", "Quantidade",
    "Subtotal do produto", "Total global", "Taxa de comissão líquida",
    "Taxa de serviço líquida", "Valor estimado do frete",
    "Nome de usuário (comprador)", "Cidade", "Cidade",
]


def _write_orders_xlsx(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(ORDERS_HEADER)
    for row in rows:
        ws.append(row)
    wb.save(path)


# --- guardrails de --apply (Fase 2 — secret em arquivo, nunca em os.environ) --

def test_apply_exige_exatamente_uma_subacao(capsys):
    code = cli.main(["--apply"])
    assert code != 0
    assert "exatamente uma" in capsys.readouterr().err


def test_apply_create_schema_bloqueado_sem_arquivo_de_secret(tmp_path):
    code = cli.run_apply_create_schema(secret_path=tmp_path / "nao_existe.local", repo_root=tmp_path)
    assert code == 2


def test_apply_pilot_bloqueado_sem_arquivo_de_secret(tmp_path):
    code = cli.run_apply_pilot(secret_path=tmp_path / "nao_existe.local", repo_root=tmp_path)
    assert code == 2


def test_apply_backfill_bloqueado_sem_arquivo_de_secret(tmp_path):
    code = cli.run_apply_backfill(secret_path=tmp_path / "nao_existe.local", repo_root=tmp_path)
    assert code == 2


def test_apply_create_schema_nao_chama_ddl_quando_secret_bloqueado(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli.ddl, "execute_ddl", lambda *a, **k: called.append(1))
    code = cli.run_apply_create_schema(secret_path=tmp_path / "nao_existe.local", repo_root=tmp_path)
    assert code == 2
    assert called == []


def test_apply_create_schema_nao_chama_ddl_quando_preflight_bloqueado(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    secret_path.write_text(
        "DATAMART_SHOPEE_WRITE_URL=postgresql://writer@host/db\nI_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1\n"
    )
    monkeypatch.setattr(cli.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    blocked_report = cli.write_conn.PreflightReport(ok=False, blocking_reasons=["rolsuper=true"])
    monkeypatch.setattr(cli.write_conn, "run_preflight", lambda *a, **k: blocked_report)
    called = []
    monkeypatch.setattr(cli.ddl, "execute_ddl", lambda *a, **k: called.append(1))
    code = cli.run_apply_create_schema(secret_path=secret_path, repo_root=tmp_path)
    assert code == 3
    assert called == []


def test_apply_create_schema_chama_ddl_quando_tudo_ok(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    monkeypatch.setattr(cli.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    ok_report = cli.write_conn.PreflightReport(ok=True, safe_summary={"rolsuper": False})
    monkeypatch.setattr(cli.write_conn, "run_preflight", lambda *a, **k: ok_report)
    calls = []
    monkeypatch.setattr(cli.ddl, "execute_ddl", lambda url: calls.append(url) or ["CREATE TABLE ..."])
    code = cli.run_apply_create_schema(secret_path=secret_path, repo_root=tmp_path)
    assert code == 0
    assert calls == ["postgresql://writer@host/db"]


# --- modos --inventory / --dry-run em cima de fixtures locais -----------------

def test_main_inventory_funciona_sem_conexao_de_escrita(tmp_path, capsys):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.20260101_20260131.xlsx",
        [["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"]],
    )
    code = cli.main(["--inventory", "--data-path", str(data_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Total de arquivos: 1" in out


def test_main_dry_run_reconcilia_linhas_fisicas_e_parseadas(tmp_path, capsys):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.20260101_20260131.xlsx",
        [
            ["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"],
            [None] * len(ORDERS_HEADER),
        ],
    )
    code = cli.main(["--dry-run", "--data-path", str(data_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Reconciliação OK" in out


def test_main_sem_arquivos_para_filtro_nao_falha(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    code = cli.main(["--inventory", "--data-path", str(data_path), "--brand", "apice", "--source", "ads"])
    assert code == 0


def test_main_diretorio_inexistente_retorna_erro(tmp_path):
    code = cli.main(["--inventory", "--data-path", str(tmp_path / "nao_existe")])
    assert code != 0


# --- guardrails estruturais ----------------------------------------------------

def test_modulo_loader_nunca_referencia_conexao_de_escrita():
    source = Path(cli.__file__).read_text(encoding="utf-8")
    for forbidden in ("common.db", "LocalSession", "DataMartSession", "local_session", "datamart_session"):
        assert forbidden not in source, f"{forbidden} não deveria aparecer em load_shopee_raw.py na Fase 1"


def test_ddl_so_tem_statements_permitidos_por_prefixo():
    """Verifica pelo statement de fato (via o mesmo parser usado para
    executar o DDL), não por substring no arquivo inteiro — um texto
    descritivo dentro de um COMMENT ON ... IS '...' pode mencionar a
    palavra UPDATE sem que isso seja uma instrução UPDATE de verdade."""
    from pipelines.ingestion.shopee_raw.ddl import DEFAULT_DDL_PATH, parse_ddl_statements

    statements = parse_ddl_statements(DEFAULT_DDL_PATH.read_text(encoding="utf-8"))
    assert len(statements) > 10  # 4 CREATE TABLE + indices + comments + revokes + sets

    allowed_prefixes = ("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX", "COMMENT ON", "REVOKE ALL", "SET LOCAL")
    forbidden_prefixes = ("DROP", "TRUNCATE", "DELETE", "UPDATE", "ALTER", "GRANT", "INSERT", "MERGE")
    for stmt in statements:
        normalized = " ".join(stmt.split()).upper()
        assert normalized.startswith(allowed_prefixes), f"statement fora do esperado: {stmt[:80]}"
        assert not normalized.startswith(forbidden_prefixes), f"statement destrutivo/inesperado: {stmt[:80]}"


def test_ddl_fks_dos_filhos_sao_deferrable():
    from pipelines.ingestion.shopee_raw.ddl import DEFAULT_DDL_PATH, parse_ddl_statements

    statements = parse_ddl_statements(DEFAULT_DDL_PATH.read_text(encoding="utf-8"))
    create_tables = [s for s in statements if s.upper().startswith("CREATE TABLE")]
    deferrable_count = sum(1 for s in create_tables if "DEFERRABLE INITIALLY DEFERRED" in s)
    assert deferrable_count == 3  # um por tabela-filha (a tabela-mae nao tem FK)


def test_ddl_revoke_all_from_public_nas_4_tabelas():
    ddl_path = Path(__file__).resolve().parents[2] / "db" / "sql" / "raw" / "shopee_raw_ddl.sql"
    text = ddl_path.read_text(encoding="utf-8")
    for table in (
        "shopee_ingestion_file", "shopee_order_item_export",
        "shopee_shop_stats_export", "shopee_ads_export",
    ):
        assert f"REVOKE ALL ON raw.{table} FROM PUBLIC;" in text


# ---------------------------------------------------------------------------
# Gate S5.5.1 — --json do backfill + correção do falso exit 9
# ---------------------------------------------------------------------------

def _fake_record(relative_path, source_type, brand="apice"):
    return inv.FileInventoryRecord(
        relative_path=relative_path, brand=brand, brand_known=True, source_type=source_type,
        extension=".xlsx", size_bytes=100, file_sha256="a" * 64, source_modified_at="2026-01-01T00:00:00",
    )


class _FakeConn:
    def close(self):
        pass


def _setup_backfill(
    monkeypatch, *, records, outcomes_by_path, recon_problems=None, batch_recon=None,
    fixed_batch_id="fixed-batch-id", preflight_ok=True,
):
    monkeypatch.setattr(cli, "_resolve_write_url", lambda secret_path, repo_root: ("postgresql://writer@host/db", None))
    ok_report = cli.write_conn.PreflightReport(ok=preflight_ok, safe_summary={"rolsuper": False}, warnings=[])
    monkeypatch.setattr(cli.write_conn, "run_preflight", lambda *a, **k: ok_report)
    monkeypatch.setattr(cli.inv, "scan_directory", lambda p: records)
    monkeypatch.setattr(cli.write_conn, "open_write_connection", lambda url: _FakeConn())
    monkeypatch.setattr(cli.write_conn, "try_acquire_advisory_lock", lambda conn: True)
    monkeypatch.setattr(cli.write_conn, "release_advisory_lock", lambda conn: None)
    monkeypatch.setattr(cli.writer, "new_batch_id", lambda: fixed_batch_id)

    def fake_insert_file(conn, data_path, record, batch_id):
        return outcomes_by_path[record.relative_path]
    monkeypatch.setattr(cli.writer, "insert_file", fake_insert_file)

    monkeypatch.setattr(cli, "create_engine", lambda url: object())

    recon_report = cli.reconcile.ReconciliationReport(problems=list(recon_problems or []))
    monkeypatch.setattr(cli.reconcile, "run_reconciliation", lambda engine: recon_report)

    if batch_recon is None:
        batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True)
    monkeypatch.setattr(cli.reconcile, "reconcile_batch_file_ids", lambda engine, ids: batch_recon)
    return recon_report


def test_json_so_e_aceito_com_apply_backfill(capsys):
    for argv in (
        ["--inventory", "--json"],
        ["--dry-run", "--json"],
        ["--apply", "--create-schema", "--json"],
        ["--apply", "--pilot", "--json"],
    ):
        code = cli.main(argv)
        assert code != 0
        assert "--json só é aceito" in capsys.readouterr().err


def test_json_invalido_bloqueia_antes_de_secret_preflight_banco(monkeypatch, capsys):
    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com --json em combinação inválida")
    monkeypatch.setattr(cli.write_conn, "load_write_secret", boom_secret)

    def boom_scan(*a, **k):
        raise AssertionError("não deveria varrer o filesystem de dados com --json em combinação inválida")
    monkeypatch.setattr(cli.inv, "scan_directory", boom_scan)

    code = cli.main(["--dry-run", "--json"])
    assert code != 0


def test_backfill_json_stdout_e_um_unico_documento_json(tmp_path, capsys, monkeypatch):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=1, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True, source_type_by_file_id={1: "orders"})
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() != ""
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["schema_version"] == 1
    assert doc["batch_id"] == "fixed-batch-id"
    assert doc["raw_status"] == "all_files_committed"
    assert doc["raw_reconciled"] is True


def test_backfill_json_nao_mistura_texto_humano_no_stdout(tmp_path, capsys, monkeypatch):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=1, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True, source_type_by_file_id={1: "orders"})
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    captured = capsys.readouterr()
    for forbidden_text in ("BACKFILL:", "batch_id=", "Reconciliação", "arquivo(s) elegível"):
        assert forbidden_text not in captured.out


def test_backfill_json_inserted_expoe_file_id_correto(tmp_path, monkeypatch, capsys):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=42, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True, source_type_by_file_id={42: "orders"})
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["file_ids_by_source"]["orders"] == [42]
    assert doc["order_file_ids"] == [42]
    assert doc["inserted_file_count"] == 1


def test_backfill_json_skipped_idempotent_expoe_file_id_historico(tmp_path, monkeypatch, capsys):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="skipped_idempotent", file_id=7)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True, source_type_by_file_id={7: "orders"})
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["file_ids_by_source"]["orders"] == [7]
    assert doc["skipped_idempotent_file_count"] == 1
    assert doc["raw_status"] == "all_files_committed"


def test_backfill_json_agrupa_ids_por_source_type(tmp_path, monkeypatch, capsys):
    records = [
        _fake_record("apice/orders.xlsx", inv.SOURCE_ORDERS),
        _fake_record("apice/stats.xlsx", inv.SOURCE_SHOP_STATS),
        _fake_record("apice/ads.csv", inv.SOURCE_ADS),
    ]
    outcomes = {
        "apice/orders.xlsx": writer.FileWriteOutcome("apice/orders.xlsx", outcome="inserted", file_id=1, rows_inserted=5),
        "apice/stats.xlsx": writer.FileWriteOutcome("apice/stats.xlsx", outcome="inserted", file_id=2, rows_inserted=3),
        "apice/ads.csv": writer.FileWriteOutcome("apice/ads.csv", outcome="inserted", file_id=3, rows_inserted=2),
    }
    batch_recon = cli.reconcile.BatchFileReconciliationReport(
        reconciled=True, source_type_by_file_id={1: "orders", 2: "shop_stats", 3: "ads"},
    )
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["file_ids_by_source"] == {"orders": [1], "shop_stats": [2], "ads": [3]}
    assert doc["order_file_ids"] == [1]


def test_backfill_json_ids_unicos_e_ordenados(tmp_path, monkeypatch, capsys):
    records = [
        _fake_record("apice/c.xlsx", inv.SOURCE_ORDERS),
        _fake_record("apice/a.xlsx", inv.SOURCE_ORDERS),
        _fake_record("apice/b.xlsx", inv.SOURCE_ORDERS),
    ]
    outcomes = {
        "apice/c.xlsx": writer.FileWriteOutcome("apice/c.xlsx", outcome="inserted", file_id=30, rows_inserted=1),
        "apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=10, rows_inserted=1),
        "apice/b.xlsx": writer.FileWriteOutcome("apice/b.xlsx", outcome="inserted", file_id=20, rows_inserted=1),
    }
    batch_recon = cli.reconcile.BatchFileReconciliationReport(
        reconciled=True, source_type_by_file_id={30: "orders", 10: "orders", 20: "orders"},
    )
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["order_file_ids"] == [10, 20, 30]


def test_backfill_json_sucesso_sem_file_id_valido_reprova(tmp_path, monkeypatch, capsys):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    # outcome "inserted" sem file_id -- violação de contrato hipotética
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=None, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True)
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 9
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["raw_reconciled"] is False
    assert any("sem file_id válido" in p for p in doc["problems"])


def test_backfill_json_failed_nao_inventa_file_id(tmp_path, monkeypatch, capsys):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="failed", error="ValueError")}
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes)

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 9
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["file_ids_by_source"] == {"orders": [], "shop_stats": [], "ads": []}
    assert doc["order_file_ids"] == []
    assert doc["failed_file_count"] == 1
    assert doc["raw_status"] == "failed"


def test_backfill_json_nunca_expoe_relative_path_filename_hash_ou_order_id(tmp_path, monkeypatch, capsys):
    records = [_fake_record("apice/pedidos_reais_do_cliente.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {
        "apice/pedidos_reais_do_cliente.xlsx": writer.FileWriteOutcome(
            "apice/pedidos_reais_do_cliente.xlsx", outcome="failed",
            error="IntegrityError pgcode=23505 constraint=uk_x file=apice/pedidos_reais_do_cliente.xlsx",
        ),
    }
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes)

    cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    raw_stdout = capsys.readouterr().out
    for forbidden in ("pedidos_reais_do_cliente", ".xlsx", "order_id", "raw_payload", "a" * 64):
        assert forbidden not in raw_stdout
    doc = json.loads(raw_stdout.strip())
    # tipo/pgcode/constraint (metadado técnico seguro) continuam presentes
    assert any("IntegrityError" in p and "pgcode=23505" in p for p in doc["problems"])


def test_backfill_json_historico_grande_nao_causa_falso_exit9(tmp_path, monkeypatch, capsys):
    """Prova direta da correção: um manifesto histórico com centenas de
    arquivos (de execuções passadas) não deve mais causar `incomplete` só
    porque diverge da contagem desta execução (1 arquivo)."""
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=999, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True, source_type_by_file_id={999: "orders"})
    recon_report = _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)
    recon_report.manifest_counts_by_source_brand["apice/orders"] = {"arquivos": 500, "linhas_no_manifesto": 999999}

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 0
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["raw_reconciled"] is True


def test_backfill_json_file_id_atual_ausente_bloqueia(tmp_path, monkeypatch, capsys):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=1, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(
        reconciled=False, missing_file_ids=[1], problems=["1 file_id(s) ausente(s) do manifesto raw.shopee_ingestion_file"],
    )
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 9
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["raw_reconciled"] is False
    assert any("ausente" in p for p in doc["problems"])


def test_backfill_json_divergencia_linhas_filhas_bloqueia(tmp_path, monkeypatch, capsys):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=1, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(
        reconciled=False, source_type_by_file_id={1: "orders"},
        row_count_mismatches={1: {"expected": 10, "actual": 7}},
        problems=["file_id=1: linhas-filhas (7) diverge de source_row_count do manifesto (10)"],
    )
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 9
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["raw_reconciled"] is False
    assert any("diverge" in p for p in doc["problems"])


def test_backfill_json_sem_arquivos_elegiveis_e_sucesso(tmp_path, monkeypatch, capsys):
    _setup_backfill(monkeypatch, records=[], outcomes_by_path={})

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 0
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["raw_status"] == "no_files"
    assert doc["raw_reconciled"] is True
    assert doc["batch_id"] is None


def test_backfill_json_preflight_bloqueado_produz_json_seguro(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_resolve_write_url", lambda secret_path, repo_root: ("postgresql://writer@host/db", None))
    blocked_report = cli.write_conn.PreflightReport(ok=False, blocking_reasons=["rolsuper=true"], warnings=[])
    monkeypatch.setattr(cli.write_conn, "run_preflight", lambda *a, **k: blocked_report)

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 3
    captured = capsys.readouterr()
    doc = json.loads(captured.out.strip())
    assert doc["raw_status"] == "blocked"
    assert doc["raw_reconciled"] is False
    assert "rolsuper=true" in doc["problems"]


def test_backfill_json_secret_bloqueado_produz_json_seguro(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_resolve_write_url", lambda secret_path, repo_root: (None, "arquivo de secret não encontrado: x.local"))

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 2
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["raw_status"] == "blocked"
    assert doc["batch_id"] is None


def test_backfill_modo_humano_preserva_comportamento_e_mostra_reconciliacao_do_lote(tmp_path, monkeypatch, capsys):
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=1, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True, source_type_by_file_id={1: "orders"})
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=False)

    assert rc == 0
    out = capsys.readouterr().out
    assert "batch_id=fixed-batch-id" in out
    assert "BACKFILL: 1 inseridos, 0 pulados (idempotência), 0 falharam, de 1 elegíveis." in out
    assert "Reconciliação OK (saúde global)" in out
    assert "Reconciliação do lote atual" in out
    assert "Reconciliação do lote atual OK" in out
    # a antiga mensagem de "incomplete" comparando contra o histórico nunca mais aparece
    assert "reconciliação na primary não reflete todos os arquivos processados" not in out


def test_regressao_estatica_json_helpers_sem_simbolos_proibidos():
    """Escaneia só as funções que MONTAM/EMITEM o payload JSON
    (`_raw_json_payload`/`_print_raw_json`) -- nunca `run_apply_backfill`
    inteira (referencia `relative_path` legitimamente no seu ramo humano,
    `if not as_json`) nem `_strip_relative_path_from_error` (cujo próprio
    docstring cita `relative_path` em prosa, explicando que a função existe
    exatamente para removê-lo)."""
    import inspect
    names = ("_raw_json_payload", "_print_raw_json")
    source = "\n".join(inspect.getsource(getattr(cli, name)) for name in names)
    for forbidden in ("relative_path", "file_sha256", "raw_payload", "order_id", "DATAMART_DATABASE_URL", "password"):
        assert forbidden not in source, f"símbolo proibido {forbidden!r} encontrado no caminho de montagem do JSON"


def test_backfill_json_nenhuma_conexao_real_e_usada(tmp_path, monkeypatch, capsys):
    """Toda a suíte deste gate mocka write_conn.open_write_connection/
    create_engine/writer.insert_file — nenhuma delas pode ser chamada de
    verdade. Reforça isso armadilhando o psycopg2 real usado por
    write_conn.open_write_connection (importado só dentro da função real,
    nunca do módulo write_conn -- confirmado abrindo com um objeto opaco
    que levantaria se qualquer código tentasse usá-lo como conexão real)."""
    records = [_fake_record("apice/a.xlsx", inv.SOURCE_ORDERS)]
    outcomes = {"apice/a.xlsx": writer.FileWriteOutcome("apice/a.xlsx", outcome="inserted", file_id=1, rows_inserted=10)}
    batch_recon = cli.reconcile.BatchFileReconciliationReport(reconciled=True, source_type_by_file_id={1: "orders"})
    _setup_backfill(monkeypatch, records=records, outcomes_by_path=outcomes, batch_recon=batch_recon)

    rc = cli.run_apply_backfill(data_path=tmp_path, as_json=True)

    assert rc == 0
    json.loads(capsys.readouterr().out.strip())  # confirma JSON válido sem qualquer I/O real
