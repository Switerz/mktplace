"""T-0069 fase B — a linha `impostos` da DRE mostra o imposto REALIZADO (webgex).

Sem banco. As linhas publicadas sao produzidas pelo PROPRIO publisher
(`pipelines.dre.insumo_publisher._linhas_impostos`), entao o contrato
publisher -> serving e' testado de ponta a ponta: mesmo nome de componente,
mesma convencao de sinal, mesmo rotulo de parcial.

O que se prova:
  * sem realizado publicado, a linha e' EXATAMENTE a de antes (indisponivel);
  * com realizado: valor = ICMS + DIFAL + PIS/COFINS, `partes`, `cobertura` e
    `fonte` do webgex; `disponivel` entra na margem conhecida;
  * `imposto parcial: ` no motivo, cobertura < 98% ou componente faltando ->
    `parcial`, fora da margem, valor medido sem extrapolar; nulo nunca vira 0;
  * lote vencido/nao concluido falha fechado; sinal inesperado nao e' corrigido;
  * agregado: soma, partes, cobertura pela razao das somas, e `parcial` se
    alguma celula for parcial; margem = soma das margens das celulas;
  * realizado + premissa: so' o realizado entra na margem conhecida, e o
    cenario estimado nao muda (nenhuma dupla contagem).

Numeros sinteticos de proposito.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.schemas.dre import DREResponse
from app.services import dre_premissas as DP
from app.services import dre_service as dre
from pipelines.dre import insumo_publisher as P
from tests.test_dre_mvp import AGORA, HOJE, JUL, PUBLICADO, cmv, mart

SET, OUT = date(2026, 9, 1), date(2026, 10, 1)


def imp_fonte(canal="ml", marca="kokeshi", mes=JUL, receita=1_000_000, coberta=None, notas=100, com=None,
              icms="127000", difal="73000", pis_cofins="21000"):
    """Uma linha de `SQL_IMPOSTOS` (Data Mart), como no teste do publisher."""
    coberta = receita if coberta is None else coberta
    com = notas if com is None else com
    return {"competencia": mes, "marca": marca, "plataforma": P.PLATAFORMA_FISCAL[canal],
            "receita_fiscal": Decimal(receita), "receita_com_imposto": None if com == 0 else Decimal(coberta),
            "notas": notas, "notas_com_imposto": com,
            "icms": None if icms is None or com == 0 else Decimal(icms),
            "difal": None if difal is None or com == 0 else Decimal(difal),
            "pis_cofins": None if pis_cofins is None or com == 0 else Decimal(pis_cofins),
            "chaves_ambiguas": 0, "atualizado_em": datetime(2026, 11, 5, 12, 6)}


def publicados(canal="ml", marca="kokeshi", mes=JUL, *, publicado_em=None, lote_status="success", **kw):
    """As tres linhas de imposto, geradas pelo publisher e lidas como o Neon devolve."""
    linhas = P._linhas_impostos(mes, canal, marca, imp_fonte(canal, marca, mes, **kw), P.Lote())
    return [{**ln, "publicado_em": publicado_em or PUBLICADO, "lote_id": 11, "lote_status": lote_status}
            for ln in linhas]


def celula(canal="ml", marca="kokeshi", mes=JUL, impostos=(), receita_fiscal=1_000_000.0, premissas=None,
           **mart_kw):
    body = montar([mart(canal, marca, mes=mes, dias=dre.ultimo_dia(mes).day, **mart_kw)],
                  cmv(canal, marca, mes=mes, receita_fiscal=receita_fiscal) + list(impostos),
                  inicio=mes, premissas=premissas)
    return body["celulas_base"][0]


def montar(mart_rows, insumos, inicio=JUL, premissas=None):
    fim = dre.ultimo_dia(inicio)
    leit = None if premissas is None else DP.LeituraPremissas(tabela_existe=True, linhas=list(premissas))
    body = dre.montar_dre(inicio=inicio, fim=fim, canais=["tiktok", "ml", "shopee"],
                          marcas=["kokeshi", "barbours", "lescent", "rituaria"],
                          mart_rows=list(mart_rows), afiliado_rows=[],
                          insumos=dre.LeituraInsumos(tabela_existe=True, linhas=list(insumos)),
                          hoje=HOJE, agora=AGORA, premissas=leit)
    DREResponse(**body)  # o contrato Pydantic aceita a resposta inteira
    return body


def imp(c):
    return c["componentes"]["impostos"]


# ---------------------------------------------------------------------------
# Sem realizado: exatamente o comportamento de antes
# ---------------------------------------------------------------------------

ANTES = dre._comp(motivo=dre.MOTIVO_IMPOSTOS_SEM_REALIZADO)


def _sem_pct(comp):
    return {k: v for k, v in comp.items() if k != "pct_receita"}


def test_sem_imposto_publicado_a_linha_e_a_de_antes():
    c = celula()
    assert _sem_pct(imp(c)) == ANTES
    assert imp(c)["status"] == dre.INDISPONIVEL and imp(c)["valor"] is None
    assert "impostos" in [a["componente"] for a in c["componentes_ausentes"]]


def test_tres_componentes_ausentes_na_origem_nao_viram_zero():
    c = celula(impostos=publicados(com=0))
    assert _sem_pct(imp(c)) == ANTES  # cai no "sem realizado"; nunca R$ 0


# ---------------------------------------------------------------------------
# Realizado
# ---------------------------------------------------------------------------

def test_realizado_completo_e_disponivel_com_partes_fonte_e_cobertura():
    c = celula(impostos=publicados())
    i = imp(c)
    assert i["status"] == dre.DISPONIVEL and i["valor"] == 221_000.0
    assert i["partes"] == {"icms": 127_000.0, "difal": 73_000.0, "pis_cofins": 21_000.0}
    assert i["cobertura"] == 1.0 and i["pct_receita"] == 22.1
    assert i["fonte"] == dre.FONTE_IMPOSTOS_WEBGEX and "webgex" in i["fonte"]
    assert i["publicado_em"] == PUBLICADO and i["data_as_of"] is not None
    assert "impostos" in c["componentes_disponiveis"]


def test_margem_conhecida_desconta_o_imposto_realizado():
    sem, com = celula(), celula(impostos=publicados())
    m_sem, m_com = sem["margem_contribuicao_conhecida"], com["margem_contribuicao_conhecida"]
    assert m_sem["valor"] - m_com["valor"] == 221_000.0
    assert "impostos" in m_com["componentes_usados"] and "impostos" not in m_com["faltam"]
    assert "impostos" in m_sem["faltam"]


def test_realizado_e_premissa_so_o_realizado_entra_e_o_cenario_nao_muda():
    """Sem dupla contagem: a premissa nunca entra no realizado, e o realizado
    nao vaza para o cenario estimado (que segue com a premissa)."""
    prem = _premissas(JUL, "kokeshi", icms=0.1)
    sem = celula(premissas=prem)
    com = celula(impostos=publicados(), premissas=prem)
    assert sem["margem_contribuicao_conhecida"]["valor"] - com["margem_contribuicao_conhecida"]["valor"] == 221_000.0
    # o realizado tem o valor do webgex, nao o da premissa (10% = 100.000)
    assert imp(com)["valor"] == 221_000.0 and imp(com)["fonte"] == dre.FONTE_IMPOSTOS_WEBGEX
    cen_sem, cen_com = sem["cenario_premissas"], com["cenario_premissas"]
    assert cen_com["margem_contribuicao_estimada"] == cen_sem["margem_contribuicao_estimada"]
    assert DP.linha(cen_com, "icms")["valor"] == 100_000.0 and DP.linha(cen_com, "icms")["origem"] == "premissa"


def test_imposto_zero_medido_e_zero_e_disponivel():
    c = celula(impostos=publicados(pis_cofins="0"))
    assert imp(c)["partes"]["pis_cofins"] == 0.0 and imp(c)["status"] == dre.DISPONIVEL


# ---------------------------------------------------------------------------
# Parcial
# ---------------------------------------------------------------------------

def _fora_da_margem(c):
    m = c["margem_contribuicao_conhecida"]
    return "impostos" in m["faltam"] and "impostos" not in m["componentes_usados"]


def test_cobertura_abaixo_do_minimo_e_parcial_sem_extrapolar():
    c = celula(marca="lescent", mes=JUL, impostos=publicados(marca="lescent", coberta=650_000, com=65,
                                                             icms="82550", difal="47450", pis_cofins="13650"))
    i = imp(c)
    assert i["status"] == dre.COBERTURA_PARCIAL and i["valor"] == 143_650.0  # o medido, nao escalado
    assert i["cobertura"] == 0.65 and "cobertura 0.6500 da receita fiscal" in i["motivo"]
    assert _fora_da_margem(c)


def test_shopee_e_parcial_mesmo_com_cobertura_total():
    c = celula("shopee", "barbours", impostos=publicados("shopee", "barbours"))
    assert imp(c)["status"] == dre.COBERTURA_PARCIAL
    assert "imposto so' sobre a receita fiscal faturada (F1 em aberto)" in imp(c)["motivo"]
    assert imp(c)["valor"] == 221_000.0 and _fora_da_margem(c)


def test_rituaria_outubro_e_parcial_pela_base_do_icms():
    c = celula("tiktok", "rituaria", mes=OUT, impostos=publicados("tiktok", "rituaria", mes=OUT))
    assert imp(c)["status"] == dre.COBERTURA_PARCIAL
    assert "base do ICMS mudou em out/26" in imp(c)["motivo"]


def test_cobertura_baixa_sem_rotulo_tambem_e_parcial():
    """Defesa em profundidade: o serving nao depende so' do rotulo do publisher."""
    linhas = publicados()
    for ln in linhas:
        ln["cobertura"] = Decimal("0.9")
    assert imp(celula(impostos=linhas))["status"] == dre.COBERTURA_PARCIAL


