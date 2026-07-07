"""
Testes de pipelines/ingestion/shopee_raw/writer.py — Fase Raw Shopee 2.

Usa conexões psycopg2 falsas e arquivos xlsx reais em tmp_path (para
exercitar inv.read_source_file de verdade) — nenhum banco real é tocado.
"""
from __future__ import annotations

import openpyxl
import psycopg2.extras
import pytest

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


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append((norm, params))
        upper = norm.upper()
        if upper.startswith("SELECT FILE_ID FROM RAW.SHOPEE_INGESTION_FILE"):
            self._last_result = (self.conn.existing_file_id,) if self.conn.existing_file_id else None
        elif upper.startswith("SELECT NEXTVAL"):
            self._last_result = (self.conn.next_file_id,)
        elif upper.startswith("INSERT INTO RAW.SHOPEE_INGESTION_FILE"):
            self.conn.manifest_insert_params = params
            self._last_result = None
        else:
            self._last_result = None

        if self.conn.fail_on_substring and self.conn.fail_on_substring in norm:
            raise RuntimeError("falha simulada")

    def fetchone(self):
        return self._last_result


class FakeConn:
    def __init__(self, existing_file_id=None, next_file_id=42, fail_on_substring=None):
        self.executed: list[tuple[str, object]] = []
        self.execute_values_calls: list[tuple[str, list]] = []
        self.committed = False
        self.rolled_back = False
        self.existing_file_id = existing_file_id
        self.next_file_id = next_file_id
        self.fail_on_substring = fail_on_substring
        self.manifest_insert_params = None

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True
        # simula persistência: depois do commit, o file_id inserido passa a
        # "existir" para a próxima checagem de idempotência no mesmo conn.
        self.existing_file_id = self.next_file_id

    def rollback(self):
        self.rolled_back = True


def _fake_execute_values(cur, sql, values, page_size=1000):
    cur.conn.execute_values_calls.append((" ".join(sql.split()), values))
    if cur.conn.fail_on_substring and cur.conn.fail_on_substring in sql:
        raise RuntimeError("falha simulada no execute_values")


@pytest.fixture(autouse=True)
def _patch_execute_values(monkeypatch):
    monkeypatch.setattr(psycopg2.extras, "execute_values", _fake_execute_values)


def _record(data_path, relative_path, brand, size_bytes=None, file_sha256=None):
    from pipelines.ingestion.shopee_raw.hashing import sha256_file

    path = data_path / relative_path
    sha = file_sha256 or sha256_file(path)
    return inv.FileInventoryRecord(
        relative_path=relative_path,
        brand=brand,
        brand_known=True,
        source_type=inv.SOURCE_ORDERS,
        extension=".xlsx",
        size_bytes=size_bytes if size_bytes is not None else path.stat().st_size,
        file_sha256=sha,
        source_modified_at="2026-01-01T00:00:00+00:00",
        sheet_name="orders",
        header_row_index=0,
        headers=ORDERS_HEADER,
        source_row_count=1,
        schema_fingerprint="fp123",
    )


def test_insert_file_pula_se_ja_ingerido(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.a.xlsx",
        [["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"]],
    )
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice")
    conn = FakeConn(existing_file_id=7)

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "skipped_idempotent"
    assert outcome.file_id == 7
    assert conn.rolled_back is True
    assert conn.committed is False
    assert conn.execute_values_calls == []


