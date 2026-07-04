"""
Testes do diagnóstico de reconciliação numérica em
pipelines/ingestion/load_shopee_raw.py (_reconcile_source /
_print_dry_run_report). Endurecimento: uma célula numérica inválida
(não vazia, não interpretável) nunca é silenciosamente excluída da
soma — é contada em `numeric_parse_errors` e reprova a reconciliação
(nunca "Reconciliação OK" ignorando o erro).
"""
from __future__ import annotations

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


def test_reconcile_source_soma_formato_real_ponto_decimal(tmp_path):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.20260101_20260131.xlsx",
        [["1", "Concluído", "2026-01-01", "1", "1546.30", "1400.00", "0", "0", "0", "u", "SP", "SP"]],
    )
    records = inv.scan_directory(data_path)

    recon = cli._reconcile_source(records, data_path)

    assert recon["totals"]["numeric_parse_errors"] == 0
    assert recon["numeric_sums"]["Subtotal do produto"] == 1546.30


def test_reconcile_source_conta_erro_numerico_sem_excluir_silenciosamente(tmp_path):
    """Célula não vazia e inválida nunca vira 'ausente' na reconciliação —
    é contada em numeric_parse_errors, distinta de uma soma normal."""
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.20260101_20260131.xlsx",
        [["1", "Concluído", "2026-01-01", "1", "lixo_nao_numerico", "1400.00", "0", "0", "0", "u", "SP", "SP"]],
    )
    records = inv.scan_directory(data_path)

    recon = cli._reconcile_source(records, data_path)

    assert recon["totals"]["numeric_parse_errors"] == 1
    assert "Subtotal do produto" not in recon["numeric_sums"]


def test_main_dry_run_reprova_quando_ha_erro_numerico(tmp_path, capsys):
    """A reconciliação nunca pode ser declarada 'OK' ignorando uma célula
    numérica inválida — exit code != 0, mensagem explícita no relatório."""
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.20260101_20260131.xlsx",
        [["1", "Concluído", "2026-01-01", "1", "lixo_nao_numerico", "1400.00", "0", "0", "0", "u", "SP", "SP"]],
    )

    code = cli.main(["--dry-run", "--data-path", str(data_path)])

    assert code != 0
    out = capsys.readouterr().out
    assert "Reconciliação OK" not in out
    assert "célula(s) numérica(s) inválida(s)" in out
    assert "lixo_nao_numerico" not in out


def test_main_dry_run_ok_quando_tudo_valido(tmp_path, capsys):
    data_path = tmp_path / "shopee"
    (data_path / "apice").mkdir(parents=True)
    _write_orders_xlsx(
        data_path / "apice" / "Order.all.20260101_20260131.xlsx",
        [["1", "Concluído", "2026-01-01", "1", "1546.30", "1400.00", "0", "0", "0", "u", "SP", "SP"]],
    )

    code = cli.main(["--dry-run", "--data-path", str(data_path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "Reconciliação OK" in out
    assert "erros_numericos=0" in out
