"""
Testes de pipelines/connectors/shopee/_numeric.py — parser numérico
canônico usado pelo parser de produção (orders, ads) e pelo diagnóstico
de reconciliação da raw.

Cobre o bug histórico (separador de milhar zerado silenciosamente),
os formatos confirmados nos exports reais e o contrato de vazio/inválido.
"""
from __future__ import annotations

import traceback

import pytest

from pipelines.connectors.shopee._numeric import ShopeeNumericParseError, parse_brl_float


# --- formato real confirmado nos exports (ponto decimal, sem milhar) ---
# 100% das 383.298 linhas de 85 arquivos Order.all*.xlsx e dos 804
# registros de ads usam este formato — ver docs/runbook_shopee_raw.md.

@pytest.mark.parametrize("raw, expected", [
    ("1234", 1234.0),
    ("1", 1.0),
    ("110.69", 110.69),
    ("1546.30", 1546.30),      # >= 1000, confirmado real, sem milhar
    ("38147.83", 38147.83),    # >= 10000, confirmado real (ads/Despesas)
    ("0.00", 0.0),
    ("0", 0.0),
])
def test_formato_real_ponto_decimal_sem_milhar(raw, expected):
    assert parse_brl_float(raw) == expected


# --- bug histórico: separador de milhar BR não era removido ---
# O parser antigo (_parse_float) fazia só replace(",", ".") — "1.234,56"
# virava "1.234.56", ValueError, e o valor era silenciosamente zerado.

@pytest.mark.parametrize("raw, expected", [
    ("1.234,56", 1234.56),
    ("12.345,67", 12345.67),
    ("1.234.567,89", 1234567.89),
])
def test_bug_historico_separador_de_milhar_br_agora_e_interpretado(raw, expected):
    assert parse_brl_float(raw) == expected


def test_bug_historico_parser_antigo_zerava_isso():
    """Documenta o comportamento do bug antigo para referência — NÃO testa
    código de produção (a função antiga foi removida). Serve de contraste
    com test_bug_historico_separador_de_milhar_br_agora_e_interpretado."""
    def _parse_float_antigo(val):
        if val is None:
            return 0.0
        s = str(val).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
        if not s or s in ("-", ""):
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    assert _parse_float_antigo("1.234,56") == 0.0
    assert parse_brl_float("1.234,56") == 1234.56


# --- vírgula decimal BR sem milhar ---

@pytest.mark.parametrize("raw, expected", [
    ("1234,56", 1234.56),
    ("0,57", 0.57),
])
def test_virgula_decimal_br_sem_milhar(raw, expected):
    assert parse_brl_float(raw) == expected


# --- prefixo de moeda, espaços, NBSP ---

@pytest.mark.parametrize("raw, expected", [
    ("R$ 1.234,56", 1234.56),
    ("R$1.234,56", 1234.56),
    ("R$ 110.69", 110.69),
    ("1.234,56\xa0", 1234.56),
    ("\xa01.234,56", 1234.56),
    (" 1234.56 ", 1234.56),
])
def test_prefixo_moeda_espacos_nbsp(raw, expected):
    assert parse_brl_float(raw) == expected


# --- negativos ---

@pytest.mark.parametrize("raw, expected", [
    ("-1234.56", -1234.56),
    ("-1.234,56", -1234.56),
    ("-1234,56", -1234.56),
    ("-5", -5.0),
])
def test_negativos(raw, expected):
    assert parse_brl_float(raw) == expected


# --- tipos nativos (openpyxl pode entregar int/float direto para células
# numéricas puras, embora os exports Shopee auditados sempre entreguem str) ---

def test_tipos_nativos_int_float():
    assert parse_brl_float(1234) == 1234.0
    assert parse_brl_float(1234.56) == 1234.56
    assert parse_brl_float(0) == 0.0


def test_bool_e_rejeitado():
    """bool é subclasse de int em Python — precisa ser rejeitado
    explicitamente para não virar 1.0/0.0 por acidente de tipagem."""
    with pytest.raises(ShopeeNumericParseError):
        parse_brl_float(True)


# --- ausência de valor -> None (nunca 0.0 silencioso) ---

@pytest.mark.parametrize("raw", [None, "", "-", "N/A", "n/a", "NA", "null", "None", "  "])
def test_ausencia_de_valor_retorna_none(raw):
    assert parse_brl_float(raw) is None


# --- valor não vazio e inválido -> exceção explícita, nunca 0.0 ---

@pytest.mark.parametrize("raw", [
    "abc", "12.34.56.78", "R$xyz", "--", "1,2,3", "12a34",
    "1.2.3,4.5",  # múltiplos separadores inválidos, sem leitura BR/US coerente
])
def test_valor_invalido_nao_vazio_levanta_excecao(raw):
    with pytest.raises(ShopeeNumericParseError):
        parse_brl_float(raw)


def test_valor_invalido_nunca_retorna_zero_silenciosamente():
    """Contrato central desta correção: um valor não vazio que não pôde
    ser interpretado nunca deve virar 0.0 sem que o chamador saiba."""
    with pytest.raises(ShopeeNumericParseError):
        parse_brl_float("abc")
    # Se não levantasse, o valor teria virado 0.0 — a asserção acima já
    # comprova que isso não acontece.


