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

FONTES — SO' O NEON (Gate DRE-DATA-BRIDGE-1)
-------------------------------------------
- `marts.fact_marketplace_daily_performance`: receita, taxas ML/Shopee, Ads,
  frete ML/Shopee.
- `marts.fact_tiktok_affiliate_cost_order_monthly`: afiliados TikTok.
- `marts.fact_dre_insumo_mensal`: CMV fiscal e repasse TikTok por coorte,
  AGREGADOS e publicados pelo `pipelines.dre.insumo_publisher`, que e' o unico
  que le o Data Mart. A requisicao nunca toca o Data Mart: o Render nao o
  alcanca (Gate G4).

FALHA FECHADA
-------------
Insumo ausente (migration nao aplicada, competencia nao publicada, lote nao
concluido) ou vencido (publicado ha mais de `INSUMO_IDADE_MAXIMA`) torna o
componente indisponivel, com o motivo. Nao ha fallback: o `total_fees` TikTok
do mart esta congelado (contrato §4.5, §7) e nunca substitui o repasse.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import dre_premissas
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
SNAPSHOT_AUSENTE = "snapshot_ausente"
SNAPSHOT_VENCIDO = "snapshot_vencido"
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
#: Idade maxima de um lote de insumos. O publisher roda diariamente; 48 h
#: toleram UMA execucao perdida. Passado isso o insumo e' tratado como vencido
#: — nunca exibido como atual.
INSUMO_IDADE_MAXIMA = timedelta(hours=48)
TABELA_INSUMOS = "marts.fact_dre_insumo_mensal"

#: Sinal com que cada fonte grava custo. Medido no contrato §4.5/§4.8.
SINAL_FONTE = {
    ("taxas", "ml"): +1, ("taxas", "shopee"): +1, ("taxas", "tiktok"): -1,
    ("ads", "ml"): +1, ("ads", "shopee"): +1,
    ("frete", "ml"): +1, ("frete", "shopee"): +1, ("frete", "tiktok"): -1,
    ("afiliados", "tiktok"): -1,
    ("cmv", "ml"): +1, ("cmv", "shopee"): +1, ("cmv", "tiktok"): +1,
}

ROTULO_MARGEM = "Margem de contribuição conhecida"



# ---------------------------------------------------------------------------
# Utilitarios puros
# ---------------------------------------------------------------------------

def _num(v) -> float | None:
    if v is None:
        return None
    return float(Decimal(str(v)))


#: Fuso do negocio. Offset fixo: o Brasil nao tem horario de verao desde 2019.
TZ_BRT = timezone(timedelta(hours=-3), "BRT")


def data_brt(v) -> date | None:
    """Instante -> data no fuso do negocio; `date` passa intacta.

    `data_max_origem` do CMV e' a ultima EMISSAO fiscal (uma data), publicada
    como meia-noite BRT = 03:00 UTC. O contrato expoe `cmv_ultima_emissao`
    como `date`: entregar o instante fazia o Pydantic recusar a resposta
    inteira (500) assim que a primeira fotografia foi publicada.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(TZ_BRT).date()
    return v


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


def competencia_madura_mais_recente(hoje: date) -> date:
    """Ultima competencia que `mes_maduro` aceita em `hoje`.

    E' o periodo padrao da DRE: o "mes anterior" dos outros endpoints nunca e'
    maduro pela regra M+2, e abriria a tela sempre NAO_CALCULAVEL.
    """
    return somar_meses(date(hoje.year, hoje.month, 1), -(MESES_MATURACAO + 1))


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

    `cmv` e `repasse` vem de `marts.fact_dre_insumo_mensal`, ja agrupados por
    chave. `None` = nada publicado para a chave. Quando ha linha, `_estado`
    diz se ela pode ser usada (`ok`) ou por que nao (`snapshot_ausente`,
    `snapshot_vencido`), e `_motivo` explica.
    """

    mes: date
    canal: str
    marca: str
    mart: dict
    afiliado: dict | None = None
    cmv: dict | None = None
    repasse: dict | None = None
    motivo_sem_insumo: str = "competencia nao publicada em marts.fact_dre_insumo_mensal"


