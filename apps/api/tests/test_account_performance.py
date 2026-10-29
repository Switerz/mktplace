"""T-0014 — Performance das contas (`/api/v1/performance-contas`).

O que estes testes existem para impedir:
  1. ausencia, null ou erro de fonte virar 0 na tela;
  2. uma fonte caida derrubar as outras, ou vazar o texto da excecao;
  3. numero Torre sair com limiar de origem oficial (a referencia de 4% do
     TikTok nao tem fonte oficial);
  4. entrada fora da allowlist voltar ecoada;
  5. a ordem dos canais e os tres estados oficiais fixos mudarem sem querer.

Sem banco: a app de teste monta SO' este router e as funcoes de servico sao
substituidas por monkeypatch.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.database import get_db
from app.routers import account_performance as rota
from app.schemas.account_performance import Indicador, Limiar
from app.services import account_performance_service as svc
from app.services import expedicao_service
from app.services import expedicao_shopee_ldr_service
from app.services import expedicao_tiktok_service
from app.services import performance_service

URL = "/api/v1/performance-contas"
SEGREDO = "postgresql://usuario:senha-NAO-PODE-VAZAR@host/db"


class DbFalso:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Respostas falsas das fontes (formato real dos servicos)
# ---------------------------------------------------------------------------
def _linha_ml(marca, pedidos, nao_entregues, cancelados=0):
    """Linha por marca no formato de `get_quality`: sem entrega registrada, o
    servico publica `ml_not_delivered_shipments` e a taxa como None. Como no
    servico, `ml_total_orders` inclui os cancelados e cancelado 0 sai None."""
    pagos = pedidos - cancelados
    return {"brand": marca, "ml_total_orders": pedidos,
            "ml_cancelled_orders": cancelados or None,
            "ml_not_delivered_shipments": nao_entregues,
            "ml_not_delivered_rate_pct": (None if nao_entregues is None or not pagos
                                          else round(nao_entregues / pagos * 100, 2))}


def quality_ok(cancel=4.5, nao_entregue=1.2, linhas=None):
    return {
        "kpis": {"ml_cancel_rate_pct": cancel, "ml_not_delivered_rate_pct": nao_entregue},
        "brands": linhas if linhas is not None else [
            _linha_ml("barbours", 100, 1), _linha_ml("kokeshi", 50, 1)],
        "refreshed_at": (_agora() - timedelta(hours=3)).isoformat(),
    }


def expedicao_ok(freshness="fresh", idade=1.0):
    return {
        "availability": "available",
        "unavailable_reason": None,
        "channel": "mercadolivre",
        "snapshot": {"effective_at": _agora() - timedelta(hours=idade)},
        "totals": {"over_48h_count": 3},
        "accounts": [{"brand": "barbours", "shop_account": "1", "over_48h_count": 2},
                     {"brand": "kokeshi", "shop_account": "2", "over_48h_count": 1}],
        "freshness": [{"brand": "barbours", "freshness": freshness},
                      {"brand": "kokeshi", "freshness": "fresh"}],
        "limitations": {"snapshot_age_hours": idade},
    }


def expedicao_indisponivel(motivo):
    return {"availability": "unavailable", "unavailable_reason": motivo,
            "channel": "mercadolivre", "snapshot": None, "totals": None,
            "accounts": [], "freshness": [], "limitations": {}}


def tiktok_ok(rate=0.031, idade_h=1.0, reconstruido=True):
    return {
        "availability": "available",
        "unavailable_reason": None,
        "deadline_is_reconstructed": reconstruido,
        "targets": {"tiktok_ldr": expedicao_tiktok_service.META_TIKTOK_LDR,
                    "internal_critical": 0.10},
        "snapshot": {"effective_at": _agora() - timedelta(hours=idade_h)},
        "ldr": {"from": date(2026, 10, 22), "to": date(2026, 10, 29),
                "rate": rate, "base": 120 if rate is not None else 0},
        "brands": [],
    }


def tiktok_indisponivel(motivo):
    return {"availability": "unavailable", "unavailable_reason": motivo,
            "deadline_is_reconstructed": True,
            "targets": {"tiktok_ldr": expedicao_tiktok_service.META_TIKTOK_LDR,
                        "internal_critical": 0.10},
            "snapshot": None, "ldr": None}


def shopee_ok(rate=0.05, base=200, stale=False, oculta=None):
    return {
        "availability": "available",
        "unavailable_reason": None,
        "official": False,
        "snapshot": {"effective_at": _agora() - timedelta(hours=1), "stale": stale},
        "ldr": {"from": date(2026, 10, 22), "to": date(2026, 10, 29), "rate": rate,
                "base": base, "rate_hidden_reason": oculta},
        "brands": [{"brand": "barbours"}, {"brand": "lescent"}],
    }


def shopee_indisponivel(motivo):
    return {"availability": "unavailable", "unavailable_reason": motivo,
            "official": False, "snapshot": None, "ldr": None, "brands": []}


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    return DbFalso()


@pytest.fixture()
def chamadas():
    return {}


@pytest.fixture()
def fontes(monkeypatch, chamadas):
    """Instala as quatro fontes com resposta `dado`. Cada teste troca a sua."""
    estado = {
        "quality": lambda: quality_ok(),
        "expedicao": lambda: expedicao_ok(),
        "tiktok": lambda: tiktok_ok(),
        "shopee": lambda: shopee_ok(),
    }

    def _registrar(nome):
        def _fn(*args, **kwargs):
            chamadas.setdefault(nome, []).append((args, kwargs))
            return estado[nome]()
        return _fn

    monkeypatch.setattr(performance_service, "get_quality", _registrar("quality"))
    monkeypatch.setattr(expedicao_service, "get_expedicao", _registrar("expedicao"))
    monkeypatch.setattr(expedicao_tiktok_service, "obter_serie", _registrar("tiktok"))
    monkeypatch.setattr(expedicao_shopee_ldr_service, "obter_ldr", _registrar("shopee"))
    return estado


@pytest.fixture()
def flag_ligada(monkeypatch):
    """A rota nasce desligada (404). Os testes de conteudo ligam a flag."""
    monkeypatch.setattr(settings, "performance_contas_api_enabled", True)


@pytest.fixture()
def cliente(db, flag_ligada):
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _get(cliente, **params):
    r = cliente.get(URL, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ind(corpo, codigo):
    for canal in corpo["canais"]:
        for ind in canal["indicadores"]:
            if ind["codigo"] == codigo:
                return ind
    raise AssertionError(f"indicador ausente: {codigo}")


def _canal(corpo, nome):
    return next(c for c in corpo["canais"] if c["canal"] == nome)


TORRE = ("ml_cancelamento_torre", "ml_nao_entregue_torre", "ml_expedicao_48h_torre",
         "shopee_ldr_operacional_torre", "tiktok_ldr_operacional_torre")


# ---------------------------------------------------------------------------
# Ordem, oficiais fixos e inercia
# ---------------------------------------------------------------------------
def test_ordem_fixa_dos_canais(cliente, fontes):
    corpo = _get(cliente)
    assert [c["canal"] for c in corpo["canais"]] == ["mercado_livre", "shopee", "tiktok"]
    assert corpo["gerado_em"]


def test_tres_oficiais_com_estados_fixos(cliente, fontes):
    corpo = _get(cliente)
    ml = _ind(corpo, "ml_reputacao_oficial")
    tk = _ind(corpo, "tiktok_sps_ahr_oficial")
    sh = _ind(corpo, "shopee_account_health_oficial")
    assert (ml["tipo"], ml["estado"], ml["valor"]) == ("oficial", "sem_fonte", None)
    assert "reputação oficial do mercado livre ainda não capturada pela torre" in (
        ml["motivo_indisponivel"].lower())
    assert (tk["tipo"], tk["estado"], tk["valor"]) == ("oficial", "sem_fonte", None)
    assert "não ingeridos pela torre" in tk["motivo_indisponivel"].lower()
    assert "capacidade por API para lojas BR não comprovada" in tk["motivo_indisponivel"]
    assert "26/10/2026" in tk["motivo_indisponivel"]
    assert "só para lojas dos EUA" in tk["motivo_indisponivel"]
    assert (sh["tipo"], sh["estado"], sh["valor"]) == ("oficial", "nao_comprovado", None)
    assert "não comprovada" in sh["motivo_indisponivel"]
    assert "sem chamadas" in sh["motivo_indisponivel"]


def test_oficiais_nao_dependem_de_fonte(cliente, fontes):
    """Com TODAS as fontes caindo, os oficiais continuam nos estados fixos."""
    def _cai():
        raise RuntimeError(SEGREDO)
    for nome in fontes:
        fontes[nome] = _cai
    corpo = _get(cliente)
    assert _ind(corpo, "ml_reputacao_oficial")["estado"] == "sem_fonte"
    assert _ind(corpo, "tiktok_sps_ahr_oficial")["estado"] == "sem_fonte"
    assert _ind(corpo, "shopee_account_health_oficial")["estado"] == "nao_comprovado"
    for codigo in TORRE:
        assert _ind(corpo, codigo)["estado"] == "erro"


# ---------------------------------------------------------------------------
# Flag (fail-closed): desligada = 404 sem tocar em fonte nem em sessao
# ---------------------------------------------------------------------------
class _SessaoProibida:
    def __getattr__(self, nome):
        raise AssertionError(f"a rota tocou a sessao (.{nome}) com a flag desligada")


def test_flag_nasce_desligada():
    assert Settings.model_fields["performance_contas_api_enabled"].default is False


def test_flag_desligada_404_sem_chamar_fonte_nem_banco(fontes, chamadas, monkeypatch):
    monkeypatch.setattr(settings, "performance_contas_api_enabled", False)
    abriu_sessao = []

    def _get_db_espiao():
        abriu_sessao.append(True)
        yield _SessaoProibida()

    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_db] = _get_db_espiao
    c = TestClient(app)
    for caminho, params in ((URL, {}), (URL + "/", {}),
                            (URL, {"brands": "barbours"}),
                            # entrada invalida tambem e' 404: a flag vem antes
                            (URL, {"brands": "<script>"})):
        r = c.get(caminho, params=params)
        assert r.status_code == 404, (caminho, params, r.text)
        assert r.json() == {"detail": "Not Found"}
    assert chamadas == {}
    assert abriu_sessao == []


def test_flag_ligada_200_com_fontes_falsas(cliente, fontes, chamadas):
    corpo = _get(cliente)
    assert [c["canal"] for c in corpo["canais"]] == ["mercado_livre", "shopee", "tiktok"]
    assert set(chamadas) == {"quality", "expedicao", "tiktok", "shopee"}


@pytest.fixture()
def app_real(db):
    from app.main import app
    app.dependency_overrides[get_db] = lambda: db
    yield app
    app.dependency_overrides.pop(get_db, None)


def test_app_real_expoe_a_rota(app_real):
    # Pelo schema, e nao por `app.routes`: no FastAPI recente o router incluido
    # vira um no' preguicoso sem `path` proprio.
    assert set(app_real.openapi()["paths"][URL]) == {"get"}


def test_app_real_flag_desligada_404(app_real, fontes, chamadas, monkeypatch):
    monkeypatch.setattr(settings, "performance_contas_api_enabled", False)
    r = TestClient(app_real).get(URL)
    assert r.status_code == 404
    assert chamadas == {}


def test_app_real_flag_ligada_200(app_real, fontes, flag_ligada):
    r = TestClient(app_real).get(URL)
    assert r.status_code == 200, r.text
    assert [c["canal"] for c in r.json()["canais"]] == ["mercado_livre", "shopee", "tiktok"]


def test_sem_banco_responde_503(fontes, flag_ligada):
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_db] = lambda: None
    r = TestClient(app).get(URL)
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Estado `dado`
# ---------------------------------------------------------------------------
def test_mapeamento_dado_de_cada_indicador_torre(cliente, fontes):
    corpo = _get(cliente)

    c = _ind(corpo, "ml_cancelamento_torre")
    assert (c["estado"], c["valor"], c["unidade"], c["tipo"]) == ("dado", 4.5, "pct", "torre")
    assert c["limiar"] is None
    assert "qualquer autor" in c["rotulo"] and "não é o cancelamento da reputação ML" in c["rotulo"]
    assert c["atualizado_em"] and c["cobertura"] == "BARBOURS, KOKESHI"

    n = _ind(corpo, "ml_nao_entregue_torre")
    # Recalculado das linhas por marca: (1 + 1) / (100 + 50) = 1,33.
    assert (n["estado"], n["valor"], n["unidade"]) == ("dado", 1.33, "pct")
    assert "shipments não entregues / pedidos pagos" in n["rotulo"]
    assert "por pedido" not in n["rotulo"] + n["ajuda"]
    assert "pedidos − cancelados" in n["ajuda"]

    e = _ind(corpo, "ml_expedicao_48h_torre")
    assert (e["estado"], e["valor"], e["unidade"]) == ("dado", 3.0, "contagem")
    assert e["limiar"] == {"valor": 48.0, "origem": "interno", "unidade": "horas",
                           "rotulo": "limite interno, não SLA do ML"}
    assert e["aviso_frescor"] is None and e["atualizado_em"]

    t = _ind(corpo, "tiktok_ldr_operacional_torre")
    assert (t["estado"], t["valor"], t["unidade"]) == ("dado", 3.1, "pct")
    assert t["janela"] and "22/10/2026" in t["janela"]
    assert t["atualizado_em"] and t["aviso_frescor"] is None

    s = _ind(corpo, "shopee_ldr_operacional_torre")
    assert (s["estado"], s["valor"], s["tipo"]) == ("dado", 5.0, "torre")
    assert s["limiar"] is None and s["cobertura"] == "BARBOURS, LESCENT"


def test_zero_medido_continua_dado(cliente, fontes):
    fontes["quality"] = lambda: quality_ok(cancel=0.0)
    corpo = _get(cliente)
    c = _ind(corpo, "ml_cancelamento_torre")
    assert (c["estado"], c["valor"]) == ("dado", 0.0)


def test_ml_quality_usa_janela_fechada_sem_comparacao(cliente, fontes, chamadas):
    _get(cliente)
    args, kwargs = chamadas["quality"][0]
    assert args[1] == "ml"
    assert kwargs["compare_period"] is None
    assert kwargs["brand_keys"] is None
    periodo = kwargs["period"]
    assert periodo.days == svc.JANELA_ML_DIAS
    # Uma chamada so' para os dois indicadores.
    assert len(chamadas["quality"]) == 1
    _, kw_exp = chamadas["expedicao"][0]
    assert kw_exp["channel"] == "mercadolivre" and kw_exp["include_queue"] is False


# ---------------------------------------------------------------------------
# null nunca vira 0
# ---------------------------------------------------------------------------
def test_null_do_ml_vira_vazio_legitimo_e_nunca_zero(cliente, fontes):
    # `get_quality` devolve None justamente quando nao ha pedido ML na janela.
    fontes["quality"] = lambda: quality_ok(cancel=None, nao_entregue=None, linhas=[])
    corpo = _get(cliente)
    for codigo in ("ml_cancelamento_torre", "ml_nao_entregue_torre"):
        ind = _ind(corpo, codigo)
        assert ind["estado"] == "vazio_legitimo"
        assert ind["valor"] is None
        assert ind["motivo_indisponivel"]


def test_nao_entregue_sem_dado_em_nenhuma_marca_e_sem_fonte_nunca_zero(cliente, fontes):
    # Agregado do servico sai 0,0 porque soma `None or 0`: nao e' medicao.
    fontes["quality"] = lambda: quality_ok(
        nao_entregue=0.0,
        linhas=[_linha_ml("barbours", 100, None), _linha_ml("kokeshi", 50, None)])
    ind = _ind(_get(cliente), "ml_nao_entregue_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert ind["motivo_indisponivel"] == "Dado de não entregue ausente na fonte."
    # O cancelamento, da mesma chamada, nao e' afetado.
    assert _ind(_get(cliente), "ml_cancelamento_torre")["estado"] == "dado"


def test_nao_entregue_parcial_recalcula_sobre_a_mesma_cobertura(cliente, fontes):
    """Assimetrico: kokeshi (sem dado) tem 3x os pedidos de barbours.

    Agregado do servico: 2 / (100 + 300) = 0,5 (kokeshi no denominador e com 0
    no numerador). Recalculo: 2 / (110 - 10) = 2,0, so' barbours dos dois lados.
    """
    fontes["quality"] = lambda: quality_ok(
        nao_entregue=0.5,
        linhas=[_linha_ml("barbours", 110, 2, cancelados=10),
                _linha_ml("kokeshi", 300, None)])
    ind = _ind(_get(cliente), "ml_nao_entregue_torre")
    assert (ind["estado"], ind["valor"]) == ("dado", 2.0)
    assert ind["valor"] != 0.5  # nunca o agregado do servico
    assert ind["cobertura"] == "BARBOURS"
    aviso = ind["aviso_frescor"]
    assert aviso and "KOKESHI" in aviso and "BARBOURS" not in aviso
    assert "fora do numerador e do denominador" in aviso


def test_nao_entregue_completo_e_dado_sem_aviso(cliente, fontes):
    fontes["quality"] = lambda: quality_ok(nao_entregue=1.33)
    ind = _ind(_get(cliente), "ml_nao_entregue_torre")
    assert (ind["estado"], ind["valor"]) == ("dado", 1.33)
    assert ind["aviso_frescor"] is None
    assert ind["cobertura"] == "BARBOURS, KOKESHI"


def test_nao_entregue_denominador_zero_e_vazio_legitimo(cliente, fontes):
    # Marca com dado mas todos os pedidos cancelados: 0 pagos nao e' taxa 0.
    fontes["quality"] = lambda: quality_ok(
        linhas=[_linha_ml("barbours", 5, 0, cancelados=5), _linha_ml("kokeshi", 50, None)])
    ind = _ind(_get(cliente), "ml_nao_entregue_torre")
    assert (ind["estado"], ind["valor"]) == ("vazio_legitimo", None)
    assert ind["motivo_indisponivel"]


class _ResultadoFalso:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return self

    def all(self):
        return self._linhas

    def first(self):
        return {"refreshed_at": None}


class _DbQualidadeFalso:
    """Devolve as linhas cruas do mart para o `get_quality` REAL."""

    def __init__(self, linhas):
        self.linhas = linhas

    def execute(self, sql, params=None):
        return _ResultadoFalso(self.linhas)


def _linha_mart_ml(marca, pedidos, cancelados, entregues):
    base = {k: 0 for k in ("returned_orders", "unique_buyers", "new_buyers",
                           "repeat_buyers", "gmv", "seller_shipping_cost")}
    return {**base, "brand_key": marca, "marketplace_id": performance_service.ML_ID,
            "orders": pedidos, "canceled_orders": cancelados,
            "delivered_orders": entregues, "avg_delivery_days": None,
            "avg_delivery_hours": None, "problem_rate": None}


def _qualidade_real(linhas):
    periodo = svc.EffectivePeriod(start=date(2026, 9, 29), end=date(2026, 10, 28))
    return performance_service.get_quality(
        _DbQualidadeFalso(linhas), "ml", 2026, 9, brand_keys=None,
        period=periodo, compare_period=None)


def test_nao_entregue_completo_bate_com_agregado_real_do_servico():
    # barbours 3 nao entregues / 100 pagos; kokeshi 1 / 50.
    res = _qualidade_real([_linha_mart_ml("barbours", 100, 10, 97),
                           _linha_mart_ml("kokeshi", 50, 5, 49)])
    agregado = res["kpis"]["ml_not_delivered_rate_pct"]
    assert agregado == 2.67
    ind = svc._ml_nao_entregue(res, False, "janela")
    assert (ind.estado, ind.valor, ind.aviso_frescor) == ("dado", agregado, None)


def test_nao_entregue_parcial_diverge_do_agregado_real_do_servico():
    # kokeshi sem entrega registrada: o servico soma 0 no numerador e mantem
    # os 50 pagos no denominador (3 / 150 = 2,0). O recalculo usa 3 / 100.
    res = _qualidade_real([_linha_mart_ml("barbours", 100, 10, 97),
                           _linha_mart_ml("kokeshi", 50, 5, 0)])
    assert res["kpis"]["ml_not_delivered_rate_pct"] == 2.0
    ind = svc._ml_nao_entregue(res, False, "janela")
    assert (ind.estado, ind.valor, ind.cobertura) == ("dado", 3.0, "BARBOURS")
    assert "KOKESHI" in ind.aviso_frescor


def test_null_da_ldr_tiktok_vira_vazio_legitimo(cliente, fontes):
    fontes["tiktok"] = lambda: tiktok_ok(rate=None)
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("vazio_legitimo", None)


def test_null_da_ldr_shopee_sem_base_vira_vazio_legitimo(cliente, fontes):
    fontes["shopee"] = lambda: shopee_ok(rate=None, base=0)
    ind = _ind(_get(cliente), "shopee_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("vazio_legitimo", None)


def test_ldr_shopee_oculta_por_cobertura_nao_e_vazio_nem_zero(cliente, fontes):
    fontes["shopee"] = lambda: shopee_ok(rate=None, base=200, oculta="coverage_insufficient")
    ind = _ind(_get(cliente), "shopee_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert "cobertura insuficiente" in ind["motivo_indisponivel"]


def test_contagem_ausente_na_expedicao_vira_erro_nao_zero(cliente, fontes):
    def _sem_contagem():
        r = expedicao_ok()
        r["accounts"][0]["over_48h_count"] = None
        return r
    fontes["expedicao"] = _sem_contagem
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert (ind["estado"], ind["valor"]) == ("erro", None)


def test_nenhum_indicador_fora_de_dado_carrega_valor(cliente, fontes):
    fontes["quality"] = lambda: quality_ok(cancel=None, nao_entregue=None, linhas=[])
    fontes["expedicao"] = lambda: expedicao_indisponivel("channel_disabled")
    fontes["tiktok"] = lambda: tiktok_indisponivel("no_series_published")
    fontes["shopee"] = lambda: shopee_indisponivel("disabled")
    corpo = _get(cliente)
    for canal in corpo["canais"]:
        for ind in canal["indicadores"]:
            assert ind["estado"] != "dado"
            assert ind["valor"] is None


# ---------------------------------------------------------------------------
# Estados de serving -> sem_fonte
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("motivo", [
    "feature_flag_disabled", "channel_disabled", "no_snapshot_published",
    "inconsistent_batch",
])
def test_expedicao_indisponivel_vira_sem_fonte_com_motivo(cliente, fontes, motivo):
    fontes["expedicao"] = lambda: expedicao_indisponivel(motivo)
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    # Texto legivel do mapa fechado; o codigo cru da API nao aparece (reteste T-0014).
    assert ind["motivo_indisponivel"] == svc.MOTIVOS_EXPEDICAO[motivo]
    assert motivo not in ind["motivo_indisponivel"]
    assert "(" not in ind["motivo_indisponivel"]
    assert ind["limiar"]["origem"] == "interno"


def test_expedicao_no_snapshot_published_diz_nenhuma_fotografia(cliente, fontes):
    fontes["expedicao"] = lambda: expedicao_indisponivel("no_snapshot_published")
    texto = _ind(_get(cliente), "ml_expedicao_48h_torre")["motivo_indisponivel"]
    assert "Nenhuma fotografia" in texto
    assert "no_snapshot_published" not in texto


def test_expedicao_motivo_desconhecido_nao_e_ecoado(cliente, fontes):
    fontes["expedicao"] = lambda: expedicao_indisponivel("<script>x</script>")
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert ind["estado"] == "sem_fonte"
    assert "<script>" not in ind["motivo_indisponivel"]
    assert ind["motivo_indisponivel"] == svc.MOTIVO_EXPEDICAO_GENERICO


@pytest.mark.parametrize("motivo", ["table_not_migrated", "no_series_published"])
def test_tiktok_nao_publicada_vira_sem_fonte(cliente, fontes, motivo):
    fontes["tiktok"] = lambda: tiktok_indisponivel(motivo)
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert "não publicada" in ind["motivo_indisponivel"]


@pytest.mark.parametrize("motivo", ["disabled", "table_not_migrated", "no_series_published"])
def test_shopee_nao_publicada_vira_sem_fonte_e_nunca_oficial(cliente, fontes, motivo):
    fontes["shopee"] = lambda: shopee_indisponivel(motivo)
    ind = _ind(_get(cliente), "shopee_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"], ind["tipo"]) == ("sem_fonte", None, "torre")
    assert "não publicada" in ind["motivo_indisponivel"]
    assert "Não é métrica oficial" in ind["motivo_indisponivel"]


# ---------------------------------------------------------------------------
# Frescor
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("estado,texto", [
    ("stale", "fonte desatualizada"),
    ("critical", "fonte muito desatualizada"),
    ("unknown", "atualização da fonte desconhecida"),
])
def test_expedicao_desatualizada_gera_aviso(cliente, fontes, estado, texto):
    fontes["expedicao"] = lambda: expedicao_ok(freshness=estado)
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert ind["estado"] == "dado"
    assert ind["aviso_frescor"] and f"BARBOURS ({texto})" in ind["aviso_frescor"]
    # Reteste T-0014: o codigo cru ("stale") nao chega a tela.
    assert f"({estado})" not in ind["aviso_frescor"]


@pytest.mark.parametrize("estado", ["<b>x</b>", "STALE", "", None])
def test_expedicao_frescor_desconhecido_vira_texto_generico(cliente, fontes, estado):
    fontes["expedicao"] = lambda: expedicao_ok(freshness=estado)
    aviso = _ind(_get(cliente), "ml_expedicao_48h_torre")["aviso_frescor"]
    assert f"BARBOURS ({svc.FRESCOR_EXPEDICAO_GENERICO})" in aviso
    if estado:
        assert estado not in aviso


def test_expedicao_fotografia_velha_gera_aviso(cliente, fontes):
    fontes["expedicao"] = lambda: expedicao_ok(idade=30.0)
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert ind["aviso_frescor"] and "30 h" in ind["aviso_frescor"]


@pytest.mark.parametrize("idade,frescor", [(30.0, "fresh"), (30.0, "stale"),
                                           (7.0, "critical"), (1.0, "unknown")])
def test_frescor_nunca_afirma_ausencia_de_agendamento(cliente, fontes, idade, frescor):
    """A Expedicao e' agendada: idade da fotografia nao prova carga manual."""
    fontes["expedicao"] = lambda: expedicao_ok(freshness=frescor, idade=idade)
    r = cliente.get(URL)
    assert r.status_code == 200
    assert "agendamento" not in r.text.lower()
    assert "sem agendamento" not in (
        _ind(r.json(), "ml_expedicao_48h_torre")["aviso_frescor"] or "")