def test_componente_faltando_e_parcial_com_o_que_existe_e_nomeia_o_que_falta():
    """SUPOSICAO documentada: faltando um imposto, soma-se o resto e a linha e' parcial."""
    c = celula(impostos=publicados(pis_cofins=None))
    i = imp(c)
    assert i["status"] == dre.COBERTURA_PARCIAL and i["valor"] == 200_000.0
    assert i["partes"] == {"icms": 127_000.0, "difal": 73_000.0, "pis_cofins": None}  # nulo, nao zero
    assert "sem pis_cofins na origem" in i["motivo"] and _fora_da_margem(c)


# ---------------------------------------------------------------------------
# Falha fechada e sinal
# ---------------------------------------------------------------------------

def test_lote_vencido_ou_nao_concluido_falha_fechado():
    vencido = celula(impostos=publicados(publicado_em=AGORA - timedelta(hours=60)))
    assert imp(vencido)["status"] == dre.SNAPSHOT_VENCIDO and imp(vencido)["valor"] is None
    falho = celula(impostos=publicados(lote_status="failed"))
    assert imp(falho)["status"] == dre.SNAPSHOT_AUSENTE and imp(falho)["valor"] is None


def test_sinal_inesperado_nao_e_corrigido():
    linhas = publicados()
    linhas[0]["sinal_origem"] = -1
    assert imp(celula(impostos=linhas))["status"] == dre.SINAL_INESPERADO
    linhas = publicados()
    linhas[1]["valor"] = Decimal("-5")
    i = imp(celula(impostos=linhas))
    assert i["status"] == dre.SINAL_INESPERADO and i["valor"] is None


