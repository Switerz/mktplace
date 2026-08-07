"""
Gate V2-2, Task 1/2 — extensao ADITIVA de `/trend`: granularidade selecionavel
e serie do periodo anterior.

Os dois contratos que estes testes existem para travar:

1. **Compatibilidade retroativa.** Sem `granularity` e sem `compare`, a resposta
   reproduz o comportamento anterior ao gate: mesma regra de grao (diaria ate 92
   dias, mensal acima) e `comparison: None`.
2. **A string do usuario nunca entra no SQL.** A granularidade e' validada contra
   uma allowlist no router (422 fora dela) e a expressao SQL e' escolhida por
   MAPEAMENTO no service — nao por interpolacao da entrada.

Nenhum banco real e' usado: a Session e' um dublê que captura o SQL emitido e
devolve linhas controladas.
"""
from datetime import date

from fastapi.testclient import TestClient

from app.deps.period import EffectivePeriod
from app.main import app
from app.services import performance_service as perf_svc

client = TestClient(app)


def P(start: str, end: str) -> EffectivePeriod:
    return EffectivePeriod(start=date.fromisoformat(start), end=date.fromisoformat(end))


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return None

    def scalars(self):
        return self

    def first(self):
        return None


class FakeSession:
    """Captura cada (sql, params) e devolve as linhas da fila, em ordem."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.calls.append((str(sql), dict(params or {})))
        rows = self.responses.pop(0) if self.responses else []
        return FakeResult(rows)


def row(bucket: date, gmv: float, orders: int) -> dict:
    return {"bucket": bucket, "gmv": gmv, "orders": orders}


# ---------------------------------------------------------------------------
# 1. `auto` preserva a regra vigente
# ---------------------------------------------------------------------------

def test_auto_preserva_day_ate_92_dias_e_month_acima():
    assert perf_svc.resolve_trend_granularity("auto", P("2026-01-01", "2026-04-02")) == "day"   # 92
    assert perf_svc.resolve_trend_granularity("auto", P("2026-01-01", "2026-04-03")) == "month"  # 93
    assert perf_svc.resolve_trend_granularity("auto", P("2026-07-01", "2026-07-31")) == "day"
    assert perf_svc.resolve_trend_granularity("auto", P("2026-01-01", "2026-12-31")) == "month"


def test_granularidades_explicitas_sao_respeitadas_em_qualquer_intervalo():
    longo = P("2026-01-01", "2026-12-31")
    curto = P("2026-07-01", "2026-07-07")
    for g in ("day", "week", "month"):
        assert perf_svc.resolve_trend_granularity(g, longo) == g
        assert perf_svc.resolve_trend_granularity(g, curto) == g


# ---------------------------------------------------------------------------
# 2. SQL escolhido por allowlist, nunca interpolado
# ---------------------------------------------------------------------------

def test_expressao_sql_vem_de_mapeamento_e_cobre_as_tres_granularidades_efetivas():
    assert set(perf_svc._TREND_TRUNC_SQL) == {"day", "week", "month"}
    # `auto` nao tem expressao: ele SEMPRE resolve para uma das tres antes do SQL.
    assert "auto" not in perf_svc._TREND_TRUNC_SQL
    assert perf_svc._TREND_TRUNC_SQL["week"] == "DATE_TRUNC('week', f.date)::date"
    assert perf_svc._TREND_TRUNC_SQL["month"] == "DATE_TRUNC('month', f.date)::date"
    assert perf_svc._TREND_TRUNC_SQL["day"] == "f.date"


def test_entrada_maliciosa_nunca_chega_ao_sql():
    """Uma granularidade invalida nem alcanca o service — e, se alcancasse, o
    mapeamento levantaria KeyError em vez de concatenar a string."""
    db = FakeSession([[]])
    try:
        perf_svc.get_trend(db, "all", None, P("2026-07-01", "2026-07-31"), granularity="day'; DROP TABLE x --")
    except KeyError:
        pass  # comportamento desejado: nao existe caminho de interpolacao
    else:
        raise AssertionError("granularidade fora da allowlist deveria falhar, nunca ser interpolada")
    # e nada foi emitido com o texto malicioso
    assert all("DROP TABLE" not in sql for sql, _ in db.calls)


def test_sql_da_semana_usa_date_trunc_week():
    db = FakeSession([[row(date(2026, 6, 29), 10.0, 1)]])
    perf_svc.get_trend(db, "all", None, P("2026-06-29", "2026-07-26"), granularity="week")
    trend_sql = db.calls[0][0]
    assert "DATE_TRUNC('week', f.date)::date" in trend_sql
    # e o GROUP BY/ORDER BY usam a MESMA expressao, senao os buckets nao fecham
    assert trend_sql.count("DATE_TRUNC('week', f.date)::date") >= 3


# ---------------------------------------------------------------------------
# 3. Semana comeca na segunda-feira
# ---------------------------------------------------------------------------

def test_semana_iso_comeca_na_segunda_feira():
    # `DATE_TRUNC('week')` do Postgres e' ISO-8601 (segunda). O contrato aqui e'
    # que o RoTULO seja derivado do dia de inicio recebido, sem reinterpretacao.
    segunda = date(2026, 6, 29)
    assert segunda.weekday() == 0, "fixture precisa ser uma segunda-feira"
    assert perf_svc._trend_label(segunda, "week") == "Sem. 29/06"


def test_label_por_granularidade_sem_locale_nem_timezone():
    assert perf_svc._trend_label(date(2026, 7, 5), "day") == "05/07"
    assert perf_svc._trend_label(date(2026, 7, 1), "month") == "Jul/26"
    assert perf_svc._trend_label(date(2026, 6, 29), "week").startswith("Sem. ")


# ---------------------------------------------------------------------------
# 4. Comparacao: ausente, presente e vazia
# ---------------------------------------------------------------------------

def test_compare_ausente_devolve_comparison_none_e_uma_unica_query_de_serie():
    db = FakeSession([[row(date(2026, 7, 1), 100.0, 2)]])
    out = perf_svc.get_trend(db, "all", None, P("2026-07-01", "2026-07-31"))
    assert out["comparison"] is None
    assert out["granularity"] == "day"
    # apenas a serie atual + o refreshed_at; nenhuma segunda agregacao
    series_calls = [c for c in db.calls if "GROUP BY" in c[0]]
    assert len(series_calls) == 1


def test_compare_presente_usa_a_janela_anterior_exata_e_a_mesma_granularidade():
    atual = P("2026-07-01", "2026-07-31")
    anterior = P("2026-06-01", "2026-06-30")
    db = FakeSession([
        [row(date(2026, 7, 1), 100.0, 2)],
        [row(date(2026, 6, 1), 90.0, 1)],
    ])
    out = perf_svc.get_trend(db, "all", None, atual, granularity="week", compare_period=anterior)

    assert out["comparison"] is not None
    assert out["comparison"]["date_from"] == anterior.start
    assert out["comparison"]["date_to"] == anterior.end
    # duas agregacoes, com a MESMA expressao de bucket
    series_calls = [c for c in db.calls if "GROUP BY" in c[0]]
    assert len(series_calls) == 2
    assert series_calls[0][0] == series_calls[1][0], "granularidade tem de ser identica nos dois periodos"
    # e cada uma com a sua propria janela
    assert series_calls[0][1]["start"] == atual.start and series_calls[0][1]["end"] == atual.end
    assert series_calls[1][1]["start"] == anterior.start and series_calls[1][1]["end"] == anterior.end


def test_comparacao_solicitada_sem_linhas_e_objeto_com_data_vazia():
    db = FakeSession([[row(date(2026, 7, 1), 100.0, 2)], []])
    out = perf_svc.get_trend(
        db, "all", None, P("2026-07-01", "2026-07-31"), compare_period=P("2026-06-01", "2026-06-30")
    )
    # "sem registros" (objeto com data vazia) e' DIFERENTE de "nao solicitada" (None)
    assert out["comparison"] is not None
    assert out["comparison"]["data"] == []


def test_mesmos_filtros_de_canal_e_marca_nos_dois_periodos():
    db = FakeSession([
        [row(date(2026, 7, 1), 1.0, 1)],
        [row(date(2026, 6, 1), 1.0, 1)],
    ])
    perf_svc.get_trend(
        db, "tiktok,ml", ["barbours", "kokeshi"], P("2026-07-01", "2026-07-31"),
        compare_period=P("2026-06-01", "2026-06-30"),
    )
    series_calls = [c for c in db.calls if "GROUP BY" in c[0]]
    a, b = series_calls[0][1], series_calls[1][1]
    assert a["mkt_ids"] == b["mkt_ids"], "canais tem de ser identicos"
    brand_keys = [k for k in a if "brand" in k.lower()]
    for k in brand_keys:
        assert a[k] == b[k], f"filtro de marca divergente em {k}"


# ---------------------------------------------------------------------------
# 5. Zero explicito e ausencia
# ---------------------------------------------------------------------------

def test_zero_explicito_e_preservado_como_zero():
    db = FakeSession([[row(date(2026, 7, 1), 0.0, 0)]])
    out = perf_svc.get_trend(db, "all", None, P("2026-07-01", "2026-07-31"))
    assert out["data"][0]["gmv"] == 0.0
    assert out["data"][0]["orders"] == 0


def test_bucket_ausente_nao_e_fabricado():
    """A serie devolve SOMENTE os buckets que a fonte trouxe. O preenchimento de
    lacunas nunca acontece aqui — no cliente, bucket ausente e' `null`."""
    db = FakeSession([[row(date(2026, 7, 1), 10.0, 1), row(date(2026, 7, 3), 30.0, 3)]])
    out = perf_svc.get_trend(db, "all", None, P("2026-07-01", "2026-07-31"))
    assert [p["date"] for p in out["data"]] == ["2026-07-01", "2026-07-03"]


