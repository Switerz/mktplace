"""
Testes do contrato de filtros globais (canal, marca, periodo, comparacao)
introduzido em app.deps.period / app.deps.filters e aplicado aos 6
endpoints agregados (overview, brands, canais, financeiro, quality, pedidos).

Cobre: resolucao pura de periodo (precedencia date_from/date_to > ref_month >
default, validacoes de intervalo), validacao de marcas contra `dim_loja` via
Session falsa, validacao 422 na borda HTTP, e a integracao com
performance_service (brand_keys chega parametrizado, refreshed_at so aparece
quando o novo contrato de periodo e usado).
"""
import datetime as datetime_module
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.deps import period as period_mod
from app.deps import filters as filters_mod
from app.deps.period import EffectivePeriod, resolve_period, resolve_previous_period, today_brt
from app.deps.filters import resolve_brands, get_scope_brand_keys, filters_query, filters_query_default_days
from app.main import app
from app.services import performance_service as perf_svc

client = TestClient(app)


# ---------------------------------------------------------------------------
# resolve_period — precedencia e validacoes puras
# ---------------------------------------------------------------------------

def test_resolve_period_date_from_date_to_tem_precedencia_sobre_ref_month():
    period = resolve_period(ref_month="2026-01", date_from=date(2026, 3, 1), date_to=date(2026, 3, 10))
    assert period.start == date(2026, 3, 1)
    assert period.end == date(2026, 3, 10)
    assert period.ref_month is None  # nunca mistura: intervalo personalizado nao carrega rotulo de mes


def test_resolve_period_ref_month_quando_sem_datas_explicitas():
    period = resolve_period(ref_month="2026-06")
    assert period.start == date(2026, 6, 1)
    assert period.end == date(2026, 6, 30)
    assert period.ref_month == "2026-06"


def test_resolve_period_default_ultimos_n_dias_quando_nada_informado():
    period = resolve_period(default_days=30)
    assert period.days == 30
    assert period.end == date.today()
    assert period.ref_month is None


def test_resolve_period_so_date_from_sem_date_to_e_422():
    with pytest.raises(HTTPException) as exc:
        resolve_period(date_from=date(2026, 1, 1))
    assert exc.value.status_code == 422


def test_resolve_period_so_date_to_sem_date_from_e_422():
    with pytest.raises(HTTPException) as exc:
        resolve_period(date_to=date(2026, 1, 1))
    assert exc.value.status_code == 422


def test_resolve_period_date_from_maior_que_date_to_e_422():
    with pytest.raises(HTTPException) as exc:
        resolve_period(date_from=date(2026, 5, 10), date_to=date(2026, 5, 1))
    assert exc.value.status_code == 422


def test_resolve_period_intervalo_maior_que_366_dias_e_422():
    with pytest.raises(HTTPException) as exc:
        resolve_period(date_from=date(2024, 1, 1), date_to=date(2026, 6, 1))
    assert exc.value.status_code == 422


def test_resolve_period_intervalo_de_366_dias_e_aceito():
    period = resolve_period(date_from=date(2025, 1, 1), date_to=date(2025, 12, 32 - 1))
    assert period.days <= 366


def test_resolve_period_date_to_no_futuro_alem_de_amanha_e_422():
    with pytest.raises(HTTPException) as exc:
        resolve_period(date_from=date.today(), date_to=date.today() + timedelta(days=10))
    assert exc.value.status_code == 422


def test_resolve_period_ref_month_formato_invalido_e_422():
    with pytest.raises(HTTPException) as exc:
        resolve_period(ref_month="2026/06")
    assert exc.value.status_code == 422


def test_resolve_period_date_to_igual_a_hoje_e_aceito():
    fixed_today = date(2026, 7, 8)
    period = resolve_period(date_from=date(2026, 7, 1), date_to=fixed_today, today=fixed_today)
    assert period.end == fixed_today


def test_resolve_period_date_to_amanha_e_rejeitado():
    # Bug real corrigido nesta rodada: `date_to > today + 1 dia` permitia
    # amanha passar. A regra correta e `date_to > today` (nenhuma data
    # futura, nem sequer amanha).
    fixed_today = date(2026, 7, 8)
    amanha = fixed_today + timedelta(days=1)
    with pytest.raises(HTTPException) as exc:
        resolve_period(date_from=date(2026, 7, 1), date_to=amanha, today=fixed_today)
    assert exc.value.status_code == 422


