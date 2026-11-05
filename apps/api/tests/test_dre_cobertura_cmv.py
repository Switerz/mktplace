"""T-0055 C3 — cobertura do CMV no serving da DRE.

O que se prova aqui:
  * a `cobertura` publicada em `marts.fact_dre_insumo_mensal` chega ao
    componente CMV (antes era descartada);
  * `cmv_pct_receita_coberta` = custo / (cobertura x receita fiscal) x 100,
    com denominador PROPRIO — `pct_receita` continua custo / receita (GMV);
  * abaixo de `COBERTURA_CMV_MINIMA` o CMV sai `parcial` (fora da margem),
    com o mesmo valor publicado: nada e' extrapolado;
  * cobertura nula mantem o estado de antes, sem virar 100%;
  * agregado = razao das somas, nunca media de razoes; nula se faltar em
    alguma celula;
  * os campos novos sao opcionais no contrato (resposta antiga continua valida).

Numeros sinteticos.
"""
from __future__ import annotations

import pytest
from app.schemas.dre import Componente, DREResponse
from app.services import dre_service as dre

from tests.test_dre_mvp import cmv, celula, mart, montar


def cmv_cob(canal="ml", marca="kokeshi", cobertura=0.99, **kw):
    linhas = cmv(canal, marca=marca, **kw)
    linhas[0]["cobertura"] = cobertura
    return linhas


def test_limiar_e_098_e_exposto_nas_regras():
    assert dre.COBERTURA_CMV_MINIMA == 0.98
    r = montar([mart("ml")], cmv_rows=[cmv_cob("ml")], canais=("ml",), marcas=("kokeshi",))
    assert r["regras"]["cmv_cobertura_minima"] == 0.98
    DREResponse.model_validate(r)


def test_cobertura_publicada_chega_ao_cmv():
    c = celula("ml", c=cmv_cob("ml", cobertura=0.995))["componentes"]["cmv"]
    assert c["status"] == dre.DISPONIVEL
    assert c["cobertura"] == pytest.approx(0.995)


def test_cmv_pct_sobre_receita_coberta_tem_denominador_proprio():
    # receita fiscal 1.000.000, cobertura 0,99 -> receita com custo 990.000; custo 300.000
    c = celula("ml", m=mart("ml", gmv=1_000_000.0), c=cmv_cob("ml", cobertura=0.99))["componentes"]["cmv"]
    assert c["cmv_pct_receita_coberta"] == pytest.approx(round(300_000 / 990_000 * 100, 2))
    assert c["pct_receita"] == pytest.approx(30.0)  # custo / GMV, inalterado


def test_cobertura_abaixo_do_limiar_vira_parcial_sem_extrapolar():
    c = celula("ml", c=cmv_cob("ml", cobertura=0.9467))
    cm = c["componentes"]["cmv"]
    assert cm["status"] == dre.COBERTURA_PARCIAL
    assert cm["valor"] == 300_000.0  # o publicado, nunca custo / cobertura
    assert "94.67%" in cm["motivo"] and "extrapolado" in cm["motivo"]
    assert cm["cobertura"] == pytest.approx(0.9467)
    assert cm["cmv_pct_receita_coberta"] == pytest.approx(round(300_000 / 946_700 * 100, 2))
    # CMV fora da margem -> sem margem conhecida, com motivo nomeado
    assert c["classificacao"] == dre.NAO_CALCULAVEL
    assert c["margem_contribuicao_conhecida"]["valor"] is None
    assert any(m.startswith("cmv parcial") for m in c["motivos_classificacao"])


def test_cobertura_exatamente_no_limiar_continua_disponivel():
    assert celula("ml", c=cmv_cob("ml", cobertura=0.98))["componentes"]["cmv"]["status"] == dre.DISPONIVEL


def test_cobertura_nula_mantem_estado_e_nao_vira_100():
    c = celula("ml", c=cmv_cob("ml", cobertura=None))
    cm = c["componentes"]["cmv"]
    assert cm["status"] == dre.DISPONIVEL  # comportamento anterior
    assert cm["cobertura"] is None
    assert cm["cmv_pct_receita_coberta"] is None
    assert any("Cobertura do CMV nao publicada" in a for a in c["avisos_qualidade"])