# ---------------------------------------------------------------------------
# 6. Borda HTTP: allowlist e 422
# ---------------------------------------------------------------------------

def test_granularidade_invalida_retorna_422():
    for invalida in ("hour", "yearly", "DAY;", "", "auto2"):
        resp = client.get(f"/api/v1/performance/trend?granularity={invalida}")
        assert resp.status_code == 422, f"{invalida!r} deveria ser 422, veio {resp.status_code}"
        assert "granularity" in resp.text


def test_allowlist_declara_exatamente_as_quatro_opcoes():
    assert perf_svc.TREND_GRANULARITIES == ("auto", "day", "week", "month")


# ---------------------------------------------------------------------------
# 7. Nenhuma mudanca nos endpoints existentes
# ---------------------------------------------------------------------------

def test_get_trend_mantem_a_assinatura_retrocompativel():
    """Chamado com os 4 argumentos posicionais de antes do gate, o service
    funciona e devolve `comparison: None`."""
    db = FakeSession([[row(date(2026, 7, 1), 5.0, 1)]])
    out = perf_svc.get_trend(db, "all", None, P("2026-07-01", "2026-07-31"))
    for campo in ("granularity", "data", "date_from", "date_to", "filters", "refreshed_at"):
        assert campo in out, f"campo {campo} do contrato antigo desapareceu"
    assert out["comparison"] is None


def test_outros_endpoints_nao_ganharam_granularity():
    """A extensao e' restrita a `/trend`: nenhuma outra rota passou a aceitar o
    parametro, para nao criar contrato acidental."""
    import inspect

    from app.routers import performance as router_mod

    for name in ("overview", "brands", "canais", "financeiro", "quality", "pedidos", "daily"):
        fn = getattr(router_mod, name, None)
        if fn is None:
            continue
        assert "granularity" not in inspect.signature(fn).parameters, f"{name} nao deve aceitar granularity"
    assert "granularity" in inspect.signature(router_mod.trend).parameters
