"""CX-MKT-1 — schema da aba CX (`/api/v1/cx/atendimento` e `/api/v1/cx/shopee`).

Contrato: `docs/cx_atendimento_contrato.md`.

Reaproveita `Indicador` e `Cobertura` da Performance das contas: as mesmas
regras valem aqui (so' `dado` carrega valor; numero Torre nunca leva limiar
oficial; ausencia nunca vira zero).

O QUE ESTA FATIA MOSTRA
-----------------------
So' o que ja' existe no Data Mart, sem ingestao nova:
  * ML: cancelamentos, taxa e motivo (`cancel_detail`). Reclamacao NAO: a
    taxa oficial vem da reputacao do ML (aba Performance);
  * Shopee: cancelamentos por motivo e por quem cancelou, e pedidos em
    devolucao agora (`to_return`).
O resto da aba (fila de acao, motivo das reclamacoes, devolucoes do TikTok e
da Shopee) chega como `pendencias`: blocos com motivo explicito, nunca zero.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, model_validator

from app.schemas.account_performance import CanalPerformance, Cobertura, Indicador
from app.schemas.performance_oficial import ContaOficial, ContaSemFoto, EstadoCanal

#: Quem originou o cancelamento/caso, em vocabulario comum aos canais.
Natureza = Literal["comprador", "reclamacao", "logistica", "vendedor", "plataforma"]
EstadoBloco = Literal["dado", "vazio_legitimo", "sem_fonte", "erro"]

#: Janelas aceitas (dias fechados ate' D-1).
JANELAS_DIAS: tuple[int, ...] = (7, 30, 90)


class Janela(BaseModel):
    inicio: date
    fim: date
    dias: int


class Motivo(BaseModel):
    codigo: str
    rotulo: str
    natureza: Natureza
    quantidade: int
    #: Participacao no total do bloco, 0-100.
    pct: float
    #: False = codigo novo, fora do mapa de rotulos: aparece com o texto da
    #: plataforma para o mapa ser corrigido, nunca descartado.
    mapeado: bool = True


class BlocoMotivos(BaseModel):
    titulo: str
    estado: EstadoBloco
    total: Optional[int] = None
    itens: list[Motivo] = []
    ajuda: str
    #: Volume deixado de fora de proposito (ex.: Shopee "pedido nao pago").
    excluidos: Optional[int] = None
    rotulo_excluidos: Optional[str] = None
    motivo_indisponivel: Optional[str] = None

    @model_validator(mode="after")
    def _ausencia_nao_e_zero(self) -> "BlocoMotivos":
        if self.estado != "dado" and (self.total is not None or self.itens):
            raise ValueError("so' o estado 'dado' carrega total e itens")
        if self.estado == "dado" and self.total is None:
            raise ValueError("estado 'dado' exige total")
        return self


class LinhaMarca(BaseModel):
    """Uma linha marca x canal. Coluna ausente na fonte = None, nunca 0."""
    marca: str
    pedidos: Optional[int] = None
    taxa_cancelamento_pct: Optional[float] = None
    cancelamentos: Optional[int] = None
    cancelamentos_comprador: Optional[int] = None
    em_devolucao: Optional[int] = None


class Pendencia(BaseModel):
    codigo: str
    titulo: str
    motivo: str


class CanalAtendimento(BaseModel):
    canal: CanalPerformance
    cobertura: Cobertura
    indicadores: list[Indicador]
    motivos: Optional[BlocoMotivos] = None
    marcas: list[LinhaMarca] = []
    pendencias: list[Pendencia] = []
    atualizado_em: Optional[datetime] = None


class AtendimentoResponse(BaseModel):
    gerado_em: datetime
    janela: Janela
    canais: list[CanalAtendimento]


# ---------------------------------------------------------------------------
# Aba CX (menu principal), 04/11/2026: primeira versao so' Shopee.
# ---------------------------------------------------------------------------
class NaoLidas(BaseModel):
    """Conversas nao lidas no chat, ultima foto da loja (`shopee_cx_coleta`)."""
    marca: str
    #: None = a Shopee nao devolveu o numero (nunca zero).
    valor: Optional[int] = None
    capturado_em: Optional[datetime] = None


class LinhaDevolucao(BaseModel):
    marca: str
    devolucoes: Optional[int] = None
    reembolsado: Optional[float] = None


class DevolucoesShopee(BaseModel):
    """Devolucoes/reembolsos criados na janela (`raw.shopee_returns`)."""
    estado: EstadoBloco
    fonte: str
    motivo_indisponivel: Optional[str] = None
    total: Optional[int] = None
    #: Soma de `refund_amount` (R$) das devolucoes da janela.
    reembolsado: Optional[float] = None
    motivos: Optional[BlocoMotivos] = None
    marcas: list[LinhaDevolucao] = []

    @model_validator(mode="after")
    def _ausencia_nao_e_zero(self) -> "DevolucoesShopee":
        if self.estado != "dado" and (self.total is not None or self.marcas):
            raise ValueError("so' o estado 'dado' carrega total e marcas")
        return self


class ChatShopee(BaseModel):
    """Metricas de atendimento da Saude da Conta (fotografia diaria oficial).

    Cada conta traz so' as metricas do grupo "Atendimento" (taxa de resposta
    do chat, nota da loja e, quando a Shopee publica, tempo de resposta e
    chats sem resposta), com a meta da propria Shopee.
    """
    estado: EstadoCanal
    motivo: Optional[str] = None
    fonte: str
    contas: list[ContaOficial] = []
    sem_foto: list[ContaSemFoto] = []
    #: Conversas nao lidas (coleta propria, a cada 2 h).
    nao_lidas_estado: EstadoBloco = "sem_fonte"
    nao_lidas: list[NaoLidas] = []


class CxShopeeResponse(BaseModel):
    gerado_em: datetime
    janela: Janela
    chat: ChatShopee
    atendimento: CanalAtendimento
    devolucoes: DevolucoesShopee
