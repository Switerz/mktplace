"""
Testes de pipelines/connectors/shopee/_parser_ads.py — mesma classe de
correção do parser numérico (thousand separator / valor inválido / fail-fast)
que _parser.py, mas para o CSV de anúncios (Despesas, GMV, Impressões, Cliques).
"""
from __future__ import annotations

import csv
import io
import traceback

import pytest

from pipelines.connectors.shopee import _parser_ads
from pipelines.connectors.shopee._numeric import ShopeeNumericParseError

HEADER = ["#", "Impressões", "Cliques", "Despesas", "GMV"]


def _write_ads_csv(path, period="01/01/2026 - 31/01/2026", rows=()):
    preamble = [
        "Relatório de Todos os Anúncios CPC - Shopee Brasil\n",
        "Nome de Usuário,marca_teste\n",
        "Nome da loja,Marca Teste\n",
        "ID da Loja,123456\n",
        "Data de Criação do Relatório,01/01/2026 00:00\n",
        f"Período,{period}\n",
        "\n",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADER)
    for i, row in enumerate(rows, start=1):
        writer.writerow([i, row["impressoes"], row["cliques"], row["despesas"], row["gmv"]])

    path.write_text("".join(preamble) + buf.getvalue(), encoding="utf-8-sig")


def test_parse_brand_ads_formato_real_ponto_decimal(tmp_path):
    """Formato real confirmado: ponto decimal, sem separador de milhar."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_ads_csv(
        brand_dir / "Dados+Gerais+de+Anúncios+Shopee-01_01_2026-31_01_2026.csv",
        rows=[{"impressoes": "90808", "cliques": "2032", "despesas": "1133.83", "gmv": "11298.96"}],
    )

    result = _parser_ads.parse_brand_ads(tmp_path, "apice")

    assert len(result) == 31  # janeiro/2026
    day = result[0]
    assert day["ad_spend"] == round(1133.83 / 31, 2)
    assert day["ad_revenue"] == round(11298.96 / 31, 2)


def test_parse_brand_ads_separador_de_milhar_br_apos_correcao(tmp_path):
    """Formato BR com milhar não observado nos exports reais (o CSV usa ','
    como delimitador de coluna, então um número BR real exigiria o campo
    entre aspas — provável razão pela qual o export sempre usa ponto
    decimal). Suportado mesmo assim como proteção; antes da correção, o
    valor virava 0.0 silenciosamente."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_ads_csv(
        brand_dir / "Dados+Gerais+de+Anúncios+Shopee-01_01_2026-31_01_2026.csv",
        rows=[{"impressoes": "1000", "cliques": "10", "despesas": "1.234,56", "gmv": "5.000,00"}],
    )

    result = _parser_ads.parse_brand_ads(tmp_path, "apice")

    assert len(result) == 31
    assert result[0]["ad_spend"] == round(1234.56 / 31, 2)
    assert result[0]["ad_revenue"] == round(5000.00 / 31, 2)


def test_parse_brand_ads_valor_invalido_interrompe_com_erro_controlado(tmp_path):
    """Fail-fast: valor não vazio e inválido nunca vira 0.0 — a exceção
    propaga com contexto sanitizado (marca/arquivo/índice/campo), nunca
    o valor bruto da célula."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    file_name = "Dados+Gerais+de+Anúncios+Shopee-01_01_2026-31_01_2026.csv"
    _write_ads_csv(
        brand_dir / file_name,
        rows=[{"impressoes": "1000", "cliques": "10", "despesas": "nao_numerico", "gmv": "500.00"}],
    )

    with pytest.raises(ShopeeNumericParseError) as excinfo:
        _parser_ads.parse_brand_ads(tmp_path, "apice")

    message = str(excinfo.value)
    assert "apice" in message
    assert file_name in message
    assert "spend" in message
    assert "nao_numerico" not in message


def test_parse_brand_ads_valor_invalido_nao_encadeia_cause_context_nem_vaza_no_traceback(tmp_path):
    """Endurecimento: __cause__/__context__ None e traceback FORMATADO
    (não só str(exc)) nunca contém o valor bruto da célula inválida."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    valor_ficticio_sensivel = "vazamento_ficticio_telefone_+5511988887777"
    _write_ads_csv(
        brand_dir / "Dados+Gerais+de+Anúncios+Shopee-01_01_2026-31_01_2026.csv",
        rows=[{"impressoes": "1000", "cliques": "10", "despesas": valor_ficticio_sensivel, "gmv": "500.00"}],
    )

    with pytest.raises(ShopeeNumericParseError) as excinfo:
        _parser_ads.parse_brand_ads(tmp_path, "apice")

    exc = excinfo.value
    assert exc.__cause__ is None
    assert exc.__context__ is None

    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert valor_ficticio_sensivel not in formatted
