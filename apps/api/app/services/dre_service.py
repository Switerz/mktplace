"""DRE gerencial por canal e marca — DRE-MVP-1.

Implementa o contrato `docs/dre_contrato_0.md` (PR #65). A unidade de calculo
e' a CELULA BASE `mes x canal x marca`; total, canal, marca e canal x marca sao
agregacoes dela, nunca calculos paralelos.

O QUE ESTE MODULO GARANTE, E POR QUE
-----------------------------------
- **Ausencia nunca vira zero.** Componente sem observacao tem `valor=None` e um
  `status` que diz por que. Zero so' aparece quando a fonte mediu zero.
- **Afiliado TikTok nunca e' somado.** Esta DENTRO do `fee_and_tax` do repasse
  (contrato §6: identidade do repasse em 100% das transacoes). Entra como
  decomposicao, `somavel=False`, e a margem so' soma componentes somaveis.
- **Comissao do ML entra uma vez**, como `taxas`, a partir de `total_fees` do
  mart. Nenhum outro componente a contem (`ad_spend` e `seller_shipping_cost`
  sao colunas separadas na mesma fonte).
- **Sinal normalizado num unico lugar** (`normalizar_custo`): ML e Shopee gravam
  custo positivo, o repasse TikTok grava negativo. Sinal inesperado nao e'
  corrigido com `abs()` — o componente sai como `sinal_inesperado`.
- **Nada aqui se chama margem liquida.** O melhor nivel possivel hoje e'
  "margem de contribuicao conhecida", porque impostos nao estao ratificados e
  devolucoes em valor nao tem fonte (contrato §1, §8).

FONTES
------
Neon (`db`):       `marts.fact_marketplace_daily_performance` (receita, taxas
                   ML/Shopee, Ads, frete ML/Shopee) e
                   `marts.fact_tiktok_affiliate_cost_order_monthly` (afiliados).
Data Mart:         `gold.nf_vendas_plataforma_sku_mensal` (CMV fiscal) e
                   `silver.stg_tiktok_payments_by_order` (taxa e frete TikTok
                   por coorte de pedido). O Data Mart pode estar inacessivel
                   (Gate G4: caso do Render hoje). Nesse caso os componentes
                   que dependem dele saem `fonte_inacessivel` — nunca caem para
                   um valor alternativo, porque o `total_fees` TikTok do mart
                   esta comprovadamente congelado (contrato §4.5, §7).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.services.performance_service import BRAND_LABELS, ML_ID, SHOPEE_ID, TIKTOK_ID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vocabulario fechado
# ---------------------------------------------------------------------------
COMPLETA = "COMPLETA"
PARCIAL = "PARCIAL"
NAO_CALCULAVEL = "NAO_CALCULAVEL"

#: Status de componente. So' `disponivel` entra na margem.
DISPONIVEL = "disponivel"
COBERTURA_PARCIAL = "parcial"
INDISPONIVEL = "indisponivel"
FONTE_INACESSIVEL = "fonte_inacessivel"
NAO_RECONCILIADO = "nao_reconciliado"
NAO_CONFIRMADO = "nao_confirmado"
PROVISORIO = "provisorio"
SINAL_INESPERADO = "sinal_inesperado"
NAO_APLICAVEL = "nao_aplicavel"

CANAIS = ("tiktok", "ml", "shopee")
CANAL_LABEL = {"tiktok": "TikTok Shop", "ml": "Mercado Livre", "shopee": "Shopee"}
CANAL_POR_MKT = {TIKTOK_ID: "tiktok", ML_ID: "ml", SHOPEE_ID: "shopee"}
MKT_POR_CANAL = {v: k for k, v in CANAL_POR_MKT.items()}
#: `plataforma` da fonte fiscal para cada canal.
PLATAFORMA_FISCAL = {"tiktok": "tiktokshop", "ml": "mercadolivre", "shopee": "shopee"}

#: Ordem de exibicao. `afiliados` vem logo apos `taxas` porque e' decomposicao dela.
COMPONENTES = ("receita", "devolucoes", "taxas", "afiliados", "ads", "frete", "cmv", "impostos")
#: Custos que, quando `disponivel`, sao subtraidos da receita. Afiliado NAO esta aqui.
CUSTOS_SOMAVEIS = ("devolucoes", "taxas", "ads", "frete", "cmv", "impostos")
#: Sem estes tres, nao ha margem conhecida (contrato §8).
REQUISITOS_PARCIAL = ("receita", "taxas", "cmv")

#: Contrato §7 / D5: mes M so' fecha depois que M+2 terminou.
MESES_MATURACAO = 2
#: Contrato §8 / F7, C7: receita fiscal / GMV aceita para usar o CMV fiscal.
CMV_RAZAO_MIN = 0.95
CMV_RAZAO_MAX = 1.05
#: CMV integralmente provisorio = mes nao fechado na fonte fiscal (contrato §4.9).
CMV_PROVISORIO_INTEGRAL = 0.995
#: Residuo tolerado na identidade settlement = revenue + fee_and_tax + shipping.
#: Medido: 0,00 em 758.324 transacoes (contrato §6.1, P1).
RESIDUO_REPASSE_MAX = 1.0

#: Sinal com que cada fonte grava custo. Medido no contrato §4.5/§4.8.
SINAL_FONTE = {
    ("taxas", "ml"): +1, ("taxas", "shopee"): +1, ("taxas", "tiktok"): -1,
    ("ads", "ml"): +1, ("ads", "shopee"): +1,
    ("frete", "ml"): +1, ("frete", "shopee"): +1, ("frete", "tiktok"): -1,
    ("afiliados", "tiktok"): -1,
    ("cmv", "ml"): +1, ("cmv", "shopee"): +1, ("cmv", "tiktok"): +1,
}

ROTULO_MARGEM = "Margem de contribuição conhecida"

LOG_DATAMART_FALHOU = "dre: data mart inacessivel"


# ---------------------------------------------------------------------------
# Utilitarios puros
# ---------------------------------------------------------------------------

def _num(v) -> float | None:
    if v is None:
        return None
    return float(Decimal(str(v)))


def _pct(valor: float | None, receita: float | None) -> float | None:
    if valor is None or not receita:
        return None
    return round(valor / receita * 100, 2)


def ultimo_dia(mes: date) -> date:
    prox = date(mes.year + (mes.month // 12), mes.month % 12 + 1, 1)
    return prox - timedelta(days=1)


def somar_meses(mes: date, n: int) -> date:
    total = mes.year * 12 + (mes.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def mes_maduro(mes: date, hoje: date) -> bool:
    """Mes M e' maduro quando o mes M+MESES_MATURACAO ja terminou."""
    return hoje > ultimo_dia(somar_meses(mes, MESES_MATURACAO))