def _comp(valor=None, status=INDISPONIVEL, motivo=None, fonte=None, somavel=True, partes=None,
          data_as_of=None, publicado_em=None) -> dict:
    return {
        "valor": valor, "status": status, "motivo": motivo, "fonte": fonte,
        "somavel": somavel, "partes": partes, "data_as_of": data_as_of, "publicado_em": publicado_em,
    }


def _bloqueio_do_insumo(insumo: dict | None, motivo_sem: str, fonte: str, somavel: bool = True) -> dict | None:
    """Componente indisponivel quando o insumo publicado nao pode ser usado. None = pode."""
    if insumo is None:
        return _comp(status=SNAPSHOT_AUSENTE, motivo=motivo_sem, fonte=fonte, somavel=somavel)
    if insumo["_estado"] != "ok":
        return _comp(status=insumo["_estado"], motivo=insumo["_motivo"], fonte=fonte, somavel=somavel,
                     data_as_of=insumo.get("data_as_of"), publicado_em=insumo.get("publicado_em"))
    return None


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
        "cmv_ultima_emissao": data_brt((ins.cmv or {}).get("data_as_of")),
        "repasse_tiktok_atualizado_em": (ins.repasse or {}).get("data_as_of"),
        "insumo_publicado_em": max(
            (x["publicado_em"] for x in (ins.cmv, ins.repasse) if x and x.get("publicado_em")), default=None),
    }
    celula = {
        "mes": ins.mes.isoformat()[:7], "canal": canal, "marca": ins.marca,
        "componentes": comps, "classificacao": classificacao,
        "motivos_classificacao": motivos, "avisos_qualidade": avisos, "data_maxima": datas,
    }
    return _finalizar(celula)


def _taxa_tiktok(ins: InsumosCelula, avisos: list[str]) -> dict:
    fonte = f"{TABELA_INSUMOS}.tiktok_taxa_repasse (repasse por coorte de pedido)"
    bloqueio = _bloqueio_do_insumo(ins.repasse, ins.motivo_sem_insumo, fonte)
    if bloqueio:
        return bloqueio
    rep = ins.repasse
    meta = {"fonte": fonte, "data_as_of": rep["data_as_of"], "publicado_em": rep["publicado_em"]}
    if rep.get("taxa") is None:
        return _comp(status=INDISPONIVEL, motivo="sem transacoes de repasse para a coorte na origem", **meta)
    if rep["status_taxa"] != "reconciliado":
        return _comp(status=NAO_RECONCILIADO, motivo=f"publicacao: {rep['motivo_taxa']}", **meta)
    residuo = abs(_num(rep.get("residuo")) or 0.0)
    if residuo > RESIDUO_REPASSE_MAX:
        return _comp(status=NAO_RECONCILIADO, motivo=f"identidade do repasse violada (residuo {residuo:.2f})", **meta)
    if (_num(rep.get("violacoes")) or 0) > 0:
        return _comp(status=NAO_RECONCILIADO, motivo="ha transacao com afiliado maior que a taxa", **meta)
    if rep.get("sinal_taxa") != SINAL_FONTE[("taxas", "tiktok")]:
        return _comp(status=SINAL_INESPERADO, motivo="convencao de sinal publicada diferente da esperada", **meta)
    magnitude, ok = normalizar_custo("taxas", "tiktok", _num(rep["taxa"]))
    if not ok:
        return _comp(status=SINAL_INESPERADO, motivo="fee_and_tax positivo na soma da coorte", **meta)
    mart_fees = _num(ins.mart.get("fees"))
    if mart_fees is not None and magnitude:
        div = (abs(mart_fees) / magnitude - 1) * 100
        if abs(div) >= 1.0:
            avisos.append(f"total_fees do mart diverge {div:+.1f}% do repasse; a DRE usa o repasse.")
    return _comp(valor=magnitude, status=DISPONIVEL, **meta)