def test_cobertura_zero_nao_divide_por_zero():
    cm = celula("ml", c=cmv_cob("ml", cobertura=0.0))["componentes"]["cmv"]
    assert cm["status"] == dre.COBERTURA_PARCIAL
    assert cm["cmv_pct_receita_coberta"] is None


def test_estado_pior_prevalece_sobre_cobertura_mas_campos_aparecem():
    cm = celula("ml", c=cmv_cob("ml", cobertura=0.5, status="nao_reconciliado"))["componentes"]["cmv"]
    assert cm["status"] == dre.NAO_RECONCILIADO
    assert cm["cobertura"] == pytest.approx(0.5)
    assert cm["cmv_pct_receita_coberta"] == pytest.approx(60.0)


def test_outros_componentes_nao_tem_cobertura():
    c = celula("ml", c=cmv_cob("ml"))
    for nome in ("receita", "taxas", "ads", "frete", "impostos"):
        assert c["componentes"][nome]["cobertura"] is None
        assert c["componentes"][nome]["cmv_pct_receita_coberta"] is None


def test_agregado_e_razao_das_somas():
    r = montar(
        [mart("ml", "kokeshi"), mart("ml", "barbours", gmv=500_000.0)],
        cmv_rows=[cmv_cob("ml", "kokeshi", cobertura=0.99),
                  cmv_cob("ml", "barbours", cobertura=1.0, receita_fiscal=500_000.0, custo=100_000.0)],
        canais=("ml",),
    )
    cm = r["total"]["componentes"]["cmv"]
    rcc = 0.99 * 1_000_000 + 500_000
    assert cm["cobertura"] == pytest.approx(round(rcc / 1_500_000, 6))
    assert cm["cmv_pct_receita_coberta"] == pytest.approx(round(400_000 / rcc * 100, 2))
    DREResponse.model_validate(r)


def test_agregado_com_celula_sem_cobertura_fica_nulo():
    r = montar(
        [mart("ml", "kokeshi"), mart("ml", "barbours")],
        cmv_rows=[cmv_cob("ml", "kokeshi", cobertura=0.99), cmv_cob("ml", "barbours", cobertura=None)],
        canais=("ml",),
    )
    cm = r["total"]["componentes"]["cmv"]
    assert cm["cobertura"] is None and cm["cmv_pct_receita_coberta"] is None


def test_agregado_com_celula_parcial_por_cobertura_e_parcial():
    r = montar(
        [mart("ml", "kokeshi"), mart("ml", "barbours")],
        cmv_rows=[cmv_cob("ml", "kokeshi", cobertura=0.99), cmv_cob("ml", "barbours", cobertura=0.90)],
        canais=("ml",),
    )
    assert r["total"]["componentes"]["cmv"]["status"] == dre.COBERTURA_PARCIAL
    assert r["total"]["classificacao"] == dre.NAO_CALCULAVEL


def test_contrato_serializa_campos_e_descarta_chaves_internas():
    r = montar([mart("ml")], cmv_rows=[cmv_cob("ml", cobertura=0.99)], canais=("ml",), marcas=("kokeshi",))
    out = DREResponse.model_validate(r).model_dump()
    cm = out["celulas_base"][0]["componentes"]["cmv"]
    assert cm["cobertura"] == pytest.approx(0.99)
    assert cm["cmv_pct_receita_coberta"] is not None
    assert not any(k.startswith("_") for k in cm)


def test_campos_novos_sao_opcionais_no_contrato():
    antigo = {"valor": 1.0, "status": "disponivel", "somavel": True}
    c = Componente.model_validate(antigo)
    assert c.cobertura is None and c.cmv_pct_receita_coberta is None


def test_mes_sem_cobertura_no_insumo_antigo_nao_quebra():
    """Linha publicada sem a chave `cobertura` (forma antiga do fake) continua servindo."""
    linhas = cmv("ml")
    for x in linhas:
        x.pop("cobertura")
    cm = celula("ml", c=linhas)["componentes"]["cmv"]
    assert cm["status"] == dre.DISPONIVEL and cm["cobertura"] is None