def test_resolve_period_usa_today_injetado_nao_o_relogio_real():
    fixed_today = date(2020, 1, 15)
    period = resolve_period(default_days=30, today=fixed_today)
    assert period.end == fixed_today
    assert period.start == fixed_today - timedelta(days=29)


# ---------------------------------------------------------------------------
# resolve_period — backfill de ref_month quando date_from/date_to explicitos
# cobrem exatamente um mes calendario completo (ex: materializados na URL
# pelo frontend a partir do preset "mes_anterior"). Sem isso, get_overview/
# get_brands/get_quality nunca reconheceriam o intervalo como "mes calendario"
# e o MoM automatico ja auditado deixaria de aparecer so por causa do formato
# do parametro usado (date_from/date_to vs ref_month) — ver docs/DECISIONS.
# ---------------------------------------------------------------------------

def test_resolve_period_date_from_date_to_mes_completo_preenche_ref_month():
    period = resolve_period(date_from=date(2026, 6, 1), date_to=date(2026, 6, 30), today=date(2026, 7, 8))
    assert period.ref_month == "2026-06"


def test_resolve_period_date_from_date_to_mes_completo_fevereiro_bissexto():
    period = resolve_period(date_from=date(2024, 2, 1), date_to=date(2024, 2, 29), today=date(2024, 3, 1))
    assert period.ref_month == "2024-02"


def test_resolve_period_date_from_date_to_parcial_nao_preenche_ref_month():
    # Mesmo comecando no dia 1, nao cobre o mes inteiro (junho tem 30 dias) —
    # nao pode ser confundido com "mes calendario completo".
    period = resolve_period(date_from=date(2026, 6, 1), date_to=date(2026, 6, 15), today=date(2026, 7, 8))
    assert period.ref_month is None


def test_resolve_period_date_from_nao_comeca_no_dia_1_nao_preenche_ref_month():
    period = resolve_period(date_from=date(2026, 6, 2), date_to=date(2026, 6, 30), today=date(2026, 7, 8))
    assert period.ref_month is None


def test_today_brt_usa_fuso_america_sao_paulo_nao_utc(monkeypatch):
    # As 02:30 UTC de 15/06 ja sao 23:30 do dia 14 em America/Sao_Paulo
    # (UTC-3, sem horario de verao). Um relogio que lesse a data em UTC (ou
    # no fuso local do servidor, se for UTC) erraria por um dia — exatamente
    # a "virada UTC/Brasilia" pedida na revisao.
    fixed_utc = datetime_module.datetime(2026, 6, 15, 2, 30, tzinfo=datetime_module.timezone.utc)

    class FixedDateTime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz is not None else fixed_utc

    monkeypatch.setattr(period_mod, "datetime", FixedDateTime)
    assert period_mod.today_brt() == date(2026, 6, 14)


def test_resolve_period_default_mode_previous_month():
    fixed_today = date(2026, 7, 8)
    period = resolve_period(default_mode="previous_month", today=fixed_today)
    assert period.ref_month == "2026-06"
    assert period.start == date(2026, 6, 1)
    assert period.end == date(2026, 6, 30)


def test_resolve_period_default_mode_previous_month_em_janeiro_vira_dezembro_anterior():
    fixed_today = date(2026, 1, 15)
    period = resolve_period(default_mode="previous_month", today=fixed_today)
    assert period.ref_month == "2025-12"
    assert period.start == date(2025, 12, 1)
    assert period.end == date(2025, 12, 31)


def test_resolve_period_default_mode_days_ignora_previous_month():
    # default_mode="days" (usado por Pedidos) nunca deve resolver para mes
    # calendario, mesmo com o mesmo `today` do teste anterior.
    fixed_today = date(2026, 7, 8)
    period = resolve_period(default_mode="days", default_days=30, today=fixed_today)
    assert period.ref_month is None
    assert period.days == 30
    assert period.end == fixed_today


