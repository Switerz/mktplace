"""EXPEDICAO-ATRASO-7D — % de vencidos pela regra oficial da Shopee.

Pedido do usuario (03/11/2026): o % de vencidos deixa de ser `vencidos ÷
backlog` e passa a ser `atrasados ÷ pedidos com vencimento nos ultimos 7
dias`, como a Shopee mede a taxa de envio atrasado. O pipeline grava as
contagens por conta no resumo; a API so' le e soma.

O que estes testes travam:
  - o SELECT le as colunas por `to_jsonb(r) ->> ...` (roda sem a migration);
  - texto do `->>` vira int/date; ausente vira None, nunca 0;
  - o total do canal soma SO' as contas com o calculo;
  - a janela da fotografia e' a uniao das janelas das contas.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.schemas.expedicao import ExpedicaoResponse
from app.services import expedicao_service as svc
from tests.test_expedicao_api import CONTAS, SessaoFake, resumo


@pytest.fixture
def ligada(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", "", raising=False)


def _com_7d(brand, backlog, due, late, inicio="2026-10-27", fim="2026-11-02"):
    """Resumo como o banco devolve pelo `->>`: TEXTO."""
    r = resumo(brand, backlog)
    r.update({"due_7d_count": str(due), "late_7d_count": str(late),
              "late_7d_window_start": inicio, "late_7d_window_end": fim})
    return r


def test_select_le_as_colunas_sem_depender_da_migration(ligada):
    s = SessaoFake()
    svc.get_expedicao(s)
    sql = next(q for q, _ in s.consultas if "FROM marts.expedicao_refresh_run r" in q)
    for c in ("due_7d_count", "late_7d_count", "late_7d_window_start", "late_7d_window_end"):
        assert f"to_jsonb(r) ->> '{c}' AS {c}" in sql


def test_sem_calculo_publicado_tudo_nulo_nunca_zero(ligada):
    r = svc.get_expedicao(SessaoFake())
    assert r["totals"]["due_7d_count"] is None
    assert r["totals"]["late_7d_count"] is None
    assert r["snapshot"]["late_7d_window_start"] is None
    assert all(c["due_7d_count"] is None and c["late_7d_count"] is None for c in r["accounts"])
    ExpedicaoResponse.model_validate(r)


def test_contas_totais_e_janela(ligada):
    (b1, n1), (b2, n2), (b3, n3), (b4, n4) = CONTAS
    resumos = [
        _com_7d(b1, n1, 1000, 30),
        _com_7d(b2, n2, 200, 4, inicio="2026-10-28"),
        resumo(b3, n3),                       # conta sem calculo: fora da soma
        _com_7d(b4, n4, 0, 0, fim="2026-11-01"),
    ]
    r = svc.get_expedicao(SessaoFake(resumos=resumos))
    assert r["totals"]["due_7d_count"] == 1200
    assert r["totals"]["late_7d_count"] == 34
    assert r["snapshot"]["late_7d_window_start"] == date(2026, 10, 27)
    assert r["snapshot"]["late_7d_window_end"] == date(2026, 11, 2)
    por_marca = {c["brand"]: c for c in r["accounts"]}
    assert (por_marca[b1]["due_7d_count"], por_marca[b1]["late_7d_count"]) == (1000, 30)
    assert por_marca[b3]["due_7d_count"] is None, "sem calculo nao vira zero"
    assert por_marca[b4]["due_7d_count"] == 0, "zero medido continua zero"
    ExpedicaoResponse.model_validate(r)


def test_normalizacao_aceita_texto_numero_e_vazio():
    r = svc._normalizar_atraso_7d({"due_7d_count": "12", "late_7d_count": 3,
                                    "late_7d_window_start": "2026-11-03",
                                    "late_7d_window_end": ""})
    assert (r["due_7d_count"], r["late_7d_count"]) == (12, 3)
    assert r["late_7d_window_start"] == date(2026, 11, 3)
    assert r["late_7d_window_end"] is None
    assert svc._normalizar_atraso_7d({})["due_7d_count"] is None


def test_pedidos_7d_do_gerencial_por_marca_e_no_total(ligada, monkeypatch):
    """EXPEDICAO-ABA-7D: o total da aba e' o numero do Gerencial."""
    (b1, n1), (b2, n2), (b3, n3), (b4, n4) = CONTAS
    monkeypatch.setattr(svc, "_pedidos_7d", lambda db, canal, ef: {b1: 3015, b2: 3081, b4: 695})
    r = svc.get_expedicao(SessaoFake())
    por = {c["brand"]: c for c in r["accounts"]}
    assert por[b1]["orders_7d_count"] == 3015
    assert por[b3]["orders_7d_count"] is None, "marca sem total no Gerencial: nulo"
    assert r["totals"]["orders_7d_count"] == 3015 + 3081 + 695
    assert r["snapshot"]["orders_7d_window_end"] - r["snapshot"]["orders_7d_window_start"] == timedelta(days=6)
    ExpedicaoResponse.model_validate(r)


def test_pedidos_7d_falha_vira_sem_total(ligada, monkeypatch):
    r = svc.get_expedicao(SessaoFake())
    assert r["totals"]["orders_7d_count"] is None
    assert all(c["orders_7d_count"] is None for c in r["accounts"])


def test_soma_de_48h_e_travados_so_das_contas_com_calculo():
    resumos = [
        {"due_7d_count": 10, "late_7d_count": 1, "over_48h_7d_count": 2, "stalled_7d_count": None},
        {"due_7d_count": 5, "late_7d_count": 0, "over_48h_7d_count": 1, "stalled_7d_count": 3},
        {"due_7d_count": None, "late_7d_count": None},
    ]
    s = svc._somar_atraso_7d(resumos)
    assert (s["over_48h_7d_count"], s["stalled_7d_count"]) == (3, 3)
