"""DRE-CMV-CMP-1 — CMV PROVISORIO por CMP, por celula `competencia x canal x marca`.

O QUE E'
--------
`CMV provisorio = unidades vendidas x CMP efetivo na competencia`, somado so'
sobre os SKUs com casamento valido. Serve para destravar o cenario ESTIMADO da
DRE quando o CMV fiscal da competencia ainda nao existe. Nunca substitui o CMV
fiscal: e' gravado em tabela propria (`marts.fact_dre_cmv_provisorio_mensal`,
migration 028) e o serving so' o usa quando o fiscal esta inutilizavel.

FONTES (somente leitura, somente API)
-------------------------------------
Vendas por SKU, recortadas ate a MESMA data maxima que a DRE usa na receita da
celula (`marts.fact_marketplace_daily_performance`), para reconciliar:

  * Mercado Livre — `gold.ml_produto_diario` (pedidos da API), por `seller_sku`;
  * TikTok — `api.tiktok_analytics_skus`, `sku_id` -> `seller_sku` pelo
    inventario (`gold.tiktok_inventory_daily`); `sku_id` com mais de um
    `seller_sku` no historico fica `sku_ambiguo`, sem CMP;
  * Shopee — `gold.shopee_product_daily` (pedidos da API), SO' as marcas
    servidas pela API. Kokeshi (export manual) fica `ausente_na_fonte`, nunca zero.

CMP E CASAMENTO
---------------
Indice de CMP de `pipelines.dre.cmp_sku` (filial SP/0301, MAGA SP) e o resolvedor
de `pipelines.dre.cmp_sku_torre`, com as regras aprovadas: vigencia por
entidade (alternativa 3, carry-forward so' para frente), escopo marca ->
entidade, conflito sem escolha, codigo exato provisorio, via GTIN restrita,
KS03041/KS03042 sem CMP, kit sem regra. Sem casamento => sem CMP (NUNCA zero).

O nome do indicador continua "margem bruta apos CMP" quando o CMV e' so' CMP;
no cenario estimado da DRE ele e' rotulado como CMV provisorio por CMP.

KITS (DRE-CMV-CMP-2)
-------------------
Composicao so' de BOM EXPLICITA: `raw.bling_kit_estrutura` (estrutura do ERP
Bling, escopada por conta = marca). Nunca por semelhanca de texto nem pela
co-ocorrencia em NF (`silver.kits_composicao_marketplace` e' inferencia).

  * homogeneo (1 componente): CMP do kit = quantidade x CMP do componente;
  * misto (2+ componentes): soma de quantidade x CMP de cada componente;
  * todos os componentes precisam de CMP valido (mesmas regras de casamento);
    faltou um, o kit fica SEM custo — nunca custo parcial;
  * se o proprio codigo do kit tambem casa com um CMP direto e os dois
    divergem, `conflito_kit` (sem escolha).

MARGEM
------
`margem bruta apos CMP = receita - CMV` por SKU coberto. Na celula, a margem
numerica so' existe com 100% de cobertura; abaixo disso so' a margem da PARCELA
COBERTA, rotulada como tal. A parcela descoberta nunca recebe custo zero.

REGRA DE FILIAL (decisao final do negocio, 16/10/2026)
-----------------------------------------------------
So' a filial 0301 (`SP` nos comparativos, `0301` no CMV-LCV); Rituaria so' MAGA
SP. Nenhuma outra filial (0302, 1301, 1303, 1401, 1403...) entra, nem custo de
NF nem `preco_custo` do cadastro, e nao ha excecao para KS03041/KS03042.

VISAO POR PRODUTO (DRE-CMV-CMP-6)
--------------------------------
`visao_produto` lista TODO produto vendido (calculavel ou nao) e cada celula
ausente da fonte, para nada sumir em silencio. Margem numerica so' com CMP
valido: `direto` (codigo/GTIN no escopo 0301) ou `kit_bom` (todos os
componentes com CMP 0301). `depara_aprovado` esta reservado: nenhum de-para foi
aprovado e esta pipeline nao le de-para sugerido.

DE-PARA PELO CADASTRO (autorizado pelo negocio, DRE-CMV-CMP-8)
-------------------------------------------------------------
Quando codigo e GTIN falham, `cmp_sku_torre.resolver_depara` usa a chave
`(marca, sku_antigo)` do cadastro oficial para chegar a UM `sku` ativo/elegivel e busca
o CMP dele na 0301. Metodo `depara_aprovado`. GTIN nunca cria de-para; NF com outro
canonico bloqueia (conflito de identidade); custo de NF so' gera diagnostico privado. Codigo
resolvido sem CMP 0301 fica `codigo_resolvido_cmp_ausente` (nulo, nunca zero).
`DEPARA_CADASTRO_AUTORIZADO` liga a regra; desligada, a proposta sai em
`proposta_depara_*` e o publicador recusa `receita_depara > 0`.

Codigos de saida: 0 ok; 65 recusa; 69 fonte indisponivel; 75 lock; 78 migration ausente.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from pipelines.dre import cmp_sku as C
from pipelines.dre import cmp_sku_torre as T

logger = logging.getLogger("pipelines.dre.cmv_cmp_provisorio")

SOURCE_NAME = "dre_cmv_provisorio_mensal"
TABELA = "marts.fact_dre_cmv_provisorio_mensal"
LOCK_KEY = 918140027
EXIT_OK, EXIT_RECUSA, EXIT_FONTE, EXIT_LOCK, EXIT_MIGRATION = 0, 65, 69, 75, 78

CANAIS = ("tiktok", "ml", "shopee")
MKT_ID = {"tiktok": 1, "ml": 2, "shopee": 3}
CANAL_POR_MKT = {v: k for k, v in MKT_ID.items()}
MARCAS = tuple(T.ENTIDADES_POR_MARCA)
#: Shopee via API: Kokeshi entra por export manual e fica fora desta fonte.
MARCAS_SHOPEE_API = ("apice", "barbours", "lescent", "rituaria")
REGRA_TEMPORAL = "entidade_vigencia"
FONTE_VENDAS = {
    "ml": "gold.ml_produto_diario (API)",
    "tiktok": "api.tiktok_analytics_skus + gold.tiktok_inventory_daily (API)",
    "shopee": "gold.shopee_product_daily (API)",
}
#: Nivel de casamento -> confianca.
CONFIANCA = {T.NIVEL_GTIN: "alta", T.NIVEL_VIA_GTIN: "alta", T.NIVEL_VOLUME: "media", T.NIVEL_PROVISORIO: "baixa",
             T.NIVEL_DEPARA_GTIN: "alta", T.NIVEL_DEPARA_VOLUME: "media", T.NIVEL_DEPARA_CODIGO: "baixa"}
NIVEL_KIT_HOMOGENEO = "KIT_BOM_HOMOGENEO"
NIVEL_KIT_MISTO = "KIT_BOM_MISTO"
ORDEM_CONFIANCA = {"baixa": 0, "media": 1, "alta": 2}
#: Regras de kit habilitaveis (medidas separadamente).
REGRAS_KIT = ("homogeneo", "misto")
#: AUTORIZADO pelo negocio em 16/10/2026 (DRE-CMV-CMP-8): o cadastro oficial sustenta o de-para
#: `(marca, sku_antigo) -> sku` nas condicoes de `cmp_sku_torre.resolver_depara`. GTIN nao foi
#: autorizado como de-para. Desligar volta a visao oficial para SEM de-para.
DEPARA_CADASTRO_AUTORIZADO = True

#: Metodo da visao por produto, a partir do nivel de casamento. `depara_aprovado`
#: fica reservado ate existir um de-para APROVADO pelo negocio (hoje nenhum).
METODO_POR_NIVEL = {T.NIVEL_GTIN: "direto", T.NIVEL_VIA_GTIN: "direto", T.NIVEL_VOLUME: "direto",
                    T.NIVEL_PROVISORIO: "direto", NIVEL_KIT_HOMOGENEO: "kit_bom", NIVEL_KIT_MISTO: "kit_bom",
                    T.NIVEL_DEPARA_GTIN: "depara_aprovado", T.NIVEL_DEPARA_VOLUME: "depara_aprovado",
                    T.NIVEL_DEPARA_CODIGO: "depara_aprovado"}
#: `depara_aprovado`: de-para pelo cadastro, autorizado pelo negocio (DRE-CMV-CMP-8).
METODOS = ("direto", "kit_bom", "depara_aprovado")
#: Situacao do de-para por produto (separada nas saidas). Kit herda a do componente mais grave.
SITUACOES_DEPARA = ("depara_aplicado", T.STATUS_CODIGO_RESOLVIDO_SEM_CMP, T.STATUS_CONFLITO_IDENTIDADE, "sem_depara")
CAMPOS_PRODUTO = ("competencia", "tipo_linha", "canal", "marca", "sku", "produto", "receita", "unidades", "cmp",
                  "cmv", "margem_bruta_apos_cmp", "margem_pct", "calculavel", "metodo", "nivel", "confianca",
                  "competencia_origem_cmp", "carry_forward", "cobertura_da_celula", "status", "motivo",
                  "situacao_depara", "diagnostico_privado", "custo_nf_comparacao")
FONTE_BOM = "raw.bling_kit_estrutura (BOM explicita do Bling, conta = marca)"
RE_KIT = re.compile(r"(?i)(^kit|\bkit\b)")

# ---------------------------------------------------------------------------
# SQL (somente leitura)
# ---------------------------------------------------------------------------

#: Receita da celula e a data maxima por canal — o mesmo recorte da DRE.
SQL_RECEITA_CELULA = """
    SELECT f.marketplace_id, l.brand_key AS marca, SUM(f.gmv) AS receita, MAX(f.date) AS data_max
    FROM marts.fact_marketplace_daily_performance f
    JOIN marts.dim_loja l ON l.loja_id = f.loja_id
    WHERE f.date >= :inicio AND f.date <= :fim AND l.brand_key = ANY(:marcas)
    GROUP BY 1, 2