def meses_do_periodo(inicio: date, fim: date) -> list[date]:
    """Meses calendario cobertos. Exige periodo alinhado a meses inteiros."""
    if inicio.day != 1 or fim != ultimo_dia(date(fim.year, fim.month, 1)):
        raise ValueError(
            "A DRE e' mensal: o periodo precisa comecar no dia 1 e terminar no "
            "ultimo dia de um mes."
        )
    out, m = [], inicio
    while m <= fim:
        out.append(m)
        m = somar_meses(m, 1)
    return out


def normalizar_custo(componente: str, canal: str, valor: float | None) -> tuple[float | None, bool]:
    """Converte o valor da fonte em MAGNITUDE de custo (>= 0).

    Devolve `(magnitude, sinal_ok)`. Valor nulo continua nulo. Um valor com o
    sinal contrario ao medido para a fonte NAO e' corrigido: `sinal_ok=False`,
    e quem chama marca o componente como `sinal_inesperado`.
    """
    if valor is None:
        return None, True
    sinal = SINAL_FONTE[(componente, canal)]
    magnitude = valor * sinal
    if magnitude < -0.005:
        return None, False
    return max(magnitude, 0.0), True


# ---------------------------------------------------------------------------
# Insumos da celula base
# ---------------------------------------------------------------------------

@dataclass
class InsumosCelula:
    """Tudo o que a celula base `mes x canal x marca` precisa, ja lido.

    `datamart_ok=False` significa que a leitura do Data Mart falhou nesta
    requisicao; `cmv`/`repasse` `None` com `datamart_ok=True` significa que a
    fonte respondeu e nao tem linha para a chave.
    """

    mes: date
    canal: str
    marca: str
    mart: dict
    afiliado: dict | None = None
    cmv: dict | None = None
    repasse: dict | None = None
    datamart_ok: bool = True


def _comp(valor=None, status=INDISPONIVEL, motivo=None, fonte=None, somavel=True, partes=None) -> dict:
    return {
        "valor": valor, "status": status, "motivo": motivo, "fonte": fonte,
        "somavel": somavel, "partes": partes,
    }


def _cobertura_diaria(n_valores: int, dias: int) -> str:
    if n_valores == 0:
        return INDISPONIVEL
    if n_valores < dias:
        return COBERTURA_PARCIAL
    return DISPONIVEL


def _custo_do_mart(componente: str, canal: str, soma, n_valores: int, n_negativos: int,
                   linhas: int, fonte: str, motivo_ausente: str) -> dict:
    """Componente de custo lido de uma coluna diaria do mart.

    A cobertura e' medida contra o numero de LINHAS da celula (dia x loja), nao
    contra dias distintos: uma marca com duas lojas no canal tem duas linhas
    por dia, e comparar com dias faria uma coluna meio vazia parecer completa.
    """
    status = _cobertura_diaria(int(n_valores or 0), linhas)
    if status == INDISPONIVEL:
        return _comp(status=INDISPONIVEL, motivo=motivo_ausente, fonte=fonte)
    sinal_fonte = SINAL_FONTE[(componente, canal)]
    # Para fontes que gravam positivo, qualquer dia negativo e' sinal inesperado.
    if sinal_fonte > 0 and int(n_negativos or 0) > 0:
        return _comp(status=SINAL_INESPERADO,
                     motivo=f"{n_negativos} dia(s) com valor negativo numa fonte que grava custo positivo",
                     fonte=fonte)
    magnitude, ok = normalizar_custo(componente, canal, _num(soma))
    if not ok:
        return _comp(status=SINAL_INESPERADO, motivo="soma com sinal contrario ao da fonte", fonte=fonte)
    if status == COBERTURA_PARCIAL:
        return _comp(valor=magnitude, status=COBERTURA_PARCIAL,
                     motivo=f"valor presente em {n_valores} de {linhas} linhas diarias", fonte=fonte)
    return _comp(valor=magnitude, status=DISPONIVEL, fonte=fonte)


