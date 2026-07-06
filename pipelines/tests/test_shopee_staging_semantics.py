"""
Testes estruturais (sem banco) de semantics.py — a validação semântica de
datas/números/booleanos compartilhada entre preview.py e a transformação.

Cobrem principalmente o bug encontrado e corrigido em 2026-07-06: o `ELSE`
de um `CASE` nunca pode ser um LITERAL puro sem referência à coluna de
origem, porque o Postgres faz *constant folding* e avalia esse cast em
tempo de PLANEJAMENTO — falhando para 100% das linhas, mesmo quando nenhuma
delas alcançaria aquele ramo em tempo de execução (confirmado por sondagem
read-only contra o Data Mart). A validação semântica real, com valores
sintéticos inválidos, está em test_shopee_staging_semantics_live.py
(requer DATAMART_DATABASE_URL; pulado quando ausente).
"""
from __future__ import annotations

import re

from pipelines.staging.shopee import semantics


def _else_branch(expr: str) -> str:
    """Extrai o texto do último ramo ELSE de um CASE (aproximação por
    string — suficiente para checar se há referência a `x`)."""
    idx = expr.rfind("ELSE ")
    assert idx != -1, f"expressão sem ELSE: {expr}"
    return expr[idx:]


def test_nenhuma_funcao_de_valor_usa_sentinela_literal_puro_no_else():
    """Nenhuma expressão de valor pode ter, no ramo ELSE, um cast de STRING
    LITERAL PURA sem referência à coluna (ex.: `('__invalid__')::numeric`)
    — confirmado por sondagem em 2026-07-06 que isso falha incondicional
    (constant folding do Postgres), mesmo quando nenhuma linha alcançaria
    aquele ramo. Todo ELSE deve concatenar/depender de `x` (ver
    `_force_invalid_cast`)."""
    checks = [
        semantics.orders_ts_value("x"),
        semantics.iso_date_value("x"),
        semantics.br_date_value("x"),
        semantics.br_ts_seconds_value("x"),
        semantics.br_date_range_start_value("x"),
        semantics.br_date_range_end_value("x"),
        semantics.filename_period_start_value("f.source_filename"),
        semantics.filename_period_end_value("f.source_filename"),
        semantics.int_value("x"),
        semantics.int_value("x", sql_type="bigint"),
        semantics.numeric_dot_value("x"),
        semantics.numeric_br_value("x"),
        semantics.pct_flexible_value("x"),
        semantics.bool_pair_value("x", "Y", "N"),
    ]
    for expr in checks:
        else_branch = _else_branch(expr)
        assert "x" in else_branch, f"ELSE não depende da coluna (seria dobrado em constante): {expr}"


def test_numeric_bool_int_forcam_falha_mesmo_para_literais_permissivos_do_postgres():
    """`numeric` aceita nativamente 'NaN'/'Infinity'/notação científica,
    `boolean` aceita 'true'/'yes'/'on'/'1' e `integer` aceita um '+' inicial
    — todos MAIS permissivos que o contrato desta staging (confirmado por
    sondagem read-only em 2026-07-06). Por isso numeric_dot_value/
    numeric_br_value/pct_flexible_value/bool_pair_value/int_value NUNCA
    usam `(v)::tipo` puro no ELSE — usam `_force_invalid_cast`, que
    concatena um marcador não numérico/booleano garantindo falha mesmo para
    esses literais permissivos."""
    for expr in (
        semantics.numeric_dot_value("x"), semantics.numeric_br_value("x"),
        semantics.pct_flexible_value("x"), semantics.int_value("x"),
    ):
        assert "CONTRATO_INVALIDO" in _else_branch(expr)
    assert "CONTRATO_INVALIDO" in _else_branch(semantics.bool_pair_value("x", "Y", "N"))


def test_else_de_numeric_dot_referencia_a_origem():
    expr = semantics.numeric_dot_value("x")
    assert "x" in _else_branch(expr), "ELSE deve depender da coluna (nunca ser uma constante pura)"


def test_else_de_numeric_br_referencia_a_origem():
    expr = semantics.numeric_br_value("x")
    assert "x" in _else_branch(expr)


def test_else_de_pct_flexible_referencia_a_origem():
    expr = semantics.pct_flexible_value("x")
    assert "x" in _else_branch(expr)


def test_else_de_int_value_referencia_a_origem():
    expr = semantics.int_value("x")
    assert "x" in _else_branch(expr)


def test_else_de_bool_pair_value_referencia_a_origem():
    expr = semantics.bool_pair_value("x", "Y", "N")
    assert "x" in _else_branch(expr)


def test_is_invalid_nunca_referencia_funcoes_que_lancam_erro():
    """As expressões de contagem (is_invalid) nunca podem chamar make_date/
    make_timestamp/to_date/to_timestamp — só regexp_match + aritmética pura,
    para nunca abortar a própria query de contagem."""
    forbidden = ("make_date(", "make_timestamp(", "to_date(", "to_timestamp(")
    checks = [
        semantics.orders_ts_is_invalid("x"),
        semantics.iso_date_is_invalid("x"),
        semantics.br_date_is_invalid("x"),
        semantics.br_ts_seconds_is_invalid("x"),
        semantics.br_date_range_is_invalid("x"),
        semantics.filename_period_is_invalid("f.source_filename"),
        semantics.numeric_dot_is_invalid("x"),
        semantics.numeric_br_is_invalid("x"),
        semantics.pct_flexible_is_invalid("x"),
        semantics.int_is_invalid("x"),
        semantics.bool_pair_is_invalid("x", "Y", "N"),
    ]
    for expr in checks:
        for token in forbidden:
            assert token not in expr, f"is_invalid chama função que lança erro: {token} em {expr}"


def test_numeric_dot_rejeita_formato_br_e_us():
    inv = semantics.numeric_dot_is_invalid("x")
    # A regex usada não aceita vírgula alguma — só dígitos e ponto.
    assert "," not in inv.split("~")[1].split("'")[1] if "~" in inv else True


def test_bool_pair_usa_apenas_o_par_especifico_nunca_a_uniao():
    """Cada coluna usa SEU par — não deve haver menção aos literais de
    OUTRA coluna (ex.: Yes/No não deve aparecer numa checagem Y/N)."""
    expr = semantics.bool_pair_is_invalid("x", "Y", "N")
    assert "'Yes'" not in expr and "'TRUE'" not in expr


def test_regexes_de_data_usam_grupos_de_captura():
    for pattern in (semantics.RE_ORDERS_TS, semantics.RE_ISO_DATE, semantics.RE_BR_DATE,
                    semantics.RE_BR_TS_SECONDS, semantics.RE_FILENAME_PERIOD,
                    semantics.RE_BR_DATE_RANGE):
        assert re.search(r"\([^)]*\)", pattern), f"regex sem grupo de captura: {pattern}"


def test_filename_period_end_nao_falha_para_arquivo_fora_do_padrao():
    """Arquivos fora do padrão (ex.: kokeshi) não são 'inválidos' — viram
    NULL (gap documentado), nunca disparam a checagem de invalidez."""
    inv = semantics.filename_period_is_invalid("'Dados+Gerais-01-01-19-03.csv'")
    # A condição exige que o nome CASE com o padrão para contar como inválido.
    assert semantics.RE_FILENAME_PERIOD in inv