def test_tiktok_publicacao_acima_de_6h_gera_aviso(cliente, fontes):
    fontes["tiktok"] = lambda: tiktok_ok(idade_h=7.0)
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert ind["estado"] == "dado" and ind["aviso_frescor"]


def test_tiktok_publicacao_recente_sem_aviso(cliente, fontes):
    fontes["tiktok"] = lambda: tiktok_ok(idade_h=5.0)
    assert _ind(_get(cliente), "tiktok_ldr_operacional_torre")["aviso_frescor"] is None


def test_shopee_stale_gera_aviso(cliente, fontes):
    fontes["shopee"] = lambda: shopee_ok(stale=True)
    ind = _ind(_get(cliente), "shopee_ldr_operacional_torre")
    assert ind["estado"] == "dado" and ind["aviso_frescor"]


# ---------------------------------------------------------------------------
# Limiar: Torre nunca oficial
# ---------------------------------------------------------------------------
def test_referencia_tiktok_e_interna_nunca_oficial(cliente, fontes):
    for resposta in (tiktok_ok(), tiktok_ok(rate=None),
                     tiktok_indisponivel("no_series_published")):
        fontes["tiktok"] = lambda r=resposta: r
        ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
        assert ind["limiar"]["origem"] == "interno"
        # Reteste T-0014: o rotulo nao repete a origem ("referência interna"),
        # que a web ja' escreve na linha "Origem do limiar".
        assert ind["limiar"]["rotulo"] == "meta operacional da Torre"
        assert "referência" not in ind["limiar"]["rotulo"]
        # Mesma unidade do indicador: 0,04 vira 4 pontos percentuais.
        assert ind["limiar"]["valor"] == pytest.approx(4.0)


