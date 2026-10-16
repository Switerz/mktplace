"""DRE-CMV-CMP-1 — o cenario estimado consome o CMV PROVISORIO por CMP.

Valores sinteticos. O que se prova:
  * sem CMV fiscal e com cobertura >= minimo: o provisorio entra, rotulado, e o
    cenario fica parcial (nunca calculavel);
  * cobertura abaixo do minimo ou celula ausente na fonte: nao_calculavel com motivo;
  * CONTRAPROVA: com CMV fiscal utilizavel, o provisorio nunca o substitui;
  * a DRE conhecida/realizada nao muda;
  * a rota funciona com a tabela 027 ausente e expoe o provisorio quando publicado.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from app.services import dre_premissas as DP
from app.services import dre_service as dre
from tests.test_dre_mvp import AGORA, HOJE, JUL, cmv, mart
from tests.test_dre_premissas import L, prem
from tests.test_dre_premissas import rota as rota  # noqa: F401 — fixture reutilizada


def prov(canal="ml", marca="kokeshi", mes=JUL, *, cobertura="0.95", cmv_valor="250000", status="calculado",
         lote_status="success"):
    ausente = status == "ausente_na_fonte"
    return {"competencia": mes, "canal": canal, "marca": marca, "status": status, "data_corte": date(2026, 7, 31),
            "receita_fonte": None if ausente else Decimal("1000000"),
            "receita_coberta": None if ausente else Decimal("1000000") * Decimal(cobertura),
            "cobertura": None if ausente else Decimal(cobertura),
            "cmv_provisorio": None if ausente else Decimal(cmv_valor),
            "unidades_sem_cmp": None if ausente else Decimal("120"), "skus_total": 0 if ausente else 50,
            "skus_sem_cmp": 0 if ausente else 5, "skus_conflito": 0 if ausente else 1,
            "receita_gtin": None if ausente else Decimal("500000"), "receita_volume": None if ausente else Decimal(0),
            "receita_codigo_provisorio": None if ausente else Decimal("450000"),
            "confianca": None if ausente else "baixa", "regra_temporal": "entidade_vigencia",
            "lote_status": lote_status}


def montar(*, insumos=(), provisorio=None, premissas=None):
    premissas = prem(JUL, "kokeshi") if premissas is None else premissas
    idx = None if provisorio is None else {(r["competencia"], r["canal"], r["marca"]): r for r in provisorio}
    return dre.montar_dre(inicio=JUL, fim=date(2026, 7, 31), canais=["tiktok", "ml", "shopee"],
                          marcas=["kokeshi", "barbours"], mart_rows=[mart("ml", "kokeshi")], afiliado_rows=[],
                          insumos=dre.LeituraInsumos(tabela_existe=True, linhas=list(insumos)), hoje=HOJE, agora=AGORA,
                          premissas=DP.LeituraPremissas(tabela_existe=True, linhas=premissas), cmv_provisorio=idx)


def test_sem_cmv_fiscal_o_provisorio_entra_rotulado_e_o_cenario_fica_parcial():
    body = montar(provisorio=[prov()])
    cel = body["celulas_base"][0]
    cen = cel["cenario_premissas"]
    cm = L(cen, "cmv")
    assert (cm["valor"], cm["origem"], cm["status"]) == (250_000.0, "cmp_provisorio", "provisorio_cmp")
    assert cen["fonte_cmv"] == "cmp_sku" and cen["cmv_provisorio"]["usado_na_margem"] is True
    assert cen["estado"] == DP.PARCIAL and cen["margem_contribuicao_estimada"] is not None
    assert any("cmv provisorio por CMP" in m for m in cen["motivos"])
    # A DRE conhecida nao mudou: sem CMV fiscal, segue nao calculavel e sem margem.
    assert cel["classificacao"] == dre.NAO_CALCULAVEL and cel["margem_contribuicao_conhecida"]["valor"] is None
    assert cel["componentes"]["cmv"]["valor"] is None


def test_cobertura_abaixo_do_minimo_nao_calcula_e_diz_por_que():
    cen = montar(provisorio=[prov(cobertura="0.30")])["celulas_base"][0]["cenario_premissas"]
    assert cen["estado"] == DP.NAO_CALCULAVEL and cen["margem_contribuicao_estimada"] is None
    assert any("insuficiente: cobre 30.0%" in m for m in cen["motivos"])
    assert L(cen, "cmv")["valor"] is None                       # ausencia, nunca zero
    assert L(cen, "cmv_provisorio_cmp")["valor"] == 250_000.0    # visivel so' como comparacao
    assert cen["cmv_provisorio"]["usado_na_margem"] is False


def test_celula_ausente_na_fonte_nao_calcula():
    cen = montar(provisorio=[prov(status="ausente_na_fonte")])["celulas_base"][0]["cenario_premissas"]
    assert cen["estado"] == DP.NAO_CALCULAVEL
    assert any("ausentes na fonte" in m for m in cen["motivos"])
    assert cen["cmv_provisorio"]["status"] == "ausente_na_fonte" and cen["cmv_provisorio"]["cmv_provisorio"] is None


def test_sem_publicacao_do_provisorio_motivo_explicito():
    cen = montar(provisorio=None)["celulas_base"][0]["cenario_premissas"]
    assert cen["estado"] == DP.NAO_CALCULAVEL and cen["cmv_provisorio"] is None
    assert any("nao publicado" in m for m in cen["motivos"])


def test_contraprova_provisorio_nunca_substitui_o_fiscal():
    fiscal = cmv("ml", "kokeshi")                                  # CMV fiscal reconciliado = 300.000
    sem = montar(insumos=fiscal)["celulas_base"][0]["cenario_premissas"]
    com = montar(insumos=fiscal, provisorio=[prov(cmv_valor="1")])["celulas_base"][0]["cenario_premissas"]
    assert L(com, "cmv")["valor"] == 300_000.0 and L(com, "cmv")["origem"] == "realizado"
    assert com["fonte_cmv"] == "dre_realizada" and com["cmv_provisorio"]["usado_na_margem"] is False
    assert com["margem_contribuicao_estimada"] == sem["margem_contribuicao_estimada"]
    assert L(com, "cmv_provisorio_cmp")["somavel"] is False


def test_agregado_soma_cmv_provisorio_e_recalcula_cobertura_por_soma():
    a = dict(prov("ml", "kokeshi", cobertura="1"), receita_fonte=Decimal(100), receita_coberta=Decimal(100))
    b = dict(prov("ml", "barbours", cobertura="0.5"), receita_fonte=Decimal(300), receita_coberta=Decimal(150))
    agg = DP.agregar_cmv_provisorio([DP.resumo_cmv_provisorio(a), DP.resumo_cmv_provisorio(b)])
    assert agg["cobertura_pct"] == 62.5                           # 250 / 400, nao media de 100% e 50%
    assert agg["cmv_provisorio"] == 500_000.0


def _get(tc):
    r = tc.get("/api/v1/dre", params={"ref_month": "2026-07"})
    assert r.status_code == 200, r.text
    b = r.json()
    json.dumps(b, allow_nan=False)
    return b


def test_rota_com_tabela_027_ausente_continua_funcionando(rota):  # noqa: F811
    tc, _ = rota(prem(JUL, "kokeshi") + prem(JUL, "barbours"))
    body = _get(tc)
    assert all(c["cenario_premissas"]["cmv_provisorio"] is None for c in body["celulas_base"])


def test_rota_expoe_o_provisorio_publicado_sem_sku(rota, monkeypatch):  # noqa: F811
    tc, neon = rota(prem(JUL, "kokeshi") + prem(JUL, "barbours"))
    neon.cmv_provisorio = [prov("ml", "kokeshi"), prov("ml", "barbours", lote_status="failed")]
    body = _get(tc)
    ml_k = next(c for c in body["celulas_base"] if c["canal"] == "ml" and c["marca"] == "kokeshi")
    assert ml_k["cenario_premissas"]["cmv_provisorio"]["cobertura_pct"] == 95.0
    ml_b = next(c for c in body["celulas_base"] if c["canal"] == "ml" and c["marca"] == "barbours")
    assert ml_b["cenario_premissas"]["cmv_provisorio"] is None     # lote nao concluido nunca e' usado
    assert "seller_sku" not in json.dumps(body)
