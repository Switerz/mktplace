"""
Gate V2-2, correcao consolidada — Finding 1 (BLOQUEADOR).

A janela comparativa era resolvida em DOIS lugares com regras diferentes:

- `filters.compare_period` = janela deslizante de mesma duracao;
- `/overview`, `/brands` e `/quality` corrigiam localmente o mes-calendario
  completo para o mes anterior completo;
- `/trend`, `/canais` e `/financeiro` usavam a janela deslizante crua.

Consequencia concreta com o periodo 01-30/06: o KPI comparava com 01-31/05 e a
serie de `/trend` com 02-31/05 — o grafico e o delta respondiam a perguntas
diferentes. Estes testes travam a regra canonica UNICA
(`app.deps.period.resolve_compare_period`) e provam que os seis endpoints
agregados recebem exatamente a mesma janela.
"""
from datetime import date

from app.deps.filters import filters_query, filters_query_default_days
from app.deps.period import (
    EffectivePeriod,
    resolve_compare_period,
    resolve_period,
    resolve_previous_period,
)
from app.services import performance_service as perf_svc


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return None

    def scalars(self):
        return self


class FakeSession:
    """Devolve sempre conjunto vazio e registra os parametros de cada consulta —
    a prova da janela usada e' o `start`/`end` que chegou ao SQL, nao apenas o
    valor ecoado na resposta."""

    def __init__(self):
        self.calls: list[dict] = []
        self.sql: list[str] = []

    def execute(self, stmt, params=None):
        self.calls.append(params or {})
        self.sql.append(str(stmt))
        return _Rows([])

    def windows(self) -> list[tuple[date, date]]:
        return [
            (c["start"], c["end"])
            for c in self.calls
            if isinstance(c.get("start"), date) and isinstance(c.get("end"), date)
        ]


def _filters(**kwargs):
    """Chama a dependency real (sem FastAPI) com `db=None`: `resolve_brands`
    aceita None e nenhum SQL e' executado na resolucao de filtros."""
    base = dict(
        channels=None, marketplace=None, brands=None,
        date_from=None, date_to=None, ref_month=None, compare=False, db=None,
    )
    base.update(kwargs)
    return filters_query(**base)


# ---------------------------------------------------------------------------
# A regra canonica, isolada
# ---------------------------------------------------------------------------

def test_compare_false_nao_produz_janela():
    period = resolve_period(ref_month="2026-06")
    assert resolve_compare_period(period, compare=False) is None


def test_junho_2026_compara_com_maio_inteiro():
    period = resolve_period(ref_month="2026-06")
    cmp = resolve_compare_period(period, compare=True)
    assert (cmp.start, cmp.end) == (date(2026, 5, 1), date(2026, 5, 31))
    assert cmp.days == 31  # maio inteiro, nao 30 dias arrastados
    assert cmp.ref_month == "2026-05"


def test_maio_2026_compara_com_abril_inteiro():
    period = resolve_period(ref_month="2026-05")
    cmp = resolve_compare_period(period, compare=True)
    assert (cmp.start, cmp.end) == (date(2026, 4, 1), date(2026, 4, 30))


def test_janeiro_2026_compara_com_dezembro_2025_inteiro():
    period = resolve_period(ref_month="2026-01")
    cmp = resolve_compare_period(period, compare=True)
    assert (cmp.start, cmp.end) == (date(2025, 12, 1), date(2025, 12, 31))
    assert cmp.ref_month == "2025-12"


def test_mes_completo_materializado_por_date_from_date_to_tambem_usa_mes_anterior():
    # O cliente materializa date_from/date_to na URL (bookmark/reload) sem abrir
    # mao da regra de mes calendario — `resolve_period` preenche `ref_month`.
    period = resolve_period(date_from=date(2026, 6, 1), date_to=date(2026, 6, 30), today=date(2026, 7, 8))
    assert period.ref_month == "2026-06"
    cmp = resolve_compare_period(period, compare=True)
    assert (cmp.start, cmp.end) == (date(2026, 5, 1), date(2026, 5, 31))


