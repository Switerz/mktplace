"""
Regras de conversão SQL "simples" para a staging tipada da Shopee (DRAFT).

Este módulo cobre só texto, o acesso ao payload e o dispatch de shop-stats
por formato (que não precisa de validação semântica própria — a validação
de calendário das datas envolvidas vive em `semantics.py`). Toda regra
numérica, de data/hora e booleana com validação semântica (rejeição de
NaN/Infinity, calendário impossível, flags fora do conjunto documentado)
está em `semantics.py`, registrada em `rules_registry.py` — única fonte
usada tanto pela transformação quanto pelo preview read-only (revisão de
2026-07-06).
"""
from __future__ import annotations

from typing import Callable


def payload(header: str, alias: str = "r") -> str:
    """Expressão de acesso a uma chave do raw_payload. Header nunca contém
    aspas simples nos exports reais; validado em mapping (defesa em código)."""
    if "'" in header:
        raise ValueError(f"header com aspas simples não suportado: {header!r}")
    return f"{alias}.raw_payload ->> '{header}'"


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------

def text_required(x: str) -> str:
    """Texto obrigatório: só trim. Vazio vira NULL e reprova no NOT NULL."""
    return f"NULLIF(btrim({x}), '')"


def text_null_blank(x: str) -> str:
    """Texto opcional: trim; vazio → NULL."""
    return f"NULLIF(btrim({x}), '')"


def text_null_placeholder(x: str) -> str:
    """Texto opcional: vazio ou o placeholder '-' → NULL."""
    return f"NULLIF(NULLIF(btrim({x}), ''), '-')"


# ---------------------------------------------------------------------------
# Regras compostas específicas dos exports Shopee
# ---------------------------------------------------------------------------

def coalesce_headers(headers: tuple[str, ...], inner: Callable[[str], str], alias: str = "r") -> str:
    """COALESCE sobre variantes de header (colunas duplicadas desambiguadas
    pelo loader Raw como "<header>__col<posição>", que variam por template —
    ex.: "Cidade__col58" na apice e "Cidade__col59" nas demais marcas)."""
    parts = [inner(payload(h, alias)) for h in headers]
    return "COALESCE(" + ", ".join(parts) + ")"


def shop_stats_row_type(x: str) -> str:
    """'DD/MM/YYYY' → 'daily'; 'DD/MM/YYYY-DD/MM/YYYY' → 'period_total'.
    Outro formato → NULL, que reprova no NOT NULL (fail-fast). Validação
    semântica de calendário é feita separadamente por
    `semantics.shop_stats_data_format_is_invalid` / `br_date_is_invalid` /
    `br_date_range_is_invalid` (contadas ANTES do INSERT)."""
    v = f"btrim({x})"
    return (
        "(CASE "
        f"WHEN {v} ~ '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}}$' THEN 'daily' "
        f"WHEN {v} ~ '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}}-[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}}$' THEN 'period_total' "
        "ELSE NULL END)"
    )


def shop_stats_stat_date(x: str) -> str:
    """Valor de `stat_date` quando `row_type='daily'`. Usa `make_date` por
    componentes (não `to_date`) para ficar consistente com a validação
    semântica compartilhada — ver `semantics.br_date_value`."""
    from pipelines.staging.shopee import semantics
    return semantics.br_date_value(x)