# ---------------------------------------------------------------------------
# Agregado
# ---------------------------------------------------------------------------

def _total(impostos, receitas=(("kokeshi", 1_000_000.0), ("barbours", 500_000.0))):
    mart_rows = [mart("ml", m, gmv=r) for m, r in receitas]
    insumos = [x for m, r in receitas for x in cmv("ml", m, receita_fiscal=r)] + list(impostos)
    return montar(mart_rows, insumos)


def test_agregado_com_celula_parcial_nao_se_diz_completo():
    body = _total(publicados(marca="kokeshi")
                  + publicados(marca="barbours", receita=500_000, coberta=400_000, com=80,
                               icms="50000", difal="30000", pis_cofins="8000"))
    t = imp(body["total"])
    assert t["status"] == dre.COBERTURA_PARCIAL and t["valor"] == 309_000.0
    assert t["partes"] == {"icms": 177_000.0, "difal": 103_000.0, "pis_cofins": 29_000.0}
    assert t["cobertura"] == round((1_000_000 + 400_000) / 1_500_000, 6)  # razao das somas
    # margem do total = soma das margens das celulas (so' a kokeshi desconta imposto)
    cel = {c["marca"]: c["margem_contribuicao_conhecida"]["valor"] for c in body["celulas_base"]}
    assert body["total"]["margem_contribuicao_conhecida"]["valor"] == round(sum(cel.values()), 2)
    assert "impostos" in body["total"]["margem_contribuicao_conhecida"]["faltam"]


def test_agregado_com_uma_celula_sem_realizado_fica_parcial_e_sem_cobertura():
    t = imp(_total(publicados(marca="kokeshi"))["total"])
    assert t["status"] == dre.COBERTURA_PARCIAL and t["valor"] == 221_000.0
    assert t["cobertura"] is None  # uma celula sem cobertura nao vira 100% nem e' ignorada


def test_agregado_todo_disponivel_e_disponivel():
    body = _total(publicados(marca="kokeshi") + publicados(marca="barbours", receita=500_000, icms="60000",
                                                            difal="36000", pis_cofins="10000"))
    t = imp(body["total"])
    assert t["status"] == dre.DISPONIVEL and t["valor"] == 327_000.0 and t["cobertura"] == 1.0
    assert "impostos" in body["total"]["margem_contribuicao_conhecida"]["componentes_usados"]


# ---------------------------------------------------------------------------
# Contrato com o publisher
# ---------------------------------------------------------------------------

def test_rotulo_parcial_e_componentes_sao_os_do_publisher():
    assert dre.IMPOSTO_PARCIAL == P.IMPOSTO_PARCIAL
    assert set(dre.PARTES_IMPOSTO) == set(P.IMPOSTOS)
    assert dre.COBERTURA_IMPOSTO_MINIMA == float(P.IMPOSTO_COBERTURA_MINIMA)


# ---------------------------------------------------------------------------

def _premissas(mes, marca, **taxas):
    out = []
    for comp in DP.COMPONENTES_PREMISSA:
        rate = taxas.get(comp, 0.01)
        nao = comp in DP.NAO_SOMAVEIS
        out.append({"competencia": mes, "brand": marca, "component": comp, "rate": rate, "somavel": not nao,
                    "status": DP.NAO_SOMAVEIS[comp][0] if nao else "ok", "source_period": "jul/26",
                    "source_sha256": "c" * 64, "source_sent_on": date(2026, 10, 13),
                    "loaded_at": datetime(2026, 10, 16, tzinfo=timezone.utc), "run_id": 1, "lote_status": "success"})
    return out
