"""
Validação semântica (não só de formato) para a staging tipada Shopee.

Comportamento real do PostgreSQL 17.9 do Data Mart, confirmado por sondagem
read-only em 2026-07-06 (SELECTs literais, nenhuma tabela tocada):

- `'NaN'::numeric`, `'Infinity'::numeric`, `'-Infinity'::numeric` são
  ACEITOS pelo cast nativo (PostgreSQL >= 14 suporta esses valores especiais
  em `numeric`). Um `::numeric` "nu" NUNCA rejeita esses literais — é preciso
  validar o formato por regex ANTES do cast.
- `('1.5')::integer` e `('1e2')::integer` já falham nativamente
  ("invalid input syntax for type integer") — fração/notação científica em
  coluna inteira já são fail-fast só com o cast; nenhuma validação extra é
  necessária para esse caso.
- `to_date`/`to_timestamp` com formato explícito (`'DD/MM/YYYY'`,
  `'YYYY-MM-DD HH24:MI'`) JÁ rejeitam datas de calendário impossíveis
  (`to_date('31/02/2026', 'DD/MM/YYYY')` levanta "date/time field value out
  of range") — ao contrário do que se costuma supor sobre essas funções,
  elas não normalizam silenciosamente neste Postgres/versão. Mesmo assim,
  esses erros só disparam DEPOIS que a linha já está sendo processada pelo
  cast — o objetivo deste módulo é detectar a mesma invalidez ANTES, via uma
  expressão booleana pura que nunca lança erro, para permitir contar
  quantas linhas falhariam (fail-fast controlado, com contagem sanitizada)
  em vez de abortar no meio de um INSERT sem diagnóstico.
- `('t')::boolean`, `('1')::boolean`, `('YEs')::boolean`, `('no')::boolean`
  são todos aceitos — o cast nativo de boolean é mais permissivo que
  qualquer par Y/N, Yes/No ou TRUE/FALSE documentado. Cada coluna booleana
  desta staging usa um par de literais ESPECÍFICO (nunca a união de todos).

Este módulo fornece, para cada formato: (a) uma expressão SQL "is_invalid"
que nunca lança erro (usada para CONTAR linhas inválidas antes do INSERT —
mesma fonte usada pelo preview read-only e pela transformação), e (b) uma
expressão de VALOR (construída por componentes com `make_date`/
`make_timestamp`, nunca por `to_date`/`to_timestamp`) usada no SELECT do
INSERT como última linha de defesa — se a contagem prévia estiver correta
(count=0), esta expressão nunca deveria falhar; se falhar mesmo assim (bug
na contagem), aborta a transação com um erro nativo do Postgres.

**Achado crítico confirmado por sondagem em 2026-07-06** (`SELECT CASE WHEN
1=2 THEN true ELSE ('__x__')::boolean END` — branch nunca alcançado — FALHA
mesmo assim): quando o ramo `ELSE` de um `CASE` é um LITERAL puro sem
referência a coluna (ex.: `('__invalid__')::numeric`), o planejador do
Postgres faz *constant folding* e avalia esse cast em tempo de
PLANEJAMENTO — o erro dispara sempre, para 100% das linhas, mesmo quando
nenhuma delas jamais alcançaria aquele ramo em tempo de execução. Por isso
NENHUMA função de valor deste módulo usa um sentinela literal no `ELSE`: o
`ELSE` sempre referencia a própria expressão de origem (dependente de
linha, nunca dobrável em constante), preservando avaliação preguiçosa por
linha. Isso significa que, no raríssimo cenário em que a contagem prévia
tiver um bug e uma linha realmente inválida chegar até aqui, a mensagem de
erro nativa do Postgres incluirá o texto original da célula — aceitável
porque nenhuma das colunas que passam por estas funções é classificada como
PII (são numéricas/booleanas/de data — ver `pii_class` em `mapping.py`); a
defesa primária contra qualquer vazamento em mensagem é a contagem
sanitizada (motivo + contagem, nunca valor) que roda ANTES do INSERT.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Formatos (regex) — única fonte da verdade, usada tanto na contagem quanto
# na construção do valor.
# ---------------------------------------------------------------------------

RE_ORDERS_TS = r"^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$"
RE_ISO_DATE = r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$"
RE_BR_DATE = r"^([0-9]{2})/([0-9]{2})/([0-9]{4})$"
RE_BR_TS_SECONDS = r"^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$"
RE_FILENAME_PERIOD = r"([0-9]{2})_([0-9]{2})_([0-9]{4})-([0-9]{2})_([0-9]{2})_([0-9]{4})"
RE_BR_DATE_RANGE = r"^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$"

# Números: dígitos puros, sem separador de milhar, sem NaN/Infinity/moeda.
RE_DOT_NUMERIC = r"^-?[0-9]+(\.[0-9]+)?$"
RE_INT = r"^-?[0-9]+$"
# BR: "1.234,56" (milhar+decimal) ou "1234,56" (decimal simples).
RE_BR_THOUSANDS = r"^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$"
RE_BR_SIMPLE_COMMA = r"^-?[0-9]+,[0-9]+$"
# Percentual: aceita separador BR (vírgula, shop-stats) ou US (ponto, ads).
RE_PCT = r"^-?[0-9]+([.,][0-9]+)?%?$"



def _group(expr: str, pattern: str, idx: int) -> str:
    """i-ésimo grupo capturado (1-based) de `pattern` sobre `expr`, como int.
    NULL (sem erro) se `expr` não casar com `pattern` — seguro para uso
    dentro de uma expressão de contagem que nunca deve lançar exceção."""
    return f"(regexp_match({expr}, '{pattern}'))[{idx}]::integer"


def _force_invalid_cast(v: str, sql_type: str) -> str:
    """ELSE de último recurso para tipos cujo cast nativo é MAIS permissivo
    que a regex do contrato — `numeric` aceita 'NaN'/'Infinity'/notação
    científica, `boolean` aceita 'true'/'yes'/'on'/'1' independente do par
    Y/N ou Yes/No documentado da coluna (confirmado por sondagem read-only
    em 2026-07-06). Um `({v})::tipo` simples NÃO garante falha nesses casos
    — o valor entraria na staging sem erro e sem passar pela regra
    documentada. Concatenar um sufixo alfabético fixo a `v` (que já falhou
    a regex do contrato, então não é um número/booleano "limpo") GARANTE
    que o cast falhe, mantendo a expressão dependente da coluna (nunca uma
    constante pura — ver nota sobre constant folding no docstring do
    módulo). Como nenhuma destas colunas é PII, o valor original aparecer
    na mensagem nativa do Postgres neste ramo de última instância é um
    trade-off aceito (documentado); a defesa primária continua sendo a
    contagem sanitizada pré-INSERT em validations.py."""
    return f"(({v} || 'CONTRATO_INVALIDO')::{sql_type})"


def _is_valid_ymd(year: str, month: str, day: str) -> str:
    """Booleano puro (nunca lança erro) — mesma regra de calendário que
    `make_date` aplica nativamente, mas sem chamar make_date (que erraria a
    query de contagem em vez de só marcar a linha como inválida)."""
    leap = f"(({year} % 4 = 0 AND {year} % 100 <> 0) OR {year} % 400 = 0)"
    days_in_month = (
        "(CASE "
        f"WHEN {month} = 2 THEN (CASE WHEN {leap} THEN 29 ELSE 28 END) "
        f"WHEN {month} IN (4, 6, 9, 11) THEN 30 "
        "ELSE 31 END)"
    )
    return f"({month} BETWEEN 1 AND 12 AND {day} BETWEEN 1 AND {days_in_month})"


def _is_valid_hms(hour: str, minute: str, second: str | None = None) -> str:
    parts = [f"{hour} BETWEEN 0 AND 23", f"{minute} BETWEEN 0 AND 59"]
    if second is not None:
        parts.append(f"{second} BETWEEN 0 AND 59")
    return "(" + " AND ".join(parts) + ")"


# ---------------------------------------------------------------------------
# orders: "YYYY-MM-DD HH:MM" — timestamps operacionais.
# ---------------------------------------------------------------------------

def orders_ts_is_invalid(x: str, *, blank_placeholder: str | None = None) -> str:
    """Verdadeiro se o valor está PRESENTE (não vazio, não placeholder) e é
    inválido — por formato OU por calendário/hora fora do domínio."""
    v = f"btrim({x})"
    present = f"NULLIF({v}, '')"
    if blank_placeholder is not None:
        present = f"NULLIF({present}, '{blank_placeholder}')"
    year, month, day = (_group(present, RE_ORDERS_TS, i) for i in (1, 2, 3))
    hour, minute = (_group(present, RE_ORDERS_TS, i) for i in (4, 5))
    return (
        f"({present} IS NOT NULL AND ("
        f"{present} !~ '{RE_ORDERS_TS}' "
        f"OR NOT {_is_valid_ymd(year, month, day)} "
        f"OR NOT {_is_valid_hms(hour, minute)}"
        "))"
    )


def orders_ts_value(x: str, *, blank_placeholder: str | None = None) -> str:
    v = f"btrim({x})"
    present = f"NULLIF({v}, '')"
    if blank_placeholder is not None:
        present = f"NULLIF({present}, '{blank_placeholder}')"
    year, month, day = (_group(present, RE_ORDERS_TS, i) for i in (1, 2, 3))
    hour, minute = (_group(present, RE_ORDERS_TS, i) for i in (4, 5))
    return (
        f"(CASE WHEN {present} IS NULL THEN NULL "
        f"ELSE make_timestamp({year}, {month}, {day}, {hour}, {minute}, 0) END)"
    )


# ---------------------------------------------------------------------------
# ISO date "YYYY-MM-DD" (orders: Domestic Delivered Date / Data da
# Finalização do Cancelamento).
# ---------------------------------------------------------------------------

def iso_date_is_invalid(x: str) -> str:
    v = f"NULLIF(btrim({x}), '')"
    year, month, day = (_group(v, RE_ISO_DATE, i) for i in (1, 2, 3))
    return (
        f"({v} IS NOT NULL AND ("
        f"{v} !~ '{RE_ISO_DATE}' OR NOT {_is_valid_ymd(year, month, day)}"
        "))"
    )


def iso_date_value(x: str) -> str:
    v = f"NULLIF(btrim({x}), '')"
    year, month, day = (_group(v, RE_ISO_DATE, i) for i in (1, 2, 3))
    return f"(CASE WHEN {v} IS NULL THEN NULL ELSE make_date({year}, {month}, {day}) END)"


# ---------------------------------------------------------------------------
# shop-stats: "DD/MM/YYYY" (linha diária).
# ---------------------------------------------------------------------------

def br_date_is_invalid(x: str) -> str:
    v = f"btrim({x})"
    day, month, year = (_group(v, RE_BR_DATE, i) for i in (1, 2, 3))
    return (
        f"({v} ~ '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}}$' AND "
        f"NOT {_is_valid_ymd(year, month, day)})"
    )


def br_date_value(x: str) -> str:
    v = f"btrim({x})"
    day, month, year = (_group(v, RE_BR_DATE, i) for i in (1, 2, 3))
    return (
        f"(CASE WHEN {v} !~ '{RE_BR_DATE}' THEN NULL "
        f"ELSE make_date({year}, {month}, {day}) END)"
    )


# ---------------------------------------------------------------------------
# ads: "DD/MM/YYYY HH:MM:SS".
# ---------------------------------------------------------------------------

def br_ts_seconds_is_invalid(x: str, *, blank_placeholder: str | None = None) -> str:
    v = f"btrim({x})"
    present = f"NULLIF({v}, '')"
    if blank_placeholder is not None:
        present = f"NULLIF({present}, '{blank_placeholder}')"
    day, month, year = (_group(present, RE_BR_TS_SECONDS, i) for i in (1, 2, 3))
    hour, minute, second = (_group(present, RE_BR_TS_SECONDS, i) for i in (4, 5, 6))
    return (
        f"({present} IS NOT NULL AND ("
        f"{present} !~ '{RE_BR_TS_SECONDS}' "
        f"OR NOT {_is_valid_ymd(year, month, day)} "
        f"OR NOT {_is_valid_hms(hour, minute, second)}"
        "))"
    )


def br_ts_seconds_value(x: str, *, blank_placeholder: str | None = None) -> str:
    v = f"btrim({x})"
    present = f"NULLIF({v}, '')"
    if blank_placeholder is not None:
        present = f"NULLIF({present}, '{blank_placeholder}')"
    day, month, year = (_group(present, RE_BR_TS_SECONDS, i) for i in (1, 2, 3))
    hour, minute, second = (_group(present, RE_BR_TS_SECONDS, i) for i in (4, 5, 6))
    return (
        f"(CASE WHEN {present} IS NULL THEN NULL "
        f"ELSE make_timestamp({year}, {month}, {day}, {hour}, {minute}, {second}::double precision) END)"
    )


# ---------------------------------------------------------------------------
# shop-stats: linha de "total do período" — "DD/MM/YYYY-DD/MM/YYYY" na mesma
# coluna "Data" que também carrega a linha diária.
# ---------------------------------------------------------------------------

def br_date_range_is_invalid(x: str) -> str:
    v = f"btrim({x})"
    d1, m1, y1 = (_group(v, RE_BR_DATE_RANGE, i) for i in (1, 2, 3))
    d2, m2, y2 = (_group(v, RE_BR_DATE_RANGE, i) for i in (4, 5, 6))
    valid1 = _is_valid_ymd(y1, m1, d1)
    valid2 = _is_valid_ymd(y2, m2, d2)
    order_ok = f"(({y1} * 10000 + {m1} * 100 + {d1}) <= ({y2} * 10000 + {m2} * 100 + {d2}))"
    return (
        f"({v} ~ '{RE_BR_DATE_RANGE}' AND ("
        f"NOT {valid1} OR NOT {valid2} OR NOT {order_ok}"
        "))"
    )


def br_date_range_start_value(x: str) -> str:
    v = f"btrim({x})"
    d1, m1, y1 = (_group(v, RE_BR_DATE_RANGE, i) for i in (1, 2, 3))
    return f"(CASE WHEN {v} !~ '{RE_BR_DATE_RANGE}' THEN NULL ELSE make_date({y1}, {m1}, {d1}) END)"


def br_date_range_end_value(x: str) -> str:
    v = f"btrim({x})"
    d2, m2, y2 = (_group(v, RE_BR_DATE_RANGE, i) for i in (4, 5, 6))
    return f"(CASE WHEN {v} !~ '{RE_BR_DATE_RANGE}' THEN NULL ELSE make_date({y2}, {m2}, {d2}) END)"


def shop_stats_data_format_is_invalid(x: str) -> str:
    """Verdadeiro quando a coluna 'Data' (sempre obrigatória) não casa nem
    com o formato diário nem com o de total de período."""
    v = f"btrim({x})"
    return f"({v} !~ '{RE_BR_DATE}' AND {v} !~ '{RE_BR_DATE_RANGE}')"


# ---------------------------------------------------------------------------
# Período extraído do NOME do arquivo de ads: "..._DD_MM_YYYY-DD_MM_YYYY..."
# ---------------------------------------------------------------------------

def filename_period_is_invalid(filename_expr: str) -> str:
    """Verdadeiro apenas quando o nome CASA com o padrão de período mas os
    componentes são calendarialmente inválidos, ou quando início > fim.
    Arquivo fora do padrão (ex.: kokeshi) não é "inválido" — é
    'sem período', tratado à parte (vira NULL, gap documentado)."""
    f = filename_expr
    d1, m1, y1 = (_group(f, RE_FILENAME_PERIOD, i) for i in (1, 2, 3))
    d2, m2, y2 = (_group(f, RE_FILENAME_PERIOD, i) for i in (4, 5, 6))
    valid1 = _is_valid_ymd(y1, m1, d1)
    valid2 = _is_valid_ymd(y2, m2, d2)
    order_ok = f"(({y1} * 10000 + {m1} * 100 + {d1}) <= ({y2} * 10000 + {m2} * 100 + {d2}))"
    return (
        f"({f} ~ '{RE_FILENAME_PERIOD}' AND ("
        f"NOT {valid1} OR NOT {valid2} OR NOT {order_ok}"
        "))"
    )


def filename_period_start_value(filename_expr: str) -> str:
    f = filename_expr
    d1, m1, y1 = (_group(f, RE_FILENAME_PERIOD, i) for i in (1, 2, 3))
    return (
        f"(CASE WHEN {f} !~ '{RE_FILENAME_PERIOD}' THEN NULL "
        f"ELSE make_date({y1}, {m1}, {d1}) END)"
    )


def filename_period_end_value(filename_expr: str) -> str:
    f = filename_expr
    d2, m2, y2 = (_group(f, RE_FILENAME_PERIOD, i) for i in (4, 5, 6))
    return (
        f"(CASE WHEN {f} !~ '{RE_FILENAME_PERIOD}' THEN NULL "
        f"ELSE make_date({y2}, {m2}, {d2}) END)"
    )


# ---------------------------------------------------------------------------
# Inteiros — o cast nativo ::integer/::bigint já rejeita fração e notação
# científica ("1.5", "1e2"), mas NÃO detecta isso antes de uma tentativa de
# INSERT já em andamento. Aqui expomos o mesmo formato como predicado puro
# (nunca lança erro) para permitir a contagem prévia fail-fast.
# ---------------------------------------------------------------------------

def int_is_invalid(x: str, *, placeholder: str | None = None) -> str:
    v = f"NULLIF(btrim({x}), '')"
    if placeholder is not None:
        v = f"NULLIF({v}, '{placeholder}')"
    return f"({v} IS NOT NULL AND {v} !~ '{RE_INT}')"


def int_value(x: str, *, placeholder: str | None = None, sql_type: str = "integer") -> str:
    v = f"NULLIF(btrim({x}), '')"
    if placeholder is not None:
        v = f"NULLIF({v}, '{placeholder}')"
    return (
        f"(CASE WHEN {v} IS NULL THEN NULL "
        f"WHEN {v} ~ '{RE_INT}' THEN ({v})::{sql_type} "
        f"ELSE {_force_invalid_cast(v, sql_type)} END)"
    )


# ---------------------------------------------------------------------------
# Números — rejeita NaN/Infinity/formato ambíguo ANTES do cast nativo.
# ---------------------------------------------------------------------------

def numeric_dot_is_invalid(x: str) -> str:
    v = f"NULLIF(btrim({x}), '')"
    return f"({v} IS NOT NULL AND {v} !~ '{RE_DOT_NUMERIC}')"


def numeric_dot_value(x: str) -> str:
    v = f"NULLIF(btrim({x}), '')"
    return (
        f"(CASE WHEN {v} IS NULL THEN NULL "
        f"WHEN {v} ~ '{RE_DOT_NUMERIC}' THEN ({v})::numeric "
        f"ELSE {_force_invalid_cast(v, 'numeric')} END)"
    )


def numeric_br_is_invalid(x: str) -> str:
    v = f"NULLIF(btrim({x}), '')"
    return (
        f"({v} IS NOT NULL AND {v} !~ '{RE_BR_THOUSANDS}' "
        f"AND {v} !~ '{RE_BR_SIMPLE_COMMA}' AND {v} !~ '{RE_DOT_NUMERIC}')"
    )


def numeric_br_value(x: str) -> str:
    v = f"NULLIF(btrim({x}), '')"
    return (
        "(CASE "
        f"WHEN {v} IS NULL THEN NULL "
        f"WHEN {v} ~ '{RE_BR_THOUSANDS}' OR {v} ~ '{RE_BR_SIMPLE_COMMA}' "
        f"THEN replace(replace({v}, '.', ''), ',', '.')::numeric "
        f"WHEN {v} ~ '{RE_DOT_NUMERIC}' THEN ({v})::numeric "
        f"ELSE {_force_invalid_cast(v, 'numeric')} END)"
    )


def pct_flexible_is_invalid(x: str) -> str:
    """'-' é o placeholder comprovado de ausência em colunas de ads
    (Add to Cart Rate, CTR do Produto) — tratado como NULL, não inválido."""
    v = f"NULLIF(NULLIF(btrim({x}), ''), '-')"
    return f"({v} IS NOT NULL AND {v} !~ '{RE_PCT}')"


def pct_flexible_value(x: str) -> str:
    v = f"NULLIF(NULLIF(btrim({x}), ''), '-')"
    return (
        f"(CASE WHEN {v} IS NULL THEN NULL "
        f"WHEN {v} ~ '{RE_PCT}' THEN replace(replace({v}, '%', ''), ',', '.')::numeric "
        f"ELSE {_force_invalid_cast(v, 'numeric')} END)"
    )


# ---------------------------------------------------------------------------
# Booleanos — cada coluna usa um par ESPECÍFICO (nunca a união de todos).
# ---------------------------------------------------------------------------

def bool_pair_is_invalid(x: str, true_literal: str, false_literal: str) -> str:
    v = f"NULLIF(btrim({x}), '')"
    return f"({v} IS NOT NULL AND {v} NOT IN ('{true_literal}', '{false_literal}'))"


def bool_pair_value(x: str, true_literal: str, false_literal: str) -> str:
    v = f"NULLIF(btrim({x}), '')"
    return (
        "(CASE "
        f"WHEN {v} IS NULL THEN NULL "
        f"WHEN {v} = '{true_literal}' THEN true "
        f"WHEN {v} = '{false_literal}' THEN false "
        f"ELSE {_force_invalid_cast(v, 'boolean')} END)"
    )
