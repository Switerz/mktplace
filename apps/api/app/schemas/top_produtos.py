from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class ProdutoTop(BaseModel):
    posicao: int
    sku: str
    produto: str
    categoria: Optional[str] = None
    pecas: float
    receita: float
    preco_medio: Optional[float] = None
    #: Parte da receita de marketplace da marca no recorte (0-1).
    share_marca: Optional[float] = None


class MarcaTop(BaseModel):
    marca: str
    receita_marca: float
    produtos: list[ProdutoTop]


class TopProdutosResponse(BaseModel):
    #: Meses da base (mensal) que cobrem o periodo pedido.
    mes_inicio: date
    mes_fim: date
    #: Ultima nota fiscal emitida dentro do recorte.
    nf_ate: Optional[date] = None
    marcas: list[MarcaTop]
