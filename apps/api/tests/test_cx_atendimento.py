"""CX-MKT-1 — aba "Atendimento" (`/api/v1/performance-contas/atendimento`).

O que estes testes existem para impedir:
  1. fonte caida, ausencia ou zero pedidos virarem 0 na tela;
  2. uma fonte caida derrubar as outras, ou o texto da excecao vazar;
  3. "pedido nao pago" da Shopee entrar como cancelamento de atendimento;
  4. motivo novo da plataforma sumir em vez de aparecer sem mapa;
  5. entrada fora da allowlist voltar ecoada; flag desligada abrir a rota.

Sem banco: `consultar` e' injetado e roteia pelo nome da consulta.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import cx_atendimento as rota
from app.services import cx_atendimento_service as svc

URL = "/api/v1/performance-contas/atendimento"
SEGREDO = "postgresql://usuario:senha-NAO-PODE-VAZAR@host/db"
AGORA = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)  # 09:00 em Sao Paulo


def _ml_marcas():
    return [
        {"marca": "kokeshi", "pedidos": 1000, "cancelados": 30,
         "atualizado_em": datetime(2026, 11, 4, 11, 50)},
        {"marca": "barbours", "pedidos": 500, "cancelados": 20,
         "atualizado_em": datetime(2026, 11, 4, 11, 40)},
    ]


def _ml_motivos():
    return [
        {"codigo": "buyer_cancel_express", "solicitante": "buyer", "quantidade": 30},
        {"codigo": "mediations", "solicitante": "meli", "quantidade": 10},
        {"codigo": "codigo_novo_xyz", "solicitante": "seller", "quantidade": 5},
        {"codigo": None, "solicitante": "meli", "quantidade": 5},
    ]


def _shopee_marcas():
    return [
        {"marca": "barbours", "pedidos": 800, "cancelados": 40, "cancelados_comprador": 30,
         "nao_pagos": 100, "atualizado_em": datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)},
        {"marca": "apice", "pedidos": 200, "cancelados": 10, "cancelados_comprador": 8,
         "nao_pagos": 20, "atualizado_em": datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)},
    ]


def _shopee_motivos():
    return [
        {"motivo": "Unpaid Order", "autor": "system", "quantidade": 120},
        {"motivo": "Need to Change Delivery Address", "autor": "buyer", "quantidade": 6},
        {"motivo": "need to change delivery address", "autor": "buyer", "quantidade": 4},
        {"motivo": "Parcel lost in transit. The eligible compensation has been credited.",
         "autor": "system", "quantidade": 3},
        {"motivo": "Motivo Inedito", "autor": "buyer", "quantidade": 2},
        {"motivo": "Don't Want to Buy Anymore", "autor": "buyer", "quantidade": 35},
    ]


def _shopee_devolucao():
    return [{"marca": "barbours", "em_devolucao": 7}]


ROTAS = {
    "ml_marcas": svc.SQL_ML_MARCAS,
    "ml_motivos": svc.SQL_ML_MOTIVOS,
    "shopee_marcas": svc.SQL_SHOPEE_MARCAS,
    "shopee_motivos": svc.SQL_SHOPEE_MOTIVOS,
    "shopee_devolucao": svc.SQL_SHOPEE_DEVOLUCAO,
}


class Fonte:
    """Roteia pelo SQL. `falhas` = nomes que levantam excecao com segredo."""

    def __init__(self, falhas=(), sobrepor=None):
        self.falhas = set(falhas)
        self.dados = {
            "ml_marcas": _ml_marcas(), "ml_motivos": _ml_motivos(),
            "shopee_marcas": _shopee_marcas(), "shopee_motivos": _shopee_motivos(),
            "shopee_devolucao": _shopee_devolucao(),
        }
        self.dados.update(sobrepor or {})
        self.chamadas = []

    def __call__(self, sql, params):
        nome = next(n for n, s in ROTAS.items() if s == sql)
        self.chamadas.append((nome, params))
        if nome in self.falhas:
            raise RuntimeError(f"falhou em {SEGREDO}")
        return self.dados[nome]


def _montar(**kw):
    fonte = kw.pop("fonte", None) or Fonte()
    return svc.montar_atendimento(consultar=fonte, agora=AGORA, **kw), fonte


def _canal(resp, canal):
    return next(c for c in resp.canais if c.canal == canal)


def _ind(canal, codigo):
    return next(i for i in canal.indicadores if i.codigo == codigo)


# ---------------------------------------------------------------------------
# Janela
# ---------------------------------------------------------------------------
def test_janela_e_de_dias_fechados_ate_ontem_no_fuso_do_negocio():
    # 02:00 UTC de 04/11 ainda e' 03/11 em Sao Paulo: ontem = 02/11
    j = svc.calcular_janela(30, datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc))
    assert (j.inicio, j.fim, j.dias) == (date(2026, 10, 4), date(2026, 11, 2), 30)


def test_parametros_das_consultas_respeitam_janela_e_cobertura():
    _, fonte = _montar(dias=7)
    params = dict(fonte.chamadas)
    assert params["ml_marcas"]["marcas"] == ["barbours", "kokeshi", "lescent", "rituaria"]
    assert params["ml_marcas"]["inicio"] == date(2026, 10, 28)
    assert params["ml_marcas"]["fim_exclusivo"] == date(2026, 11, 4)
    assert params["shopee_marcas"]["marcas"] == ["apice", "barbours", "lescent", "rituaria"]
    assert params["shopee_marcas"]["fim"] == date(2026, 11, 3)


# ---------------------------------------------------------------------------
# Mercado Livre
# ---------------------------------------------------------------------------
def test_ml_soma_marcas_e_calcula_taxa_torre():
    resp, _ = _montar()
    ml = _canal(resp, "mercado_livre")
    assert _ind(ml, "ml_cancelamentos").valor == 50
    assert _ind(ml, "ml_taxa_cancelamento").valor == pytest.approx(3.33, abs=0.01)
    kokeshi = next(m for m in ml.marcas if m.marca == "kokeshi")
    assert kokeshi.taxa_cancelamento_pct == pytest.approx(3.0)


def test_ml_nao_publica_taxa_de_reclamacao_por_mediations():
    """A contagem por `mediations` diverge da reputacao oficial (2,5% x 0,61%)."""
    resp, _ = _montar()
    ml = _canal(resp, "mercado_livre")
    assert not any("reclamacao" in i.codigo for i in ml.indicadores)
    assert "mediations" not in svc.SQL_ML_MARCAS
    assert all(i.tipo == "torre" and i.limiar is None for i in ml.indicadores)
    assert [c.marca for c in ml.cobertura.fora] == ["apice"]


def test_marca_sem_linha_na_fonte_fica_none_e_e_avisada_nunca_zero():
    resp, _ = _montar()
    ml = _canal(resp, "mercado_livre")
    lescent = next(m for m in ml.marcas if m.marca == "lescent")
    assert lescent.pedidos is None and lescent.taxa_cancelamento_pct is None
    assert "LESCENT" in _ind(ml, "ml_taxa_cancelamento").cobertura.upper()


def test_motivo_ml_novo_aparece_sem_mapa_e_sem_motivo_nao_some():
    resp, _ = _montar()
    bloco = _canal(resp, "mercado_livre").motivos
    assert bloco.estado == "dado" and bloco.total == 50
    por_codigo = {m.codigo: m for m in bloco.itens}
    assert por_codigo["buyer_cancel_express"].rotulo == "Comprador cancelou antes do envio"
    assert por_codigo["codigo_novo_xyz"].mapeado is False
    assert por_codigo["codigo_novo_xyz"].natureza == "vendedor"
    assert por_codigo["sem_motivo"].quantidade == 5
    assert bloco.itens[0].codigo == "buyer_cancel_express"
    assert sum(m.pct for m in bloco.itens) == pytest.approx(100, abs=0.2)


def test_ml_fora_do_ar_vira_erro_sem_vazar_e_shopee_segue():
    resp, _ = _montar(fonte=Fonte(falhas={"ml_marcas", "ml_motivos"}))
    ml = _canal(resp, "mercado_livre")
    assert {i.estado for i in ml.indicadores} == {"erro"}
    assert all(i.valor is None for i in ml.indicadores)
    assert ml.motivos.estado == "erro" and ml.motivos.itens == []
    assert _ind(_canal(resp, "shopee"), "shopee_cancelamentos").estado == "dado"
    assert "senha" not in resp.model_dump_json()


def test_zero_pedidos_e_vazio_legitimo_nao_zero():
    resp, _ = _montar(fonte=Fonte(sobrepor={"ml_marcas": [], "ml_motivos": []}))
    ml = _canal(resp, "mercado_livre")
    assert {i.estado for i in ml.indicadores} == {"vazio_legitimo"}
    assert all(i.valor is None for i in ml.indicadores)
    assert ml.motivos.estado == "vazio_legitimo" and ml.motivos.total is None


def test_frescor_ml_velho_ganha_aviso():
    velho = [dict(r, atualizado_em=datetime(2026, 11, 4, 2, 0)) for r in _ml_marcas()]
    resp, _ = _montar(fonte=Fonte(sobrepor={"ml_marcas": velho}))
    assert "10 h" in _ind(_canal(resp, "mercado_livre"), "ml_cancelamentos").aviso_frescor


def test_frescor_em_dia_nao_avisa():
    resp, _ = _montar()
    assert _ind(_canal(resp, "mercado_livre"), "ml_cancelamentos").aviso_frescor is None


# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------
def test_shopee_nao_pago_fica_fora_dos_motivos_e_aparece_como_excluido():
    resp, _ = _montar()
    bloco = _canal(resp, "shopee").motivos
    assert "unpaid_order" not in {m.codigo for m in bloco.itens}
    assert bloco.excluidos == 120
    assert bloco.total == 6 + 4 + 3 + 2 + 35


def test_shopee_grafias_do_mesmo_motivo_somam_e_prefixo_e_reconhecido():
    resp, _ = _montar()
    por_codigo = {m.codigo: m for m in _canal(resp, "shopee").motivos.itens}
    assert por_codigo["endereco"].quantidade == 10
    assert por_codigo["extravio"].natureza == "logistica"
    novo = next(m for m in por_codigo.values() if not m.mapeado)
    assert novo.rotulo == "Motivo Inedito" and novo.natureza == "comprador"


def test_shopee_indicadores_e_kokeshi_fora():
    resp, _ = _montar()
    sh = _canal(resp, "shopee")
    assert _ind(sh, "shopee_cancelamentos").valor == 50
    assert _ind(sh, "shopee_taxa_cancelamento").valor == pytest.approx(5.0)
    assert _ind(sh, "shopee_em_devolucao").valor == 7
    assert [f.marca for f in sh.cobertura.fora] == ["kokeshi"]


def test_devolucao_e_fotografia_marca_sem_linha_e_zero_medido():
    resp, _ = _montar()
    apice = next(m for m in _canal(resp, "shopee").marcas if m.marca == "apice")
    assert apice.em_devolucao == 0


def test_devolucao_fora_do_ar_nao_vira_zero():
    resp, _ = _montar(fonte=Fonte(falhas={"shopee_devolucao"}))
    sh = _canal(resp, "shopee")
    assert _ind(sh, "shopee_em_devolucao").estado == "erro"
    assert all(m.em_devolucao is None for m in sh.marcas)
    assert _ind(sh, "shopee_cancelamentos").estado == "dado"


# ---------------------------------------------------------------------------
# TikTok e filtro de marca
# ---------------------------------------------------------------------------
def test_tiktok_e_sem_fonte_explicado():
    resp, _ = _montar()
    tt = _canal(resp, "tiktok")
    assert {i.estado for i in tt.indicadores} == {"sem_fonte"}
    assert all(i.motivo_indisponivel for i in tt.indicadores)
    assert tt.pendencias


def test_filtro_so_kokeshi_nao_consulta_shopee():
    resp, fonte = _montar(brands=["kokeshi"])
    assert not any(n.startswith("shopee") for n, _ in fonte.chamadas)
    sh = _canal(resp, "shopee")
    assert sh.indicadores == [] and sh.cobertura.marcas == []
    assert [f.marca for f in sh.cobertura.fora] == ["kokeshi"]


def test_ordem_dos_canais():
    resp, _ = _montar()
    assert [c.canal for c in resp.canais] == ["mercado_livre", "shopee", "tiktok"]


# ---------------------------------------------------------------------------
# Rota
# ---------------------------------------------------------------------------
@pytest.fixture
def cliente(monkeypatch):
    app = FastAPI()
    app.include_router(rota.router)
    monkeypatch.setattr(settings, "performance_contas_atendimento_api_enabled", True)
    chamadas = []
    original = svc.montar_atendimento

    def falso(*, brands=None, dias=30):
        chamadas.append((brands, dias))
        return original(brands=brands, dias=dias, consultar=Fonte(), agora=AGORA)

    monkeypatch.setattr(svc, "montar_atendimento", falso)
    return TestClient(app), chamadas


def test_flag_desligada_e_404_sem_chamar_nada(cliente, monkeypatch):
    c, chamadas = cliente
    monkeypatch.setattr(settings, "performance_contas_atendimento_api_enabled", False)
    assert c.get(URL).status_code == 404
    assert chamadas == []


def test_rota_responde_e_repassa_filtros(cliente):
    c, chamadas = cliente
    r = c.get(URL, params={"brands": "Kokeshi,barbours", "dias": "7"})
    assert r.status_code == 200
    assert chamadas == [(["barbours", "kokeshi"], 7)]
    assert r.json()["janela"]["dias"] == 7


@pytest.mark.parametrize("params", [
    {"brands": "<script>"}, {"brands": "gocase"}, {"dias": "15"}, {"dias": "-1"}, {"dias": "abc"},
])
def test_entrada_invalida_e_422_sem_eco(cliente, params):
    c, chamadas = cliente
    r = c.get(URL, params=params)
    assert r.status_code == 422
    valor = list(params.values())[0]
    assert valor not in r.text or valor.isdigit()
    assert chamadas == []