# ---------------------------------------------------------------------------
# Celula base
# ---------------------------------------------------------------------------

def montar_celula_base(ins: InsumosCelula, hoje: date) -> dict:
    """Aplica o contrato a uma celula `mes x canal x marca`. Funcao PURA."""
    canal, mart = ins.canal, ins.mart
    dias_mes = ultimo_dia(ins.mes).day
    dias = int(mart.get("dias") or 0)
    linhas = int(mart.get("linhas") or 0)
    avisos: list[str] = []
    motivos: list[str] = []
    comps: dict[str, dict] = {}

    # --- receita -----------------------------------------------------------
    receita = _num(mart.get("gmv"))
    fonte_receita = "marts.fact_marketplace_daily_performance.gmv"
    if dias >= dias_mes:
        comps["receita"] = _comp(valor=receita, status=DISPONIVEL, fonte=fonte_receita, somavel=False)
    else:
        comps["receita"] = _comp(valor=receita, status=COBERTURA_PARCIAL, fonte=fonte_receita, somavel=False,
                                 motivo=f"receita presente em {dias} de {dias_mes} dias do mes")
    if canal == "tiktok":
        avisos.append("Receita do TikTok inclui o frete pago pelo comprador (Gate DQ-TK1); ML e Shopee nao incluem.")

    # --- devolucoes e impostos: sem fonte servivel em nenhum canal ---------
    comps["devolucoes"] = _comp(motivo="sem fonte de devolucao/reembolso em valor (perguntas C6, F5)")
    comps["impostos"] = _comp(motivo="fonte candidata nao ratificada (pergunta F8); 11,6%-14,6% da receita fiscal")

    # --- taxas ---------------------------------------------------------------
    if canal in ("ml", "shopee"):
        fonte = "marts.fact_marketplace_daily_performance.total_fees"
        motivo_aus = ("comissao do ML nao carregada neste periodo" if canal == "ml"
                      else "taxa da Shopee ausente no mart")
        tx = _custo_do_mart("taxas", canal, mart.get("fees"), mart.get("fees_n"),
                            mart.get("fees_neg_n"), linhas, fonte, motivo_aus)
        if canal == "shopee" and tx["status"] == DISPONIVEL:
            tx["status"] = NAO_CONFIRMADO
            tx["motivo"] = "sem contraprova independente do repasse (pergunta C4)"
        comps["taxas"] = tx
    else:
        comps["taxas"] = _taxa_tiktok(ins, avisos)

    # --- afiliados -----------------------------------------------------------
    if canal == "tiktok":
        comps["afiliados"] = _afiliado_tiktok(ins.afiliado)
    elif canal == "ml":
        comps["afiliados"] = _comp(status=NAO_APLICAVEL, somavel=False, motivo="sem programa de afiliados medido no ML")
    else:
        comps["afiliados"] = _comp(somavel=False,
                                   motivo="nao verificado se esta dentro da comissao da Shopee (pergunta C3)")

    # --- ads -----------------------------------------------------------------
    if canal in ("ml", "shopee"):
        comps["ads"] = _custo_do_mart("ads", canal, mart.get("ads"), mart.get("ads_n"), mart.get("ads_neg_n"),
                                      linhas, "marts.fact_marketplace_daily_performance.ad_spend",
                                      "Ads ausente no mart")
    else:
        comps["ads"] = _comp(motivo="sem fonte de Ads do TikTok no mart")

    # --- frete do vendedor -----------------------------------------------------
    if canal == "ml":
        comps["frete"] = _custo_do_mart("frete", canal, mart.get("frete"), mart.get("frete_n"),
                                        mart.get("frete_neg_n"), linhas,
                                        "marts.fact_marketplace_daily_performance.seller_shipping_cost",
                                        "frete do vendedor ausente no mart")
    elif canal == "shopee":
        fr = _custo_do_mart("frete", canal, mart.get("frete"), mart.get("frete_n"), mart.get("frete_neg_n"),
                            linhas, "marts.fact_marketplace_daily_performance.seller_shipping_cost",
                            "frete ausente no mart")
        if fr["status"] in (DISPONIVEL, COBERTURA_PARCIAL):
            fr["status"] = NAO_CONFIRMADO
            fr["motivo"] = "'Valor estimado do frete' nao provado como custo do vendedor (pergunta C4)"
        comps["frete"] = fr
    else:
        comps["frete"] = _frete_tiktok(ins)

    # --- cmv -------------------------------------------------------------------
    comps["cmv"] = _cmv(ins, receita, avisos)

    # --- guardrails de dupla contagem e classificacao -------------------------
    tx, af = comps["taxas"], comps["afiliados"]
    if canal == "tiktok" and tx["status"] == DISPONIVEL and af["valor"] is not None:
        if af["valor"] > tx["valor"] + 0.01:
            motivos.append("afiliado maior que a taxa do repasse: a premissa de inclusao falhou")
        else:
            avisos.append("Afiliados ja estao dentro das taxas do TikTok; exibidos so' como decomposicao.")

    if comps["receita"]["status"] != DISPONIVEL:
        motivos.append(f"receita incompleta: {comps['receita']['motivo']}")
    for req in ("taxas", "cmv"):
        c = comps[req]
        if c["status"] != DISPONIVEL:
            motivos.append(f"{req} {c['status']}: {c['motivo']}")
    if not mes_maduro(ins.mes, hoje):
        motivos.append(
            f"mes imaturo: fecha apos {ultimo_dia(somar_meses(ins.mes, MESES_MATURACAO)).isoformat()} "
            f"(regra M+{MESES_MATURACAO}, decisao D5)"
        )

    if motivos:
        classificacao = NAO_CALCULAVEL
    elif all(comps[c]["status"] in (DISPONIVEL, NAO_APLICAVEL) for c in CUSTOS_SOMAVEIS):
        classificacao = COMPLETA
    else:
        classificacao = PARCIAL

    datas = {
        "receita_ultimo_dia": mart.get("max_date"),
        "mart_carregado_em": mart.get("ingested_at"),
        "cmv_ultima_emissao": (ins.cmv or {}).get("ultima_emissao"),
        "repasse_tiktok_atualizado_em": (ins.repasse or {}).get("atualizado_em"),
    }
    celula = {
        "mes": ins.mes.isoformat()[:7], "canal": canal, "marca": ins.marca,
        "componentes": comps, "classificacao": classificacao,
        "motivos_classificacao": motivos, "avisos_qualidade": avisos, "data_maxima": datas,
    }
    return _finalizar(celula)