"""
SQL_ML = """
    SELECT brand AS marca, seller_sku AS sku, MAX(title) AS produto, NULL::text AS ean,
           SUM(units_sold) AS unidades, SUM(revenue) AS receita, false AS sku_ambiguo
    FROM gold.ml_produto_diario
    WHERE ref_date BETWEEN :inicio AND :corte AND brand = ANY(:marcas)
    GROUP BY 1, 2
"""
#: `sku_id` -> `seller_sku`: com mais de um codigo no historico, nao ha escolha.
SQL_TIKTOK = """
    WITH inv AS (
        SELECT sku_id, MAX(seller_sku) AS seller_sku, MAX(product_title) AS produto,
               COUNT(DISTINCT seller_sku) AS n_codigos
        FROM gold.tiktok_inventory_daily
        WHERE seller_sku IS NOT NULL AND seller_sku <> ''
        GROUP BY sku_id
    )
    SELECT a.brand AS marca,
           CASE WHEN i.n_codigos = 1 THEN i.seller_sku END AS sku,
           MAX(i.produto) AS produto, NULL::text AS ean,
           SUM(a.units_sold) AS unidades, SUM(a.gmv) AS receita,
           COALESCE(i.n_codigos, 0) > 1 AS sku_ambiguo
    FROM api.tiktok_analytics_skus a
    LEFT JOIN inv i ON i.sku_id = a.sku_id
    WHERE a.date BETWEEN :inicio AND :corte AND a.brand = ANY(:marcas)
    GROUP BY 1, 2, 7