def test_filters_query_sem_nenhum_parametro_usa_mes_anterior_nao_30_dias(monkeypatch):
    # Chamada legada (equivalente a bater direto na API sem nenhum filtro
    # novo nem ref_month) — precisa preservar o default historico dos 5
    # endpoints antes mensais (_parse_month(None) no router antigo, mesmo
    # comportamento documentado no comentario de refMonth() no frontend:
    # "mes anterior como referencia padrao"), nunca cair para 30 dias
    # corridos (que e o default especifico e correto so de Pedidos).
    monkeypatch.setattr(filters_mod, "today_brt", lambda: date(2026, 7, 8))
    resolved = filters_query(
        channels=None, marketplace=None, brands=None,
        date_from=None, date_to=None, ref_month=None, compare=False, db=None,
    )
    assert resolved.period.ref_month == "2026-06"
    assert resolved.period.start == date(2026, 6, 1)
    assert resolved.period.end == date(2026, 6, 30)


def test_filters_query_default_days_sem_nenhum_parametro_usa_30_dias(monkeypatch):
    # Pedidos: comportamento legado e days_back=30 corridos, nao mes.
    monkeypatch.setattr(filters_mod, "today_brt", lambda: date(2026, 7, 8))
    dep = filters_query_default_days(30)
    resolved = dep(
        channels=None, marketplace=None, brands=None,
        date_from=None, date_to=None, ref_month=None, days_back=None,
        compare=False, db=None,
    )
    assert resolved.period.ref_month is None
    assert resolved.period.days == 30
    assert resolved.period.end == date(2026, 7, 8)


def test_filters_query_rejeita_date_to_amanha(monkeypatch):
    monkeypatch.setattr(filters_mod, "today_brt", lambda: date(2026, 7, 8))
    with pytest.raises(HTTPException) as exc:
        filters_query(
            channels=None, marketplace=None, brands=None,
            date_from=date(2026, 7, 1), date_to=date(2026, 7, 9),
            ref_month=None, compare=False, db=None,
        )
    assert exc.value.status_code == 422


def test_resolve_previous_period_mesma_duracao_imediatamente_anterior():
    period = EffectivePeriod(start=date(2026, 3, 11), end=date(2026, 3, 20))  # 10 dias
    prev = resolve_previous_period(period)
    assert prev.end == date(2026, 3, 10)
    assert prev.start == date(2026, 3, 1)
    assert prev.days == period.days


# ---------------------------------------------------------------------------
# resolve_brands / get_scope_brand_keys — validacao contra dim_loja
# ---------------------------------------------------------------------------

class _FakeScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class FakeBrandSession:
    def __init__(self, brand_keys):
        self._brand_keys = brand_keys

    def execute(self, stmt, params=None):
        return _FakeScalarsResult(self._brand_keys)


def test_resolve_brands_sem_parametro_retorna_none():
    assert resolve_brands(None, FakeBrandSession(["barbours"])) is None
    assert resolve_brands("", FakeBrandSession(["barbours"])) is None


def test_resolve_brands_valida_e_ordena():
    db = FakeBrandSession(["barbours", "kokeshi", "lescent"])
    assert resolve_brands("kokeshi,barbours", db) == ["barbours", "kokeshi"]


def test_resolve_brands_marca_invalida_e_422():
    db = FakeBrandSession(["barbours", "kokeshi"])
    with pytest.raises(HTTPException) as exc:
        resolve_brands("barbours,inexistente", db)
    assert exc.value.status_code == 422


def test_resolve_brands_string_vazia_apos_split_e_422():
    with pytest.raises(HTTPException) as exc:
        resolve_brands(" , , ", FakeBrandSession(["barbours"]))
    assert exc.value.status_code == 422


def test_resolve_brands_sem_db_pula_validacao():
    # Sem conexao (engine indisponivel) o proprio endpoint ja vai falhar com
    # 503 antes de usar isso — nao ha por que barrar com 422 aqui.
    assert resolve_brands("qualquer_coisa", None) == ["qualquer_coisa"]


def test_get_scope_brand_keys_retorna_set():
    db = FakeBrandSession(["barbours", "kokeshi"])
    assert get_scope_brand_keys(db) == {"barbours", "kokeshi"}