@pytest.mark.parametrize("resposta", [expedicao_ok(), expedicao_indisponivel("channel_disabled")])
def test_limiar_da_expedicao_e_em_horas_nao_na_unidade_do_indicador(cliente, fontes, resposta):
    """QA: "48" saia formatado como contagem (48 envios). O limite e' de 48 h."""
    fontes["expedicao"] = lambda: resposta
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert ind["unidade"] == "contagem"
    assert ind["limiar"]["unidade"] == "horas"
    assert ind["limiar"]["valor"] == 48.0
    # O rotulo do limiar nao repete o texto da origem ("referencia interna").
    assert "referência" not in ind["limiar"]["rotulo"]
    assert "interno" not in ind["rotulo"]


def test_limiar_das_ldrs_mantem_pct(cliente, fontes):
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert ind["unidade"] == "pct"
    # Sem unidade propria = mesma do indicador (pct). Nunca "horas".
    assert ind["limiar"]["unidade"] in (None, "pct")


def test_schema_limiar_unidade_e_opcional_e_restrita():
    assert Limiar(valor=4.0, origem="interno", rotulo="x").unidade is None
    assert Limiar(valor=48, origem="interno", rotulo="x", unidade="horas").unidade == "horas"
    with pytest.raises(ValueError):
        Limiar(valor=48, origem="interno", rotulo="x", unidade="dias")


