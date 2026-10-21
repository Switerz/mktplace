"""EXPEDICAO-SHOPEE-LDR-1 — serving da LDR operacional da Shopee.

O que estes testes travam: a taxa so' usa vencimento MADURO, ausencia nunca
vira 0%, cobertura acima de 10% esconde a taxa (e acima de 2% avisa), as
exclusoes voltam com contador proprio, a resposta sempre diz que NAO e' a
metrica oficial, e flag desligada nao emite consulta.

O dublê devolve MAPPINGS, porque o servico le com `.mappings()`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.schemas.expedicao import ShopeeLdrResponse
from app.services import expedicao_shopee_ldr_service as svc

AGORA = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)   # 15:00 BRT
HOJE = date(2026, 10, 21)
LOTE = uuid.UUID("2b0b6f4e-5f0c-4b8e-9d55-0c9f1f3a7d11")


def venc(d: date, brand: str = "apice", *, no_prazo=0, atrasado=0, pend_venc=0,
         pend_prazo=0, fbs=0, cancel=0, nao_enviou=0, desconhecido=0, sem_marco=0,
         madura=None, effective_at=None, wm=None) -> dict:
    base = no_prazo + atrasado + pend_venc + pend_prazo
    return {
        "deadline_date": d, "shop_account": brand, "brand": brand,
        "pedidos_pagos": base + fbs + cancel + sem_marco, "excl_fbs": fbs,
        "excl_cancelado_sem_coleta": cancel, "cancelado_vendedor_nao_enviou": nao_enviou,
        "cancelado_motivo_desconhecido": desconhecido, "excl_enviado_sem_coleta": sem_marco,
        "base": base, "no_prazo": no_prazo, "atrasado": atrasado,
        "pendente_vencido": pend_venc, "pendente_no_prazo": pend_prazo,
        "is_mature": (d < HOJE) if madura is None else madura,
        "refresh_batch_id": LOTE, "effective_at": effective_at or AGORA - timedelta(hours=1),
        "source_watermark_at": wm or AGORA - timedelta(hours=2),
    }


class _Resultado:
    def __init__(self, linhas, escalar=None):
        self._linhas, self._escalar = linhas, escalar

    def mappings(self):
        return self._linhas

    def scalar(self):
        return self._escalar


class SessaoFake:
    def __init__(self, *, linhas=None, sem_prazo=None, tabela_existe=True):
        self.linhas = linhas or []
        self.sem_prazo = sem_prazo or []
        self.tabela_existe = tabela_existe
        self.consultas: list[str] = []

    def execute(self, clause, params=None):
        sql = str(clause)
        self.consultas.append(sql)
        if "to_regclass" in sql:
            return _Resultado([], escalar=self.tabela_existe)
        if "sem_prazo_daily" in sql:
            return _Resultado(self.sem_prazo)
        return _Resultado(self.linhas)


@pytest.fixture
def ligado(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_shopee_ldr_api_enabled", True, raising=False)


def ldr(linhas, sem_prazo=None, **kw):
    kw.setdefault("agora", AGORA)
    r = svc.obter_ldr(SessaoFake(linhas=linhas, sem_prazo=sem_prazo), **kw)
    ShopeeLdrResponse.model_validate(r)   # o contrato da rota aceita a resposta
    return r


# ---------------------------------------------------------------------------
# Flags e disponibilidade
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("geral,ldr_flag", [(False, False), (True, False), (False, True)])
def test_flag_desligada_nao_consulta(monkeypatch, geral, ldr_flag):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", geral, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_shopee_ldr_api_enabled", ldr_flag,
                        raising=False)
    s = SessaoFake(linhas=[venc(date(2026, 10, 20), no_prazo=5)])
    r = svc.obter_ldr(s, agora=AGORA)
    assert r["unavailable_reason"] == svc.UNAVAILABLE_DISABLED
    assert s.consultas == []


def test_flags_nascem_desligadas():
    from app.config import Settings
    campos = Settings.model_fields
    assert campos["expedicao_shopee_ldr_api_enabled"].default is False


def test_tabela_ausente_e_vazio_sao_motivos_distintos(ligado):
    r = svc.obter_ldr(SessaoFake(tabela_existe=False), agora=AGORA)
    assert r["unavailable_reason"] == svc.UNAVAILABLE_NOT_MIGRATED
    assert ldr([])["unavailable_reason"] == svc.UNAVAILABLE_NO_DATA


def test_indisponivel_ainda_declara_que_nao_e_oficial(ligado):
    r = svc.obter_ldr(SessaoFake(tabela_existe=False), agora=AGORA)
    assert r["official"] is False
    assert r["metric_name"] == "LDR operacional (coleta × prazo Shopee)"
    assert "não é a taxa de envio atrasado que a Shopee usa para penalização" in r["disclaimer"]


def test_constantes_espelham_o_pipeline():
    import importlib.util
    from pathlib import Path
    caminho = Path(__file__).resolve().parents[3] / "pipelines" / "expedicao" / "shopee_ldr.py"
    texto = caminho.read_text(encoding="utf-8")
    assert f'NOME_METRICA = "{svc.NOME_METRICA}"' in texto
    assert f"LIMIAR_AVISO_COBERTURA = {svc.LIMIAR_AVISO_COBERTURA}" in texto
    assert f"LIMIAR_OCULTA_TAXA = {svc.LIMIAR_OCULTA_TAXA}" in texto
    assert importlib.util is not None


# ---------------------------------------------------------------------------
# Taxa
# ---------------------------------------------------------------------------
def test_taxa_so_conta_vencimento_maduro(ligado):
    r = ldr([
        venc(date(2026, 10, 19), no_prazo=90, atrasado=6, pend_venc=4),
        venc(date(2026, 10, 21), no_prazo=10, pend_prazo=30),        # hoje: parcial
        venc(date(2026, 10, 22), pend_prazo=12),                      # amanha: risco
    ])["ldr"]
    assert (r["base"], r["late"], r["rate"]) == (100, 10, 0.10)
    assert (r["mature_due_days"], r["partial_due_days"]) == (1, 1)
    assert r["pending_at_risk"] == 42, "vence hoje ou amanha"
    assert [d["due_date"] for d in r["daily"]] == [date(2026, 10, 19), date(2026, 10, 21)]
    assert r["daily"][1]["rate"] is None, "dia parcial nunca mostra taxa fechada"


def test_sem_vencimento_maduro_a_taxa_e_none_nunca_zero(ligado):
    r = ldr([venc(date(2026, 10, 21), pend_prazo=5)])["ldr"]
    assert r["rate"] is None and r["base"] == 0


def test_exclusoes_voltam_com_contador_proprio(ligado):
    r = ldr([venc(date(2026, 10, 19), no_prazo=50, fbs=70, cancel=3, nao_enviou=2,
                  desconhecido=1, sem_marco=0)],
            sem_prazo=[{"paid_date": date(2026, 10, 18), "brand": "apice", "sem_prazo": 0}])
    assert r["exclusions"] == {
        "fbs": 70, "cancelled_without_pickup": 3, "cancelled_seller_did_not_ship": 2,
        "cancelled_unknown_reason": 1, "shipped_without_pickup": 0, "without_deadline": 0,
    }
    assert r["ldr"]["base"] == 50, "FBS e cancelado nao entram na base"
    assert any(a["code"] == "motivo_cancelamento_desconhecido" for a in r["alerts"])


# ---------------------------------------------------------------------------
# Cobertura
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("base,sem_marco,sem_prazo,estado,taxa_some", [
    (100, 0, 0, "completa", False),
    (98, 2, 0, "completa", False),      # 2/100 = 2%: no limite, sem aviso
    (98, 2, 1, "aviso", False),         # 3/101
    (90, 10, 0, "aviso", False),        # 10/100 = 10%: no limite, taxa aparece
    (98, 5, 6, "insuficiente", True),   # 11/109 > 10%
])
def test_cobertura(ligado, base, sem_marco, sem_prazo, estado, taxa_some):
    r = ldr([venc(date(2026, 10, 19), no_prazo=base - 1, atrasado=1, sem_marco=sem_marco)],
            sem_prazo=[{"paid_date": date(2026, 10, 18), "brand": "apice",
                        "sem_prazo": sem_prazo}])
    assert r["coverage"]["state"] == estado
    assert (r["ldr"]["rate"] is None) is taxa_some
    if taxa_some:
        assert r["ldr"]["rate_hidden_reason"] == "coverage_insufficient"
        assert any(a["code"] == "cobertura_insuficiente" for a in r["alerts"])
    if estado == "aviso":
        assert any(a["code"] == "cobertura_incompleta" for a in r["alerts"])


# ---------------------------------------------------------------------------
# Marca e frescor
# ---------------------------------------------------------------------------
def test_filtro_de_marca_recorta_tudo(ligado):
    linhas = [venc(date(2026, 10, 19), "apice", no_prazo=10, atrasado=10),
              venc(date(2026, 10, 19), "barbours", no_prazo=100)]
    sp = [{"paid_date": date(2026, 10, 18), "brand": "barbours", "sem_prazo": 50}]
    r = ldr(linhas, sp, brands=["APICE"])
    assert [m["brand"] for m in r["brands"]] == ["apice"]
    assert (r["ldr"]["base"], r["ldr"]["rate"]) == (20, 0.5)
    assert r["exclusions"]["without_deadline"] == 0, "sem prazo de outra marca nao entra"


def test_marca_sem_linha_e_indisponivel_nao_zero(ligado):
    r = ldr([venc(date(2026, 10, 19), "apice", no_prazo=10)], brands=["kokeshi"])
    assert r["unavailable_reason"] == svc.UNAVAILABLE_NO_DATA


def test_por_marca_nao_soma_marcas_e_tem_a_propria_cobertura(ligado):
    r = ldr([venc(date(2026, 10, 19), "apice", no_prazo=8, atrasado=2),
             venc(date(2026, 10, 19), "barbours", no_prazo=50, sem_marco=20)])
    por = {m["brand"]: m for m in r["brands"]}
    assert por["apice"]["rate"] == 0.2
    assert por["barbours"]["coverage_state"] == "insuficiente"
    assert por["barbours"]["rate"] is None


def test_fonte_vencida_e_sinalizada(ligado):
    velho = AGORA - timedelta(hours=7)
    r = ldr([venc(date(2026, 10, 19), no_prazo=10, effective_at=velho)])
    assert r["snapshot"]["stale"] is True
    assert r["alerts"][0]["code"] == "fonte_vencida"
    fresco = ldr([venc(date(2026, 10, 19), no_prazo=10)])
    assert fresco["snapshot"]["stale"] is False


def test_janela_limitada_a_35_dias(ligado):
    s = SessaoFake(linhas=[venc(date(2026, 10, 19), no_prazo=1)])
    r = svc.obter_ldr(s, dias=90, agora=AGORA)
    assert r["ldr"]["days"] == 35


# ---------------------------------------------------------------------------
# Rota: a fiacao inteira
# ---------------------------------------------------------------------------
def _cliente(sessao):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: sessao
    return TestClient(app), app


def test_rota_repassa_dias_e_marca(ligado):
    s = SessaoFake(linhas=[venc(date(2026, 10, 19), "apice", no_prazo=3),
                           venc(date(2026, 10, 19), "barbours", no_prazo=7)])
    cliente, app = _cliente(s)
    try:
        r = cliente.get("/api/v1/expedicao/shopee/ldr?days=10&brands=barbours")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["ldr"]["days"] == 10
    assert [m["brand"] for m in corpo["brands"]] == ["barbours"]
    assert corpo["official"] is False


@pytest.mark.parametrize("q", ["days=0", "days=36", "brands=,"])
def test_rota_recusa_entrada_invalida_sem_eco(ligado, q):
    cliente, app = _cliente(SessaoFake())
    try:
        r = cliente.get(f"/api/v1/expedicao/shopee/ldr?{q}")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 422


def test_rota_com_flag_desligada_responde_sem_banco(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_shopee_ldr_api_enabled", False, raising=False)
    cliente, app = _cliente(None)
    try:
        r = cliente.get("/api/v1/expedicao/shopee/ldr")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json()["unavailable_reason"] == "disabled"