"""
SQL_SHOPEE = """
    SELECT brand AS marca, sku, MAX(product_name) AS produto, MAX(ean) AS ean,
           SUM(units) AS unidades, SUM(gmv) AS receita, false AS sku_ambiguo
    FROM gold.shopee_product_daily
    WHERE date BETWEEN :inicio AND :corte AND brand = ANY(:marcas)
    GROUP BY 1, 2
"""
SQL_POR_CANAL = {"ml": SQL_ML, "tiktok": SQL_TIKTOK, "shopee": SQL_SHOPEE}
#: Custo unitario da NF de saida do Protheus, por marca x codigo, no ultimo mes com custo ate a
#: competencia. SO' para o diagnostico privado `comparacao_nf_disponivel`: nunca vira CMP.
SQL_NF_CUSTO = """
    SELECT marca, upper(btrim(codigo_produto)) AS codigo, data_mes,
           SUM(custo) / NULLIF(SUM(quantidade_item), 0) AS custo_unitario
    FROM silver.protheus_nfes_unificada
    WHERE data_mes BETWEEN :desde AND :ate AND custo_origem = 'protheus_nf'
      AND custo_unitario > 0 AND NOT COALESCE(custo_outlier, false) AND quantidade_item > 0
    GROUP BY 1, 2, 3
"""
#: Pares codigo emitido x canonico das NFs: SO' bloqueiam (conflito de identidade).
SQL_NF_PARES = """
    SELECT marca, codigo_sku, sku_canonico
    FROM gold.nf_vendas_plataforma_sku_mensal
    WHERE data_mes >= :desde AND codigo_sku IS NOT NULL AND sku_canonico IS NOT NULL
    GROUP BY 1, 2, 3
"""
#: BOM explicita do ERP. `conta` e' a marca: o mesmo codigo de kit existe em mais de uma conta.
SQL_BOM = """
    SELECT conta AS marca, kit_codigo, componente_codigo, componente_descricao, quantidade, data_carga
    FROM raw.bling_kit_estrutura
    WHERE conta = ANY(:marcas)
"""


class FonteIndisponivel(RuntimeError):
    pass


class LockOcupado(RuntimeError):
    pass


class MigrationAusente(RuntimeError):
    pass


class PublicacaoRecusada(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Regras puras
# ---------------------------------------------------------------------------

def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


@dataclass(frozen=True)
class LinhaVenda:
    canal: str
    marca: str
    sku: Optional[str]
    produto: Optional[str]
    ean: Optional[str]
    unidades: Decimal
    receita: Decimal
    sku_ambiguo: bool = False


def montar_bom(rows: list[dict]) -> dict:
    """(marca, kit) -> {"componentes": [(codigo, qtd, descricao)], "ambigua": motivo|None, "data_carga": d}.

    Ambigua: componente sem codigo, quantidade nula/<=0/fracionaria, ou o mesmo
    componente com quantidades diferentes no mesmo kit.
    """
    bom: dict = {}
    for r in rows:
        k = (r["marca"], C.chave_sku(r["kit_codigo"]))
        e = bom.setdefault(k, {"componentes": [], "ambigua": None, "data_carga": r.get("data_carga")})
        cod = C.chave_sku(r.get("componente_codigo"))
        q = r.get("quantidade")
        if not cod:
            e["ambigua"] = "componente sem codigo"
            continue
        if q is None or Decimal(str(q)) <= 0 or Decimal(str(q)) != Decimal(str(q)).to_integral_value():
            e["ambigua"] = f"quantidade invalida para {cod}"
            continue
        q = Decimal(str(q))
        ja = [x for x in e["componentes"] if x[0] == cod]
        if ja and ja[0][1] != q:
            e["ambigua"] = f"componente {cod} com quantidades diferentes"
            continue
        if not ja:
            e["componentes"].append((cod, q, r.get("componente_descricao")))
    return bom


def tipo_kit(entrada: dict) -> str:
    """homogeneo | misto | ambigua (sem composicao = ausente do BOM)."""
    if entrada["ambigua"] or not entrada["componentes"]:
        return "ambigua"
    return "homogeneo" if len(entrada["componentes"]) == 1 else "misto"


def situacao_depara(resolucoes: list) -> str:
    """Pior situacao entre as resolucoes (conflito > sem CMP > aplicado > sem de-para)."""
    st = {r.status for r in resolucoes if r is not None}
    if T.STATUS_CONFLITO_IDENTIDADE in st:
        return T.STATUS_CONFLITO_IDENTIDADE
    if T.STATUS_CODIGO_RESOLVIDO_SEM_CMP in st:
        return T.STATUS_CODIGO_RESOLVIDO_SEM_CMP
    if any(r is not None and r.nivel in T.NIVEIS_DEPARA for r in resolucoes):
        return "depara_aplicado"
    return "sem_depara"


def cmp_do_kit(ind: C.Indice, cat: T.Catalogo, idx_gtin: dict, entrada: dict, marca: str, comp: date,
               depara: bool = DEPARA_CADASTRO_AUTORIZADO) -> dict:
    """CMP do kit pela BOM, so' com TODOS os componentes cobertos. Nunca custo parcial."""
    partes, faltas, origem, carry = [], [], None, False
    confs, resolucoes = [], []
    for cod, q, desc in entrada["componentes"]:
        r = T.resolver(ind, {"componente": cod}, comp, desc, cat, marca, idx_gtin, depara)
        resolucoes.append(r)
        if r.status != "coberto":
            faltas.append(f"{cod}:{r.status}")
            continue
        partes.append(q * r.cmp)
        confs.append(CONFIANCA[r.nivel])
        origem = max(origem or r.vigencia.competencia_origem, r.vigencia.competencia_origem)
        carry = carry or r.vigencia.carry_forward
    diag = T.DIAGNOSTICO_COMPARACAO_NF if any(r.diagnostico for r in resolucoes) else ""
    if faltas:
        return {"status": "kit_componente_sem_cmp", "cmp": None, "motivo": "componentes sem CMP: " + ",".join(faltas),
                "situacao_depara": situacao_depara(resolucoes), "diagnostico": ""}
    return {"status": "coberto", "cmp": sum(partes, Decimal(0)),
            "confianca": min(confs, key=ORDEM_CONFIANCA.get), "competencia_origem": origem, "carry_forward": carry,
            "motivo": "BOM explicita: " + " + ".join(f"{q}x{c}" for c, q, _ in entrada["componentes"]),
            "situacao_depara": situacao_depara(resolucoes), "diagnostico": diag}