def _taxa_tiktok(ins: InsumosCelula, avisos: list[str]) -> dict:
    fonte = "silver.stg_tiktok_payments_by_order.fee_and_tax_amount (coorte de pedido)"
    if not ins.datamart_ok:
        return _comp(status=FONTE_INACESSIVEL, fonte=fonte,
                     motivo="Data Mart inacessivel; o total_fees do mart esta congelado e nao e' usado")
    rep = ins.repasse
    if rep is None or rep.get("fee_and_tax") is None:
        return _comp(status=INDISPONIVEL, fonte=fonte, motivo="sem transacoes de repasse para a coorte")
    residuo = abs(_num(rep.get("residuo")) or 0.0)
    if residuo > RESIDUO_REPASSE_MAX:
        return _comp(status=NAO_RECONCILIADO, fonte=fonte,
                     motivo=f"identidade do repasse violada (residuo {residuo:.2f})")
    magnitude, ok = normalizar_custo("taxas", "tiktok", _num(rep["fee_and_tax"]))
    if not ok:
        return _comp(status=SINAL_INESPERADO, fonte=fonte, motivo="fee_and_tax positivo na soma da coorte")
    mart_fees = _num(ins.mart.get("fees"))
    if mart_fees is not None and magnitude:
        div = (abs(mart_fees) / magnitude - 1) * 100
        if abs(div) >= 1.0:
            avisos.append(f"total_fees do mart diverge {div:+.1f}% do repasse; a DRE usa o repasse.")
    return _comp(valor=magnitude, status=DISPONIVEL, fonte=fonte)


def _frete_tiktok(ins: InsumosCelula) -> dict:
    fonte = "silver.stg_tiktok_payments_by_order.shipping_cost_amount (coorte de pedido)"
    if not ins.datamart_ok:
        return _comp(status=FONTE_INACESSIVEL, fonte=fonte, motivo="Data Mart inacessivel")
    rep = ins.repasse
    if rep is None or rep.get("frete") is None:
        return _comp(status=INDISPONIVEL, fonte=fonte, motivo="sem transacoes de repasse para a coorte")
    magnitude, ok = normalizar_custo("frete", "tiktok", _num(rep["frete"]))
    if not ok:
        return _comp(status=SINAL_INESPERADO, fonte=fonte, motivo="shipping_cost positivo na soma da coorte")
    return _comp(valor=magnitude, status=DISPONIVEL, fonte=fonte)