def test_rotulos_torre_nao_repetem_o_chip(cliente, fontes):
    """O chip do tipo ja' diz "Calculo Torre"; o rotulo nao repete."""
    corpo = _get(cliente)
    for codigo in TORRE:
        assert "Cálculo Torre" not in _ind(corpo, codigo)["rotulo"], codigo
    for meta in svc.META.values():
        assert "· Cálculo Torre ·" not in meta["rotulo"]
        assert not meta["rotulo"].endswith("Cálculo Torre")


CHAVES_CRUAS = re.compile(r"\b(apice|barbours|kokeshi|lescent|rituaria)\b")


def test_textos_de_cobertura_e_aviso_usam_rotulo_legivel_da_marca(cliente, fontes):
    fontes["quality"] = lambda: quality_ok(
        linhas=[_linha_ml("barbours", 110, 2, cancelados=10), _linha_ml("kokeshi", 300, None)])
    fontes["expedicao"] = lambda: expedicao_ok(freshness="stale")
    corpo = _get(cliente, brands="barbours,kokeshi")
    for canal in corpo["canais"]:
        for ind in canal["indicadores"]:
            for campo in ("cobertura", "aviso_frescor"):
                assert not CHAVES_CRUAS.search(ind[campo] or ""), (ind["codigo"], campo, ind[campo])
    assert _ind(corpo, "ml_nao_entregue_torre")["aviso_frescor"].startswith(
        "Dado de não entregue ausente na fonte para: KOKESHI.")
    assert _ind(corpo, "tiktok_ldr_operacional_torre")["cobertura"] == "BARBOURS, KOKESHI"
    # A cobertura estruturada continua em CHAVE (a web aplica o rotulo dela).
    assert _canal(corpo, "mercado_livre")["cobertura"]["marcas"] == ["barbours", "kokeshi"]