def classificar(ind: C.Indice, cat: T.Catalogo, vendas: list[LinhaVenda], comp: date,
                bom: Optional[dict] = None, regras_kit: tuple = REGRAS_KIT,
                depara: bool = DEPARA_CADASTRO_AUTORIZADO) -> list[dict]:
    """Resolve o CMP de cada SKU vendido. Sem casamento => `cmp=None` (nunca zero).

    `regras_kit` liga as regras de kit separadamente ("homogeneo", "misto"),
    para medir quanto cada uma eleva a cobertura.
    """
    idx_gtin = T.indice_gtin(ind)
    bom = bom or {}
    out = []
    for v in vendas:
        r: Optional[T.Resolucao] = None
        kit = None
        kit_tipo = ""
        cmp, nivel, conf, origem, carry = None, "", "", None, False
        cod = C.chave_sku(v.sku)
        if v.sku_ambiguo:
            status, motivo = "sku_ambiguo", "sku_id com mais de um seller_sku no inventario"
        elif not v.sku:
            status, motivo = "sem_sku", "venda sem seller_sku"
        elif T.origem_a_confirmar(v.marca, {"seller_sku": v.sku}):
            # A BOM nunca contorna o bloqueio de origem (KS03041/KS03042).
            status, motivo = "origem_a_confirmar", "origem empresarial nao confirmada"
        else:
            entrada = bom.get((v.marca, cod))
            e_kit_cadastro = T.classificar_tipo(cat, v.marca, [cod]) == "kit"
            if not e_kit_cadastro:
                texto = " ".join(x for x in (v.ean, v.produto) if x)
                r = T.resolver(ind, {"seller_sku": v.sku}, comp, texto, cat, v.marca, idx_gtin, depara)
            if entrada is not None:
                kit_tipo = tipo_kit(entrada)
                if kit_tipo == "ambigua":
                    kit = {"status": "kit_composicao_ambigua", "cmp": None, "motivo": entrada["ambigua"]}
                elif kit_tipo in regras_kit:
                    kit = cmp_do_kit(ind, cat, idx_gtin, entrada, v.marca, comp, depara)
                else:
                    kit = {"status": "kit_regra_nao_aplicada", "cmp": None, "motivo": f"regra {kit_tipo} desligada"}
            direto = r is not None and r.status == "coberto"
            if r is not None and r.status in ("conflito", "ambiguo", T.STATUS_CONFLITO_IDENTIDADE):
                # Conflito ou ambiguidade no codigo direto nunca e' resolvido pela BOM.
                status, motivo = r.status, r.motivo
            elif direto and kit and kit["cmp"] is not None and abs(kit["cmp"] - r.cmp) > Decimal("0.005"):
                status, motivo = "conflito_kit", f"CMP direto {r.cmp} x BOM {kit['cmp']}: sem escolha"
            elif direto:
                status, motivo, cmp, nivel = r.status, r.motivo, r.cmp, r.nivel
                conf, origem, carry = CONFIANCA[nivel], r.vigencia.competencia_origem, r.vigencia.carry_forward
            elif kit is not None and kit["cmp"] is not None:
                status, motivo, cmp = "coberto", kit["motivo"], kit["cmp"]
                nivel = NIVEL_KIT_HOMOGENEO if kit_tipo == "homogeneo" else NIVEL_KIT_MISTO
                conf, origem, carry = kit["confianca"], kit["competencia_origem"], kit["carry_forward"]
            elif kit is not None:
                status, motivo = kit["status"], kit["motivo"]
            elif e_kit_cadastro:
                status, motivo = "kit_sem_composicao", "kit no cadastro sem BOM explicita"
            else:
                status, motivo = r.status, r.motivo
        cmv = (v.unidades * cmp) if cmp is not None else None
        usou_kit = kit is not None and (status == "coberto" and nivel in (NIVEL_KIT_HOMOGENEO, NIVEL_KIT_MISTO)
                                        or status == "kit_componente_sem_cmp")
        sit = kit.get("situacao_depara", "sem_depara") if usou_kit else situacao_depara([r])
        direto_coberto = not usou_kit and r is not None and status == "coberto"
        diag = kit.get("diagnostico", "") if usou_kit else (r.diagnostico if direto_coberto else "")
        custo_nf = r.custo_nf if direto_coberto else None
        out.append({
            "canal": v.canal, "marca": v.marca, "sku": v.sku, "produto": v.produto,
            "unidades": v.unidades, "receita": v.receita, "status": status, "nivel": nivel,
            "confianca": conf, "motivo": motivo, "kit_tipo": kit_tipo,
            "cmp": cmp, "cmv": cmv,
            "margem_bruta_apos_cmp": (v.receita - cmv) if cmv is not None else None,
            "competencia_origem": origem, "carry_forward": carry,
            "entidades": "|".join(r.entidades) if r is not None else "",
            "candidatos": "|".join(f"{e}:{s}:{c}" for e, s, c, _ in r.candidatos) if r is not None else "",
            "parece_kit": bool(RE_KIT.search(f"{v.sku or ''} {v.produto or ''}")),
            "situacao_depara": sit, "diagnostico": diag, "custo_nf_comparacao": custo_nf,
        })
    return out