def _frete_tiktok(ins: InsumosCelula) -> dict:
    fonte = f"{TABELA_INSUMOS}.tiktok_frete_repasse (repasse por coorte de pedido)"
    bloqueio = _bloqueio_do_insumo(ins.repasse, ins.motivo_sem_insumo, fonte)
    if bloqueio:
        return bloqueio
    rep = ins.repasse
    meta = {"fonte": fonte, "data_as_of": rep["data_as_of"], "publicado_em": rep["publicado_em"]}
    if rep.get("frete") is None:
        return _comp(status=INDISPONIVEL, motivo="sem transacoes de repasse para a coorte na origem", **meta)
    if rep["status_taxa"] != "reconciliado":
        return _comp(status=NAO_RECONCILIADO, motivo=f"publicacao: {rep['motivo_taxa']}", **meta)
    if rep.get("sinal_frete") != SINAL_FONTE[("frete", "tiktok")]:
        return _comp(status=SINAL_INESPERADO, motivo="convencao de sinal publicada diferente da esperada", **meta)
    magnitude, ok = normalizar_custo("frete", "tiktok", _num(rep["frete"]))
    if not ok:
        return _comp(status=SINAL_INESPERADO, motivo="shipping_cost positivo na soma da coorte", **meta)
    return _comp(valor=magnitude, status=DISPONIVEL, **meta)


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
    fonte = f"{TABELA_INSUMOS}.cmv (CMV fiscal agregado)"
    bloqueio = _bloqueio_do_insumo(ins.cmv, ins.motivo_sem_insumo, fonte)
    if bloqueio:
        return bloqueio
    c = ins.cmv
    meta = {"fonte": fonte, "data_as_of": c["data_as_of"], "publicado_em": c["publicado_em"]}
    if c.get("custo") is None:
        return _comp(status=INDISPONIVEL, motivo="sem NF de venda para marca x plataforma x mes na origem", **meta)
    if c.get("sinal") != SINAL_FONTE[("cmv", ins.canal)]:
        return _comp(status=SINAL_INESPERADO, motivo="convencao de sinal publicada diferente da esperada", **meta)
    custo = _num(c["custo"])
    if custo < 0:
        return _comp(status=SINAL_INESPERADO, motivo="CMV negativo", **meta)
    if c["status"] != "reconciliado":
        return _comp(valor=custo, status=NAO_RECONCILIADO, motivo=f"publicacao: {c['motivo']} (pergunta F1/F7)", **meta)
    # A publicacao reconciliou contra o GMV DAQUELE instante. O GMV pode ter
    # mudado desde entao: a razao e' conferida de novo com o valor de agora.
    receita_fiscal = _num(c.get("receita_fiscal"))
    razao = (receita_fiscal / receita) if receita and receita_fiscal is not None else None
    if razao is None or not (CMV_RAZAO_MIN <= razao <= CMV_RAZAO_MAX):
        txt = "sem receita para comparar" if razao is None else f"razao receita fiscal / GMV atual = {razao:.3f}"
        return _comp(valor=custo, status=NAO_RECONCILIADO,
                     motivo=f"{txt}; aceito entre {CMV_RAZAO_MIN} e {CMV_RAZAO_MAX} (pergunta F1/F7)", **meta)
    share = _num(c.get("provisorio_share")) or 0.0
    if share >= CMV_PROVISORIO_INTEGRAL:
        return _comp(valor=custo, status=PROVISORIO,
                     motivo="CMV integralmente provisorio: mes nao fechado na fonte fiscal (pergunta F2)", **meta)
    if share > 0:
        avisos.append(f"{share * 100:.0f}% do CMV ainda e' provisorio na fonte fiscal.")
    avisos.append(f"CMV fiscal reconciliado: receita fiscal / GMV = {razao:.3f}.")
    return _comp(valor=custo, status=DISPONIVEL, **meta)


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
        # Datas do insumo publicado: a MAIS ANTIGA entre as celulas. Um agregado
        # nunca pode parecer mais fresco que a sua parcela mais velha.
        for chave_data in ("data_as_of", "publicado_em"):
            datas = [p[chave_data] for p, _ in aplic if p.get(chave_data) is not None]
            comps[nome][chave_data] = min(datas) if datas else None
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
# Leitura — insumos publicados (Neon)
# ---------------------------------------------------------------------------

SQL_INSUMOS_EXISTE = "SELECT to_regclass('marts.fact_dre_insumo_mensal') IS NOT NULL"

SQL_INSUMOS = """
    SELECT i.competencia, i.canal, i.marca, i.componente, i.valor, i.sinal_origem,
           i.cobertura, i.provisorio_share, i.status_reconciliacao, i.motivo,
           i.data_max_origem, i.publicado_em, i.lote_id, a.status AS lote_status
    FROM marts.fact_dre_insumo_mensal i
    LEFT JOIN audit.source_sync_run a ON a.sync_run_id = i.lote_id
    WHERE i.competencia BETWEEN :inicio AND :fim
      AND i.canal = ANY(:canais)
      AND i.marca = ANY(:marcas)
"""