def test_rotulo_marca_fora_do_mapa_usa_title_case():
    assert svc._rotulo_marca("rituaria") == "RITUÁRIA"
    assert svc._rotulo_marca("nova_marca") == "Nova_Marca"


def test_nenhum_indicador_torre_tem_limiar_oficial(cliente, fontes):
    corpo = _get(cliente)
    for canal in corpo["canais"]:
        for ind in canal["indicadores"]:
            if ind["tipo"] == "torre" and ind["limiar"] is not None:
                assert ind["limiar"]["origem"] != "oficial"


def test_schema_recusa_torre_com_limiar_oficial():
    with pytest.raises(ValueError):
        Indicador(codigo="x", rotulo="x", tipo="torre", estado="dado", valor=1.0,
                  fonte="x", ajuda="x",
                  limiar=Limiar(valor=4.0, origem="oficial", rotulo="x"))


def test_schema_recusa_valor_fora_do_estado_dado():
    with pytest.raises(ValueError):
        Indicador(codigo="x", rotulo="x", tipo="torre", estado="sem_fonte",
                  valor=0.0, fonte="x", ajuda="x")
    with pytest.raises(ValueError):
        Indicador(codigo="x", rotulo="x", tipo="torre", estado="dado",
                  valor=None, fonte="x", ajuda="x")


