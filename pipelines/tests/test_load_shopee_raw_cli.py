from pathlib import Path

import openpyxl
import pytest

from pipelines.ingestion import load_shopee_raw as cli
from pipelines.ingestion.shopee_raw import inventory as inv

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