@dataclass
class LeituraInsumos:
    tabela_existe: bool
    linhas: list[dict] = field(default_factory=list)


def ler_insumos(db: Session, inicio: date, fim: date, canais: list[str], marcas: list[str]) -> LeituraInsumos:
    """Le os insumos publicados. Tabela ausente (migration 025 nao aplicada) nao
    e' erro: devolve `tabela_existe=False` e os componentes saem
    `snapshot_ausente`. `to_regclass` evita uma excecao que envenenaria a
    transacao da sessao."""
    if not db.execute(text(SQL_INSUMOS_EXISTE)).scalar():
        return LeituraInsumos(tabela_existe=False)
    rows = db.execute(text(SQL_INSUMOS), {"inicio": inicio, "fim": fim, "canais": canais,
                                          "marcas": marcas}).mappings().all()
    return LeituraInsumos(tabela_existe=True, linhas=[dict(r) for r in rows])


def _estado_do_lote(linhas: list[dict], agora: datetime) -> tuple[str, str | None]:
    """`ok`, ou o estado que torna o insumo inutilizavel e o motivo."""
    if any(r.get("lote_status") != "success" for r in linhas):
        return SNAPSHOT_AUSENTE, "lote de publicacao nao concluido com sucesso"
    publicado = min(r["publicado_em"] for r in linhas)
    if agora - publicado > INSUMO_IDADE_MAXIMA:
        return SNAPSHOT_VENCIDO, (
            f"publicado em {publicado.isoformat()}; limite de {int(INSUMO_IDADE_MAXIMA.total_seconds() // 3600)} h"
        )
    return "ok", None


def indexar_insumos(linhas: list[dict], agora: datetime) -> tuple[dict, dict]:
    """Agrupa as linhas publicadas em insumo de CMV e de repasse por chave."""
    por_chave: dict[tuple, dict[str, dict]] = {}
    for r in linhas:
        k = (_chave_mes(r["competencia"]), r["canal"], r["marca"])
        por_chave.setdefault(k, {})[r["componente"]] = r
    cmv, repasse = {}, {}
    for k, comps in por_chave.items():
        if "cmv" in comps:
            c, rf = comps["cmv"], comps.get("cmv_receita_fiscal") or {}
            grupo = [x for x in (c, rf) if x]
            estado, motivo = _estado_do_lote(grupo, agora)
            cmv[k] = {
                "_estado": estado, "_motivo": motivo,
                "custo": c["valor"], "receita_fiscal": rf.get("valor"), "sinal": c["sinal_origem"],
                "provisorio_share": c["provisorio_share"], "status": c["status_reconciliacao"],
                "motivo": c["motivo"], "data_as_of": c["data_max_origem"], "publicado_em": c["publicado_em"],
                "lote_id": c["lote_id"],
            }
        if "tiktok_taxa_repasse" in comps:
            tx = comps["tiktok_taxa_repasse"]
            frete = comps.get("tiktok_frete_repasse") or {}
            estado, motivo = _estado_do_lote(list(comps.values()), agora)
            repasse[k] = {
                "_estado": estado, "_motivo": motivo,
                "taxa": tx["valor"], "sinal_taxa": tx["sinal_origem"], "status_taxa": tx["status_reconciliacao"],
                "motivo_taxa": tx["motivo"],
                "frete": frete.get("valor"),
                "sinal_frete": frete.get("sinal_origem"),
                "residuo": (comps.get("tiktok_residuo_identidade") or {}).get("valor"),
                "violacoes": (comps.get("tiktok_tx_afiliado_maior_taxa") or {}).get("valor"),
                "data_as_of": tx["data_max_origem"], "publicado_em": tx["publicado_em"], "lote_id": tx["lote_id"],
            }
    return cmv, repasse