def test_prazo_reconstruido_aparece_na_ajuda(cliente, fontes):
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert "prazo reconstruído (2/1 dias úteis)" in ind["ajuda"]
    fontes["tiktok"] = lambda: tiktok_ok(reconstruido=False)
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert "prazo oficial TikTok" in ind["ajuda"]
    assert ind["limiar"]["origem"] == "interno"


# ---------------------------------------------------------------------------
# Excecao de uma fonte: so' ela vira erro, sem vazar texto
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fonte,afetados", [
    ("quality", {"ml_cancelamento_torre", "ml_nao_entregue_torre"}),
    ("expedicao", {"ml_expedicao_48h_torre"}),
    ("tiktok", {"tiktok_ldr_operacional_torre"}),
    ("shopee", {"shopee_ldr_operacional_torre"}),
])
def test_excecao_de_uma_fonte_so_afeta_ela(cliente, fontes, db, caplog, fonte, afetados):
    def _cai():
        raise RuntimeError(SEGREDO)
    fontes[fonte] = _cai
    r = cliente.get(URL)
    assert r.status_code == 200
    assert "NAO-PODE-VAZAR" not in r.text
    assert "NAO-PODE-VAZAR" not in caplog.text
    corpo = r.json()
    for codigo in TORRE:
        ind = _ind(corpo, codigo)
        if codigo in afetados:
            assert ind["estado"] == "erro"
            assert ind["valor"] is None
            assert ind["motivo_indisponivel"] == svc.MOTIVO_ERRO
        else:
            assert ind["estado"] == "dado", codigo
    # A transacao abortada e' liberada para a fonte seguinte.
    assert db.rollbacks >= 1