def confianca_predominante(receita_por_confianca: dict) -> Optional[str]:
    """Confianca com mais receita coberta; empate vai para a MENOR confianca.

    Aceita chaves de confianca (`alta`/`media`/`baixa`) ou niveis de casamento.
    """
    por_conf: Counter = Counter()
    for chave, rec in receita_por_confianca.items():
        por_conf[CONFIANCA.get(chave, chave)] += rec
    if not por_conf or sum(por_conf.values()) == 0:
        return None
    topo = max(por_conf.values())
    return min((c for c, v in por_conf.items() if v == topo), key=ORDEM_CONFIANCA.get)


def agregar(linhas: list[dict], receita_celula: dict, cortes: dict, comp: date) -> list[dict]:
    """Uma linha por celula ESPERADA (as que tem receita na DRE).

    Celula sem nenhuma venda na fonte -> `ausente_na_fonte`, com receita e CMV
    NULL. Celula com venda e nenhum SKU coberto -> `cmv_provisorio = NULL`.
    """
    por_celula: dict = defaultdict(list)
    for ln in linhas:
        por_celula[(ln["canal"], ln["marca"])].append(ln)
    out = []
    for (canal, marca), rec_cel in sorted(receita_celula.items()):
        ls = por_celula.get((canal, marca), [])
        base = {"competencia": comp, "canal": canal, "marca": marca, "data_corte": cortes.get(canal),
                "receita_celula": rec_cel, "fonte_vendas": FONTE_VENDAS[canal], "regra_temporal": REGRA_TEMPORAL}
        if not ls:
            out.append({**base, "status": "ausente_na_fonte", "receita_fonte": None, "unidades": None,
                        "receita_coberta": None, "cobertura": None, "cmv_provisorio": None,
                        "unidades_sem_cmp": None, "skus_total": 0, "skus_cobertos": 0, "skus_conflito": 0,
                        "skus_sem_cmp": 0, "receita_gtin": None, "receita_volume": None,
                        "receita_codigo_provisorio": None, "receita_carry_forward": None,
                        "receita_kit_homogeneo": None, "receita_kit_misto": None, "receita_depara": None,
                        "margem_bruta_parcela_coberta": None, "margem_bruta_apos_cmp": None,
                        "receita_por_status": {}, "confianca": None})
            continue
        cobertos = [x for x in ls if x["cmp"] is not None]
        rec_fonte = sum((x["receita"] for x in ls), Decimal(0))
        rec_cob = sum((x["receita"] for x in cobertos), Decimal(0))
        por_nivel: Counter = Counter()
        por_conf: Counter = Counter()
        por_status: Counter = Counter()
        for x in ls:
            por_status[x["status"]] += x["receita"]
            if x["cmp"] is not None:
                por_nivel[x["nivel"]] += x["receita"]
                por_conf[x["confianca"]] += x["receita"]
        cmv_cob = sum((x["cmv"] for x in cobertos), Decimal(0)) if cobertos else None
        integral = bool(rec_fonte) and rec_cob == rec_fonte
        out.append({
            **base, "status": "calculado", "receita_fonte": rec_fonte,
            "unidades": sum((x["unidades"] for x in ls), Decimal(0)),
            "receita_coberta": rec_cob,
            "cobertura": (rec_cob / rec_fonte).quantize(Decimal("0.000001")) if rec_fonte else Decimal(0),
            "cmv_provisorio": cmv_cob,
            "unidades_sem_cmp": sum((x["unidades"] for x in ls if x["cmp"] is None), Decimal(0)),
            "skus_total": len(ls), "skus_cobertos": len(cobertos),
            "skus_conflito": sum(1 for x in ls if x["status"] in ("conflito", "conflito_kit",
                                                                    T.STATUS_CONFLITO_IDENTIDADE)),
            "skus_sem_cmp": sum(1 for x in ls if x["cmp"] is None),
            "receita_gtin": por_nivel[T.NIVEL_GTIN] + por_nivel[T.NIVEL_VIA_GTIN],
            "receita_volume": por_nivel[T.NIVEL_VOLUME],
            "receita_codigo_provisorio": por_nivel[T.NIVEL_PROVISORIO],
            "receita_carry_forward": sum((x["receita"] for x in cobertos if x["carry_forward"]), Decimal(0)),
            "receita_kit_homogeneo": por_nivel[NIVEL_KIT_HOMOGENEO],
            "receita_kit_misto": por_nivel[NIVEL_KIT_MISTO],
            # Direto por de-para deterministico. Kit com componente por de-para conta como kit.
            "receita_depara": sum((por_nivel[n] for n in T.NIVEIS_DEPARA), Decimal(0)),
            # So' da parcela coberta, rotulada assim. A margem da CELULA so' existe com 100%.
            "margem_bruta_parcela_coberta": (rec_cob - cmv_cob) if cmv_cob is not None else None,
            "margem_bruta_apos_cmp": (rec_fonte - cmv_cob) if integral and cmv_cob is not None else None,
            "receita_por_status": dict(por_status),
            "confianca": confianca_predominante(dict(por_conf)),
        })
    return out