def test_insert_file_insere_filhas_e_manifesto_por_ultimo(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.a.xlsx",
        [
            ["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"],
            ["2", "Cancelado", "2026-01-02", 2, 20.0, 20.0, 0, 0, 0, "u2", "RJ", "RJ"],
        ],
    )
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice")
    conn = FakeConn(existing_file_id=None, next_file_id=99)

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "inserted"
    assert outcome.file_id == 99
    assert outcome.rows_inserted == 2
    assert conn.committed is True
    assert conn.rolled_back is False

    assert len(conn.execute_values_calls) == 1
    _, rows_payload = conn.execute_values_calls[0]
    assert len(rows_payload) == 2
    assert all(r[0] == 99 for r in rows_payload)  # file_id correto em todas as filhas
    assert {r[2] for r in rows_payload} == {1, 2}  # source_row_number

    # manifesto e a ULTIMA instrucao antes do commit
    last_sql, _ = conn.executed[-1]
    assert last_sql.upper().startswith("INSERT INTO RAW.SHOPEE_INGESTION_FILE")
    assert conn.manifest_insert_params[0] == 99  # file_id explicito, reservado via nextval


def test_insert_file_cancelado_nao_e_descartado(tmp_path):
    """Confirma que raw nao filtra por status mesmo na carga real."""
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.a.xlsx",
        [["1", "Cancelado", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"]],
    )
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice")
    conn = FakeConn()

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "inserted"
    assert outcome.rows_inserted == 1


def test_insert_file_payload_sem_nan(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.a.xlsx",
        [["1", "Concluído", "2026-01-01", 1, float("nan"), 10.0, 0, 0, 0, "u", "SP", "SP"]],
    )
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice")
    conn = FakeConn()

    writer.insert_file(conn, data_path, record, "batch-1")

    _, rows_payload = conn.execute_values_calls[0]
    payload_json_wrapper = rows_payload[0][3]
    assert payload_json_wrapper.adapted["Subtotal do produto"] is None


def test_insert_file_detecta_arquivo_alterado_durante_leitura(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    path = data_path / "apice" / "Order.all.a.xlsx"
    _write_orders_xlsx(path, [["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"]])
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice", file_sha256="0" * 64)  # hash errado de propósito
    conn = FakeConn()

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "failed"
    assert "FileChangedDuringReadError" in outcome.error
    assert conn.rolled_back is True
    assert conn.committed is False


def test_insert_file_rollback_em_falha_no_execute_values(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.a.xlsx",
        [["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"]],
    )
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice")
    conn = FakeConn(fail_on_substring="INSERT INTO raw.shopee_order_item_export")

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "failed"
    assert conn.rolled_back is True
    assert conn.committed is False
    # o manifesto NUNCA deve ter sido inserido se as filhas falharam
    assert not any(sql.upper().startswith("INSERT INTO RAW.SHOPEE_INGESTION_FILE") for sql, _ in conn.executed)


def test_insert_file_erro_nunca_expoe_payload(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.a.xlsx",
        [["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "SEGREDO_COMPRADOR", "SP", "SP"]],
    )
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice")
    conn = FakeConn(fail_on_substring="INSERT INTO raw.shopee_order_item_export")

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert "SEGREDO_COMPRADOR" not in (outcome.error or "")


def test_new_batch_id_e_uuid_valido():
    import uuid

    batch_id = writer.new_batch_id()
    uuid.UUID(batch_id)  # não deve levantar


# --- idempotência null-safe (sheet_name NULL, típico de ads/CSV) --------------

def _write_ads_csv(path, ad_rows):
    lines = [
        "Relatório de Todos os Anúncios CPC - Shopee Brasil",
        "Nome de Usuário,marca",
        "Nome da loja,Marca Loja",
        "ID da Loja,123",
        "Data de Criação do Relatório,19/06/2026 17:12",
        "Período,01/01/2026 - 31/03/2026",
        "",
        "#,Nome do Anúncio,Status,Impressões,Cliques,Despesas,GMV",
    ]
    for i, row in enumerate(ad_rows, start=1):
        lines.append(f"{i}," + ",".join(str(v) for v in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def _ads_record(data_path, relative_path, brand):
    from pipelines.ingestion.shopee_raw.hashing import sha256_file

    path = data_path / relative_path
    return inv.FileInventoryRecord(
        relative_path=relative_path,
        brand=brand,
        brand_known=True,
        source_type=inv.SOURCE_ADS,
        extension=".csv",
        size_bytes=path.stat().st_size,
        file_sha256=sha256_file(path),
        source_modified_at="2026-01-01T00:00:00+00:00",
        sheet_name=None,  # CSV nao tem sheet -- este e' o caso que o indice null-safe protege
        header_row_index=7,
        headers=["#", "Nome do Anúncio", "Status", "Impressões", "Cliques", "Despesas", "GMV"],
        source_row_count=1,
        schema_fingerprint="fp-ads",
    )


def test_insert_file_dois_arquivos_ads_identicos_sheet_name_null_e_idempotente(tmp_path):
    """UNIQUE(file_sha256, sheet_name) sozinha nao bloqueia duplicidade
    quando sheet_name e' NULL (Postgres trata cada NULL como distinto).
    A checagem da aplicacao usa `IS NOT DISTINCT FROM`, que e' null-safe --
    este teste prova que dois arquivos ads BYTE-A-BYTE IDENTICOS (mesmo
    conteudo -> mesmo file_sha256, sheet_name sempre None) resultam em UM
    insert e UM skip, nunca dois inserts."""
    import shutil

    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    ad_rows = [["Anúncio 1", "Ativo", 1000, 50, "10,00", "100,00"]]
    path_a = data_path / "apice" / "Dados+Gerais+A.csv"
    path_b = data_path / "apice" / "Dados+Gerais+B.csv"
    _write_ads_csv(path_a, ad_rows)
    shutil.copyfile(path_a, path_b)  # garante file_sha256 identico, nao so' conteudo logico igual

    record_a = _ads_record(data_path, "apice/Dados+Gerais+A.csv", "apice")
    record_b = _ads_record(data_path, "apice/Dados+Gerais+B.csv", "apice")
    assert record_a.file_sha256 == record_b.file_sha256
    assert record_a.sheet_name is None and record_b.sheet_name is None

    conn = FakeConn(next_file_id=555)

    outcome_a = writer.insert_file(conn, data_path, record_a, "batch-1")
    outcome_b = writer.insert_file(conn, data_path, record_b, "batch-1")

    assert outcome_a.outcome == "inserted"
    assert outcome_b.outcome == "skipped_idempotent"
    assert outcome_b.file_id == outcome_a.file_id


def _write_ads_csv_missing_periodo(path):
    """CSV de ads com preâmbulo inválido (sem 'Período') — usado para
    provar que insert_file aborta ANTES de qualquer INSERT/execute_values."""
    lines = [
        "Relatório de Todos os Anúncios CPC - Shopee Brasil",
        "Nome de Usuário,marca",
        "ID da Loja,123",
        "Data de Criação do Relatório,19/06/2026 17:12",
        "",
        "#,Nome do Anúncio,Status,Impressões,Cliques,Despesas,GMV",
        "1,Anúncio 1,Ativo,1000,50,10.00,100.00",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def test_insert_file_ads_grava_source_metadata_minimizado_no_manifesto(tmp_path):
    """source_metadata para ads deve conter só period_start/period_end/
    report_created_at/shop_id — nunca shop_username/shop_display_name
    (minimização, revisão de 2026-07-06)."""
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    path = data_path / "apice" / "Dados+Gerais+A.csv"
    _write_ads_csv(path, [["Anúncio 1", "Ativo", 1000, 50, "10,00", "100,00"]])
    record = _ads_record(data_path, "apice/Dados+Gerais+A.csv", "apice")
    conn = FakeConn(next_file_id=321)

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "inserted"
    source_metadata_param = conn.manifest_insert_params[-1]
    assert source_metadata_param.adapted == {
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "report_created_at": "2026-06-19T17:12:00",
        "shop_id": "123",
    }


def test_insert_file_orders_source_metadata_fica_none_no_manifesto(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.a.xlsx",
        [["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"]],
    )
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice")
    conn = FakeConn()

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "inserted"
    assert conn.manifest_insert_params[-1] is None


def test_insert_file_ads_preambulo_invalido_aborta_antes_de_qualquer_insert(tmp_path):
    """Falha ANTES de nextval/execute_values/INSERT do manifesto — mesma
    política success-only das linhas-filhas: um preâmbulo inválido não
    deixa NENHUM rastro, nem parcial."""
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    path = data_path / "apice" / "Dados+Gerais+Ruim.csv"
    _write_ads_csv_missing_periodo(path)
    record = _ads_record(data_path, "apice/Dados+Gerais+Ruim.csv", "apice")
    conn = FakeConn()

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "failed"
    assert "AdsPreambleError" in outcome.error
    assert conn.rolled_back is True
    assert conn.committed is False
    assert conn.execute_values_calls == []
    assert not any(sql.upper().startswith("SELECT NEXTVAL") for sql, _ in conn.executed)
    assert not any(sql.upper().startswith("INSERT INTO RAW.SHOPEE_INGESTION_FILE") for sql, _ in conn.executed)


def test_insert_file_ads_erro_de_preambulo_nunca_vaza_valor_bruto(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    path = data_path / "apice" / "Dados+Gerais+Ruim.csv"
    _write_ads_csv_missing_periodo(path)
    record = _ads_record(data_path, "apice/Dados+Gerais+Ruim.csv", "apice")
    conn = FakeConn()

    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert "marca" not in (outcome.error or "")
    assert "123" not in (outcome.error or "")


def test_is_already_ingested_usa_comparacao_null_safe():
    """A query de idempotencia precisa usar IS NOT DISTINCT FROM (nao '='),
    senao sheet_name=NULL nunca bateria com outro NULL."""
    conn = FakeConn(existing_file_id=None)
    cur = conn.cursor()
    writer.is_already_ingested(cur, "a" * 64, None)
    sql, params = conn.executed[-1]
    assert "IS NOT DISTINCT FROM" in sql.upper()
    assert params == ("a" * 64, None)


# --- PII nunca aparece em mensagens de erro -----------------------------------

def test_insert_file_erro_com_pii_ficticia_na_excecao_nao_vaza(tmp_path, monkeypatch):
    """Simula o pior caso: uma excecao de baixo nivel do driver cuja
    mensagem (DETAIL/statement do servidor) contem PII de uma linha real.
    _safe_error_summary NUNCA deve repassar o texto da excecao — so tipo,
    pgcode/constraint (quando existirem) e o nome do arquivo."""
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.a.xlsx",
        [["1", "Concluído", "2026-01-01", 1, 10.0, 10.0, 0, 0, 0, "u", "SP", "SP"]],
    )
    record = _record(data_path, "apice/Order.all.a.xlsx", "apice")

    class FakePgError(Exception):
        pgcode = "23505"

        class diag:
            constraint_name = "uk_shopee_order_item_export_file_row"

        def __str__(self):
            return (
                "duplicate key value violates unique constraint "
                "\"uk_shopee_order_item_export_file_row\"\n"
                "DETAIL: Key already exists. Row: (Maria da Silva, 11988887777, 111.222.333-44)"
            )

    def fake_execute_values_raising(cur, sql, values, page_size=1000):
        raise FakePgError()

    monkeypatch.setattr(psycopg2.extras, "execute_values", fake_execute_values_raising)

    conn = FakeConn()
    outcome = writer.insert_file(conn, data_path, record, "batch-1")

    assert outcome.outcome == "failed"
    for leaked in ("Maria da Silva", "11988887777", "111.222.333-44", "DETAIL", "duplicate key"):
        assert leaked not in outcome.error
    assert "FakePgError" in outcome.error
    assert "pgcode=23505" in outcome.error
    assert "constraint=uk_shopee_order_item_export_file_row" in outcome.error
    assert "apice/Order.all.a.xlsx" in outcome.error