# ---------------------------------------------------------------------------
# Allowlist de marcas e cobertura
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("entrada", [
    "zzz-marca-inexistente", "barbours,<script>alert(1)</script>", " , ", "",
])
def test_brands_fora_da_allowlist_da_422_sem_eco(cliente, fontes, chamadas, entrada):
    r = cliente.get(URL, params={"brands": entrada})
    assert r.status_code == 422
    if entrada.strip(" ,"):
        assert "zzz-marca" not in r.text
        assert "<script>" not in r.text
    assert chamadas == {}


def test_brands_validas_sao_repassadas(cliente, fontes, chamadas):
    _get(cliente, brands="Barbours,kokeshi")
    assert chamadas["quality"][0][1]["brand_keys"] == ["barbours", "kokeshi"]
    assert chamadas["tiktok"][0][1]["brands"] == ["barbours", "kokeshi"]
    # Kokeshi nao tem app Shopee: so' barbours chega a fonte da Shopee.
    assert chamadas["shopee"][0][1]["brands"] == ["barbours"]


def test_brands_repetido_equivale_ao_csv(cliente, fontes, chamadas):
    r = cliente.get(URL, params=[("brands", "kokeshi"), ("brands", "apice,barbours")])
    assert r.status_code == 200, r.text
    assert chamadas["tiktok"][0][1]["brands"] == ["apice", "barbours", "kokeshi"]


def test_brands_repetido_com_valor_invalido_da_422_sem_eco(cliente, fontes, chamadas):
    r = cliente.get(URL, params=[("brands", "barbours"), ("brands", "<b>zzz</b>")])
    assert r.status_code == 422
    assert "<b>" not in r.text and "zzz" not in r.text
    assert chamadas == {}