def _afiliado_tiktok(af: dict | None) -> dict:
    fonte = "marts.fact_tiktok_affiliate_cost_order_monthly"
    if af is None:
        return _comp(somavel=False, fonte=fonte, motivo="sem lancamento de afiliado para o mes")
    nomes = ("creator", "partner", "ads")
    brutos = {n: _num(af.get(n)) for n in nomes}
    partes = {}
    for n, v in brutos.items():
        mag, ok = normalizar_custo("afiliados", "tiktok", v)
        if not ok:
            return _comp(status=SINAL_INESPERADO, somavel=False, fonte=fonte,
                         motivo=f"componente {n} positivo numa fonte assinada negativa")
        partes[n] = mag
    if any(v is None for v in partes.values()):
        return _comp(status=COBERTURA_PARCIAL, somavel=False, fonte=fonte, partes=partes,
                     motivo="algum componente ausente na fonte")
    return _comp(valor=sum(partes.values()), status=DISPONIVEL, somavel=False, fonte=fonte, partes=partes,
                 motivo="ja incluido em taxas (contrato §6); decomposicao, nao custo adicional")


def _cmv(ins: InsumosCelula, receita: float | None, avisos: list[str]) -> dict:
    fonte = "gold.nf_vendas_plataforma_sku_mensal.custo"
    if not ins.datamart_ok:
        return _comp(status=FONTE_INACESSIVEL, fonte=fonte, motivo="Data Mart inacessivel")
    c = ins.cmv
    if c is None or c.get("custo") is None:
        return _comp(status=INDISPONIVEL, fonte=fonte, motivo="sem NF de venda para marca x plataforma x mes")
    custo = _num(c["custo"])
    if custo < 0:
        return _comp(status=SINAL_INESPERADO, fonte=fonte, motivo="CMV negativo")
    receita_fiscal = _num(c.get("receita_fiscal"))
    razao = (receita_fiscal / receita) if receita and receita_fiscal is not None else None
    if razao is None or not (CMV_RAZAO_MIN <= razao <= CMV_RAZAO_MAX):
        txt = "sem receita para comparar" if razao is None else f"razao receita fiscal / GMV = {razao:.3f}"
        return _comp(valor=custo, status=NAO_RECONCILIADO, fonte=fonte,
                     motivo=f"{txt}; aceito entre {CMV_RAZAO_MIN} e {CMV_RAZAO_MAX} (pergunta F1/F7)")
    prov = _num(c.get("custo_provisorio")) or 0.0
    share = prov / custo if custo else 0.0
    if share >= CMV_PROVISORIO_INTEGRAL:
        return _comp(valor=custo, status=PROVISORIO, fonte=fonte,
                     motivo="CMV integralmente provisorio: mes nao fechado na fonte fiscal (pergunta F2)")
    if share > 0:
        avisos.append(f"{share * 100:.0f}% do CMV ainda e' provisorio na fonte fiscal.")
    avisos.append(f"CMV fiscal reconciliado: receita fiscal / GMV = {razao:.3f}.")
    return _comp(valor=custo, status=DISPONIVEL, fonte=fonte)


# ---------------------------------------------------------------------------
# Margem, completude e apresentacao
# ---------------------------------------------------------------------------

def _finalizar(celula: dict, agregado: dict | None = None) -> dict:
    """Calcula percentuais, listas de disponivel/ausente, completude e margem.

    `agregado` (so' para agregacoes) traz a margem ja somada celula a celula e
    as listas de custos usados em TODAS / em ALGUMA celula.
    """
    comps = celula["componentes"]
    receita = comps["receita"]["valor"]
    for c in comps.values():
        c["pct_receita"] = _pct(c["valor"], receita)

    aplicaveis = [c for c in CUSTOS_SOMAVEIS if comps[c]["status"] != NAO_APLICAVEL]
    if agregado is None:
        usados = [c for c in CUSTOS_SOMAVEIS if comps[c]["status"] == DISPONIVEL]
    else:
        usados = agregado["usados_em_todas"]
    celula["componentes_disponiveis"] = [c for c in COMPONENTES if comps[c]["status"] == DISPONIVEL]
    celula["componentes_ausentes"] = [
        {"componente": c, "status": comps[c]["status"], "motivo": comps[c]["motivo"]}
        for c in COMPONENTES
        if comps[c]["status"] not in (DISPONIVEL, NAO_APLICAVEL)
    ]
    celula["completude"] = {
        "usados": len(usados), "aplicaveis": len(aplicaveis),
        "texto": f"{len(usados)} de {len(aplicaveis)} custos",
    }

    if celula["classificacao"] == NAO_CALCULAVEL or receita is None:
        celula["margem_contribuicao_conhecida"] = {
            "valor": None, "pct_receita": None, "rotulo": ROTULO_MARGEM,
            "formula": None, "componentes_usados": [],
            "faltam": [c for c in aplicaveis if c not in usados],
        }
        return celula
    if agregado is None:
        valor = receita - sum(comps[c]["valor"] for c in usados)
        formula = " − ".join(["receita"] + list(usados))
        usados_algum = list(usados)
    else:
        valor = agregado["margem"]
        usados_algum = agregado["usados_em_alguma"]
        formula = (
            "soma, celula a celula (mes x canal x marca), de: receita − custos disponiveis na celula"
            if usados_algum != usados else " − ".join(["receita"] + list(usados))
        )
    celula["margem_contribuicao_conhecida"] = {
        "valor": round(valor, 2),
        "pct_receita": _pct(valor, receita),
        "rotulo": ROTULO_MARGEM,
        "formula": formula,
        "componentes_usados": usados_algum,
        "faltam": [c for c in aplicaveis if c not in usados],
    }
    return celula


