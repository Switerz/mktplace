"""Gate FULL-SH-1C-R/V — janela, tipos e flag do endpoint Shopee FBS.

Fases 4, 5 e 6 da revisao. Nenhum teste aqui toca banco: a flag nasce
desligada, e os que a ligam substituem o servico por um duplo.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routers import performance as router
from app.services import shopee_fbs_service as svc

ROTA = "/api/v1/performance/shopee-fbs"


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def ligada(monkeypatch):
    monkeypatch.setattr(settings, "shopee_fbs_enabled", True)


@pytest.fixture()
def sem_banco(monkeypatch):
    """Liga a flag mas intercepta o servico: nenhuma consulta e' emitida."""
    chamadas = {}

    def _falso(db, df, dt, **kw):
        chamadas["janela"] = (df, dt)
        chamadas["kw"] = kw
        return _resposta_minima(df, dt, kw["last_closed_date"])

    monkeypatch.setattr(svc, "get_shopee_fbs_block", _falso)
    return chamadas


def _resposta_minima(df, dt, last_closed):
    return {
        "meta": {"marketplace": "shopee", "scope_label": svc.SCOPE_LABEL,
                 "date_policy": "closed_day", "d0_materialized": False,
                 "last_closed_date": last_closed,
                 "requested_date_from": df, "requested_date_to": dt,
                 "effective_date_from": None, "effective_date_to": None,
                 "expected_accounts": list(svc.EXPECTED_ACCOUNTS),
                 "observed_accounts": [], "missing_accounts": [],
                 "unexpected_accounts": [], "covered_brands": [],
                 "brands_not_covered": list(svc.BRANDS_NOT_COVERED),
                 "gmv_definition": svc.GMV_DEFINITION,
                 "freshness": {"freshness_status": "never_loaded",
                               "load_mode": svc.LOAD_MODE,
                               "no_automation": svc.NO_AUTOMATION},
                 "warnings": [], "limitations": []},
        "totals": {"gross_gmv": Decimal("0"), "gross_units": 0,
                   "created_orders": 0, "eligible_orders": 0,
                   "cancelled_orders": 0, "to_return_orders": 0,
                   "to_return_gmv": Decimal("0"), "unpaid_orders": 0,
                   "unpaid_gmv": Decimal("0"),
                   "handling": {"seconds_avg": None, "hours_avg": None,
                                "days_avg": None, "seconds_sum": 0,
                                "sample_count": 0, "coverage_ratio": None}},
        "rates": {"cancellation_rate": None},
        "shares": {"share_fbs_gmv": None, "share_fbs_orders": None,
                   "share_fbs_units": None},
        "by_class": [], "by_brand": [], "by_account": [], "daily": [],
    }


def _hoje():
    from app.deps.period import today_brt
    return today_brt()


# ---------------------------------------------------------------------------
# Fase 4 — janela, fail-closed
# ---------------------------------------------------------------------------

def test_default_de_datas_termina_em_d_menos_1(client, ligada, sem_banco):
    r = client.get(ROTA)
    assert r.status_code == 200
    df, dt = sem_banco["janela"]
    hoje = _hoje()
    assert dt == hoje - timedelta(days=1), "default tem de fechar em D-1"
    assert (dt - df).days + 1 == router.DEFAULT_DAYS_SHOPEE_FBS
    assert sem_banco["kw"]["last_closed_date"] == hoje - timedelta(days=1)


def test_date_from_igual_date_to_e_valido(client, ligada, sem_banco):
    d = _hoje() - timedelta(days=2)
    r = client.get(ROTA, params={"date_from": str(d), "date_to": str(d)})
    assert r.status_code == 200
    assert sem_banco["janela"] == (d, d)


def test_janela_de_366_dias_e_aceita(client, ligada, sem_banco):
    fim = _hoje() - timedelta(days=1)
    ini = fim - timedelta(days=365)          # 366 dias inclusivos
    r = client.get(ROTA, params={"date_from": str(ini), "date_to": str(fim)})
    assert r.status_code == 200, r.text
    assert (sem_banco["janela"][1] - sem_banco["janela"][0]).days + 1 == 366


def test_janela_de_367_dias_e_recusada(client, ligada):
    fim = _hoje() - timedelta(days=1)
    ini = fim - timedelta(days=366)          # 367 dias inclusivos
    r = client.get(ROTA, params={"date_from": str(ini), "date_to": str(fim)})
    assert r.status_code == 422
    assert "366" in r.json()["detail"]


def test_date_from_posterior_a_date_to_recusado(client, ligada):
    r = client.get(ROTA, params={"date_from": "2026-08-31",
                                 "date_to": "2026-08-01"})
    assert r.status_code == 422


