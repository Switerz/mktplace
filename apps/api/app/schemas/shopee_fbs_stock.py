"""Contrato da tela de Estoque Full (FBS) da Shopee — Gate FULL-SOURCE-3.

ESTOQUE FULL = soma de `shopee_stock` VENDAVEL, conciliada com o "Total
Vendavel" do Seller Center. `summary_info`, estoque do vendedor e reservado
viajam como CONTEXTO e nunca como estoque Full.

INDISPONIVEL NAO E' ZERO
-------------------------
Tres estados distintos, e a tela precisa distinguir os tres:
  - `unavailable`  -> flag desligada OU fato ainda nao criada/publicada
  - dados com `full_stock_saleable = 0` -> medimos e deu zero
  - lista vazia com filtro -> o filtro nao casou com nada
Devolver 0 no lugar de "nao sei" e' o erro que este contrato existe para
impedir.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


class EstoquePorCD(BaseModel):
    """Onde o estoque esta'. `location_id` e' o CD da Shopee."""

    location_id: str
    full_stock: int
    #: `None` quando a API omitiu a chave -- distinto de `False`, que e'
    #: "medido como nao vendavel".
    is_saleable: Optional[bool] = None


class ProdutoEstoque(BaseModel):
    shop_account: str
    brand: str
    item_id: str
    model_id: str
    item_sku: Optional[str] = None
    item_name: Optional[str] = None
    item_status: Optional[str] = None
    is_kit: bool

    #: 🔑 A UNICA medida de estoque Full.
    full_stock_saleable: int
    #: Inclui CD nao vendavel/sem flag. A diferenca para o de cima e' o retido.
    full_stock_total: int
    location_count: int
    por_cd: list[EstoquePorCD] = Field(default_factory=list)

    #: Demanda OPERACIONAL: so' status com pagamento comprovado. `unpaid` fora.
    units_sold_28d: int
    days_with_sales_28d: int
    avg_daily_units_28d: Decimal
    #: `None` = sem venda na janela. Cobertura infinita nao e' numero grande.
    cobertura_torre_dias: Optional[Decimal] = None
    classificacao_torre: str
    vinculo_vendas: str

    # ---- CONTEXTO: nao e' estoque Full, nao entra em cobertura ----
    reserved_stock: Optional[int] = None
    seller_stock_total: Optional[int] = None
    summary_available_stock: Optional[int] = None
    units_sold_28d_legado_com_unpaid: Optional[int] = None

    ref_date: date


class Indicadores(BaseModel):
    """Contagens da fotografia. Kits sao contados A PARTE, nunca no operacional."""

    unidades_vendaveis_operacional: int
    unidades_vendaveis_kits_contexto: int
    unidades_vendaveis_total: int

    produtos_ruptura: int
    produtos_baixo: int
    produtos_excesso: int
    produtos_sem_giro: int
    produtos_suficientes: int
    produtos_sem_demanda_medida: int
    produtos_kit_nao_conciliado: int
    produtos_total: int
    #: Ruptura + baixo: o que a operacao precisa olhar hoje.
    produtos_exigem_acao: int


class Frescor(BaseModel):
    ref_date: date
    source_captured_at: Optional[datetime] = None
    ingested_at: Optional[datetime] = None
    #: Dias entre a fotografia e hoje. > 1 significa carga parada.
    dias_desde_a_fotografia: Optional[int] = None
    load_mode: Literal["manual_snapshot"] = "manual_snapshot"
    no_automation: bool = True


class LimitesProvisorios(BaseModel):
    """🔴 Nenhum destes foi ratificado para a Shopee."""

    cobertura_baixa_dias: int
    cobertura_excesso_dias: int
    provisorio: bool = True
    observacao: str


class EstoqueFullResponse(BaseModel):
    status: Literal["ok"] = "ok"
    marketplace: Literal["shopee"] = "shopee"
    scope_label: str
    indicadores: Indicadores
    produtos: list[ProdutoEstoque]
    #: Quantos produtos casam com o filtro. `len(produtos)` pode ser MENOR --
    #: a tela precisa dizer "10 de 42", nunca deixar o corte invisivel.
    total_no_filtro: int
    truncado: bool = False
    frescor: Frescor
    limites: LimitesProvisorios
    contas_cobertas: list[str]
    marcas_nao_cobertas: list[str]
    limitacoes: list[str]


class EstoqueFullUnavailableResponse(BaseModel):
    """Indisponivel. NUNCA confundir com estoque zero."""

    status: Literal["unavailable"] = "unavailable"
    marketplace: Literal["shopee"] = "shopee"
    #: Por que nao ha' dado. Distingue flag desligada de fato ausente.
    unavailable_reason: str
    motivo_tecnico: Literal[
        "feature_flag_desligada", "fato_inexistente", "sem_fotografia_publicada"
    ]
    scope_label: str
    limitacoes: list[str] = Field(default_factory=list)