_ORDEM_PIOR = {NAO_CALCULAVEL: 0, PARCIAL: 1, COMPLETA: 2}


def agregar(celulas: list[dict], **chave) -> dict | None:
    """Agrega celulas base (soma de valores, pior classificacao).

    Regras:
    - componente `disponivel` no agregado so' se `disponivel` em TODAS as
      celulas onde e' aplicavel; senao `parcial` (com o valor conhecido e a
      cobertura sobre a receita) ou o status comum quando nenhuma tem valor;
    - margem so' existe se nenhuma celula for NAO_CALCULAVEL, e e' a SOMA das
      margens das celulas. Usar so' os custos presentes em todas as celulas
      descartaria custo conhecido (ex.: Ads do ML ao agregar com o TikTok, que
      nao tem Ads) e superestimaria a margem. `faltam` lista o que faltou em
      pelo menos uma celula.
    """
    if not celulas:
        return None
    receita_total = sum(c["componentes"]["receita"]["valor"] or 0.0 for c in celulas)
    comps: dict[str, dict] = {}
    for nome in COMPONENTES:
        parts = [c["componentes"][nome] for c in celulas]
        aplic = [(p, c) for p, c in zip(parts, celulas) if p["status"] != NAO_APLICAVEL]
        somavel = parts[0]["somavel"]
        if not aplic:
            comps[nome] = _comp(status=NAO_APLICAVEL, somavel=somavel, motivo=parts[0]["motivo"])
            continue
        com_valor = [(p, c) for p, c in aplic if p["valor"] is not None]
        valor = sum(p["valor"] for p, _ in com_valor) if com_valor else None
        status_set = {p["status"] for p, _ in aplic}
        if status_set == {DISPONIVEL}:
            comps[nome] = _comp(valor=valor, status=DISPONIVEL, somavel=somavel)
        elif len(status_set) == 1:
            unico = aplic[0][0]
            comps[nome] = _comp(valor=valor, status=unico["status"], somavel=somavel, motivo=unico["motivo"])
        else:
            rec_ok = sum((c["componentes"]["receita"]["valor"] or 0.0)
                         for p, c in aplic if p["status"] == DISPONIVEL)
            cob = _pct(rec_ok, receita_total)
            comps[nome] = _comp(
                valor=valor, status=COBERTURA_PARCIAL, somavel=somavel,
                motivo=f"disponivel em celulas que somam {cob}% da receita; status: {', '.join(sorted(status_set))}",
            )
    rec_status = {c["componentes"]["receita"]["status"] for c in celulas}
    comps["receita"] = _comp(valor=receita_total, status=DISPONIVEL if rec_status == {DISPONIVEL} else COBERTURA_PARCIAL,
                             somavel=False)

    classificacao = min((c["classificacao"] for c in celulas), key=_ORDEM_PIOR.get)
    motivos = []
    for c in celulas:
        if c["classificacao"] == NAO_CALCULAVEL:
            for m in c["motivos_classificacao"]:
                motivos.append(f"{c['mes']} {c['canal']} {c['marca']}: {m}")
    avisos = []
    for c in celulas:
        for a in c["avisos_qualidade"]:
            if a not in avisos:
                avisos.append(a)
    datas: dict[str, Any] = {}
    for c in celulas:
        for k, v in c["data_maxima"].items():
            if v is not None and (datas.get(k) is None or v > datas[k]):
                datas[k] = v
        for k in c["data_maxima"]:
            datas.setdefault(k, None)

    usados_cel = [c["margem_contribuicao_conhecida"]["componentes_usados"] for c in celulas]
    agregado = {
        "usados_em_todas": [n for n in CUSTOS_SOMAVEIS if all(n in u for u in usados_cel)],
        "usados_em_alguma": [n for n in CUSTOS_SOMAVEIS if any(n in u for u in usados_cel)],
        "margem": None,
    }
    if classificacao != NAO_CALCULAVEL:
        agregado["margem"] = sum(c["margem_contribuicao_conhecida"]["valor"] for c in celulas)
    out = {
        **chave, "componentes": comps, "classificacao": classificacao,
        "motivos_classificacao": motivos, "avisos_qualidade": avisos, "data_maxima": datas,
        "celulas": len(celulas),
    }
    return _finalizar(out, agregado=agregado)


# ---------------------------------------------------------------------------
# Leitura — Neon
# ---------------------------------------------------------------------------

