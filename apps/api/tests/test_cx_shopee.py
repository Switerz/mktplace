"""Aba CX (menu principal) — `GET /api/v1/cx/shopee` (04/11/2026).

Junta o recorte de atendimento da Saude da Conta (fotografia oficial) com
cancelamentos e devolucoes dos pedidos Shopee. Uma fonte caida nao derruba a
outra.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import cx_atendimento as rota
from app.services import cx_atendimento_service as svc
from app.services import performance_oficial_service as po
from tests import test_cx_atendimento as base_cx
from tests import test_performance_oficial as base_po

AGORA = base_cx.AGORA


class FonteCx:
    """Roteia o SQL dos pedidos (CX) e o da Saude da Conta (Performance)."""

    def __init__(self, falhas_cx=(), falhas_po=(), metricas=None):
        self.cx = base_cx.Fonte(falhas=falhas_cx)
        self.po = base_po.Fonte(falhas=falhas_po, metricas=metricas)

    def __call__(self, sql, params):
        if sql in (po.SQL_EXISTE, po.SQL_ML, po.SQL_SHOPEE, po.SQL_SHOPEE_METRICAS):
            return self.po(sql, params)
        return self.cx(sql, params)


def _montar(**kw):
    fonte = kw.pop("fonte", None) or FonteCx()
    return svc.montar_cx_shopee(consultar=fonte, agora=AGORA, **kw), fonte


def test_chat_traz_so_metricas_de_atendimento_com_a_meta_da_shopee():
    resp, _ = _montar()
    assert resp.chat.estado == "dado"
    conta = resp.chat.contas[0]
    assert conta.marca == "apice"
    assert [m.codigo for m in conta.metricas] == ["shopee_11"]      # envio fica de fora
    m = conta.metricas[0]
    assert m.rotulo == "Taxa de resposta do chat" and m.valor == 71 and m.meta == 80
    assert m.situacao == "fora"
    assert conta.destaques == [] and conta.tom == "atencao"
    assert conta.resumo == "1 métrica(s) de atendimento fora da meta"


def test_atendimento_e_o_canal_shopee_dos_pedidos():
    resp, _ = _montar(dias=7)
    assert resp.atendimento.canal == "shopee"
    assert resp.janela.dias == 7
    codigos = [i.codigo for i in resp.atendimento.indicadores]
    assert codigos == ["shopee_cancelamentos", "shopee_taxa_cancelamento", "shopee_em_devolucao"]


def test_saude_da_conta_caida_nao_derruba_cancelamentos():
    fonte = FonteCx(falhas_po={"shopee"})
    resp, _ = _montar(fonte=fonte)
    assert resp.chat.estado == "erro"
    assert resp.atendimento.indicadores[0].estado == "dado"
    assert base_cx.SEGREDO not in resp.model_dump_json()


def test_pedidos_caidos_nao_derrubam_o_chat():
    fonte = FonteCx(falhas_cx={"shopee_marcas", "shopee_motivos", "shopee_devolucao"})
    resp, _ = _montar(fonte=fonte)
    assert resp.chat.estado == "dado" and resp.chat.contas
    assert all(i.estado == "erro" for i in resp.atendimento.indicadores)
    assert base_cx.SEGREDO not in resp.model_dump_json()


@pytest.fixture
def cliente(monkeypatch):
    app = FastAPI()
    app.include_router(rota.router)
    monkeypatch.setattr(settings, "cx_api_enabled", True)
    chamadas = []
    original = svc.montar_cx_shopee

    def falso(*, brands=None, dias=30):
        chamadas.append((brands, dias))
        return original(brands=brands, dias=dias, consultar=FonteCx(), agora=AGORA)

    monkeypatch.setattr(svc, "montar_cx_shopee", falso)
    return TestClient(app), chamadas


def test_rota_shopee_responde_e_repassa_filtros(cliente):
    c, chamadas = cliente
    r = c.get("/api/v1/cx/shopee", params={"brands": "apice", "dias": "90"})
    assert r.status_code == 200
    assert chamadas == [(["apice"], 90)]
    corpo = r.json()
    assert set(corpo) == {"gerado_em", "janela", "chat", "atendimento"}


def test_flag_desligada_e_404(cliente, monkeypatch):
    c, chamadas = cliente
    monkeypatch.setattr(settings, "cx_api_enabled", False)
    assert c.get("/api/v1/cx/shopee").status_code == 404
    assert chamadas == []


def test_flag_nasce_ligada():
    from app.config import Settings
    assert Settings.model_fields["cx_api_enabled"].default is True
