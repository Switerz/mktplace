"""
Registro único de regras de conversão — fonte compartilhada entre:

- `build_sql.py` (expressão de VALOR usada no SELECT do INSERT);
- `validations.py` (expressão "is_invalid", usada tanto pelo preview quanto
  pela contagem fail-fast dentro da transação de transformação).

Isso existe para nunca haver duas listas divergentes de regras — pedido
explícito da revisão de 2026-07-06. Cada `Rule` tem `value` (obrigatório) e
`is_invalid` (opcional; `None` para regras sem validação de formato própria,
como texto livre, onde a única checagem é "obrigatório vazio").

Formato de uma string de regra no mapping: `"nome"` ou `"nome:param1:param2"`
(parâmetros separados por `:`; nenhum header real contém `:`, então não há
ambiguidade). `parse_rule` faz o split.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from pipelines.staging.shopee import semantics, sql_rules


@dataclass(frozen=True)
class Rule:
    value: Callable[..., str]
    is_invalid: Optional[Callable[..., str]] = None


def parse_rule(rule: str) -> tuple[str, list[str]]:
    name, *params = rule.split(":")
    return name, params


def _text(fn: Callable[[str], str]) -> Rule:
    return Rule(value=fn, is_invalid=None)


REGISTRY: dict[str, Rule] = {
    "text_required": _text(sql_rules.text_required),
    "text_null_blank": _text(sql_rules.text_null_blank),
    "text_null_placeholder": _text(sql_rules.text_null_placeholder),

    "int_strict": Rule(value=semantics.int_value, is_invalid=semantics.int_is_invalid),
    "bigint_strict": Rule(
        value=lambda x: semantics.int_value(x, sql_type="bigint"),
        is_invalid=semantics.int_is_invalid,
    ),
    "int_null_placeholder": Rule(
        value=lambda x: semantics.int_value(x, placeholder="-"),
        is_invalid=lambda x: semantics.int_is_invalid(x, placeholder="-"),
    ),

    "numeric_dot": Rule(value=semantics.numeric_dot_value, is_invalid=semantics.numeric_dot_is_invalid),
    "numeric_br": Rule(value=semantics.numeric_br_value, is_invalid=semantics.numeric_br_is_invalid),
    "pct_flexible": Rule(value=semantics.pct_flexible_value, is_invalid=semantics.pct_flexible_is_invalid),

    "orders_ts": Rule(value=semantics.orders_ts_value, is_invalid=semantics.orders_ts_is_invalid),
    "orders_ts_placeholder": Rule(
        value=lambda x, ph: semantics.orders_ts_value(x, blank_placeholder=ph),
        is_invalid=lambda x, ph: semantics.orders_ts_is_invalid(x, blank_placeholder=ph),
    ),
    "iso_date": Rule(value=semantics.iso_date_value, is_invalid=semantics.iso_date_is_invalid),
    "br_ts_seconds": Rule(value=semantics.br_ts_seconds_value, is_invalid=semantics.br_ts_seconds_is_invalid),
    "br_ts_seconds_placeholder": Rule(
        value=lambda x, ph: semantics.br_ts_seconds_value(x, blank_placeholder=ph),
        is_invalid=lambda x, ph: semantics.br_ts_seconds_is_invalid(x, blank_placeholder=ph),
    ),

    "bool_pair": Rule(
        value=lambda x, t, f: semantics.bool_pair_value(x, t, f),
        is_invalid=lambda x, t, f: semantics.bool_pair_is_invalid(x, t, f),
    ),

    "shop_stats_row_type": Rule(value=sql_rules.shop_stats_row_type,
                                 is_invalid=semantics.shop_stats_data_format_is_invalid),
    "shop_stats_stat_date": Rule(value=sql_rules.shop_stats_stat_date,
                                  is_invalid=semantics.br_date_is_invalid),
    "shop_stats_period_start": Rule(value=semantics.br_date_range_start_value,
                                     is_invalid=semantics.br_date_range_is_invalid),
    "shop_stats_period_end": Rule(value=semantics.br_date_range_end_value,
                                   is_invalid=semantics.br_date_range_is_invalid),
}

# Regras que operam sobre um campo do MANIFESTO (raw.shopee_ingestion_file,
# alias "f"), não sobre uma chave do raw_payload — por isso não recebem `x`.
# Revisão de 2026-07-06 (2ª rodada): o período de ads vem de
# `f.source_metadata` (jsonb do preâmbulo do CSV), nunca mais de
# `f.source_filename` — ver docstring de `semantics.ads_metadata_period_is_invalid`.
MANIFEST_REGISTRY: dict[str, Rule] = {
    "ads_metadata_period_start": Rule(
        value=lambda: semantics.ads_metadata_period_start_value("f.source_metadata"),
        is_invalid=lambda: semantics.ads_metadata_period_is_invalid("f.source_metadata"),
    ),
    "ads_metadata_period_end": Rule(
        value=lambda: semantics.ads_metadata_period_end_value("f.source_metadata"),
        is_invalid=lambda: semantics.ads_metadata_period_is_invalid("f.source_metadata"),
    ),
}


def resolve(rule: str) -> tuple[Rule, list[str]]:
    name, params = parse_rule(rule)
    if name in MANIFEST_REGISTRY:
        return MANIFEST_REGISTRY[name], params
    return REGISTRY[name], params
