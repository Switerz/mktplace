"""
Testes de pipelines/connectors/shopee/_parser.py (orders) — cobre a
correção do parser numérico na agregação diária: valores reais (ponto
decimal, sem milhar), valores BR com milhar (bug histórico corrigido),
e fail-fast para valor inválido (nunca 0.0 silencioso — a exceção
propaga com contexto sanitizado, nunca o valor bruto da célula).
"""
from __future__ import annotations

import traceback

import openpyxl
import pytest

from pipelines.connectors.shopee import _parser
from pipelines.connectors.shopee._numeric import ShopeeNumericParseError

HEADER = [
    "ID do pedido", "Status do pedido", "Status da Devolução / Reembolso",
    "Data de criação do pedido", "Quantidade", "Subtotal do produto",
    "Total global", "Taxa de comissão líquida", "Taxa de serviço líquida",
    "Valor estimado do frete", "Nome de usuário (comprador)",
]


def _write_orders_xlsx(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(HEADER)
    for row in rows:
        ws.append(row)
    wb.save(path)


def _row(order_id, status="Concluído", return_status=None, date="2026-05-10",
         qty="1", subtotal="100.00", total_global="95.00", commission="10.00",
         service_fee="5.00", freight="8.00", buyer="comprador1"):
    return [
        order_id, status, return_status, date, qty, subtotal, total_global,
        commission, service_fee, freight, buyer,
    ]


def test_parse_brand_agrega_valores_reais_ponto_decimal(tmp_path):
    """Formato real confirmado: ponto decimal, sem separador de milhar,
    incluindo valores >= 1000 (ex.: Subtotal do produto = 1098.30)."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_orders_xlsx(
        brand_dir / "Order.all.20260501_20260531.xlsx",
        [_row("1001", subtotal="1098.30", total_global="1019.38", buyer="c1")],
    )

    result = _parser.parse_brand(tmp_path, "apice")

    assert len(result) == 1
    assert result[0]["gmv"] == 1098.30
    assert result[0]["total_settlement"] == 1019.38


def test_parse_brand_interpreta_separador_de_milhar_br_apos_correcao(tmp_path):
    """Bug histórico: com o parser antigo, '1.234,56' virava 0.0 (ValueError
    silencioso). Após a correção, o valor é interpretado corretamente.
    Nenhum arquivo real observado usa este formato hoje — este teste cobre
    o formato como proteção futura, não como reprodução de um caso real."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_orders_xlsx(
        brand_dir / "Order.all.20260501_20260531.xlsx",
        [_row("1002", subtotal="1.234,56", total_global="1.100,00", buyer="c2")],
    )

    result = _parser.parse_brand(tmp_path, "apice")

    assert len(result) == 1
    assert result[0]["gmv"] == 1234.56
    assert result[0]["total_settlement"] == 1100.00


def test_parse_brand_valor_invalido_nao_vazio_interrompe_com_erro_controlado(tmp_path):
    """Fail-fast: valor não vazio e inválido nunca vira 0.0. A exceção
    propaga (o pipeline retorna exit code != 0; o orquestrador externo
    marca o step como FAILED e segue com as fontes independentes)."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_orders_xlsx(
        brand_dir / "Order.all.20260501_20260531.xlsx",
        [_row("1003", subtotal="lixo_nao_numerico", total_global="90.00", buyer="c3")],
    )

    with pytest.raises(ShopeeNumericParseError) as excinfo:
        _parser.parse_brand(tmp_path, "apice")

    message = str(excinfo.value)
    assert "apice" in message
    assert "Order.all.20260501_20260531.xlsx" in message
    assert "subtotal" in message
    # nunca o valor bruto da célula, nunca o buyer/order_id na mensagem
    assert "lixo_nao_numerico" not in message
    assert "c3" not in message
    assert "1003" not in message


def test_parse_brand_valor_invalido_nao_encadeia_cause_context_nem_vaza_no_traceback(tmp_path):
    """Endurecimento: __cause__/__context__ da exceção pública devem ser
    None (a exceção interna do parser numérico nunca é encadeada), e o
    traceback FORMATADO (traceback.format_exception) — não só str(exc) —
    nunca deve conter buyer/order_id/valor bruto, usando dados fictícios
    sensíveis para simular um vazamento por engano."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    buyer_ficticio = "nome_ficticio_joao_da_silva"
    order_id_ficticio = "pedido_ficticio_999888777"
    _write_orders_xlsx(
        brand_dir / "Order.all.20260501_20260531.xlsx",
        [_row(order_id_ficticio, subtotal="lixo_nao_numerico", total_global="90.00", buyer=buyer_ficticio)],
    )

    with pytest.raises(ShopeeNumericParseError) as excinfo:
        _parser.parse_brand(tmp_path, "apice")

    exc = excinfo.value
    assert exc.__cause__ is None
    assert exc.__context__ is None

    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert "lixo_nao_numerico" not in formatted
    assert buyer_ficticio not in formatted
    assert order_id_ficticio not in formatted


def test_parse_brand_valor_ausente_vira_zero_sem_erro(tmp_path):
    """None/vazio é 'sem valor' (contrato do parser), não um erro — não
    deve interromper a agregação."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_orders_xlsx(
        brand_dir / "Order.all.20260501_20260531.xlsx",
        [_row("1004", subtotal=None, total_global="50.00", buyer="c4")],
    )

    result = _parser.parse_brand(tmp_path, "apice")

    assert len(result) == 1
    assert result[0]["gmv"] == 0.0


def test_parse_brand_negativo_e_multiplas_linhas_por_pedido(tmp_path):
    """Pedido com 2 linhas de SKU: subtotal soma, total_global usa max
    (comportamento pré-existente, preservado pela correção)."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_orders_xlsx(
        brand_dir / "Order.all.20260501_20260531.xlsx",
        [
            _row("1005", subtotal="30.00", total_global="80.00", buyer="c5"),
            _row("1005", subtotal="-5.00", total_global="80.00", buyer="c5"),
        ],
    )

    result = _parser.parse_brand(tmp_path, "apice")

    assert len(result) == 1
    assert result[0]["gmv"] == pytest.approx(25.00)  # 30.00 + (-5.00)
    assert result[0]["total_settlement"] == 80.00     # max, não soma
    assert result[0]["orders"] == 1