# ---------------------------------------------------------------------------
# Validacao 422 na borda HTTP (sem banco real) — mesmo padrao de
# test_marketplace_param.py: erro de validacao vira 422; parametro valido sem
# banco vira 503 (nao 422), confirmando que passou pela validacao.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/v1/performance/overview", "/api/v1/performance/canais",
                                  "/api/v1/performance/financeiro", "/api/v1/performance/quality",
                                  "/api/v1/performance/brands"])
def test_date_from_sem_date_to_retorna_422(path):
    resp = client.get(path, params={"date_from": "2026-01-01"})
    assert resp.status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/performance/overview", "/api/v1/performance/pedidos"])
def test_date_from_maior_que_date_to_retorna_422(path):
    resp = client.get(path, params={"date_from": "2026-05-10", "date_to": "2026-05-01"})
    assert resp.status_code == 422


def test_overview_channels_invalido_retorna_422():
    resp = client.get("/api/v1/performance/overview", params={"channels": "tiktok,invalido"})
    assert resp.status_code == 422


def test_overview_channels_alias_de_marketplace_funciona_sem_banco():
    resp = client.get("/api/v1/performance/overview", params={"channels": "tiktok,ml"})
    assert resp.status_code != 422


def test_overview_date_from_date_to_validos_sem_banco_retorna_503_nao_422():
    resp = client.get(
        "/api/v1/performance/overview",
        params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
    )
    assert resp.status_code != 422


def test_overview_brands_param_sem_banco_nao_e_rejeitado_na_borda():
    # Sem banco, resolve_brands nao valida contra dim_loja — a rejeicao (se
    # houver) so aconteceria dentro do service, que nunca roda porque
    # _require_db falha antes com 503.
    resp = client.get("/api/v1/performance/overview", params={"brands": "barbours,kokeshi"})
    assert resp.status_code != 422


def test_pedidos_days_back_ate_366_e_aceito_bound_relaxado():
    # Bound antigo era le=90; o novo contrato compartilhado usa o mesmo
    # MAX_RANGE_DAYS (366) de date_from/date_to para nao ter dois limites
    # divergentes para o mesmo conceito de "janela de dias".
    resp = client.get("/api/v1/performance/pedidos", params={"days_back": 200})
    assert resp.status_code != 422


def test_pedidos_date_from_date_to_aceito_como_alternativa_a_days_back():
    resp = client.get(
        "/api/v1/performance/pedidos",
        params={"date_from": "2026-01-01", "date_to": "2026-01-10"},
    )
    assert resp.status_code != 422


