"""CX-MKT-3 — LDR da Shopee pelo Data Mart (reserva da faixa do topo)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import ml_ldr as rota
from app.services import shopee_ldr_dm_service as svc

AGORA = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


def _fonte(linhas=None, erro=False):
    chamadas = []

    def consultar(sql, params):
        chamadas.append(params)
        if erro:
            raise RuntimeError("postgresql://u:SECRETA@h/db")
        return linhas if linhas is not None else [
            {"marca": "apice", "base": 1187, "atrasados": 18, "sem_marco": 0},
            {"marca": "lescent", "base": 0, "atrasados": 0, "sem_marco": 2},
        ]
    return consultar, chamadas


def test_janela_de_7_dias_fechados_ate_ontem():
    consultar, chamadas = _fonte()
    r = svc.obter_ldr_shopee_dm(consultar=consultar, agora=AGORA)
    assert (chamadas[0]["inicio"], chamadas[0]["fim"]) == (date(2026, 10, 28), date(2026, 11, 3))
    assert chamadas[0]["marcas"] == ["apice", "barbours", "lescent", "rituaria"]
    assert r["source"] == "datamart" and r["mature_days"] == 7


def test_taxa_e_base_zero_sem_taxa():
    consultar, _ = _fonte()
    r = svc.obter_ldr_shopee_dm(consultar=consultar, agora=AGORA)
    por = {b["brand"]: b for b in r["brands"]}
    assert por["apice"]["rate"] == pytest.approx(18 / 1187)
    assert por["lescent"]["rate"] is None and por["lescent"]["without_pickup_mark"] == 2


def test_kokeshi_sem_app_nao_consulta():
    consultar, chamadas = _fonte()
    r = svc.obter_ldr_shopee_dm(brands=["kokeshi"], consultar=consultar, agora=AGORA)
    assert r["brands"] == [] and chamadas == []


def test_erro_nao_vaza():
    consultar, _ = _fonte(erro=True)
    r = svc.obter_ldr_shopee_dm(consultar=consultar, agora=AGORA)
    assert r["availability"] == "unavailable" and "SECRETA" not in str(r)


def test_regra_no_sql():
    s = svc.SQL_LDR_SHOPEE_DM
    assert "fulfilled_by_shopee" in s and "pickup_done_time > ship_by_date" in s
    assert "cancelado_sem_coleta" in s and "pay_time IS NOT NULL" in s


def test_rota_flag_e_validacao(monkeypatch):
    app = FastAPI()
    app.include_router(rota.router_shopee)
    monkeypatch.setattr(settings, "expedicao_performance_api_enabled", True)
    chamadas = []
    monkeypatch.setattr(svc, "obter_ldr_shopee_dm", lambda **kw: chamadas.append(kw) or {"availability": "available"})
    c = TestClient(app)
    assert c.get("/api/v1/expedicao/shopee/ldr-datamart", params={"days": 7}).status_code == 200
    assert chamadas == [{"dias": 7, "brands": None}]
    assert c.get("/api/v1/expedicao/shopee/ldr-datamart", params={"days": 0}).status_code == 422
    monkeypatch.setattr(settings, "expedicao_performance_api_enabled", False)
    assert c.get("/api/v1/expedicao/shopee/ldr-datamart").status_code == 404
