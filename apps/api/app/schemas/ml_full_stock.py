"""Contrato da tela de Estoque Full do Mercado Livre — ML-FULL-STOCK-API-1.

ESTOQUE FISICO no CD do ML, por INVENTARIO. Nao confundir com
`/performance/ml-fulfillment`, que classifica o PEDIDO pela modalidade de
envio e nao mede estoque nenhum.

GRAO: INVENTARIO, NUNCA ANUNCIO
--------------------------------
Um inventario pode ser compartilhado por varios anuncios (74,5% das unidades
Full vendidas em 28 dias, medido em 16/10/2026). O estoque aparece UMA vez por
inventario; anuncios viajam so' como CONTAGEM de associacao (`anuncios`,
`anuncios_ativos`, `anuncios_full`), e nenhum campo deste contrato soma
estoque por anuncio.

INDISPONIVEL NAO E' ZERO
-------------------------
  - `unavailable`                 -> flag desligada, fatos inexistentes (029
                                     nao aplicada), nada publicado, ou nenhuma
                                     fotografia na data pedida
  - quantidade `None`             -> inventario SEM_LEITURA, ou marca sem
                                     inventario Full lido: "nao sabemos"
  - quantidade `0`                -> medimos e deu zero
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

MotivoIndisponivel = Literal[
    "feature_flag_desligada",
    "fato_inexistente",
    "sem_fotografia_publicada",
    "sem_fotografia_na_data",
]


class InventarioEstoque(BaseModel):
    """Uma linha por inventario. `seller_id` e `inventory_id` NAO viajam: a API
    da Torre e' publica e ambos sao identificadores do ML. A agregacao continua
    por inventario no banco; so' o ID nao sai."""

    #: Ordinal da linha NESTA resposta (1..n, na ordem operacional). Chave
    #: estavel para a tela; sem relacao nenhuma com o ID do inventario e nao
    #: e' estavel entre respostas diferentes.
    posicao: int = Field(ge=1)
    brand: str
    read_status: str
    eh_full: bool

    #: `None` quando `read_status != 'OK'` (SEM_LEITURA). Nunca 0 por ausencia.
    disponivel: Optional[int] = None
    nao_disponivel: Optional[int] = None
    #: Movimento INTERNO do ML. Nao e' remessa do operador ao Full.
    transfer: Optional[int] = None
    total_coerente: Optional[bool] = None
    #: FALSE = detalhe por status nao fecha com `nao_disponivel` (payload do ML).
    detalhe_coerente: Optional[bool] = None

    #: ASSOCIACAO, nao estoque. Nunca multiplicar estoque por isto.
    anuncios: int
    anuncios_ativos: int
    anuncios_full: int
    compartilhado: bool

    unidades_28d: int
    media_diaria_28d: Decimal
    #: `None` = sem demanda na janela (ou sem leitura). Nao e' numero grande.
    cobertura_torre_dias: Optional[Decimal] = None
    classificacao_torre: str
    vinculo_vendas: str
    ref_date: date


class StatusNaoDisponivel(BaseModel):
    """Detalhe do nao disponivel por status cru do ML, somado POR INVENTARIO
    Full lido (cada inventario uma vez)."""

    status_raw: str
    quantidade: int
    inventarios: int


class Leitura(BaseModel):
    inventarios: int
    inventarios_lidos: int
    #: `read_status != 'OK'`: quantidades NULAS na fonte.
    inventarios_sem_leitura: int


