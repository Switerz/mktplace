"""Curva de pesos por dia das metas de GMV — copia de `apps/web/src/lib/metas-curva.ts`.

    meta do dia = meta do mes x peso do dia / soma dos pesos do mes

So' ML e Shopee tem curva, e so' nos meses listados; fora disso todos os dias
pesam igual (a conta linear). As curvas sao FIXAS no codigo dos dois lados:
mudou la', mude aqui (um teste confere que as duas copias batem).
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Optional

#: Por mes (`AAAA-MM`): (evento, peso ML, peso Shopee), um por dia a partir do dia 1.
CURVAS: dict[str, list[tuple[Optional[str], float, float]]] = {
    "2026-11": [
        (None, 0.98, 0.89),
        ("Finados (feriado)", 1, 0.88),
        (None, 1.02, 1.1),
        (None, 1.02, 1.12),
        (None, 1.04, 1),
        (None, 0.94, 0.91),
        (None, 0.9, 0.86),
        (None, 0.98, 0.89),
        ("Esquenta 11/11", 1, 1.2),
        ("Véspera 11/11", 1.05, 1.4),
        ("11/11", 2.45, 3.5),
        ("Pós 11/11", 1.2, 1.5),
        ("Pós 11/11", 1, 1.1),
        (None, 0.9, 0.86),
        (None, 0.98, 0.89),
        (None, 1, 0.88),
        (None, 1.02, 1.1),
        (None, 1.02, 1.12),
        (None, 1.04, 1),
        ("Consciência Negra (feriado)", 0.94, 0.91),
        (None, 0.9, 0.86),
        (None, 0.98, 0.89),
        ("Semana Black", 1.2, 1.2),
        ("Semana Black", 1.5, 1.5),
        ("Semana Black", 1.8, 1.8),
        ("Véspera Black", 1.75, 1.8),
        ("Black Friday", 2.45, 3.5),
        ("Fim de semana Black", 1.6, 1.6),
        ("Fim de semana Black", 1.55, 1.55),
        ("Cyber Monday", 1.45, 1.45),
    ],
}

_COLUNA = {"ml": 1, "shopee": 2}


def _mes(d: date) -> str:
    return f"{d:%Y-%m}"


def pesos_do_mes(mes_ref: date, canal: str) -> list[float]:
    n = calendar.monthrange(mes_ref.year, mes_ref.month)[1]
    curva = CURVAS.get(_mes(mes_ref))
    if not curva or canal not in _COLUNA:
        return [1.0] * n
    return [float(d[_COLUNA[canal]]) for d in curva]


def fracao_do_dia(dia: date, canal: str) -> float:
    pesos = pesos_do_mes(dia, canal)
    return pesos[dia.day - 1] / sum(pesos)


def fracao_ate(dia: date, canal: str) -> float:
    """Fatia do mes acumulada ate' o FIM de `dia`."""
    pesos = pesos_do_mes(dia, canal)
    return sum(pesos[: dia.day]) / sum(pesos)


def evento_do_dia(dia: date) -> Optional[str]:
    curva = CURVAS.get(_mes(dia))
    return curva[dia.day - 1][0] if curva and dia.day <= len(curva) else None
