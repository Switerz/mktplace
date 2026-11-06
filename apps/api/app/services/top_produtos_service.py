"""Top produtos de marketplace por marca (aba Gerencial).

Fonte: `gold.agg_vendas_consolidada_produto` (canal = 'marketplace'), a base
consolidada por nota fiscal: ML, Shopee e TikTok juntos, um SKU canonico por
produto. Mesma ideia do "Mix Marketplace" da Natalia (ranking por receita e
pecas), sem refazer o motor dela: o de-para e a consolidacao ja vem prontos.

A base e' MENSAL: o periodo pedido vira os meses inteiros que ele toca. A
receita e' a faturada (NF), nao o GMV de pedidos das outras telas.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Callable, Optional

from app.schemas.top_produtos import MarcaTop, ProdutoTop, TopProdutosResponse
from app.services.gold_service import BRANDS_IN_SCOPE

Consultar = Callable[[str, dict], list[dict]]

TOP_N = 5

SQL_TOP = """
WITH base AS (
    SELECT marca,
           COALESCE(sku_canonico, codigo_sku, produto) AS sku,
           MAX(produto) AS produto,
           MAX(categoria_macro) AS categoria,
           SUM(quantidade) AS pecas,
           SUM(receita_produto) AS receita,
           MAX(ultima_emissao) AS ultima
      FROM gold.agg_vendas_consolidada_produto
     WHERE canal = 'marketplace'
       AND data_mes BETWEEN :mes_inicio AND :mes_fim
       AND marca = ANY(:marcas)
     GROUP BY 1, 2
), ranqueado AS (
    SELECT base.*,
           SUM(receita) OVER (PARTITION BY marca) AS receita_marca,
           ROW_NUMBER() OVER (PARTITION BY marca ORDER BY receita DESC, sku) AS posicao,
           MAX(ultima) OVER () AS nf_ate
      FROM base
     WHERE receita > 0
)
SELECT marca, sku, produto, categoria, pecas, receita, receita_marca, posicao, nf_ate
  FROM ranqueado
 WHERE posicao <= :top_n
 ORDER BY receita_marca DESC, marca, posicao
"""

# "7898652877971 - (N)Ativador ... 500gr - I4" -> "Ativador ... 500gr"
_EAN_INICIO = re.compile(r"^\s*\d{8,14}\s*-\s*")
_MARCADOR_N = re.compile(r"^\(N\)\s*", re.IGNORECASE)
_POSICAO_FIM = re.compile(r"\s*-\s*[A-Z]\d{1,2}(\(\w\))?\s*$")
_PONTOS_FIM = re.compile(r"[\s.]+$")


def limpar_nome(nome: Optional[str]) -> str:
    s = _EAN_INICIO.sub("", nome or "")
    s = _MARCADOR_N.sub("", s)
    s = _POSICAO_FIM.sub("", s)
    s = _PONTOS_FIM.sub("", s)
    return re.sub(r"\s{2,}", " ", s).strip() or (nome or "").strip()


def _num(v) -> float:
    return float(v) if v is not None else 0.0


def _data(v) -> Optional[date]:
    if v is None:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def get_top_produtos(
    consultar: Consultar,
    inicio: date,
    fim: date,
    marcas: Optional[list[str]],
) -> TopProdutosResponse:
    escopo = [m for m in (marcas or BRANDS_IN_SCOPE) if m in BRANDS_IN_SCOPE]
    mes_inicio, mes_fim = inicio.replace(day=1), fim.replace(day=1)
    linhas = consultar(SQL_TOP, {
        "mes_inicio": mes_inicio, "mes_fim": mes_fim, "marcas": escopo, "top_n": TOP_N,
    }) if escopo else []

    por_marca: dict[str, MarcaTop] = {}
    nf_ate = None
    for l in linhas:
        nf_ate = nf_ate or _data(l.get("nf_ate"))
        total = _num(l["receita_marca"])
        bloco = por_marca.setdefault(l["marca"], MarcaTop(marca=l["marca"], receita_marca=total, produtos=[]))
        pecas, receita = _num(l["pecas"]), _num(l["receita"])
        bloco.produtos.append(ProdutoTop(
            posicao=int(l["posicao"]),
            sku=str(l["sku"]),
            produto=limpar_nome(l["produto"]),
            categoria=l.get("categoria"),
            pecas=pecas,
            receita=receita,
            preco_medio=receita / pecas if pecas > 0 else None,
            share_marca=receita / total if total > 0 else None,
        ))
    return TopProdutosResponse(
        mes_inicio=mes_inicio, mes_fim=mes_fim, nf_ate=nf_ate, marcas=list(por_marca.values()),
    )