# --- sanitização: a exceção NUNCA inclui o valor bruto da célula ---

@pytest.mark.parametrize("raw", ["abc", "segredo_confidencial_xyz", "12.34.56.78", "1,234.56"])
def test_excecao_nunca_inclui_valor_bruto_da_celula(raw):
    with pytest.raises(ShopeeNumericParseError) as excinfo:
        parse_brl_float(raw)
    assert raw not in str(excinfo.value)
    assert repr(raw) not in str(excinfo.value)


# --- __cause__/__context__ nunca carregam o valor bruto — nem a mensagem,
# nem o traceback formatado (traceback.format_exception, não só str()).
# O ValueError interno de float() inclui o valor bruto em sua própria
# mensagem (ex.: "could not convert string to float: 'xxx'") — precisa
# nunca ficar acessível via encadeamento de exceção. ---

_FICTITIOUS_SENSITIVE_VALUES = [
    "nome_ficticio_maria_da_silva",       # simula nome (buyer) vazado por engano
    "cpf_ficticio_123.456.789-00",        # simula CPF vazado por engano
    "telefone_ficticio_+5511999998888",   # simula telefone vazado por engano
]


@pytest.mark.parametrize("raw", _FICTITIOUS_SENSITIVE_VALUES + ["1,234.56", "1.2.3,4.5"])
def test_excecao_nao_encadeia_cause_nem_context(raw):
    """__cause__ e __context__ devem ser None — não apenas suprimidos da
    exibição padrão (`from None` sozinho suprime a exibição mas NÃO limpa
    __context__; a implementação usa uma flag booleana para nunca levantar
    a exceção pública enquanto o ValueError interno está 'sendo tratado')."""
    with pytest.raises(ShopeeNumericParseError) as excinfo:
        parse_brl_float(raw)
    exc = excinfo.value
    assert exc.__cause__ is None
    assert exc.__context__ is None


@pytest.mark.parametrize("raw", _FICTITIOUS_SENSITIVE_VALUES + ["1,234.56", "1.2.3,4.5"])
def test_traceback_formatado_nao_vaza_valor_bruto(raw):
    """Verifica o traceback FORMATADO (traceback.format_exception), não só
    str(exc) — um __context__/__cause__ silenciosamente populado apareceria
    aqui mesmo que a mensagem de exc esteja limpa."""
    with pytest.raises(ShopeeNumericParseError) as excinfo:
        parse_brl_float(raw)
    exc = excinfo.value
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert raw not in formatted
    assert "Traceback" in formatted  # confere que de fato formatou algo não-trivial


# --- formato US ("1,234.56") -> rejeitado explicitamente, nunca convertido
# silenciosamente para um valor errado (ex.: 1.23456). Não há nenhuma
# evidência desse formato nas fontes reais auditadas. ---

@pytest.mark.parametrize("raw", [
    "1,234.56",       # US clássico: vírgula de milhar, ponto decimal
    "1,234,567.89",   # US com múltiplos grupos de milhar
    "12,345.6",
])
def test_formato_us_e_rejeitado_explicitamente_nunca_convertido_errado(raw):
    with pytest.raises(ShopeeNumericParseError):
        parse_brl_float(raw)


def test_formato_br_com_milhar_continua_suportado_apos_decisao_sobre_us():
    """A rejeição do formato US não pode quebrar o suporte ao formato BR
    equivalente (mesmos separadores, ordem inversa)."""
    assert parse_brl_float("1.234,56") == 1234.56
    assert parse_brl_float("12.345.678,90") == 12345678.90


# --- valores não finitos (NaN/Infinity) -> sempre rejeitados ---

@pytest.mark.parametrize("raw", ["NaN", "nan", "Infinity", "-Infinity", "inf", "-inf"])
def test_strings_nao_finitas_sao_rejeitadas(raw):
    with pytest.raises(ShopeeNumericParseError):
        parse_brl_float(raw)


def test_floats_nativos_nao_finitos_sao_rejeitados():
    with pytest.raises(ShopeeNumericParseError):
        parse_brl_float(float("nan"))
    with pytest.raises(ShopeeNumericParseError):
        parse_brl_float(float("inf"))
    with pytest.raises(ShopeeNumericParseError):
        parse_brl_float(float("-inf"))


# --- amostras numéricas anonimizadas derivadas dos arquivos reais ---
# Os valores abaixo NÃO são PII (são apenas números de subtotal/comissão/
# frete) e foram observados durante a auditoria read-only desta fase —
# nenhum nome, pedido, comprador ou endereço é referenciado.

@pytest.mark.parametrize("raw, expected", [
    ("12.37", 12.37),     # Taxa de comissão líquida
    ("10.06", 10.06),     # Taxa de serviço líquida
    ("10.83", 10.83),     # Valor estimado do frete
    ("1098.30", 1098.30),  # Subtotal do produto, >= 1000
    ("1019.38", 1019.38),  # Total global, >= 1000
])
def test_amostras_anonimizadas_de_orders_reais(raw, expected):
    assert parse_brl_float(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("108525", 108525.0),   # Impressões
    ("1133.83", 1133.83),   # Despesas
    ("11298.96", 11298.96),  # GMV
])
def test_amostras_anonimizadas_de_ads_reais(raw, expected):
    assert parse_brl_float(raw) == expected