def test_periodo_customizado_de_10_dias_compara_com_os_10_dias_imediatamente_anteriores():
    period = resolve_period(date_from=date(2026, 3, 11), date_to=date(2026, 3, 20), today=date(2026, 7, 8))
    assert period.ref_month is None
    cmp = resolve_compare_period(period, compare=True)
    assert (cmp.start, cmp.end) == (date(2026, 3, 1), date(2026, 3, 10))
    assert cmp.days == period.days == 10


def test_mes_parcial_nao_e_tratado_como_mes_fechado():
    # 01-15/06 nao e' o mes inteiro: a regra correta e' a janela deslizante.
    period = resolve_period(date_from=date(2026, 6, 1), date_to=date(2026, 6, 15), today=date(2026, 7, 8))
    assert period.ref_month is None
    cmp = resolve_compare_period(period, compare=True)
    assert (cmp.start, cmp.end) == (date(2026, 5, 17), date(2026, 5, 31))


def test_primitivo_deslizante_permanece_disponivel_e_inalterado():
    # `resolve_previous_period` continua sendo o primitivo puro de janela
    # deslizante — e continua NAO servindo para resolver um mes fechado.
    period = resolve_period(ref_month="2026-06")
    assert resolve_previous_period(period).start == date(2026, 5, 2)


# ---------------------------------------------------------------------------
# A resolucao canonica chega pronta em ResolvedFilters
# ---------------------------------------------------------------------------

def test_filters_query_ja_entrega_a_janela_canonica():
    f = _filters(ref_month="2026-06", compare=True)
    assert (f.compare_period.start, f.compare_period.end) == (date(2026, 5, 1), date(2026, 5, 31))


def test_filters_query_sem_compare_nao_tem_janela():
    assert _filters(ref_month="2026-06", compare=False).compare_period is None


def test_filters_query_default_days_usa_a_mesma_regra():
    dep = filters_query_default_days(30)
    # mes fechado -> mes anterior inteiro
    mensal = dep(
        channels=None, marketplace=None, brands=None, date_from=None, date_to=None,
        ref_month="2026-06", days_back=None, compare=True, db=None,
    )
    assert (mensal.compare_period.start, mensal.compare_period.end) == (date(2026, 5, 1), date(2026, 5, 31))
    # periodo customizado -> janela deslizante
    custom = dep(
        channels=None, marketplace=None, brands=None,
        date_from=date(2026, 3, 11), date_to=date(2026, 3, 20),
        ref_month=None, days_back=None, compare=True, db=None,
    )
    assert (custom.compare_period.start, custom.compare_period.end) == (date(2026, 3, 1), date(2026, 3, 10))


# ---------------------------------------------------------------------------
# Os SEIS endpoints agregados recebem a MESMA janela
# ---------------------------------------------------------------------------

def _overview_window(period, cmp_period) -> tuple[date, date]:
    db = FakeSession()
    res = perf_svc.get_overview(db, "all", 2026, 6, period=period, compare_period=cmp_period)
    return res["compare_date_from"], res["compare_date_to"]


def _brands_window(period, cmp_period) -> tuple[date, date]:
    db = FakeSession()
    res = perf_svc.get_brands(db, "all", 2026, 6, period=period, compare_period=cmp_period)
    return res["compare_date_from"], res["compare_date_to"]


def _quality_window(period, cmp_period) -> tuple[date, date]:
    db = FakeSession()
    res = perf_svc.get_quality(db, "all", 2026, 6, period=period, compare_period=cmp_period)
    return res["compare_date_from"], res["compare_date_to"]


def _canais_window(period, cmp_period) -> tuple[date, date]:
    db = FakeSession()
    res = perf_svc.get_canais(db, "all", 2026, 6, period=period, compare_period=cmp_period)
    return res["compare_date_from"], res["compare_date_to"]


def _financeiro_window(period, cmp_period) -> tuple[date, date]:
    db = FakeSession()
    res = perf_svc.get_financeiro(db, "all", 2026, 6, period=period, compare_period=cmp_period)
    return res["compare_date_from"], res["compare_date_to"]


def _trend_window(period, cmp_period) -> tuple[date, date]:
    db = FakeSession()
    res = perf_svc.get_trend(db, "all", None, period, compare_period=cmp_period)
    return res["comparison"]["date_from"], res["comparison"]["date_to"]


ENDPOINT_WINDOW = {
    "overview": _overview_window,
    "brands": _brands_window,
    "quality": _quality_window,
    "canais": _canais_window,
    "financeiro": _financeiro_window,
    "trend": _trend_window,
}