class MarcaEstoque(BaseModel):
    """Cobertura por marca. `tem_fotografia_no_dia = False` -> todos os
    numeros desta marca sao `None`: ausencia, nunca zero."""

    brand: str
    tem_fotografia_no_dia: bool
    #: Ultima fotografia da marca ate' a data exibida (pode ser anterior).
    ultima_fotografia: Optional[date] = None
    published_at: Optional[datetime] = None

    inventarios: Optional[int] = None
    inventarios_full_lidos: Optional[int] = None
    inventarios_compartilhados: Optional[int] = None
    leitura: Optional[Leitura] = None
    #: `None` quando nenhum inventario Full foi lido (CHECK da 029).
    disponivel_full: Optional[int] = None
    nao_disponivel_full: Optional[int] = None
    transfer_full: Optional[int] = None

    unidades_28d_alocadas: Optional[int] = None
    unidades_28d_item_varios_inventarios: Optional[int] = None
    unidades_28d_sem_inventory_id: Optional[int] = None
    unidades_28d_fora_da_ponte: Optional[int] = None
    #: Fracao das vendas 28d ligadas a exatamente um inventario. Nao rateada.
    cobertura_da_alocacao: Optional[Decimal] = None

    por_classificacao: dict[str, int] = Field(default_factory=dict)
    nao_disponivel_por_status: list[StatusNaoDisponivel] = Field(
        default_factory=list)


class Indicadores(BaseModel):
    """Escopo inteiro (marcas filtradas, data exibida). Nao segue o limite da
    listagem."""

    #: Soma das marcas COM disponivel medido. `None` se nenhuma tem.
    disponivel_full: Optional[int] = None
    nao_disponivel_full: Optional[int] = None
    transfer_full: Optional[int] = None
    #: TRUE quando o total acima nao cobre o escopo inteiro (marca sem
    #: fotografia, marca sem Full lido, inventario Full SEM_LEITURA).
    total_parcial: bool
    motivos_total_parcial: list[str] = Field(default_factory=list)

    inventarios: int
    inventarios_full: int
    inventarios_full_lidos: int
    inventarios_fora_do_full: int
    inventarios_compartilhados: int
    inventarios_lidos: int
    inventarios_sem_leitura: int

    inventarios_ruptura: int
    inventarios_baixo: int
    inventarios_excesso: int
    inventarios_sem_giro: int
    inventarios_suficientes: int
    inventarios_sem_demanda_medida: int
    #: Ruptura + baixo.
    inventarios_exigem_acao: int


class Frescor(BaseModel):
    ref_date: date
    #: Ultima fotografia publicada, considerando as marcas filtradas.
    ultima_fotografia_disponivel: date
    #: Publicacao mais recente entre as marcas do dia.
    published_at: Optional[datetime] = None
    dias_desde_a_fotografia: int
    horas_desde_a_publicacao: Optional[float] = None
    #: O publicador ainda nao tem agendamento.
    publicacao_agendada: bool = False


class Cobertura(BaseModel):
    marcas_esperadas: list[str]
    marcas_com_fotografia: list[str]
    marcas_sem_fotografia: list[str]
    leitura: Leitura


class LimitesProvisorios(BaseModel):
    cobertura_baixa_dias: int
    cobertura_excesso_dias: int
    provisorio: bool = True
    observacao: str


class MLFullEstoqueResponse(BaseModel):
    status: Literal["ok"] = "ok"
    marketplace: Literal["ml"] = "ml"
    scope_label: str
    frescor: Frescor
    cobertura: Cobertura
    indicadores: Indicadores
    marcas: list[MarcaEstoque]
    inventarios: list[InventarioEstoque]
    #: Quantos inventarios casam com o filtro da listagem. `len(inventarios)`
    #: pode ser menor; a tela diz "N de M".
    total_no_filtro: int
    truncado: bool = False
    incluiu_fora_do_full: bool = False
    limites: LimitesProvisorios
    limitacoes: list[str]


class MLFullEstoqueUnavailableResponse(BaseModel):
    """Indisponivel. NUNCA confundir com estoque zero."""

    status: Literal["unavailable"] = "unavailable"
    marketplace: Literal["ml"] = "ml"
    unavailable_reason: str
    motivo_tecnico: MotivoIndisponivel
    scope_label: str
    limitacoes: list[str] = Field(default_factory=list)
