"""
REGIONAL-SOURCE-RECOVERY-2 — cobertura regional por canal.

Cenario real medido em 16/10/2026: ML carregado ate 15/10 e Shopee congelada
em 24/08 (fonte descontinuada, endereco mascarado na API). O `refreshed_at`
(instante da carga mais recente de QUALQUER canal) parecia saudavel e a
Shopee aparecia com 0 em set/out. Estes testes travam:

- status e `dados_ate` por canal, com motivo;
- `dados_completos_ate` = data ate a qual o TOTAL cobre todos os canais;
- ponto de tendencia marca os canais ausentes (ausencia, nao zero);
- o payload passa pelo `response_model` HTTP sem perder os campos.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps.period import EffectivePeriod
from app.main import app
from app.services import regioes_service as svc
from app.services.performance_service import ML_ID, SHOPEE_ID, TIKTOK_ID

HOJE = date(2026, 10, 16)
SET_OUT = EffectivePeriod(start=date(2026, 9, 1), end=date(2026, 10, 15))
CARGA_ATUAL = {ML_ID: date(2026, 10, 15), SHOPEE_ID: date(2026, 8, 24)}


def _por_canal(resultado):
    return {c["channel"]: c for c in resultado["cobertura_por_canal"]}


# ---------------------------------------------------------------------------
# build_cobertura_canais — puro
# ---------------------------------------------------------------------------
def test_ml_atual_e_shopee_defasada():
    r = svc.build_cobertura_canais([TIKTOK_ID, ML_ID, SHOPEE_ID], CARGA_ATUAL, SET_OUT, HOJE)
    c = _por_canal(r)

    assert c["ml"]["status"] == "atualizada"
    assert c["ml"]["dados_ate"] == date(2026, 10, 15)
    assert c["ml"]["motivo"] is None and c["ml"]["motivo_detalhe"] is None

    assert c["shopee"]["status"] == "defasada"
    assert c["shopee"]["dados_ate"] == date(2026, 8, 24)
    assert c["shopee"]["motivo"] == "carga_defasada"
    assert "mascarado" in c["shopee"]["motivo_detalhe"]

    assert c["tiktok"]["status"] == "sem_cobertura"
    assert c["tiktok"]["dados_ate"] is None
    assert c["tiktok"]["motivo"] == "sem_fonte_regional"

    # O total so' cobre TODOS os canais com fonte ate 24/08 — nao ate 15/10.
    assert r["dados_completos_ate"] == date(2026, 8, 24)
    assert r["canais_defasados"] == ["shopee"]


def test_data_recente_do_ml_nao_vira_data_do_total():
    """A regra que o PR precisa garantir: o ML em dia nunca empurra a data
    do total para frente enquanto a Shopee estiver parada."""
    r = svc.build_cobertura_canais([ML_ID, SHOPEE_ID], CARGA_ATUAL, SET_OUT, HOJE)
    assert r["dados_completos_ate"] != CARGA_ATUAL[ML_ID]
    assert r["dados_completos_ate"] == min(CARGA_ATUAL.values())


def test_esperado_ate_nunca_passa_de_d_menos_1():
    periodo_com_futuro = EffectivePeriod(start=date(2026, 10, 1), end=date(2026, 10, 31))
    r = svc.build_cobertura_canais([ML_ID], {ML_ID: date(2026, 10, 15)}, periodo_com_futuro, HOJE)
    assert _por_canal(r)["ml"]["esperado_ate"] == date(2026, 10, 15)
    assert _por_canal(r)["ml"]["status"] == "atualizada"


def test_tolerancia_de_um_dia_e_nao_mais():
    um_dia = svc.build_cobertura_canais([ML_ID], {ML_ID: date(2026, 10, 14)}, SET_OUT, HOJE)
    dois_dias = svc.build_cobertura_canais([ML_ID], {ML_ID: date(2026, 10, 13)}, SET_OUT, HOJE)
    assert _por_canal(um_dia)["ml"]["status"] == "atualizada"
    assert _por_canal(dois_dias)["ml"]["status"] == "defasada"


def test_periodo_anterior_ao_congelamento_e_shopee_atualizada():
    julho = EffectivePeriod(start=date(2026, 7, 1), end=date(2026, 7, 31))
    r = svc.build_cobertura_canais([ML_ID, SHOPEE_ID], CARGA_ATUAL, julho, HOJE)
    assert _por_canal(r)["shopee"]["status"] == "atualizada"
    assert _por_canal(r)["shopee"]["motivo_detalhe"] is None
    assert r["canais_defasados"] == []


def test_canal_sem_nenhuma_carga_e_sem_carga_e_anula_a_data_do_total():
    r = svc.build_cobertura_canais([ML_ID, SHOPEE_ID], {ML_ID: date(2026, 10, 15)}, SET_OUT, HOJE)
    assert _por_canal(r)["shopee"]["status"] == "sem_carga"
    assert _por_canal(r)["shopee"]["dados_ate"] is None
    assert r["dados_completos_ate"] is None
    assert r["canais_defasados"] == ["shopee"]


def test_so_tiktok_nao_tem_data_de_total():
    r = svc.build_cobertura_canais([TIKTOK_ID], {}, SET_OUT, HOJE)
    assert r["dados_completos_ate"] is None
    assert r["canais_defasados"] == []


def test_canais_ausentes_no_bucket_diario_e_mensal():
    cob = svc.build_cobertura_canais([TIKTOK_ID, ML_ID, SHOPEE_ID], CARGA_ATUAL, SET_OUT, HOJE)["cobertura_por_canal"]
    assert svc.canais_ausentes_no_bucket(cob, date(2026, 8, 24)) == []
    assert svc.canais_ausentes_no_bucket(cob, date(2026, 8, 25)) == ["shopee"]
    # Agosto mensal termina em 31/08: Shopee so' tem ate 24/08 -> ponto parcial.
    fim_agosto = svc._fim_do_bucket(date(2026, 8, 1), "month", date(2026, 10, 15))
    assert fim_agosto == date(2026, 8, 31)
    assert svc.canais_ausentes_no_bucket(cob, fim_agosto) == ["shopee"]
    # TikTok nunca entra: a ausencia dele e' estrutural e ja tem campo proprio.
    assert "tiktok" not in svc.canais_ausentes_no_bucket(cob, date(2026, 10, 15))


# ---------------------------------------------------------------------------
# Integracao com as consultas (Session falsa)
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Responde pela FORMA da consulta, nao pela ordem — o teste nao quebra
    se a ordem das consultas mudar."""

    def __init__(self, principal, dados_ate, refreshed_at):
        self.principal, self.dados_ate, self.refreshed_at = principal, dados_ate, refreshed_at

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "MAX(f.date) AS dados_ate" in sql:
            return _Result(self.dados_ate)
        if "MAX(f.ingested_at) AS refreshed_at" in sql:
            return _Result([{"refreshed_at": self.refreshed_at}])
        return _Result(self.principal)


