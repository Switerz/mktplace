"""CX-MKT-3 — LDR do Mercado Livre (`/api/v1/expedicao/ml/ldr`).

Impede: serie antes de 03/11 contaminar a taxa; dia aberto entrar; base zero
virar 0%; excecao vazar; flag desligada abrir a rota.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import ml_ldr as rota
from app.services import ml_ldr_service as svc


def _fonte(linhas=None, erro=False):
    chamadas = []

    def consultar(sql, params):
        chamadas.append(params)
        if erro:
            raise RuntimeError("postgresql://u:senha-SECRETA@h/db")
        return linhas if linhas is not None else [
            {"marca": "kokeshi", "base": 340, "atrasados": 3, "sem_marco": 1},
            {"marca": "barbours", "base": 0, "atrasados": 0, "sem_marco": 0},
        ]
    return consultar, chamadas


def test_antes_do_primeiro_dia_fechado_nao_consulta():
    consultar, chamadas = _fonte()
    r = svc.obter_ldr_ml(consultar=consultar, agora=datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc))
    assert r["availability"] == "unavailable" and r["unavailable_reason"] == "series_not_started"
    assert chamadas == []


def test_janela_comeca_no_inicio_da_serie_e_vai_ate_ontem():
    consultar, chamadas = _fonte()
    r = svc.obter_ldr_ml(consultar=consultar, agora=datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc))
    assert (chamadas[0]["inicio"], chamadas[0]["fim"]) == (date(2026, 11, 3), date(2026, 11, 5))
    assert (r["mature_days"], r["requested_days"]) == (3, 7)
    assert chamadas[0]["logisticas"] == ["cross_docking", "xd_drop_off", "drop_off"]


def test_janela_cheia_depois_de_7_dias():
    consultar, chamadas = _fonte()
    r = svc.obter_ldr_ml(consultar=consultar, agora=datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc))
    assert chamadas[0]["inicio"] == date(2026, 11, 13) and r["mature_days"] == 7


def test_taxa_e_base_zero_sem_taxa():
    consultar, _ = _fonte()
    r = svc.obter_ldr_ml(consultar=consultar, agora=datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc))
    por = {b["brand"]: b for b in r["brands"]}
    assert por["kokeshi"]["rate"] == pytest.approx(3 / 340)
    assert por["kokeshi"]["without_pickup_mark"] == 1
    assert por["barbours"]["rate"] is None


def test_erro_nao_vaza():
    consultar, _ = _fonte(erro=True)
    r = svc.obter_ldr_ml(consultar=consultar, agora=datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc))
    assert r["availability"] == "unavailable" and r["unavailable_reason"] == "source_error"
    assert "SECRETA" not in str(r)


def test_marca_sem_conta_ml_devolve_vazio_sem_consultar():
    consultar, chamadas = _fonte()
    r = svc.obter_ldr_ml(brands=["apice"], consultar=consultar,
                         agora=datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc))
    assert r["availability"] == "available" and r["brands"] == [] and chamadas == []


def test_regra_no_sql():
    sql = svc.SQL_LDR_ML
    assert "sla_status = 'delayed'" in sql
    assert "(dia_prazo + 1)::timestamp AT TIME ZONE 'America/Sao_Paulo'" in sql
    assert "cancelado_sem_coleta" in sql and "sem_marco" in sql


@pytest.fixture
def cliente(monkeypatch):
    app = FastAPI()
    app.include_router(rota.router)
    monkeypatch.setattr(settings, "expedicao_performance_api_enabled", True)
    chamadas = []
    monkeypatch.setattr(svc, "obter_ldr_ml", lambda **kw: chamadas.append(kw) or {"availability": "available"})
    return TestClient(app), chamadas


def test_rota_flag_e_validacao(cliente, monkeypatch):
    c, chamadas = cliente
    assert c.get("/api/v1/expedicao/ml/ldr", params={"days": 7, "brands": "kokeshi"}).status_code == 200
    assert chamadas == [{"dias": 7, "brands": ["kokeshi"]}]
    assert c.get("/api/v1/expedicao/ml/ldr", params={"days": 99}).status_code == 422
    assert c.get("/api/v1/expedicao/ml/ldr", params={"brands": "<x>"}).status_code == 422
    monkeypatch.setattr(settings, "expedicao_performance_api_enabled", False)
    assert c.get("/api/v1/expedicao/ml/ldr").status_code == 404