def visao_produto(linhas: list[dict], celulas: list[dict], comp: date) -> list[dict]:
    """Uma linha por produto vendido + uma por celula ausente da fonte.

    Margem numerica so' quando ha CMP valido (`status == coberto`). Sem CMP, CMP,
    CMV, margem e percentual ficam NULL (nunca zero) e `motivo` diz por que.
    Falha fechada: CMP fora de `coberto`, ou nivel sem metodo conhecido, levanta.
    """
    cob = {(c["canal"], c["marca"]): c["cobertura"] for c in celulas}
    out = []
    for x in linhas:
        calculavel = x["cmp"] is not None
        if calculavel != (x["status"] == "coberto"):
            raise ValueError(f"linha incoerente: status {x['status']} com cmp {x['cmp']}")
        metodo = METODO_POR_NIVEL.get(x["nivel"]) if calculavel else None
        if calculavel and metodo is None:
            raise ValueError(f"nivel sem metodo conhecido: {x['nivel']!r}")
        out.append({
            "competencia": comp, "tipo_linha": "produto", "canal": x["canal"], "marca": x["marca"],
            "sku": x["sku"], "produto": x["produto"], "receita": x["receita"], "unidades": x["unidades"],
            "cmp": x["cmp"] if calculavel else None,
            "cmv": x["cmv"] if calculavel else None,
            "margem_bruta_apos_cmp": x["margem_bruta_apos_cmp"] if calculavel else None,
            "margem_pct": (x["margem_bruta_apos_cmp"] / x["receita"] * 100).quantize(Decimal("0.01"))
            if calculavel and x["receita"] else None,
            "calculavel": calculavel, "metodo": metodo, "nivel": x["nivel"] or None,
            "confianca": x["confianca"] or None,
            "competencia_origem_cmp": x["competencia_origem"] if calculavel else None,
            "carry_forward": x["carry_forward"] if calculavel else None,
            "cobertura_da_celula": cob.get((x["canal"], x["marca"])),
            "status": x["status"],
            "motivo": None if calculavel else f"{x['status']}: {x['motivo'] or 'sem detalhe'}",
            "situacao_depara": x.get("situacao_depara", "sem_depara"),
            # Diagnostico PRIVADO: so' no CSV por produto; nunca na saida publica nem em CMV/margem.
            "diagnostico_privado": x.get("diagnostico") or None,
            "custo_nf_comparacao": x.get("custo_nf_comparacao"),
        })
    for c in celulas:
        if c["status"] == "ausente_na_fonte":
            out.append({**{k: None for k in CAMPOS_PRODUTO}, "competencia": comp, "tipo_linha": "celula_ausente",
                        "canal": c["canal"], "marca": c["marca"], "calculavel": False, "status": c["status"],
                        "motivo": "ausente_na_fonte: celula com receita na DRE e sem nenhuma venda na fonte de API "
                                  "(nunca custo zero)"})
    return out


def resumo_publico(celulas: list[dict]) -> list[dict]:
    """So' percentuais e contagens: nada de receita, CMP, CMV ou SKU."""
    out = []
    for c in celulas:
        pct = None if c["cobertura"] is None else float((c["cobertura"] * 100).quantize(Decimal("0.1")))
        out.append({"canal": c["canal"], "marca": c["marca"], "status": c["status"], "cobertura_pct": pct,
                    "skus_total": c["skus_total"], "skus_cobertos": c["skus_cobertos"],
                    "skus_conflito": c["skus_conflito"], "confianca": c["confianca"]})
    return out


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def _rows(conn, sql: str, p: dict) -> list[dict]:
    from sqlalchemy import text
    return [dict(r) for r in conn.execute(text(sql), p).mappings().all()]


def ler_receita_celulas(neon, comp: date, fim: date) -> tuple[dict, dict]:
    """Receita da DRE por celula e data maxima por canal (Neon, READ ONLY)."""
    from sqlalchemy import text
    with neon.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = _rows(conn, SQL_RECEITA_CELULA, {"inicio": comp, "fim": fim, "marcas": list(MARCAS)})
        conn.rollback()
    receita, cortes = {}, {}
    for r in rows:
        canal = CANAL_POR_MKT.get(r["marketplace_id"])
        if canal is None:
            continue
        receita[(canal, r["marca"])] = _d(r["receita"])
        cortes[canal] = max(cortes.get(canal, r["data_max"]), r["data_max"])
    return receita, cortes


def ler_vendas(datamart, comp: date, cortes: dict) -> tuple[list[LinhaVenda], T.Catalogo, dict]:
    """Vendas por SKU de cada canal ate a data de corte da DRE (Data Mart, READ ONLY)."""
    from sqlalchemy import text
    vendas = []
    with datamart.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '600s'"))
        for canal, sql in SQL_POR_CANAL.items():
            if canal not in cortes:
                continue
            marcas = list(MARCAS_SHOPEE_API) if canal == "shopee" else list(MARCAS)
            for r in _rows(conn, sql, {"inicio": comp, "corte": cortes[canal], "marcas": marcas}):
                vendas.append(LinhaVenda(canal, r["marca"], r["sku"], r["produto"], r["ean"],
                                         _d(r["unidades"]), _d(r["receita"]), bool(r["sku_ambiguo"])))
        cad = _rows(conn, T.SQL_CADASTRO, {})
        kits = _rows(conn, T.SQL_KITS, {})
        bom = montar_bom(_rows(conn, SQL_BOM, {"marcas": list(MARCAS)}))
        nf = T.montar_pares_nf(_rows(conn, SQL_NF_PARES, {"desde": date(comp.year - 1, 1, 1)}))
        nf_custo = T.montar_custos_nf(_rows(conn, SQL_NF_CUSTO, {"desde": C.somar_meses(comp, -2), "ate": comp}))
        conn.rollback()
    cat = T.montar_catalogo(cad, kits)
    cat.nf = nf
    cat.nf_custo = nf_custo
    return vendas, cat, bom


# ---------------------------------------------------------------------------
# Publicacao (so' com --apply)
# ---------------------------------------------------------------------------

