"""CX-MKT-2 — secao "Performance" da Expedicao (`/api/v1/expedicao/performance`).

Impede:
  1. tabela ainda nao criada virar erro ou zero (tem de ser `sem_fonte`);
  2. metrica que a plataforma nao devolveu aparecer com 0;
  3. a cor/situacao sair de uma meta que nao e' a oficial;
  4. excecao vazar texto; flag desligada abrir a rota; entrada ecoada.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import performance_oficial as rota
from app.services import performance_oficial_service as svc

URL = "/api/v1/expedicao/performance"
AGORA = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
SEGREDO = "postgresql://u:senha-NAO-PODE-VAZAR@h/db"


def _rep_kokeshi():
    """Payload no formato da doc [ML-REP], com os numeros do Seller Center de 03/11/2026."""
    return {
        "level_id": "5_green", "power_seller_status": "platinum", "real_level": None,
        "metrics": {
            "sales": {"period": "60 days", "completed": 59990},
            "claims": {"period": "60 days", "rate": 0.0061, "value": 366},
            "cancellations": {"period": "60 days", "rate": 0, "value": 0},
            "delayed_handling_time": {"period": "60 days", "rate": 0.0113, "value": 644},
        },
    }


def _linha_ml(brand="kokeshi", dia=date(2026, 11, 4), **extra):
    base = {
        "brand": brand, "captured_date": dia, "captured_at": datetime(2026, 11, 4, 10, 0),
        "level_id": "5_green", "power_seller_status": "platinum", "real_level": None,
        "protection_end_date": None, "sales_period": "60 days", "sales_completed": 59990,
        "seller_reputation": _rep_kokeshi(),
    }
    base.update(extra)
    return base


def _shopee():
    return [{"brand": "apice", "captured_date": date(2026, 11, 4),
             "captured_at": datetime(2026, 11, 4, 10, 10, tzinfo=timezone.utc),
             "overall_rating": 2, "fulfillment_failed": 0, "listing_failed": 0,
             "custom_service_failed": 1, "penalty_points": 2, "ongoing_punishments": 0}]


def _shopee_metricas():
    return [
        {"brand": "apice", "captured_date": date(2026, 11, 4), "metric_id": 1, "metric_name": "Late Shipment Rate",
         "metric_type": 1, "parent_metric_id": 0, "unit": 2, "current_period": 5.9, "last_period": 4.1,
         "target_value": 10, "target_comparator": "<"},
        {"brand": "apice", "captured_date": date(2026, 11, 4), "metric_id": 11, "metric_name": "Chat Response Rate",
         "metric_type": 3, "parent_metric_id": 0, "unit": 2, "current_period": 71, "last_period": 75,
         "target_value": 80, "target_comparator": ">="},
        {"brand": "apice", "captured_date": date(2026, 11, 4), "metric_id": 2001, "metric_name": "Sub",
         "metric_type": 1, "parent_metric_id": 25, "unit": 2, "current_period": 90, "last_period": None,
         "target_value": None, "target_comparator": None},
        {"brand": "apice", "captured_date": date(2026, 11, 4), "metric_id": -1, "metric_name": "Grupo",
         "metric_type": 3, "parent_metric_id": 0, "unit": 1, "current_period": None, "last_period": None,
         "target_value": None, "target_comparator": None},
    ]


class Fonte:
    def __init__(self, existe=True, falhas=(), ml=None, shopee=None, metricas=None):
        self.existe, self.falhas = existe, set(falhas)
        self.ml = [_linha_ml()] if ml is None else ml
        self.shopee = _shopee() if shopee is None else shopee
        self.metricas = _shopee_metricas() if metricas is None else metricas
        self.chamadas = []

    def __call__(self, sql, params):
        nome = {svc.SQL_EXISTE: "existe", svc.SQL_ML: "ml", svc.SQL_SHOPEE: "shopee",
                svc.SQL_SHOPEE_METRICAS: "shopee_metricas"}[sql]
        self.chamadas.append((nome, params))
        if nome in self.falhas:
            raise RuntimeError(f"falhou em {SEGREDO}")
        if nome == "existe":
            return [{"existe": self.existe}]
        return {"ml": self.ml, "shopee": self.shopee, "shopee_metricas": self.metricas}[nome]


class FonteTorre:
    """Neon da Torre (contas do bot). Padrao: migration 036 ainda nao aplicada."""

    def __init__(self, existe=False, falhas=(), shopee=None, metricas=None):
        self.existe, self.falhas = existe, set(falhas)
        self.shopee = shopee or []
        self.metricas = metricas or []
        self.chamadas = []

    def __call__(self, sql, params):
        nome = {svc.SQL_EXISTE: "existe", svc.SQL_SHOPEE_EXPORT: "shopee",
                svc.SQL_SHOPEE_METRICAS_EXPORT: "shopee_metricas"}[sql]
        self.chamadas.append((nome, params))
        if nome in self.falhas:
            raise RuntimeError(f"falhou em {SEGREDO}")
        if nome == "existe":
            return [{"existe": self.existe}]
        return {"shopee": self.shopee, "shopee_metricas": self.metricas}[nome]


def _montar(canal, **kw):
    fonte = kw.pop("fonte", None) or Fonte()
    torre = kw.pop("torre", None) or FonteTorre()
    return svc.montar_performance_oficial(canal=canal, consultar=fonte, consultar_torre=torre,
                                          agora=AGORA, **kw), fonte


# ---------------------------------------------------------------------------
# Mercado Livre
# ---------------------------------------------------------------------------
def test_ml_reproduz_a_tela_de_reputacao():
    resp, _ = _montar("mercadolivre")
    assert resp.estado == "dado"
    k = resp.contas[0]
    assert (k.marca, k.nivel, k.tom, k.termometro) == ("kokeshi", "MercadoLíder Platinum", "bom", 5)
    assert k.resumo == "59.990 vendas concluídas nos últimos 60 dias"
    m = {x.codigo: x for x in k.metricas}
    assert m["ml_claims"].valor == pytest.approx(0.61)
    assert m["ml_claims"].quantidade == 366
    assert (m["ml_claims"].meta, m["ml_claims"].situacao) == (1.0, "ok")
    assert m["ml_delayed_handling_time"].valor == pytest.approx(1.13)
    assert m["ml_cancellations"].valor == 0 and m["ml_cancellations"].situacao == "ok"


def test_ml_metrica_ausente_nao_aparece_com_zero():
    resp, _ = _montar("mercadolivre")
    assert "ml_mediations" not in {x.codigo for x in resp.contas[0].metricas}


def test_ml_mediacoes_aparecem_quando_a_api_devolve():
    rep = _rep_kokeshi()
    rep["metrics"]["mediations"] = {"rate": 0.0003, "value": 2}
    resp, _ = _montar("mercadolivre", fonte=Fonte(ml=[_linha_ml(seller_reputation=rep)]))
    med = next(x for x in resp.contas[0].metricas if x.codigo == "ml_mediations")
    assert (med.quantidade, med.meta) == (2, 0.5)


def test_ml_acima_do_limite_fica_fora():
    rep = _rep_kokeshi()
    rep["metrics"]["claims"] = {"rate": 0.0123, "value": 700}
    resp, _ = _montar("mercadolivre", fonte=Fonte(ml=[_linha_ml(level_id="3_yellow", power_seller_status=None,
                                                                 seller_reputation=rep)]))
    k = resp.contas[0]
    assert (k.nivel, k.tom, k.termometro) == ("Amarelo", "atencao", 3)
    assert next(x for x in k.metricas if x.codigo == "ml_claims").situacao == "fora"


def test_ml_conta_em_protecao_avisa():
    resp, _ = _montar("mercadolivre", fonte=Fonte(ml=[_linha_ml(real_level="red")]))
    assert any("proteção" in a for a in resp.contas[0].avisos)


def test_foto_velha_avisa_e_conta_sem_foto_e_listada():
    resp, _ = _montar("mercadolivre", fonte=Fonte(ml=[_linha_ml(dia=date(2026, 10, 30))]))
    assert any("atrasada" in a for a in resp.contas[0].avisos)
    assert {s.marca for s in resp.sem_foto} == {"barbours", "lescent", "rituaria"}
    assert [f.marca for f in resp.cobertura.fora] == ["apice"]


def test_tabela_inexistente_e_sem_fonte_e_nao_consulta():
    resp, fonte = _montar("mercadolivre", fonte=Fonte(existe=False))
    assert resp.estado == "sem_fonte" and "primeira captura" in resp.motivo
    assert resp.contas == []
    assert [n for n, _ in fonte.chamadas] == ["existe"]


def test_falha_vira_erro_sem_vazar():
    resp, _ = _montar("mercadolivre", fonte=Fonte(falhas={"ml"}))
    assert resp.estado == "erro" and resp.contas == []
    assert "senha" not in resp.model_dump_json()
    resp2, _ = _montar("shopee", fonte=Fonte(falhas={"existe"}))
    assert resp2.estado == "erro"


# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------
def test_shopee_nota_metricas_e_meta_oficial():
    resp, _ = _montar("shopee")
    k = resp.contas[0]
    assert (k.nivel, k.tom, k.termometro, k.termometro_max) == ("Precisa melhorar", "atencao", 2, 4)
    assert k.resumo == "1 métrica(s) fora da meta"
    m = {x.codigo: x for x in k.metricas}
    assert set(m) == {"shopee_1", "shopee_11"}  # sub-metrica e grupo ficam de fora
    assert (m["shopee_1"].rotulo, m["shopee_1"].situacao, m["shopee_1"].grupo) == (
        "Taxa de envio atrasado", "ok", "Envio")
    assert m["shopee_11"].situacao == "fora" and m["shopee_11"].rotulo_meta == "Meta >= 80"
    assert {d.rotulo: d.valor for d in k.destaques}["Pontos de penalidade no trimestre"] == "2"
    # 05/11/2026: a Kokeshi entra pelo bot (CX-KOKESHI-BOT); sem a 036 ainda, sem foto com motivo
    assert resp.cobertura.fora == []
    assert {f.marca: f.motivo for f in resp.sem_foto}["kokeshi"] == svc.MOTIVO_SEM_BOT


def test_shopee_metricas_novas_saem_em_portugues():
    # ids que a API devolveu em 04/11/2026 com o nome tecnico em ingles
    assert svc.NOMES_SHOPEE[2032] == "Envios aos sábados"
    assert svc.NOMES_SHOPEE[2033] == "Tempo médio de preparação"
    assert svc.NOMES_SHOPEE[2037] == "Taxa de entrega no prazo"
    assert all("_" not in r for r in svc.NOMES_SHOPEE.values())


def test_shopee_metricas_indisponiveis_nao_derrubam_a_nota():
    resp, _ = _montar("shopee", fonte=Fonte(falhas={"shopee_metricas"}))
    k = resp.contas[0]
    assert k.nivel == "Precisa melhorar" and k.metricas == []
    assert any("indisponíveis" in a for a in k.avisos)


def test_shopee_sem_meta_nao_ganha_situacao():
    met = [dict(_shopee_metricas()[0], target_value=None, target_comparator=None)]
    resp, _ = _montar("shopee", fonte=Fonte(metricas=met))
    assert resp.contas[0].metricas[0].situacao == "sem_meta"


# ---------------------------------------------------------------------------
# TikTok e filtro
# ---------------------------------------------------------------------------
def test_tiktok_sem_fonte_explicado_sem_consulta():
    resp, fonte = _montar("tiktokshop")
    assert resp.estado == "sem_fonte" and "EUA" in resp.motivo
    assert fonte.chamadas == []


def test_filtro_so_kokeshi_nao_consulta_o_data_mart():
    resp, fonte = _montar("shopee", brands=["kokeshi"])
    assert resp.estado == "dado" and resp.contas == [] and fonte.chamadas == []
    assert resp.cobertura.marcas == ["kokeshi"] and resp.cobertura.fora == []
    assert [f.marca for f in resp.sem_foto] == ["kokeshi"]


# ---------------------------------------------------------------------------
# Kokeshi pelo bot do Seller Centre (CX-KOKESHI-BOT, 05/11/2026)
# ---------------------------------------------------------------------------
def _kokeshi_neon():
    linha = dict(_shopee()[0], brand="kokeshi", overall_rating=3, custom_service_failed=1, penalty_points=0)
    met = [dict(m, brand="kokeshi") for m in _shopee_metricas()[:2]]
    return FonteTorre(existe=True, shopee=[linha], metricas=met)


def test_kokeshi_vem_do_neon_com_a_mesma_regra_e_aviso_de_origem():
    torre = _kokeshi_neon()
    resp, _ = _montar("shopee", torre=torre)
    assert [c.marca for c in resp.contas] == ["apice", "kokeshi"]
    k = resp.contas[1]
    assert (k.nivel, k.termometro) == ("Bom", 3)
    assert {m.codigo for m in k.metricas} == {"shopee_1", "shopee_11"}
    assert k.avisos[0] == svc.AVISO_EXPORT
    assert "kokeshi" not in {f.marca for f in resp.sem_foto}
    assert all(p == {"marcas": ["kokeshi"]} for _, p in torre.chamadas if _ != "existe")


def test_neon_caido_nao_derruba_as_lojas_com_app():
    resp, _ = _montar("shopee", torre=FonteTorre(existe=True, falhas={"shopee"}))
    assert resp.estado == "dado" and [c.marca for c in resp.contas] == ["apice"]
    assert {f.marca: f.motivo for f in resp.sem_foto}["kokeshi"] == svc.MOTIVO_ERRO
    assert SEGREDO not in resp.model_dump_json()


def test_data_mart_caido_com_kokeshi_de_pe():
    resp, _ = _montar("shopee", fonte=Fonte(falhas={"shopee"}), torre=_kokeshi_neon())
    assert resp.estado == "dado" and [c.marca for c in resp.contas] == ["kokeshi"]
    assert {f.marca for f in resp.sem_foto} == {"apice", "barbours", "lescent", "rituaria"}


def test_bot_ainda_sem_foto_da_kokeshi():
    resp, _ = _montar("shopee", torre=FonteTorre(existe=True))
    assert {f.marca: f.motivo for f in resp.sem_foto}["kokeshi"] == svc.MOTIVO_SEM_BOT


@pytest.mark.parametrize("valor,meta,comp,esperado", [
    (0.61, 1.0, "<=", "ok"), (1.0, 1.0, "<=", "ok"), (1.01, 1.0, "<=", "fora"),
    (80, 80, ">=", "ok"), (79.9, 80, ">=", "fora"), (None, 1.0, "<=", "sem_meta"),
    (1.0, None, "<=", "sem_meta"), (1.0, 1.0, "~", "sem_meta"),
])
def test_situacao(valor, meta, comp, esperado):
    assert svc._situacao(valor, meta, comp) == esperado


# ---------------------------------------------------------------------------
# Rota
# ---------------------------------------------------------------------------
@pytest.fixture
def cliente(monkeypatch):
    app = FastAPI()
    app.include_router(rota.router)
    monkeypatch.setattr(settings, "expedicao_performance_api_enabled", True)
    chamadas = []
    original = svc.montar_performance_oficial

    def falso(*, canal, brands=None):
        chamadas.append((canal, brands))
        return original(canal=canal, brands=brands, consultar=Fonte(), agora=AGORA)

    monkeypatch.setattr(svc, "montar_performance_oficial", falso)
    return TestClient(app), chamadas


def test_flag_desligada_e_404(cliente, monkeypatch):
    c, chamadas = cliente
    monkeypatch.setattr(settings, "expedicao_performance_api_enabled", False)
    assert c.get(URL, params={"canal": "shopee"}).status_code == 404
    assert chamadas == []


def test_rota_responde(cliente):
    c, chamadas = cliente
    r = c.get(URL, params={"canal": "mercadolivre", "brands": "Kokeshi"})
    assert r.status_code == 200 and chamadas == [("mercadolivre", ["kokeshi"])]
    assert r.json()["contas"][0]["nivel"] == "MercadoLíder Platinum"


@pytest.mark.parametrize("params", [{}, {"canal": "amazon"}, {"canal": "shopee", "brands": "<x>"}])
def test_entrada_invalida_e_422_sem_eco(cliente, params):
    c, chamadas = cliente
    r = c.get(URL, params=params)
    assert r.status_code == 422 and chamadas == []
    assert "<x>" not in r.text and "amazon" not in r.text