def test_pct_em_pontos_percentuais_fracao_convertida(cliente, fontes):
    """Contrato da web: `pct` = pontos percentuais em `valor` E `limiar.valor`.

    get_quality ja' devolve pontos; as LDRs (TikTok e Shopee) e o
    `targets.tiktok_ldr` vem em FRACAO e precisam ser multiplicados por 100.
    """
    fontes["quality"] = lambda: quality_ok(cancel=4.5)
    fontes["tiktok"] = lambda: tiktok_ok(rate=0.0425)
    fontes["shopee"] = lambda: shopee_ok(rate=0.012)
    corpo = _get(cliente)
    assert _ind(corpo, "ml_cancelamento_torre")["valor"] == 4.5  # sem dupla conversao
    tk = _ind(corpo, "tiktok_ldr_operacional_torre")
    assert tk["valor"] == pytest.approx(4.25)
    assert tk["limiar"]["valor"] == pytest.approx(4.0)  # 0.04 -> 4.0
    assert _ind(corpo, "shopee_ldr_operacional_torre")["valor"] == pytest.approx(1.2)


def test_cobertura_padrao_por_canal(cliente, fontes):
    corpo = _get(cliente)
    ml = _canal(corpo, "mercado_livre")["cobertura"]
    assert ml["marcas"] == ["barbours", "kokeshi", "lescent", "rituaria"]
    assert [f["marca"] for f in ml["fora"]] == ["apice"]
    sh = _canal(corpo, "shopee")["cobertura"]
    assert "kokeshi" not in sh["marcas"]
    assert [f["marca"] for f in sh["fora"]] == ["kokeshi"]
    tk = _canal(corpo, "tiktok")["cobertura"]
    assert tk["fora"] == []


def test_so_apice_nao_consulta_fontes_ml(cliente, fontes, chamadas):
    corpo = _get(cliente, brands="apice")
    assert "quality" not in chamadas and "expedicao" not in chamadas
    for codigo in ("ml_cancelamento_torre", "ml_nao_entregue_torre", "ml_expedicao_48h_torre"):
        ind = _ind(corpo, codigo)
        assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert _canal(corpo, "mercado_livre")["cobertura"]["fora"][0]["marca"] == "apice"


def test_so_kokeshi_nao_consulta_ldr_shopee(cliente, fontes, chamadas):
    corpo = _get(cliente, brands="kokeshi")
    assert "shopee" not in chamadas
    ind = _ind(corpo, "shopee_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert _ind(corpo, "shopee_account_health_oficial")["estado"] == "nao_comprovado"


def test_expedicao_filtra_contas_pela_marca(cliente, fontes):
    corpo = _get(cliente, brands="kokeshi")
    ind = _ind(corpo, "ml_expedicao_48h_torre")
    assert (ind["estado"], ind["valor"], ind["cobertura"]) == ("dado", 1.0, "KOKESHI")


# ---------------------------------------------------------------------------
# Capacidade de API: limite da descoberta, nunca ausencia universal
# ---------------------------------------------------------------------------

#: Frases que afirmam que a plataforma NAO TEM o dado/API. O que sabemos e' so'
#: o que a Torre nao ingeriu e o que a doc consultada documentou.
AUSENCIA_UNIVERSAL = re.compile(
    r"não expõe|não existe|inexistente|não sai por api|indisponível por api"
    r"|não publica\b|nao expoe|nao existe",
    re.IGNORECASE,
)


def _textos_estaticos() -> list[str]:
    textos = [v for k, v in vars(svc).items()
              if k.startswith(("TEXTO_", "AJUDA_", "MOTIVO_")) and isinstance(v, str)]
    textos += list(svc.MOTIVOS_EXPEDICAO.values())
    for meta in svc.META.values():
        textos += [v for v in meta.values() if isinstance(v, str)]
    return textos


def test_textos_estaticos_nao_afirmam_ausencia_universal_de_api():
    achados = [t for t in _textos_estaticos() if AUSENCIA_UNIVERSAL.search(t)]
    assert achados == []


@pytest.mark.parametrize("cenario", ["ok", "indisponivel", "erro"])
def test_resposta_nao_afirma_ausencia_universal_de_api(cliente, fontes, cenario):
    if cenario == "indisponivel":
        fontes["quality"] = lambda: quality_ok(linhas=[_linha_ml("barbours", 10, None)])
        fontes["expedicao"] = lambda: expedicao_indisponivel("channel_disabled")
        fontes["tiktok"] = lambda: tiktok_indisponivel("no_series_published")
        fontes["shopee"] = lambda: shopee_indisponivel("disabled")
    elif cenario == "erro":
        def _cai():
            raise RuntimeError("x")
        for nome in fontes:
            fontes[nome] = _cai
    r = cliente.get(URL)
    assert r.status_code == 200
    assert AUSENCIA_UNIVERSAL.findall(r.text) == []


def test_oficiais_qualificados_como_nao_ingeridos_ou_nao_comprovados(cliente, fontes):
    corpo = _get(cliente)
    tk = _ind(corpo, "tiktok_sps_ahr_oficial")
    for campo in ("motivo_indisponivel", "ajuda"):
        assert "não comprovada" in tk[campo]
    assert "não ingerido" in tk["fonte"]
    assert "não comprovada" in _ind(corpo, "shopee_account_health_oficial")["motivo_indisponivel"]
    ml = _ind(corpo, "ml_reputacao_oficial")
    assert "não ingerido" in ml["fonte"]