COLUNAS = ("competencia", "canal", "marca", "data_corte", "status", "receita_fonte", "unidades", "receita_coberta",
           "cobertura", "cmv_provisorio", "unidades_sem_cmp", "skus_total", "skus_cobertos", "skus_conflito",
           "skus_sem_cmp", "receita_gtin", "receita_volume", "receita_codigo_provisorio", "receita_carry_forward",
           "receita_kit_homogeneo", "receita_kit_misto", "receita_depara", "confianca", "regra_temporal",
           "fonte_vendas",
           "cmp_fontes_sha256")
SQL_EXISTE = f"SELECT to_regclass('{TABELA}') IS NOT NULL"
SQL_LOCK = f"SELECT pg_try_advisory_xact_lock({LOCK_KEY})"
SQL_APAGA = f"DELETE FROM {TABELA} WHERE competencia = :competencia"
SQL_INSERE = (f"INSERT INTO {TABELA} ({', '.join(COLUNAS)}, publicado_em, run_id) VALUES ("
              + ", ".join(f":{c}" for c in COLUNAS) + ", :publicado_em, :run_id)")
SQL_CONTA = f"SELECT count(*) FROM {TABELA} WHERE competencia = :competencia"
SQL_FORA = f"SELECT count(*) FROM {TABELA} WHERE competencia <> :competencia"
SQL_AUDIT_ABRE = """INSERT INTO audit.source_sync_run (source_name, status, started_at, source_min_date, source_max_date)
    VALUES (:fonte, 'running', :agora, :competencia, :competencia) RETURNING sync_run_id"""
SQL_AUDIT_FECHA = """UPDATE audit.source_sync_run SET status = 'success', finished_at = :agora,
    rows_extracted = :n, rows_loaded = :n WHERE sync_run_id = :run_id"""
SQL_AUDIT_FALHA = """INSERT INTO audit.source_sync_run (source_name, status, started_at, finished_at, error_message,
    source_min_date, source_max_date) VALUES (:fonte, 'failed', :agora, :agora, :erro, :competencia, :competencia)"""


def sha_das_fontes(leituras: list[C.Leitura]) -> str:
    return hashlib.sha256("|".join(sorted(le.sha256 for le in leituras)).encode()).hexdigest()


def publicar(neon, celulas: list[dict], comp: date, cmp_sha: str, *, agora: Optional[datetime] = None,
             antes_do_commit: Optional[Callable[[Any], None]] = None) -> dict:
    """Transacao unica, lock sem espera, DELETE so' da competencia, reconciliacao. Sem retry."""
    from sqlalchemy import text
    from pipelines.dre.premissa_publisher import sanitizar

    if not DEPARA_CADASTRO_AUTORIZADO and any(c.get("receita_depara") for c in celulas):
        raise PublicacaoRecusada("celula com cobertura por de-para deterministico, ainda nao autorizado pelo negocio")
    agora = agora or datetime.now(timezone.utc)
    p = {"competencia": comp}
    try:
        with neon.begin() as conn:
            if not conn.execute(text(SQL_LOCK)).scalar():
                raise LockOcupado(f"advisory lock {LOCK_KEY} ocupado")
            if not conn.execute(text(SQL_EXISTE)).scalar():
                raise MigrationAusente(f"{TABELA} nao existe: aplique a migration 028 antes do --apply")
            run_id = conn.execute(text(SQL_AUDIT_ABRE), {"fonte": SOURCE_NAME, "agora": agora,
                                                          "competencia": comp}).scalar_one()
            fora = conn.execute(text(SQL_FORA), p).scalar_one()
            conn.execute(text(SQL_APAGA), p)
            for c in celulas:
                conn.execute(text(SQL_INSERE), {**{k: c.get(k) for k in COLUNAS}, "cmp_fontes_sha256": cmp_sha,
                                                "publicado_em": agora, "run_id": run_id})
            if conn.execute(text(SQL_CONTA), p).scalar_one() != len(celulas):
                raise PublicacaoRecusada("linhas gravadas diferem das montadas")
            if conn.execute(text(SQL_FORA), p).scalar_one() != fora:
                raise PublicacaoRecusada("linhas de outras competencias mudaram")
            conn.execute(text(SQL_AUDIT_FECHA), {"agora": agora, "n": len(celulas), "run_id": run_id})
            if antes_do_commit is not None:
                antes_do_commit(conn)
    except BaseException as exc:
        if not isinstance(exc, LockOcupado):
            try:
                with neon.begin() as conn:
                    conn.execute(text(SQL_AUDIT_FALHA), {"fonte": SOURCE_NAME, "agora": agora,
                                                          "erro": sanitizar(exc), "competencia": comp})
            except Exception as aud:  # noqa: BLE001
                logger.error("auditoria da falha nao registrada: %s", sanitizar(aud))
        raise
    return {"run_id": run_id, "gravadas": len(celulas), "outras_competencias_preservadas": fora}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _mes(s: str) -> date:
    m = re.fullmatch(r"(\d{4})-(\d{2})", s or "")
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise argparse.ArgumentTypeError(f"competencia invalida {s!r}; use YYYY-MM")
    return date(int(m.group(1)), int(m.group(2)), 1)


def _json(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o).__name__)


def _engines():
    from sqlalchemy import create_engine
    from pipelines.common.config import settings
    from pipelines.dre.insumo_publisher import url_psycopg2

    if not settings.database_url or not settings.datamart_url:
        raise FonteIndisponivel("DATABASE_URL ou Data Mart nao configurado")
    return (create_engine(url_psycopg2(settings.database_url), pool_pre_ping=True, future=True),
            create_engine(url_psycopg2(settings.datamart_url), pool_pre_ping=True, future=True))