def test_brand_detail_channels_invalido_retorna_422():
    resp = client.get(
        "/api/v1/performance/brand-detail",
        params={"brand": "barbours", "channels": "tiktok,invalido"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("channels", ["ml", "shopee", "ml,shopee"])
def test_brand_detail_rejeita_channels_que_excluem_tiktok(channels):
    # Fonte e TikTok-only (gold.tiktok_brand_daily) — pedir um canal sem
    # TikTok precisa ser rejeitado explicitamente, nunca retornar dados de
    # TikTok como se o filtro tivesse sido respeitado (aceito e ignorado
    # silenciosamente, o comportamento antigo que foi corrigido aqui).
    resp = client.get(
        "/api/v1/performance/brand-detail",
        params={"brand": "barbours", "channels": channels},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("channels", ["tiktok", "all", "tiktok,ml"])
def test_brand_detail_aceita_channels_que_incluem_tiktok(channels):
    # raise_server_exceptions=False: sem DATAMART_DATABASE_URL neste
    # ambiente de teste, a chamada passa da validacao mas falha adiante
    # (gold_service exige Data Mart) — o que importa aqui e confirmar que
    # NAO foi rejeitada como 422 na validacao de channels.
    local_client = TestClient(app, raise_server_exceptions=False)
    resp = local_client.get(
        "/api/v1/performance/brand-detail",
        params={"brand": "barbours", "channels": channels},
    )
    assert resp.status_code != 422


def test_daily_date_from_sem_date_to_retorna_422():
    resp = client.get(
        "/api/v1/performance/daily",
        params={"brand": "barbours", "date_from": "2026-01-01"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integracao com performance_service — Session falsa, sem banco real
# ---------------------------------------------------------------------------

class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeMappingSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.captured_params = []

    def execute(self, stmt, params=None):
        self.captured_params.append(params)
        rows = self._responses.pop(0)
        return _FakeMappingsResult(rows)


def test_get_overview_brand_keys_chega_parametrizado_no_sql():
    cur_rows = [{"marketplace_id": 1, "gmv": 100, "orders": 1, "canceled_orders": 0,
                 "unique_buyers": 1, "ad_spend": 0, "ad_revenue": 0}]
    db = FakeMappingSession([cur_rows, []])  # cur, prev (period=None -> legado, sem refreshed_at)
    perf_svc.get_overview(db, "tiktok", 2026, 6, brand_keys=["barbours", "kokeshi"])
    assert db.captured_params[0]["brand_keys"] == ["barbours", "kokeshi"]
    assert db.captured_params[1]["brand_keys"] == ["barbours", "kokeshi"]


def test_get_overview_sem_period_nao_calcula_refreshed_at():
    cur_rows = [{"marketplace_id": 1, "gmv": 100, "orders": 1, "canceled_orders": 0,
                 "unique_buyers": 1, "ad_spend": 0, "ad_revenue": 0}]
    db = FakeMappingSession([cur_rows, []])
    result = perf_svc.get_overview(db, "tiktok", 2026, 6)
    assert result["refreshed_at"] is None
    assert result["ref_month"] == "2026-06"


def test_get_overview_com_period_customizado_sem_compare_pula_periodo_anterior():
    period = EffectivePeriod(start=date(2026, 3, 1), end=date(2026, 3, 15))
    cur_rows = [{"marketplace_id": 1, "gmv": 500, "orders": 5, "canceled_orders": 0,
                 "unique_buyers": 3, "ad_spend": 0, "ad_revenue": 0}]
    refreshed_row = [{"refreshed_at": None}]
    # 1 chamada para o periodo atual + 1 para refreshed_at (sem periodo anterior)
    db = FakeMappingSession([cur_rows, refreshed_row])
    result = perf_svc.get_overview(db, "tiktok", 2026, 3, period=period)
    assert result["date_from"] == date(2026, 3, 1)
    assert result["date_to"] == date(2026, 3, 15)
    assert result["compare_date_from"] is None
    assert result["ref_month"] is None
    assert result["gmv_mom_pct"] is None


def test_get_overview_com_compare_period_calcula_periodo_anterior_e_mom():
    period = EffectivePeriod(start=date(2026, 3, 11), end=date(2026, 3, 20))
    compare_period = resolve_previous_period(period)
    cur_rows = [{"marketplace_id": 1, "gmv": 1000, "orders": 10, "canceled_orders": 0,
                 "unique_buyers": 5, "ad_spend": 0, "ad_revenue": 0}]
    prev_rows = [{"marketplace_id": 1, "gmv": 500, "orders": 5, "canceled_orders": 0,
                  "unique_buyers": 3, "ad_spend": 0, "ad_revenue": 0}]
    refreshed_row = [{"refreshed_at": None}]
    db = FakeMappingSession([cur_rows, prev_rows, refreshed_row])
    result = perf_svc.get_overview(
        db, "tiktok", 2026, 3, period=period, compare_period=compare_period,
    )
    assert result["compare_date_from"] == compare_period.start
    assert result["compare_date_to"] == compare_period.end
    assert result["gmv_mom_pct"] == 100.0  # 1000 vs 500


# ---------------------------------------------------------------------------
# get_overview — compare=true sobre um mes calendario completo deve usar o
# mes calendario anterior de verdade (nao a janela de N dias corridos de
# compare_period), para nao divergir do MoM ja auditado quando os meses
# vizinhos tem contagens de dias diferentes. E o "toggle" (compare) que
# decide se o MoM aparece: sem compare=true, mesmo um mes calendario
# completo nao calcula periodo anterior.
# ---------------------------------------------------------------------------

def test_get_overview_mes_completo_via_date_from_date_to_sem_compare_nao_calcula_mom():
    # date_from/date_to cobrem junho inteiro (backfill de ref_month), mas
    # compare nao foi pedido -> MoM deve desaparecer (toggle desligado).
    period = resolve_period(date_from=date(2026, 6, 1), date_to=date(2026, 6, 30), today=date(2026, 7, 8))
    cur_rows = [{"marketplace_id": 1, "gmv": 900, "orders": 9, "canceled_orders": 0,
                 "unique_buyers": 4, "ad_spend": 0, "ad_revenue": 0}]
    refreshed_row = [{"refreshed_at": None}]
    db = FakeMappingSession([cur_rows, refreshed_row])
    result = perf_svc.get_overview(db, "tiktok", 2026, 6, period=period)
    assert result["compare_date_from"] is None
    assert result["gmv_mom_pct"] is None


def test_get_overview_mes_completo_via_date_from_date_to_com_compare_usa_mes_calendario_anterior():
    # Maio tem 31 dias, junho tem 30 — a janela de N dias corridos
    # (resolve_previous_period) daria 02/05 a 31/05 (30 dias). O mes
    # calendario anterior de verdade e 01/05 a 31/05 (31 dias). O MoM deve
    # usar o segundo, byte-a-byte igual ao que ref_month=2026-06 produziria.
    period = resolve_period(date_from=date(2026, 6, 1), date_to=date(2026, 6, 30), today=date(2026, 7, 8))
    compare_period = resolve_previous_period(period)
    assert compare_period.start == date(2026, 5, 2)  # confirma que a janela arrastada NAO e o mes cheio

    cur_rows = [{"marketplace_id": 1, "gmv": 1000, "orders": 10, "canceled_orders": 0,
                 "unique_buyers": 5, "ad_spend": 0, "ad_revenue": 0}]
    prev_rows = [{"marketplace_id": 1, "gmv": 500, "orders": 5, "canceled_orders": 0,
                  "unique_buyers": 3, "ad_spend": 0, "ad_revenue": 0}]
    refreshed_row = [{"refreshed_at": None}]
    db = FakeMappingSession([cur_rows, prev_rows, refreshed_row])
    result = perf_svc.get_overview(
        db, "tiktok", 2026, 6, period=period, compare_period=compare_period,
    )
    assert result["compare_date_from"] == date(2026, 5, 1)
    assert result["compare_date_to"] == date(2026, 5, 31)
    assert result["gmv_mom_pct"] == 100.0  # 1000 vs 500


def test_get_overview_mes_completo_via_ref_month_e_via_date_from_date_to_dao_o_mesmo_mom():
    # O numero de MoM nao pode depender de qual formato de parametro o
    # cliente usou para pedir o mesmo mes — ref_month=2026-06 e
    # date_from=2026-06-01&date_to=2026-06-30 tem que reconciliar.
    cur_rows = [{"marketplace_id": 1, "gmv": 1000, "orders": 10, "canceled_orders": 0,
                 "unique_buyers": 5, "ad_spend": 0, "ad_revenue": 0}]
    prev_rows = [{"marketplace_id": 1, "gmv": 500, "orders": 5, "canceled_orders": 0,
                  "unique_buyers": 3, "ad_spend": 0, "ad_revenue": 0}]
    refreshed_row = [{"refreshed_at": None}]

    period_ref_month = resolve_period(ref_month="2026-06", today=date(2026, 7, 8))
    db1 = FakeMappingSession([cur_rows, prev_rows, refreshed_row])
    result_ref_month = perf_svc.get_overview(
        db1, "tiktok", 2026, 6, period=period_ref_month,
        compare_period=resolve_previous_period(period_ref_month),
    )

    period_dates = resolve_period(date_from=date(2026, 6, 1), date_to=date(2026, 6, 30), today=date(2026, 7, 8))
    db2 = FakeMappingSession([cur_rows, prev_rows, refreshed_row])
    result_dates = perf_svc.get_overview(
        db2, "tiktok", 2026, 6, period=period_dates,
        compare_period=resolve_previous_period(period_dates),
    )

    assert result_ref_month["compare_date_from"] == result_dates["compare_date_from"]
    assert result_ref_month["compare_date_to"] == result_dates["compare_date_to"]
    assert result_ref_month["gmv_mom_pct"] == result_dates["gmv_mom_pct"]


def test_get_overview_mes_completo_com_compare_vira_dezembro_do_ano_anterior():
    # Virada janeiro -> dezembro do ano anterior, com ambos os meses
    # completos (31 dias) — contagem de dias igual, mas o ano muda.
    period = resolve_period(date_from=date(2026, 1, 1), date_to=date(2026, 1, 31), today=date(2026, 2, 1))
    compare_period = resolve_previous_period(period)
    cur_rows = [{"marketplace_id": 1, "gmv": 1000, "orders": 10, "canceled_orders": 0,
                 "unique_buyers": 5, "ad_spend": 0, "ad_revenue": 0}]
    prev_rows = [{"marketplace_id": 1, "gmv": 800, "orders": 8, "canceled_orders": 0,
                  "unique_buyers": 4, "ad_spend": 0, "ad_revenue": 0}]
    refreshed_row = [{"refreshed_at": None}]
    db = FakeMappingSession([cur_rows, prev_rows, refreshed_row])
    result = perf_svc.get_overview(
        db, "tiktok", 2026, 1, period=period, compare_period=compare_period,
    )
    assert result["compare_date_from"] == date(2025, 12, 1)
    assert result["compare_date_to"] == date(2025, 12, 31)


def test_get_overview_periodo_customizado_parcial_com_compare_continua_usando_janela_corrida():
    # Regressao: intervalo customizado (nao um mes calendario completo) com
    # compare=true continua usando a janela de N dias corridos — comportamento
    # novo desta feature, nao deve ser afetado pelo backfill de ref_month.
    period = EffectivePeriod(start=date(2026, 3, 11), end=date(2026, 3, 20))
    compare_period = resolve_previous_period(period)
    assert period.ref_month is None
    cur_rows = [{"marketplace_id": 1, "gmv": 1000, "orders": 10, "canceled_orders": 0,
                 "unique_buyers": 5, "ad_spend": 0, "ad_revenue": 0}]
    prev_rows = [{"marketplace_id": 1, "gmv": 500, "orders": 5, "canceled_orders": 0,
                  "unique_buyers": 3, "ad_spend": 0, "ad_revenue": 0}]
    refreshed_row = [{"refreshed_at": None}]
    db = FakeMappingSession([cur_rows, prev_rows, refreshed_row])
    result = perf_svc.get_overview(
        db, "tiktok", 2026, 3, period=period, compare_period=compare_period,
    )
    assert result["compare_date_from"] == compare_period.start  # janela corrida, nao mes calendario
    assert result["compare_date_from"] != date(2026, 2, 1)


def test_get_pedidos_intersecta_canal_shopee_isolado_com_zero_honesto():
    # Pedidos so cobre TikTok/ML na fonte atual; pedir so Shopee deve dar
    # mkt_ids vazio (zero honesto), nao TK+ML.
    db = FakeMappingSession([[], [], []])
    result = perf_svc.get_pedidos(db, 30, marketplace="shopee")
    assert result["kpis"]["total_orders"] == 0
    assert db.captured_params[0]["mkt_ids"] == []


# ---------------------------------------------------------------------------
# get_trend — usado pelo grafico de tendencia do Gerencial (substitui
# fetchMonthly, que ignorava marca/periodo). A soma da serie precisa
# reconciliar com o GMV de /overview no mesmo escopo — ambos usam a mesma
# WHERE clause (mkt_ids + brand_keys + intervalo de datas) sobre
# fact_marketplace_daily_performance, entao a reconciliacao e estrutural,
# nao coincidencia.
# ---------------------------------------------------------------------------

def test_get_trend_granularidade_diaria_ate_92_dias():
    period = EffectivePeriod(start=date(2026, 5, 1), end=date(2026, 5, 3))
    trend_rows = [
        {"bucket": date(2026, 5, 1), "gmv": 100, "orders": 1},
        {"bucket": date(2026, 5, 2), "gmv": 200, "orders": 2},
        {"bucket": date(2026, 5, 3), "gmv": 300, "orders": 3},
    ]
    db = FakeMappingSession([trend_rows, [{"refreshed_at": None}]])
    result = perf_svc.get_trend(db, "tiktok", None, period)
    assert result["granularity"] == "day"
    assert [p["date"] for p in result["data"]] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert sum(p["gmv"] for p in result["data"]) == 600


def test_get_trend_granularidade_mensal_acima_de_92_dias():
    period = EffectivePeriod(start=date(2026, 1, 1), end=date(2026, 6, 30))  # 181 dias
    trend_rows = [
        {"bucket": date(2026, 1, 1), "gmv": 1000, "orders": 10},
        {"bucket": date(2026, 2, 1), "gmv": 2000, "orders": 20},
    ]
    db = FakeMappingSession([trend_rows, [{"refreshed_at": None}]])
    result = perf_svc.get_trend(db, "all", None, period)
    assert result["granularity"] == "month"
    assert result["data"][0]["label"] == "Jan/26"


def test_get_trend_brand_keys_chega_parametrizado():
    db = FakeMappingSession([[], [{"refreshed_at": None}]])
    period = EffectivePeriod(start=date(2026, 5, 1), end=date(2026, 5, 3))
    perf_svc.get_trend(db, "tiktok", ["barbours", "kokeshi"], period)
    assert db.captured_params[0]["brand_keys"] == ["barbours", "kokeshi"]


def test_get_trend_soma_reconcilia_com_overview_no_mesmo_escopo():
    period = EffectivePeriod(start=date(2026, 5, 1), end=date(2026, 5, 3))

    trend_rows = [
        {"bucket": date(2026, 5, 1), "gmv": 100, "orders": 1},
        {"bucket": date(2026, 5, 2), "gmv": 200, "orders": 2},
        {"bucket": date(2026, 5, 3), "gmv": 300, "orders": 3},
    ]
    db_trend = FakeMappingSession([trend_rows, [{"refreshed_at": None}]])
    trend = perf_svc.get_trend(db_trend, "tiktok", None, period)
    trend_total = sum(p["gmv"] for p in trend["data"])

    overview_rows = [{"marketplace_id": 1, "gmv": trend_total, "orders": 6, "canceled_orders": 0,
                       "unique_buyers": 1, "ad_spend": 0, "ad_revenue": 0}]
    db_overview = FakeMappingSession([overview_rows, [{"refreshed_at": None}]])
    overview = perf_svc.get_overview(db_overview, "tiktok", 2026, 5, period=period)

    assert overview["current"]["gmv"] == trend_total


def test_get_trend_soma_reconcilia_com_overview_no_periodo_default_mes_anterior():
    # Mesmo cenario acima, mas usando o periodo efetivamente resolvido pelo
    # default das telas mensais (nada informado -> mes calendario anterior)
    # e materializado como date_from/date_to explicitos pelo frontend — os
    # dois caminhos tem que continuar reconciliando apos o backfill de
    # ref_month em resolve_period.
    period_default = resolve_period(default_mode="previous_month", today=date(2026, 6, 8))
    assert (period_default.start, period_default.end) == (date(2026, 5, 1), date(2026, 5, 31))

    period_materializado = resolve_period(
        date_from=period_default.start, date_to=period_default.end, today=date(2026, 6, 8),
    )
    assert period_materializado.ref_month == "2026-05"

    trend_rows = [
        {"bucket": date(2026, 5, 1), "gmv": 100, "orders": 1},
        {"bucket": date(2026, 5, 2), "gmv": 200, "orders": 2},
    ]
    db_trend = FakeMappingSession([trend_rows, [{"refreshed_at": None}]])
    trend = perf_svc.get_trend(db_trend, "tiktok", None, period_materializado)
    trend_total = sum(p["gmv"] for p in trend["data"])

    overview_rows = [{"marketplace_id": 1, "gmv": trend_total, "orders": 3, "canceled_orders": 0,
                       "unique_buyers": 1, "ad_spend": 0, "ad_revenue": 0}]
    db_overview = FakeMappingSession([overview_rows, [{"refreshed_at": None}]])
    overview = perf_svc.get_overview(db_overview, "tiktok", 2026, 5, period=period_materializado)

    assert overview["current"]["gmv"] == trend_total
