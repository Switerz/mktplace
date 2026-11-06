"""Top produtos por marca na Gerencial — o que MAIS VENDE, como foi vendido.

Referencia: "Mix Marketplace" da Natalia (question 19829: data, canal, marca,
sku, quantidade, receita). Aqui o ranking e' por ANUNCIO VENDIDO em cada canal,
sem de-para e sem explodir kit: se o kit vende mais, aparece o kit.

Fontes (Data Mart), uma consulta por canal selecionado:
  TikTok  gold.fct_tiktok_product_daily  (product_id; soma = GMV da Torre)
  ML      gold.vw_ml_produto_daily       (seller_sku; soma = GMV da Torre)
  Shopee  silver.stg_shopee_order_items x stg_shopee_orders
          (sem cancelado/nao pago/brinde; preco do item, antes do cupom)
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Callable, Optional

from app.schemas.top_produtos import (
    DetalheProduto, ExtraCanal, MarcaTop, PontoDiario, ProdutoTop, ResumoCanal, TopProdutosResponse,
)
from app.services.gold_service import BRANDS_IN_SCOPE

Consultar = Callable[[str, dict], list[dict]]

TOP_N = 15
CANAIS = ("tiktok", "ml", "shopee")
_ID_DO_CANAL = {1: "tiktok", 2: "ml", 3: "shopee"}

# Cada base devolve (marca, sku, nome, dia, pecas, receita) no intervalo :de..:ate.
_BASE = {
    "tiktok": """
        SELECT brand AS marca, product_id AS sku, product_name AS nome, date AS dia,
               items_sold AS pecas, gmv AS receita
          FROM gold.fct_tiktok_product_daily
         WHERE brand = ANY(:marcas) AND date BETWEEN :de AND :ate""",
    "ml": """
        SELECT brand AS marca, COALESCE(NULLIF(seller_sku, ''), item_id) AS sku, title AS nome,
               ref_date::date AS dia, units_sold AS pecas, revenue AS receita
          FROM gold.vw_ml_produto_daily
         WHERE brand = ANY(:marcas) AND ref_date BETWEEN :de AND :ate""",
    "shopee": """
        SELECT o.brand AS marca, COALESCE(NULLIF(i.sku, ''), i.item_id) AS sku, i.item_name AS nome,
               o.created_date_brt AS dia, i.quantity AS pecas, i.quantity * i.discounted_price AS receita
          FROM silver.stg_shopee_order_items i
          JOIN silver.stg_shopee_orders o ON o.order_sn = i.order_sn
         WHERE o.order_status NOT IN ('cancelled', 'unpaid')
           AND COALESCE(i.is_freebie, false) = false
           AND o.brand = ANY(:marcas) AND o.created_date_brt BETWEEN :de AND :ate""",
}

SQL_TOP = """
WITH base AS ({base}),
agg AS (
    SELECT marca, sku, MAX(nome) AS nome,
           SUM(pecas)   FILTER (WHERE dia BETWEEN :ini AND :fim) AS pecas,
           SUM(receita) FILTER (WHERE dia BETWEEN :ini AND :fim) AS receita,
           SUM(pecas)   FILTER (WHERE dia BETWEEN :ini_ant AND :fim_ant) AS pecas_ant,
           SUM(receita) FILTER (WHERE dia BETWEEN :ini_ant AND :fim_ant) AS receita_ant
      FROM base GROUP BY marca, sku
), ranqueado AS (
    SELECT agg.*,
           SUM(receita) OVER (PARTITION BY marca) AS receita_marca,
           SUM(receita_ant) OVER (PARTITION BY marca) AS receita_marca_ant,
           ROW_NUMBER() OVER (PARTITION BY marca ORDER BY receita DESC NULLS LAST, sku) AS posicao
      FROM agg
)
SELECT marca, sku, nome, pecas, receita, pecas_ant, receita_ant, receita_marca, receita_marca_ant
  FROM ranqueado
 WHERE posicao <= :top_n AND receita > 0
"""

SQL_SERIE = """
WITH base AS ({base})
SELECT dia, MAX(nome) AS nome, SUM(pecas) AS pecas, SUM(receita) AS receita
  FROM base WHERE marca = :marca AND sku = :sku
 GROUP BY dia ORDER BY dia