SQL_MART = """
    SELECT
        date_trunc('month', f.date)::date               AS mes,
        f.marketplace_id                                AS marketplace_id,
        l.brand_key                                     AS marca,
        COUNT(DISTINCT f.date)                          AS dias,
        COUNT(*)                                        AS linhas,
        SUM(f.gmv)                                      AS gmv,
        SUM(f.total_fees)                               AS fees,
        COUNT(f.total_fees)                             AS fees_n,
        COUNT(*) FILTER (WHERE f.total_fees < 0)        AS fees_neg_n,
        SUM(f.ad_spend)                                 AS ads,
        COUNT(f.ad_spend)                               AS ads_n,
        COUNT(*) FILTER (WHERE f.ad_spend < 0)          AS ads_neg_n,
        SUM(f.seller_shipping_cost)                     AS frete,
        COUNT(f.seller_shipping_cost)                   AS frete_n,
        COUNT(*) FILTER (WHERE f.seller_shipping_cost < 0) AS frete_neg_n,
        MAX(f.date)                                     AS max_date,
        MAX(f.ingested_at)                              AS ingested_at
    FROM marts.fact_marketplace_daily_performance f
    JOIN marts.dim_loja l ON l.loja_id = f.loja_id
    WHERE f.date BETWEEN :inicio AND :fim
      AND f.marketplace_id = ANY(:mkt_ids)
      AND l.brand_key = ANY(:marcas)
    GROUP BY 1, 2, 3
"""

SQL_AFILIADO = """
    SELECT ref_month AS mes, brand AS marca,
           affiliate_creator_commission AS creator,
           affiliate_partner_commission AS partner,
           affiliate_ads_commission     AS ads
    FROM marts.fact_tiktok_affiliate_cost_order_monthly
    WHERE ref_month BETWEEN :inicio AND :fim
      AND brand = ANY(:marcas)
"""


def ler_neon(db: Session, inicio: date, fim: date, mkt_ids: list[int], marcas: list[str]) -> tuple[list[dict], list[dict]]:
    params = {"inicio": inicio, "fim": fim, "mkt_ids": mkt_ids, "marcas": marcas}
    mart = [dict(r) for r in db.execute(text(SQL_MART), params).mappings().all()]
    afiliado = []
    if TIKTOK_ID in mkt_ids:
        afiliado = [dict(r) for r in db.execute(text(SQL_AFILIADO), params).mappings().all()]
    return mart, afiliado


def marcas_ativas(db: Session) -> list[str]:
    return sorted(db.execute(text("SELECT brand_key FROM marts.dim_loja WHERE ativo")).scalars().all())


# ---------------------------------------------------------------------------
# Leitura — Data Mart
# ---------------------------------------------------------------------------

SQL_CMV = """
    SELECT data_mes AS mes, marca, plataforma,
           SUM(receita_produto)   AS receita_fiscal,
           SUM(custo)             AS custo,
           SUM(custo_provisorio)  AS custo_provisorio,
           MAX(ultima_emissao)    AS ultima_emissao
    FROM gold.nf_vendas_plataforma_sku_mensal
    WHERE data_mes BETWEEN :inicio AND :fim
      AND canal = 'marketplace'
      AND plataforma = ANY(:plataformas)
      AND marca = ANY(:marcas)
    GROUP BY 1, 2, 3
"""

SQL_REPASSE_TIKTOK = """
    SELECT date_trunc('month', order_create_time)::date AS mes,
           brand AS marca,
           SUM(fee_and_tax_amount)   AS fee_and_tax,
           SUM(shipping_cost_amount) AS frete,
           SUM(settlement_amount - revenue_amount - fee_and_tax_amount - shipping_cost_amount) AS residuo,
           COUNT(*)                  AS transacoes,
           MAX(updated_at)           AS atualizado_em
    FROM silver.stg_tiktok_payments_by_order
    WHERE transaction_type = 'ORDER'
      AND order_create_time >= :inicio
      AND order_create_time < :fim_exclusivo
      AND brand = ANY(:marcas)
    GROUP BY 1, 2
"""


@dataclass
class LeituraDataMart:
    ok: bool
    cmv: list[dict] = field(default_factory=list)
    repasse: list[dict] = field(default_factory=list)


def ler_datamart(engine, inicio: date, fim: date, canais: list[str], marcas: list[str]) -> LeituraDataMart:
    """Le CMV e repasse TikTok em transacao READ ONLY.

    Qualquer falha devolve `ok=False` — os componentes dependentes saem como
    `fonte_inacessivel`. O log registra so' uma categoria fixa e o NOME da
    classe da excecao: a mensagem do driver pode carregar o DSN.
    """
    if engine is None:
        logger.warning("%s (engine ausente)", LOG_DATAMART_FALHOU)
        return LeituraDataMart(ok=False)
    params = {
        "inicio": inicio, "fim": fim, "fim_exclusivo": fim + timedelta(days=1),
        "marcas": marcas, "plataformas": [PLATAFORMA_FISCAL[c] for c in canais],
    }
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '120s'"))
            cmv = [dict(r) for r in conn.execute(text(SQL_CMV), params).mappings().all()]
            repasse = []
            if "tiktok" in canais:
                repasse = [dict(r) for r in conn.execute(text(SQL_REPASSE_TIKTOK), params).mappings().all()]
            conn.rollback()
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("%s (%s)", LOG_DATAMART_FALHOU, type(exc).__name__)
        return LeituraDataMart(ok=False)
    return LeituraDataMart(ok=True, cmv=cmv, repasse=repasse)


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