def test_os_seis_endpoints_reportam_a_mesma_janela_no_filtro_mensal():
    f = _filters(ref_month="2026-06", compare=True)
    esperado = (date(2026, 5, 1), date(2026, 5, 31))
    obtido = {name: fn(f.period, f.compare_period) for name, fn in ENDPOINT_WINDOW.items()}
    assert obtido == {name: esperado for name in ENDPOINT_WINDOW}, obtido


def test_os_seis_endpoints_reportam_a_mesma_janela_no_periodo_customizado():
    f = _filters(date_from=date(2026, 3, 11), date_to=date(2026, 3, 20), compare=True)
    esperado = (date(2026, 3, 1), date(2026, 3, 10))
    obtido = {name: fn(f.period, f.compare_period) for name, fn in ENDPOINT_WINDOW.items()}
    assert obtido == {name: esperado for name in ENDPOINT_WINDOW}, obtido


def test_overview_e_trend_com_o_mesmo_filtro_mensal_reportam_a_mesma_janela():
    # O caso concreto do finding: junho com compare=true. Antes, /overview dizia
    # 01-31/05 e /trend 02-31/05.
    f = _filters(ref_month="2026-06", compare=True)
    assert _overview_window(f.period, f.compare_period) == _trend_window(f.period, f.compare_period)


def test_trend_consulta_de_fato_a_janela_canonica_no_sql():
    # Prova mais forte que o eco: e' o intervalo que chegou ao SQL da serie
    # comparativa.
    f = _filters(ref_month="2026-06", compare=True)
    db = FakeSession()
    perf_svc.get_trend(db, "all", None, f.period, compare_period=f.compare_period)
    janelas = db.windows()
    assert (date(2026, 6, 1), date(2026, 6, 30)) in janelas  # serie atual
    assert (date(2026, 5, 1), date(2026, 5, 31)) in janelas  # serie anterior
    assert (date(2026, 5, 2), date(2026, 5, 31)) not in janelas  # a antiga janela arrastada


def test_correcao_local_de_mes_calendario_e_idempotente():
    # A janela canonica ja chega correta; o tratamento local de
    # overview/brands/quality nao pode deslocar nada ao ser reaplicado.
    f = _filters(ref_month="2026-06", compare=True)
    for fn in (_overview_window, _brands_window, _quality_window):
        assert fn(f.period, f.compare_period) == (date(2026, 5, 1), date(2026, 5, 31))


def test_sem_compare_nenhum_dos_seis_endpoints_reporta_janela():
    f = _filters(ref_month="2026-06", compare=False)
    assert f.compare_period is None
    for name, fn in ENDPOINT_WINDOW.items():
        if name == "trend":
            db = FakeSession()
            res = perf_svc.get_trend(db, "all", None, f.period, compare_period=None)
            assert res["comparison"] is None, name
            continue
        assert fn(f.period, None) == (None, None), name


def test_janela_comparativa_do_trend_usa_a_mesma_granularidade_efetiva():
    # A janela mudou de tamanho (junho 30 dias -> maio 31): a granularidade
    # continua sendo resolvida pelo periodo ATUAL, e as DUAS series usam a mesma
    # expressao de truncamento.
    f = _filters(ref_month="2026-06", compare=True)
    db = FakeSession()
    res = perf_svc.get_trend(db, "all", None, f.period, granularity="week", compare_period=f.compare_period)
    assert res["granularity"] == "week"
    series_sql = [s for s in db.sql if "GROUP BY" in s]
    assert len(series_sql) == 2, "uma consulta por janela"
    assert all("DATE_TRUNC('week', f.date)" in s for s in series_sql)
    # e as duas consultas sao a MESMA consulta, mudando so os parametros
    assert series_sql[0] == series_sql[1]


def test_executive_summary_usa_a_regra_canonica_tambem():
    import app.services.executive_summary_service as es

    src_uses_canonical = "resolve_compare_period(period, compare=True)" in _read_source(es)
    assert src_uses_canonical, "nao pode existir uma segunda definicao de 'periodo anterior'"
    assert "resolve_previous_period" not in _read_source(es)


def _read_source(module) -> str:
    with open(module.__file__, encoding="utf-8") as fh:
        return fh.read()