def main(argv: Optional[list[str]] = None, *, engines: Optional[Callable] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipelines.dre.cmv_cmp_provisorio",
                                 description="CMV provisorio por CMP. Dry-run por padrao.")
    ap.add_argument("--pasta", required=True, type=Path, help="planilhas de CMP (privadas)")
    ap.add_argument("--competencia", required=True, type=_mes)
    ap.add_argument("--saida", type=Path, default=Path(".local/dre_cmv_cmp"), help="relatorio privado")
    ap.add_argument("--apply", action="store_true", help="publica no Neon; sem ele e' dry-run")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    comp = args.competencia
    fim = C.somar_meses(comp, 1) - timedelta(days=1)
    try:
        fontes, _ = C.descobrir_fontes(args.pasta)
        leituras = [C.ler(f) for f in fontes]
    except C.FonteRecusada as exc:
        print(json.dumps({"status": "recusado", "motivo": str(exc)}, ensure_ascii=False))
        return EXIT_RECUSA
    ind = C.construir_indice(leituras)
    try:
        neon, datamart = (engines or _engines)()
        receita_cel, cortes = ler_receita_celulas(neon, comp, fim)
        vendas, cat, bom = ler_vendas(datamart, comp, cortes)
    except Exception as exc:  # noqa: BLE001
        from pipelines.dre.premissa_publisher import sanitizar
        print(json.dumps({"status": "fonte_indisponivel", "detalhe": sanitizar(exc)}, ensure_ascii=False))
        return EXIT_FONTE
    # Quanto cada regra eleva a cobertura: sem kit, so' homogeneo, homogeneo + misto.
    cenarios = {"sem_kit": ((), False), "homogeneo": (("homogeneo",), False), "homogeneo_e_misto": (REGRAS_KIT, False),
                "com_depara_cadastro": (REGRAS_KIT, True)}
    cobertura_por_regra = {}
    for nome, (regras, dp) in cenarios.items():
        cels = agregar(classificar(ind, cat, vendas, comp, bom, regras, dp), receita_cel, cortes, comp)
        cobertura_por_regra[nome] = {f"{c['canal']}|{c['marca']}": c["cobertura"] for c in cels}
    linhas = classificar(ind, cat, vendas, comp, bom, REGRAS_KIT)   # oficial: so' o autorizado
    celulas = agregar(linhas, receita_cel, cortes, comp)
    # Proposta (nao oficial): o mesmo calculo com o de-para deterministico ligado.
    linhas_prop = classificar(ind, cat, vendas, comp, bom, REGRAS_KIT, True)
    celulas_prop = agregar(linhas_prop, receita_cel, cortes, comp)
    args.saida.mkdir(parents=True, exist_ok=True)
    with open(args.saida / f"matriz_cmv_cmp_{comp:%Y-%m}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]) if linhas else ["vazio"])
        w.writeheader()
        w.writerows(linhas)
    produtos = visao_produto(linhas, celulas, comp)
    # Visao completa (calculaveis, nao calculaveis com motivo e celulas ausentes) e o recorte so' calculavel.
    for nome, recorte in ((f"produto_cmp_{comp:%Y-%m}.csv", produtos),
                          (f"margem_produto_{comp:%Y-%m}.csv", [p for p in produtos if p["calculavel"]])):
        with open(args.saida / nome, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(CAMPOS_PRODUTO))
            w.writeheader()
            w.writerows(recorte)
    if not DEPARA_CADASTRO_AUTORIZADO:
        with open(args.saida / f"proposta_depara_produto_cmp_{comp:%Y-%m}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(CAMPOS_PRODUTO))
            w.writeheader()
            w.writerows(visao_produto(linhas_prop, celulas_prop, comp))
        (args.saida / f"proposta_depara_celulas_{comp:%Y-%m}.json").write_text(
            json.dumps(celulas_prop, default=_json, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.saida / f"cobertura_por_regra_{comp:%Y-%m}.json").write_text(
        json.dumps(cobertura_por_regra, default=_json, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.saida / f"celulas_cmv_cmp_{comp:%Y-%m}.json").write_text(
        json.dumps(celulas, default=_json, ensure_ascii=False, indent=1), encoding="utf-8")
    saida: dict = {"modo": "apply" if args.apply else "dry-run", "competencia": comp.isoformat()[:7],
                   "cortes": {k: v.isoformat() for k, v in cortes.items()}, "celulas": resumo_publico(celulas),
                   "produtos": {"calculaveis": sum(1 for p in produtos if p["calculavel"]),
                                "nao_calculaveis": sum(1 for p in produtos
                                                       if p["tipo_linha"] == "produto" and not p["calculavel"]),
                                "celulas_ausentes": sum(1 for p in produtos if p["tipo_linha"] == "celula_ausente"),
                                "por_situacao_depara": dict(Counter(p["situacao_depara"] for p in produtos
                                                                    if p["tipo_linha"] == "produto"))},
                   "depara_cadastro_autorizado": DEPARA_CADASTRO_AUTORIZADO}
    if not DEPARA_CADASTRO_AUTORIZADO:
        saida["proposta_depara_nao_oficial"] = resumo_publico(celulas_prop)
    codigo = EXIT_OK
    if args.apply:
        try:
            saida["escrita"] = publicar(neon, celulas, comp, sha_das_fontes(leituras))
        except LockOcupado as exc:
            saida["erro"], codigo = str(exc), EXIT_LOCK
        except MigrationAusente as exc:
            saida["erro"], codigo = str(exc), EXIT_MIGRATION
        except PublicacaoRecusada as exc:
            saida["erro"], codigo = str(exc), EXIT_RECUSA
    print(json.dumps(saida, default=_json, ensure_ascii=False, indent=1))
    return codigo


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