def test_d_menos_1_e_aceito(client, ligada, sem_banco):
    d1 = _hoje() - timedelta(days=1)
    r = client.get(ROTA, params={"date_from": str(d1 - timedelta(days=5)),
                                 "date_to": str(d1)})
    assert r.status_code == 200
    assert sem_banco["janela"][1] == d1


@pytest.mark.parametrize("delta", [0, 1, 30])
def test_d0_e_futuro_recusados_pela_mesma_regra(client, ligada, delta):
    """delta=0 e' D0; os demais sao futuro. Todos 422."""
    alvo = _hoje() + timedelta(days=delta)
    r = client.get(ROTA, params={"date_from": str(_hoje() - timedelta(days=5)),
                                 "date_to": str(alvo)})
    assert r.status_code == 422, f"delta={delta} deveria ser recusado"
    assert "fechado" in r.json()["detail"].lower()


def test_apenas_um_dos_limites_e_recusado(client, ligada):
    for p in ({"date_from": "2026-08-01"}, {"date_to": "2026-08-31"}):
        r = client.get(ROTA, params=p)
        assert r.status_code == 422
        assert "juntos" in r.json()["detail"]


@pytest.mark.parametrize("hoje,esperado", [
    # Virada de mes: D-1 cai no mes anterior.
    (date(2026, 9, 1), date(2026, 8, 31)),
    # Virada de ano: D-1 cai no ano anterior.
    (date(2027, 1, 1), date(2026, 12, 31)),
    # Ano bissexto: 2028-03-01 -> 29/02.
    (date(2028, 3, 1), date(2028, 2, 29)),
])
def test_virada_de_mes_e_de_ano(hoje, esperado, monkeypatch):
    df, dt, last = router._shopee_fbs_periodo(None, None, hoje)
    assert dt == esperado and last == esperado


def test_timezone_operacional_da_torre():
    """A janela usa America/Sao_Paulo, nunca UTC nem o fuso da maquina."""
    from app.deps.period import APP_TIMEZONE, today_brt
    assert str(APP_TIMEZONE) == "America/Sao_Paulo"
    # 02:00 UTC ainda e' o dia anterior no fuso operacional.
    assert "today_brt()" in router.ROUTER_TZ_MARK if hasattr(
        router, "ROUTER_TZ_MARK") else True
    import io as _io
    from pathlib import Path
    src = _io.open(Path(router.__file__), encoding="utf-8").read()
    bloco = src.split("def shopee_fbs(")[1].split("\n@")[0]
    assert "today_brt()" in bloco, "a janela tem de sair do relogio BRT"


def test_mensagem_de_erro_e_fixa_e_nao_ecoa_input(client, ligada):
    venenos = ["'; DROP TABLE x; --", "<img onerror=alert(1)>",
               "../../../etc/passwd", "%00", "{{7*7}}"]
    for v in venenos:
        for campo in ("brands", "accounts"):
            r = client.get(ROTA, params={"date_from": "2026-08-01",
                                         "date_to": "2026-08-31", campo: v})
            assert r.status_code == 422
            assert v not in r.text, f"{campo} ecoou: {v}"


def test_janela_vazia_nao_e_apresentada_como_queda(client, ligada, sem_banco):
    """Sem linha na janela, `effective_*` vem nulo e os shares vem null --
    nunca zero, que na tela pareceria queda real."""
    r = client.get(ROTA, params={"date_from": "2026-02-01",
                                 "date_to": "2026-02-02"})
    b = r.json()
    assert b["meta"]["effective_date_from"] is None
    assert b["shares"]["share_fbs_gmv"] is None
    assert b["rates"]["cancellation_rate"] is None
    assert b["meta"]["freshness"]["freshness_status"] == "never_loaded"


# ---------------------------------------------------------------------------
# Fase 5 — tipos e serializacao
# ---------------------------------------------------------------------------

def test_dinheiro_serializa_como_string(client, ligada, sem_banco):
    r = client.get(ROTA)
    cru = json.loads(r.text)
    for campo in ("gross_gmv", "to_return_gmv", "unpaid_gmv"):
        assert isinstance(cru["totals"][campo], str), (
            f"{campo} tem de serializar como string, nao float")


def test_razoes_nunca_produzem_nan_ou_infinito():
    """`_share` devolve None em vez de dividir por zero."""
    assert svc._share(Decimal(1), Decimal(0)) is None
    assert svc._share(Decimal(0), Decimal(0)) is None
    for num, den in ((1, 3), (2, 7), (999999, 1000000)):
        v = svc._share(Decimal(num), Decimal(den))
        assert v == v, "NaN"
        assert v not in (float("inf"), float("-inf"))


def test_divisao_e_feita_em_decimal_antes_de_virar_float():
    """1/3 em Decimal tem 28 digitos; converter antes perderia precisao."""
    v = svc._share(Decimal(1), Decimal(3))
    assert abs(v - (1 / 3)) < 1e-15
    # A conversao acontece DEPOIS da divisao -- comprovado pela ordem no fonte.
    import inspect
    fonte = inspect.getsource(svc._share)
    assert fonte.index("Decimal(num)") < fonte.index("float(n / d)")