def _chave_mes(v) -> date:
    if isinstance(v, datetime):
        v = v.date()
    return date(v.year, v.month, 1)


def montar_dre(
    *,
    inicio: date,
    fim: date,
    canais: list[str],
    marcas: list[str],
    mart_rows: list[dict],
    afiliado_rows: list[dict],
    datamart: LeituraDataMart,
    hoje: date,
) -> dict:
    """Monta a resposta inteira a partir de linhas ja lidas. Funcao PURA."""
    meses = meses_do_periodo(inicio, fim)
    af_idx = {(_chave_mes(r["mes"]), r["marca"]): r for r in afiliado_rows}
    cmv_idx = {(_chave_mes(r["mes"]), r["marca"], r["plataforma"]): r for r in datamart.cmv}
    rep_idx = {(_chave_mes(r["mes"]), r["marca"]): r for r in datamart.repasse}

    base: list[dict] = []
    for r in mart_rows:
        canal = CANAL_POR_MKT.get(r["marketplace_id"])
        if canal not in canais or r["marca"] not in marcas:
            continue
        mes = _chave_mes(r["mes"])
        ins = InsumosCelula(
            mes=mes, canal=canal, marca=r["marca"], mart=r,
            afiliado=af_idx.get((mes, r["marca"])) if canal == "tiktok" else None,
            cmv=cmv_idx.get((mes, r["marca"], PLATAFORMA_FISCAL[canal])),
            repasse=rep_idx.get((mes, r["marca"])) if canal == "tiktok" else None,
            datamart_ok=datamart.ok,
        )
        base.append(montar_celula_base(ins, hoje))
    base.sort(key=lambda c: (c["mes"], CANAIS.index(c["canal"]), c["marca"]))

    def _grupo(chave_fn: Callable[[dict], tuple], rotulos: Callable[[tuple], dict]) -> list[dict]:
        grupos: dict[tuple, list[dict]] = {}
        for c in base:
            grupos.setdefault(chave_fn(c), []).append(c)
        return [agregar(cs, **rotulos(k)) for k, cs in sorted(grupos.items(), key=lambda kv: kv[0])]

    por_canal = _grupo(lambda c: (CANAIS.index(c["canal"]),),
                       lambda k: {"canal": CANAIS[k[0]], "canal_label": CANAL_LABEL[CANAIS[k[0]]]})
    por_marca = _grupo(lambda c: (c["marca"],),
                       lambda k: {"marca": k[0], "marca_label": BRAND_LABELS.get(k[0], k[0].upper())})
    por_canal_marca = _grupo(
        lambda c: (CANAIS.index(c["canal"]), c["marca"]),
        lambda k: {"canal": CANAIS[k[0]], "canal_label": CANAL_LABEL[CANAIS[k[0]]],
                   "marca": k[1], "marca_label": BRAND_LABELS.get(k[1], k[1].upper())},
    )
    return {
        "periodo": {"date_from": inicio.isoformat(), "date_to": fim.isoformat(),
                    "meses": [m.isoformat()[:7] for m in meses]},
        "filtros": {"canais": canais, "marcas": marcas},
        "fontes": {"neon": "ok", "data_mart": "ok" if datamart.ok else "inacessivel"},
        "regras": {
            "maturacao_meses": MESES_MATURACAO,
            "cmv_razao_aceita": [CMV_RAZAO_MIN, CMV_RAZAO_MAX],
            "afiliado_tiktok": "decomposicao de taxas; nunca somado",
            "rotulo_margem": ROTULO_MARGEM,
            "contrato": "docs/dre_contrato_0.md",
        },
        "total": agregar(base, escopo="total"),
        "por_canal": por_canal,
        "por_marca": por_marca,
        "por_canal_marca": por_canal_marca,
        "celulas_base": base,
    }


def get_dre(db: Session, datamart_engine, *, inicio: date, fim: date, canais: list[str],
            marcas: list[str] | None, hoje: date) -> dict:
    meses_do_periodo(inicio, fim)  # valida antes de ir ao banco
    escopo_marcas = marcas if marcas else marcas_ativas(db)
    mkt_ids = [MKT_POR_CANAL[c] for c in canais]
    mart_rows, afiliado_rows = ler_neon(db, inicio, fim, mkt_ids, escopo_marcas)
    datamart = ler_datamart(datamart_engine, inicio, fim, canais, escopo_marcas)
    return montar_dre(inicio=inicio, fim=fim, canais=canais, marcas=escopo_marcas,
                      mart_rows=mart_rows, afiliado_rows=afiliado_rows, datamart=datamart, hoje=hoje)