def resumo_insumos(leitura: LeituraInsumos, agora: datetime) -> dict:
    if not leitura.tabela_existe:
        return {"estado": "tabela_ausente", "publicado_em": None,
                "publicado_em_mais_antigo": None, "publicado_em_mais_recente": None,
                "motivo": "marts.fact_dre_insumo_mensal nao existe (migration 025 nao aplicada)"}
    if not leitura.linhas:
        return {"estado": "nao_publicado", "publicado_em": None,
                "publicado_em_mais_antigo": None, "publicado_em_mais_recente": None,
                "motivo": "nenhum insumo publicado para o periodo"}
    # `publicado_em` e' a publicacao MAIS ANTIGA do conjunto: referencia
    # conservadora. Mostrar so' o lote mais novo esconderia a parte velha.
    mais_antigo = min(r["publicado_em"] for r in leitura.linhas)
    mais_recente = max(r["publicado_em"] for r in leitura.linhas)
    vencidas = sum(1 for r in leitura.linhas if agora - r["publicado_em"] > INSUMO_IDADE_MAXIMA)
    estado = "ok" if vencidas == 0 else ("vencido" if vencidas == len(leitura.linhas) else "parcialmente_vencido")
    return {"estado": estado, "publicado_em": mais_antigo, "publicado_em_mais_antigo": mais_antigo,
            "publicado_em_mais_recente": mais_recente, "motivo": None if estado == "ok" else
            f"{vencidas} de {len(leitura.linhas)} linhas publicadas ha mais de "
            f"{int(INSUMO_IDADE_MAXIMA.total_seconds() // 3600)} h"}


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

def _chave_mes(v) -> date:
    if isinstance(v, datetime):
        v = v.date()
    return date(v.year, v.month, 1)


# ---------------------------------------------------------------------------
# Leitura — premissas estimadas (Neon, DRE-PREMISSAS-1)
# ---------------------------------------------------------------------------

SQL_PREMISSAS_EXISTE = f"SELECT to_regclass('{dre_premissas.TABELA_PREMISSAS}') IS NOT NULL"

#: So' a competencia exata do periodo: a premissa nunca e' estendida a outro mes.
SQL_PREMISSAS = f"""
    SELECT p.competencia, p.brand, p.component, p.rate, p.somavel, p.status, p.source_period,
           p.source_sha256, p.source_sent_on, p.loaded_at, p.run_id, a.status AS lote_status
    FROM {dre_premissas.TABELA_PREMISSAS} p
    LEFT JOIN audit.source_sync_run a ON a.sync_run_id = p.run_id
    WHERE p.competencia BETWEEN :inicio AND :fim
      AND p.brand = ANY(:marcas)
"""


def ler_premissas(db: Session, inicio: date, fim: date, marcas: list[str]) -> dre_premissas.LeituraPremissas:
    """Tabela ausente (migration 026 nao aplicada) nao e' erro: `tabela_existe=False`.
    `to_regclass` evita a excecao que envenenaria a transacao da sessao."""
    if not db.execute(text(SQL_PREMISSAS_EXISTE)).scalar():
        return dre_premissas.LeituraPremissas(tabela_existe=False)
    rows = db.execute(text(SQL_PREMISSAS), {"inicio": inicio, "fim": fim, "marcas": marcas}).mappings().all()
    return dre_premissas.LeituraPremissas(tabela_existe=True, linhas=[dict(r) for r in rows])


def _com_cenario(base: list[dict], premissas: dre_premissas.LeituraPremissas | None, hoje: date) -> None:
    """Acrescenta `cenario_premissas` em cada celula base. NAO toca nos campos realizados."""
    idx = dre_premissas.indexar_premissas(premissas)
    for c in base:
        mes = date.fromisoformat(c["mes"] + "-01")
        c["cenario_premissas"] = dre_premissas.montar_cenario(
            c, idx.get((mes, c["marca"])), mes=mes, hoje=hoje, maduro=mes_maduro(mes, hoje),
            fim_do_mes=ultimo_dia(mes))