"""

SQL_EXTRAS = {
    "tiktok": """
        SELECT SUM(gmv_video) AS video, SUM(gmv_live) AS live, SUM(gmv_product_card) AS card
          FROM gold.fct_tiktok_product_daily
         WHERE brand = :marca AND product_id = :sku AND date BETWEEN :ini AND :fim""",
    # O relatorio de ads do ML chega no dia seguinte: o dia de hoje vem com
    # venda e ad_spend 0 em TODOS os anuncios. `ads_ate` = ultimo dia com algum
    # ad na tabela, para nunca mostrar "R$ 0" onde o dado so' nao chegou.
    "ml": """
        SELECT SUM(ad_spend) AS investimento, SUM(ad_revenue) AS receita_ads,
               (SELECT MAX(ref_date)::date FROM gold.vw_ml_produto_daily
                 WHERE ad_spend > 0 AND ref_date BETWEEN :fim_menos_10 AND :fim) AS ads_ate
          FROM gold.vw_ml_produto_daily
         WHERE brand = :marca AND COALESCE(NULLIF(seller_sku, ''), item_id) = :sku
           AND ref_date BETWEEN :ini AND :fim""",
}

# Mesmos extras somados por MARCA, para a faixa "por canal" do card.
SQL_RESUMO = {
    "tiktok": """
        SELECT brand AS marca, SUM(gmv_video) AS video, SUM(gmv_live) AS live, SUM(gmv_product_card) AS card
          FROM gold.fct_tiktok_product_daily
         WHERE brand = ANY(:marcas) AND date BETWEEN :ini AND :fim
         GROUP BY brand""",
    "ml": """
        SELECT brand AS marca, SUM(ad_spend) AS investimento, SUM(ad_revenue) AS receita_ads,
               (SELECT MAX(ref_date)::date FROM gold.vw_ml_produto_daily
                 WHERE ad_spend > 0 AND ref_date BETWEEN :fim_menos_10 AND :fim) AS ads_ate
          FROM gold.vw_ml_produto_daily
         WHERE brand = ANY(:marcas) AND ref_date BETWEEN :ini AND :fim
         GROUP BY brand""",
}

_KIT = re.compile(r"\b(kit|combo)\b", re.IGNORECASE)


def eh_kit(nome: Optional[str]) -> bool:
    return bool(_KIT.search(nome or ""))


def canais_do_filtro(mkt_ids: list[int]) -> list[str]:
    return [_ID_DO_CANAL[i] for i in sorted(mkt_ids) if i in _ID_DO_CANAL]


def _num(v) -> float:
    return float(v) if v is not None else 0.0


def _opt(v) -> Optional[float]:
    return float(v) if v is not None else None


def _data(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _extras(canal: str, e: dict, inicio: date, fim: date) -> tuple[list[ExtraCanal], Optional[str]]:
    """O que so' o canal sabe, a partir de uma linha de SQL_EXTRAS/SQL_RESUMO."""
    if canal == "tiktok":
        return [ExtraCanal(rotulo=r, valor=_num(e.get(k)), formato="brl")
                for r, k in (("GMV por vídeo", "video"), ("GMV por live", "live"), ("GMV pelo card", "card"))], None
    if canal != "ml":
        return [], None
    ads_ate = _data(e["ads_ate"]) if e.get("ads_ate") else None
    if ads_ate is None or ads_ate < inicio:
        return [], "Os ads do Mercado Livre chegam no dia seguinte: ainda não há ads deste período."
    aviso = f"Ads até {ads_ate:%d/%m}: os do dia seguinte ainda não chegaram." if ads_ate < fim else None
    invest, rec_ads = _num(e.get("investimento")), _num(e.get("receita_ads"))
    extras = [ExtraCanal(rotulo="Investimento em ads", valor=invest, formato="brl"),
              ExtraCanal(rotulo="Receita via ads", valor=rec_ads, formato="brl")]
    if invest > 0:
        extras.append(ExtraCanal(rotulo="ROAS", valor=rec_ads / invest, formato="vezes"))
    return extras, aviso


