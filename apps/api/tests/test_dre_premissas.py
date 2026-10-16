"""DRE-PREMISSAS-1 — cenario "estimado com premissas" na DRE.

Taxas SINTETICAS. O que se prova:
  * a taxa e' aplicada na celula base e o agregado soma celulas (nunca reaplica);
  * zero explicito e' zero; branco e' ausencia e nunca vira zero;
  * Outras Receitas Operacionais fica fora da margem;
  * afiliado TikTok separado uma vez so' (marketplace + influencer = taxas);
  * a margem CONHECIDA e o payload antigo nao mudam;
  * premissa so' vale na propria competencia (sem retroagir, sem carregar);
  * mes em andamento e' previa; componente real ausente degrada o estado;
  * a rota funciona com a tabela ausente, sem competencia publicada e publicada.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone

import pytest
from app import database
from app.database import get_db
from app.main import app
from app.routers import dre as dre_router
from app.services import dre_premissas as DP
from app.services import dre_service as dre
from fastapi.testclient import TestClient
from tests.test_dre_mvp import (
    AGORA,
    HOJE,
    JUL,
    FakeNeon,
    _DataMartQueExplode,
    afiliado,
    cmv,
    mart,
    repasse,
)

SET, OUT, NOV = date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)
SHA = "b" * 64


def prem(mes, marca, taxas=None, *, padrao=0.01, run_id=1, lote_status="success"):
    """As 18 linhas publicadas de uma marca. `taxas` sobrescreve (None = ausente)."""
    taxas = taxas or {}
    out = []
    for comp in DP.COMPONENTES_PREMISSA:
        rate = taxas.get(comp, padrao)
        nao_somavel = comp in DP.NAO_SOMAVEIS
        out.append({
            "competencia": mes, "brand": marca, "component": comp, "rate": rate, "somavel": not nao_somavel,
            "status": ("ausente_na_origem" if rate is None
                       else DP.NAO_SOMAVEIS[comp][0] if nao_somavel else "ok"),
            "source_period": "out/26", "source_sha256": SHA, "source_sent_on": date(2026, 10, 13),
            "loaded_at": datetime(2026, 10, 16, tzinfo=timezone.utc), "run_id": run_id, "lote_status": lote_status,
        })
    return out


def montar(mart_rows, *, premissas=None, afiliado_rows=(), insumos=(), inicio=JUL, fim=date(2026, 7, 31),
           hoje=HOJE, marcas=("kokeshi", "barbours"), canais=("tiktok", "ml", "shopee")):
    leit = None if premissas is None else DP.LeituraPremissas(tabela_existe=True, linhas=list(premissas))
    return dre.montar_dre(inicio=inicio, fim=fim, canais=list(canais), marcas=list(marcas),
                          mart_rows=list(mart_rows), afiliado_rows=list(afiliado_rows),
                          insumos=dre.LeituraInsumos(tabela_existe=True, linhas=list(insumos)),
                          hoje=hoje, agora=AGORA, premissas=leit)


def L(cen, nome):
    return DP.linha(cen, nome)


def ml(marca="kokeshi", gmv=1_000_000.0, mes=JUL):
    return mart("ml", marca, mes=mes, gmv=gmv, dias=dre.ultimo_dia(mes).day)


def ins_ml(marca="kokeshi", receita=1_000_000.0, mes=JUL):
    return cmv("ml", marca, mes=mes, receita_fiscal=receita)


# ---------------------------------------------------------------------------
# Celula base
# ---------------------------------------------------------------------------

def test_taxa_aplicada_na_celula_base_e_estado_calculavel():
    body = montar([ml()], premissas=prem(JUL, "kokeshi", {"icms": 0.1}), insumos=ins_ml())
    cen = body["celulas_base"][0]["cenario_premissas"]
    assert cen["natureza"] == "estimada" and cen["estado"] == DP.CALCULAVEL and not cen["previa"]
    icms = L(cen, "icms")
    assert (icms["valor"], icms["percentual"], icms["origem"]) == (100_000.0, 0.1, "premissa")
    assert L(cen, "receita_bruta")["origem"] == "realizado" and L(cen, "cmv")["origem"] == "realizado"


def test_margem_estimada_e_a_receita_menos_os_itens_somaveis():
    body = montar([ml()], premissas=prem(JUL, "kokeshi", padrao=0.01), insumos=ins_ml())
    cen = body["celulas_base"][0]["cenario_premissas"]
    # 16 itens de premissa somaveis a 1% (18 menos Outras Receitas e Comissoes) = 160.000;
    # CMV 300.000; Ads 40.000; taxas 150.000.
    esperado = 1_000_000 - 16 * 10_000 - 300_000 - 40_000 - 150_000
    assert cen["margem_contribuicao_estimada"] == esperado
    assert cen["margem_contribuicao_estimada_pct"] == round(esperado / 1_000_000 * 100, 2)
    assert L(cen, "impostos_sobre_receita")["valor"] == 50_000
    assert L(cen, "receita_liquida_estimada")["valor"] == 1_000_000 - 60_000


def test_agregado_soma_celulas_e_nunca_reaplica_taxa():
    body = montar([ml("kokeshi", 1_000_000.0), ml("barbours", 500_000.0)],
                  premissas=prem(JUL, "kokeshi", {"icms": 0.1}) + prem(JUL, "barbours", {"icms": 0.2}),
                  insumos=ins_ml("kokeshi") + ins_ml("barbours", 500_000.0))
    canal = next(c for c in body["por_canal"] if c["canal"] == "ml")["cenario_premissas"]
    assert L(canal, "icms")["valor"] == 100_000 + 100_000          # nao 0,1 x 1,5 mi nem 0,2 x 1,5 mi
    assert L(canal, "icms")["percentual"] is None                  # taxa nao existe no agregado
    total = body["total"]["cenario_premissas"]
    soma = sum(c["cenario_premissas"]["margem_contribuicao_estimada"] for c in body["celulas_base"])
    assert total["margem_contribuicao_estimada"] == pytest.approx(soma)


def test_zero_explicito_e_zero_e_branco_e_ausencia():
    body = montar([ml()], premissas=prem(JUL, "kokeshi", {"difal": 0.0, "brindes": None}), insumos=ins_ml())
    cen = body["celulas_base"][0]["cenario_premissas"]
    assert (L(cen, "difal")["valor"], L(cen, "difal")["status"]) == (0.0, "ok")
    brindes = L(cen, "brindes")
    assert (brindes["valor"], brindes["status"], brindes["origem"]) == (None, "ausente_na_origem", "indisponivel")
    assert cen["estado"] == DP.PARCIAL and {"linha": "brindes", "motivo": "premissa ausente"} in cen["componentes_ausentes"]
    # A margem parcial nao subtrai nada por brindes (nao inventa zero, nem valor).
    completo = montar([ml()], premissas=prem(JUL, "kokeshi", {"difal": 0.0}), insumos=ins_ml())
    m_completo = completo["celulas_base"][0]["cenario_premissas"]["margem_contribuicao_estimada"]
    assert cen["margem_contribuicao_estimada"] == m_completo + 10_000


def test_outras_receitas_fica_fora_da_margem():
    a = montar([ml()], premissas=prem(JUL, "kokeshi", {"outras_receitas_operacionais": 0.0}), insumos=ins_ml())
    b = montar([ml()], premissas=prem(JUL, "kokeshi", {"outras_receitas_operacionais": 0.5}), insumos=ins_ml())
    ca, cb = (x["celulas_base"][0]["cenario_premissas"] for x in (a, b))
    assert ca["margem_contribuicao_estimada"] == cb["margem_contribuicao_estimada"]
    outras = L(cb, "outras_receitas_operacionais")
    assert (outras["somavel"], outras["status"], outras["tipo"]) == (False, "sinal_nao_ratificado", "informativo")
    assert any("nao ratificado" in a for a in cb["avisos"])


def test_comissao_diferente_de_zero_fica_visivel_e_nao_e_somada():
    """Sobreposicao com as taxas de marketplace nao ratificada: premissa exibida, margem intocada."""
    zero = montar([ml()], premissas=prem(JUL, "kokeshi", {"comissoes": 0.0}), insumos=ins_ml())
    alta = montar([ml()], premissas=prem(JUL, "kokeshi", {"comissoes": 0.25}), insumos=ins_ml())
    cz, ca = (x["celulas_base"][0]["cenario_premissas"] for x in (zero, alta))
    assert ca["margem_contribuicao_estimada"] == cz["margem_contribuicao_estimada"]
    com = L(ca, "comissoes")
    assert (com["valor"], com["percentual"], com["origem"]) == (250_000.0, 0.25, "premissa")
    assert (com["somavel"], com["status"]) == (False, "sobreposicao_nao_ratificada")
    assert any("Comissoes fora da margem" in a for a in ca["avisos"])
    # Agregado tambem nao soma: margem do total = soma das margens das celulas.
    assert alta["total"]["cenario_premissas"]["margem_contribuicao_estimada"] == ca["margem_contribuicao_estimada"]


def test_tiktok_afiliado_separado_uma_vez_so():
    body = montar([mart("tiktok")], premissas=prem(JUL, "kokeshi"), afiliado_rows=[afiliado()],
                  insumos=cmv("tiktok", "kokeshi") + repasse("kokeshi"))
    cel = body["celulas_base"][0]
    cen = cel["cenario_premissas"]
    taxas = cel["componentes"]["taxas"]["valor"]
    mk, inf = L(cen, "despesas_marketplace"), L(cen, "influencer")
    assert (mk["origem"], inf["origem"]) == ("decomposicao", "decomposicao")
    assert mk["valor"] + inf["valor"] == pytest.approx(taxas) and inf["valor"] == 90_000
    # Margem com a decomposicao = margem com a taxa integral: o afiliado nao foi somado de novo.
    deducoes = sum(ln["valor"] for ln in cen["linhas"]
                   if ln["tipo"] == "item" and ln["somavel"] and ln["valor"] is not None and ln["linha"] != "receita_bruta")
    assert cen["margem_contribuicao_estimada"] == pytest.approx(cel["componentes"]["receita"]["valor"] - deducoes)
    assert "midia_online" in {a["linha"] for a in cen["componentes_ausentes"]}   # TikTok sem Ads no mart
    assert cen["estado"] == DP.PARCIAL


def test_tiktok_afiliado_maior_que_taxa_mantem_taxa_integral():
    body = montar([mart("tiktok")], premissas=prem(JUL, "kokeshi"),
                  afiliado_rows=[afiliado(creator=-400_000.0)], insumos=cmv("tiktok", "kokeshi") + repasse("kokeshi"))
    cel = body["celulas_base"][0]
    cen = cel["cenario_premissas"]
    assert L(cen, "despesas_marketplace")["valor"] == cel["componentes"]["taxas"]["valor"]
    assert L(cen, "influencer")["somavel"] is False


def test_frete_realizado_so_como_comparacao():
    body = montar([ml()], premissas=prem(JUL, "kokeshi"), insumos=ins_ml())
    cen = body["celulas_base"][0]["cenario_premissas"]
    fr = L(cen, "frete_realizado_comparacao")
    assert fr["valor"] == 130_000.0 and fr["somavel"] is False and fr["tipo"] == "informativo"


def test_margem_conhecida_e_payload_antigo_nao_mudam():
    rows, ins = [ml(), mart("tiktok")], ins_ml() + cmv("tiktok", "kokeshi") + repasse("kokeshi")
    sem = montar(rows, insumos=ins, afiliado_rows=[afiliado()])
    com = montar(rows, insumos=ins, afiliado_rows=[afiliado()],
                 premissas=prem(JUL, "kokeshi", {"icms": 0.3}))

    def sem_cenario(x):
        x = copy.deepcopy(x)
        x["fontes"].pop("premissas_dre")
        for k in ("contrato_premissas", "rotulo_margem_estimada"):
            x["regras"].pop(k)
        for grupo in ([x["total"]], x["por_canal"], x["por_marca"], x["por_canal_marca"], x["celulas_base"]):
            for c in grupo:
                c.pop("cenario_premissas")
        return x

    assert sem_cenario(com) == sem_cenario(sem)
    assert com["total"]["margem_contribuicao_conhecida"]["rotulo"] == dre.ROTULO_MARGEM
    assert com["total"]["cenario_premissas"]["rotulo"] == DP.ROTULO_MARGEM_ESTIMADA != dre.ROTULO_MARGEM


def test_premissa_de_outubro_nao_aparece_em_setembro():
    body = montar([ml(mes=SET)], premissas=prem(OUT, "kokeshi"), insumos=ins_ml(mes=SET),
                  inicio=SET, fim=date(2026, 9, 30))
    assert body["celulas_base"][0]["cenario_premissas"] is None
    assert body["fontes"]["premissas_dre"]["estado"] == "nao_publicado"


def test_novembro_nao_herda_outubro():
    body = montar([ml(mes=NOV)], premissas=prem(OUT, "kokeshi"), insumos=ins_ml(mes=NOV),
                  inicio=NOV, fim=date(2026, 11, 30), hoje=date(2026, 11, 20))
    assert body["celulas_base"][0]["cenario_premissas"] is None


def test_mes_em_andamento_e_previa_e_nunca_calculavel():
    body = montar([ml(mes=OUT, gmv=500_000.0) | {"dias": 16}], premissas=prem(OUT, "kokeshi"),
                  insumos=ins_ml(mes=OUT, receita=500_000.0), inicio=OUT, fim=date(2026, 10, 31),
                  hoje=date(2026, 10, 16))
    cel = body["celulas_base"][0]
    cen = cel["cenario_premissas"]
    assert cen["previa"] is True and cen["estado"] == DP.PARCIAL and cen["competencia_premissas"] == "2026-10"
    assert any("previa" in m for m in cen["motivos"])
    assert cel["classificacao"] == dre.NAO_CALCULAVEL     # a DRE realizada continua imatura


def test_outubro_em_andamento_com_receita_cmv_e_taxas_tem_margem_estimada_previa():
    """Antes da maturidade M+2: cenario parcial/previa COM valor; a DRE realizada segue sem margem."""
    hoje = date(2026, 10, 16)
    body = montar([ml(mes=OUT, gmv=500_000.0) | {"dias": 16}],
                  premissas=prem(OUT, "kokeshi", padrao=0.01),
                  insumos=ins_ml(mes=OUT, receita=500_000.0), inicio=OUT, fim=date(2026, 10, 31), hoje=hoje)
    cel = body["celulas_base"][0]
    cen = cel["cenario_premissas"]
    assert cel["componentes"]["cmv"]["valor"] == 300_000.0 and cel["componentes"]["taxas"]["valor"] == 150_000.0
    assert (cen["estado"], cen["previa"], cen["competencia_madura"]) == (DP.PARCIAL, True, False)
    # 500.000 - 16 x 1% x 500.000 - CMV 300.000 - Ads 40.000 - taxas 150.000
    assert cen["margem_contribuicao_estimada"] == 500_000 - 16 * 5_000 - 300_000 - 40_000 - 150_000
    assert cen["margem_contribuicao_estimada_pct"] is not None
    # A DRE conhecida obedece integralmente a M+2: imatura, sem margem.
    assert cel["classificacao"] == dre.NAO_CALCULAVEL
    assert cel["margem_contribuicao_conhecida"]["valor"] is None
    assert any("mes imaturo" in m for m in cel["motivos_classificacao"])
    # A maturidade nao apaga o valor nos agregados.
    for recorte in (body["total"], body["por_canal"][0], body["por_marca"][0]):
        assert recorte["cenario_premissas"]["margem_contribuicao_estimada"] == cen["margem_contribuicao_estimada"]
        assert recorte["margem_contribuicao_conhecida"]["valor"] is None


def test_competencia_encerrada_mas_imatura_continua_parcial():
    body = montar([ml(mes=OUT)], premissas=prem(OUT, "kokeshi"), insumos=ins_ml(mes=OUT),
                  inicio=OUT, fim=date(2026, 10, 31), hoje=date(2026, 11, 20))
    cen = body["celulas_base"][0]["cenario_premissas"]
    assert cen["previa"] is False and cen["competencia_madura"] is False and cen["estado"] == DP.PARCIAL


def test_cmv_ausente_torna_nao_calculavel_sem_fabricar_zero():
    body = montar([ml()], premissas=prem(JUL, "kokeshi"), insumos=[])
    cen = body["celulas_base"][0]["cenario_premissas"]
    assert cen["estado"] == DP.NAO_CALCULAVEL and cen["margem_contribuicao_estimada"] is None
    assert L(cen, "cmv")["valor"] is None
    assert L(cen, "icms")["valor"] == 10_000.0       # a premissa continua visivel, a margem nao


def test_lote_de_premissa_invalido_torna_nao_calculavel():
    body = montar([ml()], premissas=prem(JUL, "kokeshi", lote_status="failed"), insumos=ins_ml())
    cen = body["celulas_base"][0]["cenario_premissas"]
    assert cen["estado"] == DP.NAO_CALCULAVEL and any("origem da premissa" in m for m in cen["motivos"])
    assert body["fontes"]["premissas_dre"]["estado"] == "lote_invalido"


def test_recorte_que_mistura_celula_sem_premissa_nao_e_calculavel():
    body = montar([ml("kokeshi"), ml("barbours", 500_000.0)], premissas=prem(JUL, "kokeshi"),
                  insumos=ins_ml("kokeshi") + ins_ml("barbours", 500_000.0))
    total = body["total"]["cenario_premissas"]
    assert total["estado"] == DP.NAO_CALCULAVEL and total["margem_contribuicao_estimada"] is None
    barbours = next(c for c in body["por_marca"] if c["marca"] == "barbours")
    assert barbours["cenario_premissas"] is None


# ---------------------------------------------------------------------------
# Rota
# ---------------------------------------------------------------------------

@pytest.fixture
def rota(monkeypatch):
    def _abrir(premissas):
        neon = FakeNeon(mart_rows=[ml(), mart("tiktok"), ml("barbours", 500_000.0)],
                        afiliado_rows=[afiliado()],
                        insumos=ins_ml() + ins_ml("barbours", 500_000.0) + cmv("tiktok", "kokeshi") + repasse("kokeshi"),
                        premissas=premissas)
        app.dependency_overrides[get_db] = lambda: neon
        monkeypatch.setattr(database, "datamart_engine", _DataMartQueExplode())
        monkeypatch.setattr(dre_router, "today_brt", lambda: HOJE)
        import app.deps.filters as filtros
        monkeypatch.setattr(filtros, "today_brt", lambda: HOJE)
        return TestClient(app), neon
    yield _abrir
    app.dependency_overrides.pop(get_db, None)


def _get(tc, **params):
    r = tc.get("/api/v1/dre", params={"ref_month": "2026-07", **params})
    assert r.status_code == 200, r.text
    body = r.json()
    json.dumps(body, allow_nan=False)  # sem NaN/Infinity
    return body


def _todas(body):
    return [body["total"], *body["por_canal"], *body["por_marca"], *body["por_canal_marca"], *body["celulas_base"]]


def test_rota_tabela_ausente_continua_funcionando(rota):
    tc, _ = rota(None)
    body = _get(tc)
    assert body["fontes"]["premissas_dre"]["estado"] == "tabela_ausente"
    assert "migration 026" in body["fontes"]["premissas_dre"]["motivo"]
    assert all(c["cenario_premissas"] is None for c in _todas(body))
    assert body["total"]["margem_contribuicao_conhecida"]["rotulo"] == dre.ROTULO_MARGEM


def test_rota_competencia_nao_publicada(rota):
    tc, _ = rota(prem(OUT, "kokeshi"))
    body = _get(tc)
    assert body["fontes"]["premissas_dre"]["estado"] == "nao_publicado"
    assert all(c["cenario_premissas"] is None for c in _todas(body))


def test_rota_premissa_publicada_em_todos_os_recortes(rota):
    tc, _ = rota(prem(JUL, "kokeshi") + prem(JUL, "barbours", {"brindes": None}))
    body = _get(tc)
    assert body["fontes"]["premissas_dre"]["estado"] == "ok"
    assert body["fontes"]["premissas_dre"]["source_sha256"] == SHA
    assert body["fontes"]["premissas_dre"]["rotulo"] == "Premissas de julho/2026"
    assert body["fontes"]["premissas_dre"]["data_fonte"] == "2026-10-13"
    fonte = body["total"]["cenario_premissas"]["fonte_premissas"]
    assert set(fonte) == {"rotulo", "competencia", "data_fonte", "source_sha256", "carregado_em"}
    texto = json.dumps(body)
    assert "Orcamento" not in texto and "source_note" not in texto and ".xlsx" not in texto
    assert all(c["cenario_premissas"]["natureza"] == "estimada" for c in _todas(body))
    barbours = next(c for c in body["celulas_base"] if c["marca"] == "barbours")
    brindes = next(ln for ln in barbours["cenario_premissas"]["linhas"] if ln["linha"] == "brindes")
    assert brindes["valor"] is None                      # ausencia nao virou zero no JSON


def test_rota_payload_antigo_compativel(rota):
    tc_sem, _ = rota(None)
    sem = _get(tc_sem)
    tc_com, _ = rota(prem(JUL, "kokeshi") + prem(JUL, "barbours"))
    com = _get(tc_com)
    assert len(_todas(sem)) == len(_todas(com))
    for a, b in zip(_todas(sem), _todas(com)):
        assert {k: v for k, v in a.items() if k != "cenario_premissas"} == \
               {k: v for k, v in b.items() if k != "cenario_premissas"}


def test_rota_filtros_de_marca_e_canal(rota):
    tc, neon = rota(prem(JUL, "kokeshi") + prem(JUL, "barbours"))
    body = _get(tc, brands="barbours", channels="ml")
    p = next(p for p, q in zip(neon.params, neon.sqls) if "fact_dre_premissa_mensal" in q and "to_regclass" not in q)
    assert p["marcas"] == ["barbours"]
    assert {c["marca"] for c in body["celulas_base"]} == {"barbours"}
    assert {c["canal"] for c in body["celulas_base"]} == {"ml"}
    assert body["total"]["cenario_premissas"]["celulas"] == 1
