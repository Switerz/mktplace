"""
Resolucao de periodo (date_from/date_to vs ref_month vs default) — modulo
sem dependencia de services, para evitar import circular com
app.services.performance_service (que tambem usa EffectivePeriod).

O relogio e sempre injetavel (`today`) e nunca lido implicitamente com
`date.today()` dentro da logica de negocio — isso permite testes
deterministicos e evita depender do fuso do SO. Quando nao injetado, o
default e resolvido explicitamente em America/Sao_Paulo (ver `today_brt`),
nunca UTC nem o fuso local da maquina.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException

MAX_RANGE_DAYS = 366
APP_TIMEZONE = ZoneInfo("America/Sao_Paulo")

DefaultMode = Literal["days", "previous_month"]


def today_brt() -> date:
    """Data corrente em America/Sao_Paulo — unico ponto de leitura do
    relogio do sistema nesta camada. Todo o resto recebe `today` por
    parametro para permitir testes deterministicos."""
    return datetime.now(APP_TIMEZONE).date()


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _previous_month_bounds(today: date) -> tuple[date, date, str]:
    y, m = today.year, today.month
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    start, end = _month_bounds(py, pm)
    return start, end, f"{py:04d}-{pm:02d}"


@dataclass(frozen=True)
class EffectivePeriod:
    start: date
    end: date
    ref_month: Optional[str] = None  # "YYYY-MM" apenas quando resolvido de um mes calendario unico

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def resolve_period(
    *,
    ref_month: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    default_days: int = 30,
    default_mode: DefaultMode = "days",
    today: Optional[date] = None,
) -> EffectivePeriod:
    """Resolve o periodo efetivo com precedencia estrita: date_from/date_to >
    ref_month > default. Nunca mistura fragmentos de fontes diferentes (ex:
    date_from + ref_month juntos).

    `default_mode` controla o comportamento quando NADA e informado:
    - "days": ultimos `default_days` dias ate `today` (comportamento legado
      de Pedidos, que sempre foi baseado em `days_back`, nunca em mes).
    - "previous_month": mes calendario anterior a `today` (comportamento
      legado de overview/brands/canais/financeiro/quality, que usavam
      `_parse_month(None)` — ver tambem o comentario historico em
      `refMonth()` no frontend: "mes anterior como referencia padrao").

    `today` deve ser injetado pelo chamador (`today_brt()` por padrao) para
    manter a resolucao determinística e testável.
    """
    today = today if today is not None else today_brt()

    if (date_from is None) != (date_to is None):
        raise HTTPException(422, "date_from e date_to devem ser informados juntos.")

    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise HTTPException(422, "date_from nao pode ser posterior a date_to.")
        span_days = (date_to - date_from).days + 1
        if span_days > MAX_RANGE_DAYS:
            raise HTTPException(422, f"Intervalo maximo permitido e de {MAX_RANGE_DAYS} dias.")
        if date_to > today:
            raise HTTPException(422, "date_to nao pode ser uma data futura.")
        # Se o intervalo explicito cobre exatamente um mes calendario
        # completo, preenche ref_month tambem aqui (nao so no branch de
        # ref_month=YYYY-MM) — e o que permite ao frontend materializar
        # date_from/date_to concretos na URL (para bookmark/reload) sem abrir
        # mao do MoM de mes-calendario-completo ja auditado em
        # get_overview/get_brands/get_quality, que so calcula esse MoM
        # quando ref_month esta presente.
        month_start, month_end = _month_bounds(date_from.year, date_from.month)
        ref_month = (
            f"{date_from.year:04d}-{date_from.month:02d}"
            if date_from == month_start and date_to == month_end
            else None
        )
        return EffectivePeriod(start=date_from, end=date_to, ref_month=ref_month)

    if ref_month:
        try:
            year_s, month_s = ref_month.split("-")
            year, month = int(year_s), int(month_s)
            if not (1 <= month <= 12):
                raise ValueError
        except Exception:
            raise HTTPException(422, "ref_month deve ser YYYY-MM")
        start, end = _month_bounds(year, month)
        return EffectivePeriod(start=start, end=end, ref_month=ref_month)

    if default_mode == "previous_month":
        start, end, label = _previous_month_bounds(today)
        return EffectivePeriod(start=start, end=end, ref_month=label)

    end = today
    start = end - timedelta(days=default_days - 1)
    return EffectivePeriod(start=start, end=end, ref_month=None)


def resolve_previous_period(period: EffectivePeriod) -> EffectivePeriod:
    """Periodo imediatamente anterior, com a mesma duracao em dias. Usado
    apenas quando `compare=true` e passado explicitamente — nao substitui o
    MoM calendario ja existente em /overview, /brands e /quality."""
    span = period.days
    prev_end = period.start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return EffectivePeriod(start=prev_start, end=prev_end, ref_month=None)
