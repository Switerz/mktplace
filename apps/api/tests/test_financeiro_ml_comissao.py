"""
MARGEM-REAL-2 — o contrato de `/financeiro` passa a declarar a comissão do ML.

Até este gate, `total_fees` do Mercado Livre era NULL na fato e a resposta não
tinha campo algum de tarifa para o canal: a tela conseguia dizer quanto o ML
custou em Ads e frete, e nada sobre quanto o marketplace cobrou.

O que se trava aqui:

  * `ml_fees` e `ml_avg_fee_pct` existem por marca e no total;
  * o take rate é razão sobre o GMV da MESMA linha;
  * ausência continua nula — nunca zero;
  * `ml_total_cost_pct` NÃO muda de valor (decisão explícita do gate);
  * nada na resposta se chama margem ou lucro.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.schemas.performance import FinanceiroResponse
from app.services import performance_service as perf_svc

TIKTOK_ID, ML_ID, SHOPEE_ID = 1, 2, 3


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return None


class FakeSession:
    """Devolve linhas como `.mappings().all()` devolve: dicionários.

    A fato é lida por nome de coluna (`r["total_fees"]`), então um duplo que
    devolvesse tuplas passaria no teste e quebraria no primeiro contato com o
    banco real.
    """

    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt, params=None):
        return _Rows(self._rows)


def _linha_ml(brand="kokeshi", gmv=1_000_000.0, total_fees=170_000.0, **over):
    row = {
        "brand_key": brand,
        "marketplace_id": ML_ID,
        "gmv": gmv,
        "total_settlement": 0,
        "total_fees": total_fees,
        "ad_spend": 50_000.0,
        "ad_revenue": 400_000.0,
        "ad_clicks": 300,
        "ad_impressions": 10_000,
        "seller_shipping_cost": 120_000.0,
    }
    row.update(over)
    return row


def _financeiro(rows):
    return perf_svc.get_financeiro(FakeSession(rows), "all", 2026, 7)


def _marca(res, brand):
    return next(b for b in res["brands"] if b["brand"] == brand)


# ---------------------------------------------------------------------------
# O campo passa a existir
# ---------------------------------------------------------------------------

def test_comissao_do_ml_aparece_por_marca():
    res = _financeiro([_linha_ml()])
    linha = _marca(res, "kokeshi")
    assert linha["ml_fees"] == 170_000.0
    assert linha["ml_avg_fee_pct"] == 17.0


def test_comissao_do_ml_aparece_no_total():
    res = _financeiro([
        _linha_ml("kokeshi", gmv=1_000_000.0, total_fees=170_000.0),
        _linha_ml("lescent", gmv=1_000_000.0, total_fees=120_000.0),
    ])
    assert res["kpis"]["ml_fees"] == 290_000.0
    assert res["kpis"]["ml_avg_fee_pct"] == 14.5


def test_take_rate_usa_o_gmv_da_mesma_linha():
    """Razão sobre outro denominador seria um número plausível e errado."""
    res = _financeiro([_linha_ml(gmv=2_000_000.0, total_fees=170_000.0)])
    assert _marca(res, "kokeshi")["ml_avg_fee_pct"] == 8.5


def test_schema_publica_os_dois_campos():
    """Sem o campo no schema Pydantic, o serviço calcula e a resposta descarta
    em silêncio — o pior desfecho possível, porque parece funcionar."""
    res = _financeiro([_linha_ml()])
    validado = FinanceiroResponse.model_validate(res)
    assert validado.kpis.ml_fees == 170_000.0
    assert validado.kpis.ml_avg_fee_pct == 17.0
    assert validado.brands[0].ml_fees == 170_000.0
    assert validado.brands[0].ml_avg_fee_pct == 17.0


# ---------------------------------------------------------------------------
# Ausência e bordas
# ---------------------------------------------------------------------------

def test_comissao_nula_nao_vira_zero():
    res = _financeiro([_linha_ml(total_fees=None)])
    linha = _marca(res, "kokeshi")
    assert linha["ml_fees"] is None
    assert linha["ml_avg_fee_pct"] is None


def test_gmv_zero_nao_gera_take_rate():
    res = _financeiro([_linha_ml(gmv=0.0, total_fees=170_000.0)])
    assert _marca(res, "kokeshi")["ml_avg_fee_pct"] is None


def test_sem_linha_de_ml_a_resposta_nao_inventa_comissao():
    res = _financeiro([{
        "brand_key": "apice", "marketplace_id": TIKTOK_ID,
        "gmv": 500_000.0, "total_settlement": 0, "total_fees": -100_000.0,
        "ad_spend": 0, "ad_revenue": 0, "ad_clicks": 0, "ad_impressions": 0,
        "seller_shipping_cost": 0,
    }])
    assert _marca(res, "apice").get("ml_fees") is None
    assert res["kpis"]["ml_fees"] is None


def test_comissao_negativa_na_fonte_e_exibida_em_magnitude():
    """O armazenamento preserva o sinal; a apresentação normaliza. Se um dia a
    fonte inverter a convenção, a tela não passa a mostrar take rate negativo."""
    res = _financeiro([_linha_ml(total_fees=-170_000.0)])
    linha = _marca(res, "kokeshi")
    assert linha["ml_fees"] == 170_000.0
    assert linha["ml_avg_fee_pct"] == 17.0


# ---------------------------------------------------------------------------
# O que este gate deliberadamente NÃO muda
# ---------------------------------------------------------------------------

def test_ml_total_cost_pct_continua_sendo_ads_mais_frete():
    """Incluir a comissão mudaria o valor de um KPI já exibido. Essa decisão
    não pertence a este gate, e o teste trava o valor para que a mudança, se
    vier, seja deliberada."""
    res = _financeiro([_linha_ml(gmv=1_000_000.0)])
    linha = _marca(res, "kokeshi")
    # (50_000 + 120_000) / 1_000_000 = 17,0% — sem os 170_000 de comissão
    assert linha["ml_total_cost_pct"] == 17.0
    assert linha["ml_fees"] == 170_000.0


def test_shopee_e_tiktok_nao_sao_afetados():
    res = _financeiro([
        _linha_ml(),
        {
            "brand_key": "kokeshi", "marketplace_id": SHOPEE_ID,
            "gmv": 1_000_000.0, "total_settlement": 700_000.0, "total_fees": 260_000.0,
            "ad_spend": 0, "ad_revenue": 0, "ad_clicks": 0, "ad_impressions": 0,
            "seller_shipping_cost": 0,
        },
    ])
    linha = _marca(res, "kokeshi")
    assert linha["shopee_fees"] == 260_000.0
    assert linha["shopee_avg_fee_pct"] == 26.0
    assert linha["ml_fees"] == 170_000.0


def test_resposta_nao_declara_margem_nem_lucro():
    """O gate entrega custo de marketplace, não margem. Um campo com esse nome
    seria lido como resultado — e CMV, frete e imposto não estão aqui."""
    res = _financeiro([_linha_ml()])
    chaves = set(res["kpis"]) | set(res["brands"][0])
    proibidas = [k for k in chaves if any(t in k.lower() for t in ("margem", "margin", "lucro", "profit"))]
    assert proibidas == []


@pytest.mark.parametrize("campo", ["ml_fees", "ml_avg_fee_pct"])
def test_campos_existem_no_schema_declarado(campo):
    from app.schemas.performance import FinanceiroBrandRow, FinanceiroKpis

    assert campo in FinanceiroKpis.model_fields
    assert campo in FinanceiroBrandRow.model_fields