DADOS_ATE_ROWS = [
    {"marketplace_id": ML_ID, "dados_ate": date(2026, 10, 15)},
    {"marketplace_id": SHOPEE_ID, "dados_ate": date(2026, 8, 24)},
]
CARGA_RECENTE_ML = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)
SUMMARY_ROW = [{
    "bucket": date(2026, 9, 10),  # lido so' pela tendencia
    "gmv": 1000.0, "orders": 10, "units_sold": 0, "ufs_com_venda": 3,
    "uf_known_orders": 10, "uf_eligible_orders": 10,
    "shipping_cost_covered_orders": 10, "shipping_cost_eligible_orders": 10,
    "seller_shipping_cost": 50.0,
}]


def test_summary_expoe_cobertura_e_nao_deixa_refreshed_at_falar_pelo_total():
    db = _Session(SUMMARY_ROW, DADOS_ATE_ROWS, CARGA_RECENTE_ML)
    r = svc.get_summary(db, [TIKTOK_ID, ML_ID, SHOPEE_ID], None, SET_OUT, channels="all", hoje=HOJE)

    assert r["refreshed_at"].startswith("2026-10-16")  # a carga do ML e' recente...
    assert r["dados_completos_ate"] == date(2026, 8, 24)  # ...mas o total nao.
    assert r["canais_defasados"] == ["shopee"]
    assert _por_canal(r)["shopee"]["status"] == "defasada"
    assert r["channels_sem_cobertura_regional"] == ["tiktok"]


def test_trend_marca_shopee_ausente_nos_dias_apos_o_congelamento():
    periodo = EffectivePeriod(start=date(2026, 8, 23), end=date(2026, 8, 26))
    rows = [{"bucket": date(2026, 8, d), "gmv": 10.0, "orders": 1,
             "uf_known_orders": 1, "uf_eligible_orders": 1} for d in (23, 24, 25, 26)]
    db = _Session(rows, DADOS_ATE_ROWS, CARGA_RECENTE_ML)
    r = svc.get_trend(db, [ML_ID, SHOPEE_ID], None, periodo, hoje=HOJE)
    assert [p["canais_ausentes"] for p in r["data"]] == [[], [], ["shopee"], ["shopee"]]


def test_brand_filter_tambem_chega_na_consulta_de_dados_ate():
    capturados = []

    class _Captura(_Session):
        def execute(self, stmt, params=None):
            if "MAX(f.date) AS dados_ate" in str(stmt):
                capturados.append(params)
            return super().execute(stmt, params)

    db = _Captura(SUMMARY_ROW, DADOS_ATE_ROWS, None)
    svc.get_summary(db, [ML_ID, SHOPEE_ID], ["barbours"], SET_OUT, hoje=HOJE)
    assert capturados and capturados[0]["brand_keys"] == ["barbours"]
    assert "start" not in capturados[0]  # dados_ate nunca e' recortado pelo periodo


# ---------------------------------------------------------------------------
# Borda HTTP — o response_model preserva os campos novos
# ---------------------------------------------------------------------------
@pytest.fixture
def client_com_carga_atual(monkeypatch):
    monkeypatch.setattr(svc, "today_brt", lambda: HOJE)
    app.dependency_overrides[get_db] = lambda: _Session(SUMMARY_ROW, DADOS_ATE_ROWS, CARGA_RECENTE_ML)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_http_summary_serializa_cobertura_por_canal(client_com_carga_atual):
    resp = client_com_carga_atual.get(
        "/api/v1/regioes/summary",
        params={"channels": "tiktok,ml,shopee", "date_from": "2026-09-01", "date_to": "2026-10-15"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dados_completos_ate"] == "2026-08-24"
    assert body["canais_defasados"] == ["shopee"]
    shopee = next(c for c in body["cobertura_por_canal"] if c["channel"] == "shopee")
    assert shopee == {
        "channel": "shopee", "marketplace_id": SHOPEE_ID, "status": "defasada",
        "dados_ate": "2026-08-24", "esperado_ate": "2026-10-15",
        "motivo": "carga_defasada", "motivo_detalhe": shopee["motivo_detalhe"],
    }
    assert shopee["motivo_detalhe"]


def test_http_trend_serializa_canais_ausentes(client_com_carga_atual):
    resp = client_com_carga_atual.get(
        "/api/v1/regioes/trend",
        params={"channels": "ml,shopee", "date_from": "2026-09-01", "date_to": "2026-10-15"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["canais_defasados"] == ["shopee"]
    assert all(p["canais_ausentes"] == ["shopee"] for p in body["data"])