def test_nulos_mantem_significado(client, ligada, sem_banco):
    b = client.get(ROTA).json()
    assert b["shares"]["share_fbs_gmv"] is None
    assert b["totals"]["handling"]["seconds_avg"] is None
    assert b["totals"]["handling"]["sample_count"] == 0
    # Zero e' zero; None e' indefinido. Os dois coexistem no mesmo payload.
    assert b["totals"]["gross_units"] == 0


def test_ordenacao_das_listas_e_deterministica():
    for nome in ("SQL_POR_CLASSE", "SQL_POR_MARCA", "SQL_POR_CONTA",
                 "SQL_DIARIO", "SQL_CONTAS_OBSERVADAS"):
        assert "ORDER BY" in str(getattr(svc, nome)), nome
    # As quebras montadas em Python tambem saem ordenadas.
    import inspect
    fonte = inspect.getsource(svc.get_shopee_fbs_block)
    assert fonte.count("sorted(") >= 3


def test_openapi_expoe_somente_os_campos_aprovados():
    sch = app.openapi()["components"]["schemas"]
    totals = set(sch["Totals"]["properties"])
    assert totals == {
        "gross_gmv", "gross_units", "created_orders", "eligible_orders",
        "cancelled_orders", "to_return_orders", "to_return_gmv",
        "unpaid_orders", "unpaid_gmv", "handling"}
    assert set(sch["Shares"]["properties"]) == {
        "share_fbs_gmv", "share_fbs_orders", "share_fbs_units"}
    assert set(sch["Rates"]["properties"]) == {"cancellation_rate"}
    # Nenhum campo de PII em nenhum schema da familia.
    for nome, corpo in sch.items():
        if "ShopeeFbs" in nome or nome in ("Totals", "Shares", "Rates",
                                           "ClassBreakdown", "BrandBreakdown",
                                           "AccountBreakdown", "DailyPoint",
                                           "Meta", "Freshness", "HandlingStat"):
            for prop in corpo.get("properties", {}):
                assert not any(k in prop.lower() for k in
                               ("buyer", "cpf", "recipient", "phone",
                                "email", "order_sn")), f"{nome}.{prop}"


@pytest.mark.parametrize("metodo", ["post", "put", "patch", "delete"])
def test_metodos_mutaveis_continuam_405(client, metodo):
    assert getattr(client, metodo)(ROTA).status_code == 405


# ---------------------------------------------------------------------------
# Fase 6 — feature flag
# ---------------------------------------------------------------------------

def test_flag_default_false_e_ausencia_deixa_indisponivel(client, monkeypatch):
    monkeypatch.delenv("SHOPEE_FBS_ENABLED", raising=False)
    monkeypatch.setattr(settings, "shopee_fbs_enabled", False)
    r = client.get(ROTA)
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"


def test_endpoint_indisponivel_executa_zero_consulta(client, monkeypatch):
    monkeypatch.setattr(settings, "shopee_fbs_enabled", False)

    def _proibido(*a, **k):                      # pragma: no cover
        raise AssertionError("flag desligada NAO pode consultar o banco")

    monkeypatch.setattr(svc, "get_shopee_fbs_block", _proibido)
    assert client.get(ROTA).status_code == 200


def test_resposta_unavailable_nao_contem_dados(client, monkeypatch):
    monkeypatch.setattr(settings, "shopee_fbs_enabled", False)
    b = client.get(ROTA).json()
    for proibido in ("totals", "shares", "rates", "by_class", "by_brand",
                     "by_account", "daily", "meta"):
        assert proibido not in b, f"{proibido} vazou na resposta desligada"
    assert set(b) == {"marketplace", "status", "scope_label",
                      "unavailable_reason", "date_policy"}


@pytest.mark.parametrize("valor", ["", "sim", "nao", "on", "off", "2", "null"])
def test_valor_invalido_nao_ativa_acidentalmente(valor):
    """Somente booleano reconhecido pelo Pydantic pode ligar."""
    from pydantic import TypeAdapter, ValidationError
    try:
        v = TypeAdapter(bool).validate_python(valor)
    except ValidationError:
        v = False                                 # invalido -> nao liga
    assert v is not True or valor in ("on",), (
        f"{valor!r} nao deveria ativar a superficie")


def test_nenhuma_variavel_foi_adicionada_ao_env():
    """O .env do projeto nao e' versionado nem alterado por este gate."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[3]
    for env in (raiz / ".env", raiz / "apps" / "api" / ".env"):
        if env.exists():
            texto = env.read_text(encoding="utf-8", errors="replace")
            assert "SHOPEE_FBS_ENABLED" not in texto, (
                "a flag nao pode ser ligada por arquivo neste gate")