def montar_dre(
    *,
    inicio: date,
    fim: date,
    canais: list[str],
    marcas: list[str],
    mart_rows: list[dict],
    afiliado_rows: list[dict],
    insumos: LeituraInsumos,
    hoje: date,
    agora: datetime,
    periodo_origem: str = "informado",
    premissas: dre_premissas.LeituraPremissas | None = None,
) -> dict:
    """Monta a resposta inteira a partir de linhas ja lidas. Funcao PURA.

    `premissas` (opcional) acrescenta `cenario_premissas` — sempre estimado —
    sem alterar nenhum campo realizado.
    """
    meses = meses_do_periodo(inicio, fim)
    af_idx = {(_chave_mes(r["mes"]), r["marca"]): r for r in afiliado_rows}
    cmv_idx, rep_idx = indexar_insumos(insumos.linhas, agora)
    motivo_sem = ("marts.fact_dre_insumo_mensal nao existe (migration 025 nao aplicada)"
                  if not insumos.tabela_existe else
                  "competencia nao publicada em marts.fact_dre_insumo_mensal")

    base: list[dict] = []
    for r in mart_rows:
        canal = CANAL_POR_MKT.get(r["marketplace_id"])
        if canal not in canais or r["marca"] not in marcas:
            continue
        mes = _chave_mes(r["mes"])
        ins = InsumosCelula(
            mes=mes, canal=canal, marca=r["marca"], mart=r,
            afiliado=af_idx.get((mes, r["marca"])) if canal == "tiktok" else None,
            cmv=cmv_idx.get((mes, canal, r["marca"])),
            repasse=rep_idx.get((mes, canal, r["marca"])) if canal == "tiktok" else None,
            motivo_sem_insumo=motivo_sem,
        )
        base.append(montar_celula_base(ins, hoje))
    base.sort(key=lambda c: (c["mes"], CANAIS.index(c["canal"]), c["marca"]))
    _com_cenario(base, premissas, hoje)

    def _agregar(cs: list[dict], **rotulos) -> dict | None:
        out = agregar(cs, **rotulos)
        if out is not None:
            # Soma dos cenarios das celulas base; nenhuma taxa e' reaplicada aqui.
            out["cenario_premissas"] = dre_premissas.agregar_cenarios([c["cenario_premissas"] for c in cs])
        return out

    def _grupo(chave_fn: Callable[[dict], tuple], rotulos: Callable[[tuple], dict]) -> list[dict]:
        grupos: dict[tuple, list[dict]] = {}
        for c in base:
            grupos.setdefault(chave_fn(c), []).append(c)
        return [_agregar(cs, **rotulos(k)) for k, cs in sorted(grupos.items(), key=lambda kv: kv[0])]

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
                    "meses": [m.isoformat()[:7] for m in meses], "origem": periodo_origem},
        "filtros": {"canais": canais, "marcas": marcas},
        "fontes": {"neon": "ok", "insumos_dre": resumo_insumos(insumos, agora),
                   "premissas_dre": dre_premissas.resumo_premissas(premissas, meses, marcas)},
        "regras": {
            "maturacao_meses": MESES_MATURACAO,
            "insumo_idade_maxima_horas": int(INSUMO_IDADE_MAXIMA.total_seconds() // 3600),
            "cmv_razao_aceita": [CMV_RAZAO_MIN, CMV_RAZAO_MAX],
            "afiliado_tiktok": "decomposicao de taxas; nunca somado",
            "rotulo_margem": ROTULO_MARGEM,
            "rotulo_margem_estimada": dre_premissas.ROTULO_MARGEM_ESTIMADA,
            "contrato": "docs/dre_contrato_0.md",
            "contrato_premissas": "docs/dre_premissas_1.md",
        },
        "total": _agregar(base, escopo="total") if base else None,
        "por_canal": por_canal,
        "por_marca": por_marca,
        "por_canal_marca": por_canal_marca,
        "celulas_base": base,
    }


def get_dre(db: Session, *, inicio: date, fim: date, canais: list[str],
            marcas: list[str] | None, hoje: date, agora: datetime,
            periodo_origem: str = "informado") -> dict:
    """Le SO' o Neon. Nenhum caminho desta funcao abre conexao com o Data Mart."""
    meses_do_periodo(inicio, fim)  # valida antes de ir ao banco
    escopo_marcas = marcas if marcas else marcas_ativas(db)
    mkt_ids = [MKT_POR_CANAL[c] for c in canais]
    mart_rows, afiliado_rows = ler_neon(db, inicio, fim, mkt_ids, escopo_marcas)
    insumos = ler_insumos(db, inicio, fim, canais, escopo_marcas)
    premissas = ler_premissas(db, inicio, fim, escopo_marcas)
    return montar_dre(inicio=inicio, fim=fim, canais=canais, marcas=escopo_marcas,
                      mart_rows=mart_rows, afiliado_rows=afiliado_rows, insumos=insumos,
                      hoje=hoje, agora=agora, periodo_origem=periodo_origem, premissas=premissas)