def get_top_produtos(
    consultar: Consultar,
    inicio: date,
    fim: date,
    comparacao: Optional[tuple[date, date]],
    marcas: Optional[list[str]],
    canais: list[str],
) -> TopProdutosResponse:
    escopo = [m for m in (marcas or BRANDS_IN_SCOPE) if m in BRANDS_IN_SCOPE]
    canais = [c for c in canais if c in CANAIS]
    # Sem comparacao, a janela anterior fica vazia (ini_ant > fim_ant).
    ini_ant, fim_ant = comparacao or (fim + timedelta(days=1), inicio)
    params = {
        "marcas": escopo, "de": min(inicio, ini_ant), "ate": fim,
        "ini": inicio, "fim": fim, "ini_ant": ini_ant, "fim_ant": fim_ant, "top_n": TOP_N,
    }

    produtos: dict[str, list[ProdutoTop]] = {}
    totais: dict[str, list[Optional[float]]] = {}
    por_canal: dict[str, dict[str, float]] = {}
    for canal in canais if escopo else []:
        for l in consultar(SQL_TOP.format(base=_BASE[canal]), params):
            marca = l["marca"]
            t = totais.setdefault(marca, [0.0, None])
            # Totais da marca vem repetidos em cada linha do canal: soma uma vez por canal.
            if not any(p.canal == canal for p in produtos.get(marca, [])):
                t[0] += _num(l["receita_marca"])
                por_canal.setdefault(marca, {})[canal] = _num(l["receita_marca"])
                if l.get("receita_marca_ant") is not None:
                    t[1] = (t[1] or 0.0) + _num(l["receita_marca_ant"])
            pecas, receita = _num(l["pecas"]), _num(l["receita"])
            produtos.setdefault(marca, []).append(ProdutoTop(
                posicao=0, canal=canal, sku=str(l["sku"]), produto=(l["nome"] or str(l["sku"])).strip(),
                kit=eh_kit(l["nome"]), pecas=pecas, receita=receita,
                preco_medio=receita / pecas if pecas > 0 else None,
                pecas_ant=_opt(l.get("pecas_ant")) if comparacao else None,
                receita_ant=_opt(l.get("receita_ant")) if comparacao else None,
            ))

    resumo: dict[tuple[str, str], dict] = {}
    for canal in [c for c in canais if c in SQL_RESUMO] if produtos else []:
        for e in consultar(SQL_RESUMO[canal], {**params, "fim_menos_10": fim - timedelta(days=10)}):
            resumo[(e["marca"], canal)] = e

    blocos = []
    for marca, lista in produtos.items():
        total, total_ant = totais[marca]
        top = sorted(lista, key=lambda p: (-p.receita, p.canal, p.sku))[:TOP_N]
        for i, p in enumerate(top, start=1):
            p.posicao = i
            p.share_marca = p.receita / total if total > 0 else None
        canais_marca = []
        for canal in canais:
            if canal not in por_canal[marca]:
                continue
            extras, aviso = _extras(canal, resumo.get((marca, canal), {}), inicio, fim)
            canais_marca.append(ResumoCanal(canal=canal, receita=por_canal[marca][canal], extras=extras, aviso_extras=aviso))
        blocos.append(MarcaTop(
            marca=marca, receita_marca=total, receita_marca_ant=total_ant if comparacao else None, produtos=top,
            canais=canais_marca,
        ))
    blocos.sort(key=lambda b: -b.receita_marca)
    return TopProdutosResponse(
        inicio=inicio, fim=fim,
        comparacao_inicio=comparacao[0] if comparacao else None,
        comparacao_fim=comparacao[1] if comparacao else None,
        canais=canais, marcas=blocos,
    )


def get_detalhe(
    consultar: Consultar, canal: str, marca: str, sku: str, inicio: date, fim: date,
) -> Optional[DetalheProduto]:
    """Serie diaria de 12 meses ate `fim` (para dia/semana/mes no front) e os
    extras do canal somados em `inicio..fim`. None = anuncio sem venda."""
    de = fim - timedelta(days=364)
    linhas = consultar(SQL_SERIE.format(base=_BASE[canal]), {
        "marcas": [marca], "de": de, "ate": fim, "marca": marca, "sku": sku,
    })
    if not linhas:
        return None
    nome = next((l["nome"] for l in reversed(linhas) if l.get("nome")), sku)

    extras: list[ExtraCanal] = []
    aviso = None
    if canal in SQL_EXTRAS:
        [e] = consultar(SQL_EXTRAS[canal], {
            "marca": marca, "sku": sku, "ini": inicio, "fim": fim, "fim_menos_10": fim - timedelta(days=10),
        }) or [{}]
        extras, aviso = _extras(canal, e, inicio, fim)

    return DetalheProduto(
        canal=canal, marca=marca, sku=sku, produto=str(nome).strip(), kit=eh_kit(nome),
        serie=[PontoDiario(dia=_data(l["dia"]), pecas=_num(l["pecas"]), receita=_num(l["receita"])) for l in linhas],
        extras=extras, aviso_extras=aviso,
    )
