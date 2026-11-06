from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

Canal = Literal["tiktok", "ml", "shopee"]


class ProdutoTop(BaseModel):
    posicao: int
    canal: Canal
    #: Codigo do anuncio como foi vendido (kit e' kit): product_id no TikTok,
    #: seller_sku no ML, sku na Shopee. Sem de-para.
    sku: str
    produto: str
    kit: bool
    pecas: float
    receita: float
    preco_medio: Optional[float] = None
    #: Parte da receita da marca nos canais selecionados (0-1).
    share_marca: Optional[float] = None
    pecas_ant: Optional[float] = None
    receita_ant: Optional[float] = None


class MarcaTop(BaseModel):
    marca: str
    receita_marca: float
    receita_marca_ant: Optional[float] = None
    produtos: list[ProdutoTop]


class TopProdutosResponse(BaseModel):
    inicio: date
    fim: date
    comparacao_inicio: Optional[date] = None
    comparacao_fim: Optional[date] = None
    canais: list[Canal]
    marcas: list[MarcaTop]


class PontoDiario(BaseModel):
    dia: date
    pecas: float
    receita: float


class ExtraCanal(BaseModel):
    """O que so' um canal sabe, somado no periodo pedido."""
    rotulo: str
    valor: float
    formato: Literal["brl", "num", "vezes"]


class DetalheProduto(BaseModel):
    canal: Canal
    marca: str
    sku: str
    produto: str
    kit: bool
    #: Serie diaria dos ultimos 12 meses ate o fim do periodo.
    serie: list[PontoDiario]
    extras: list[ExtraCanal]
    #: Quando os extras ainda nao cobrem o periodo (ads do ML chegam D+1).
    aviso_extras: Optional[str] = None
