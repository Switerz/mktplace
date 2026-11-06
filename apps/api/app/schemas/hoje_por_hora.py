"""Schemas do "Hoje" padronizado por hora (VENDAS-POR-HORA-1).

Ver `app/services/hoje_por_hora_service.py`.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Canal = Literal["tiktok", "ml", "shopee"]


class BlocoVendas(BaseModel):
    gmv: float
    pedidos: int
    #: GMV / pedidos; `None` sem pedido.
    ticket: Optional[float] = None


class CanalFonte(BaseModel):
    channel: Canal
    #: Ate' quando a fonte do canal comprovadamente tem dado.
    fonte_ate: Optional[str] = None
    #: Horas de hoje ja' fechadas no canal (0 a 24).
    horas_fechadas_hoje: int


class CanalVendas(BaseModel):
    channel: Canal
    hoje: BlocoVendas
    ontem: BlocoVendas
    semana_passada: BlocoVendas


class TendenciaDoCanal(BaseModel):
    channel: Canal
    hoje: float
    ontem_mesma_hora: float
    ontem_dia: float
    projecao: Optional[float] = None


class MarcaTendencia(BaseModel):
    brand: str
    #: GMV de hoje ate' o corte.
    hoje: float
    ontem_mesma_hora: float
    ontem_dia: float
    #: Fechamento provavel pelo ritmo de ontem; `None` sem venda ontem ate' o corte.
    projecao: Optional[float] = None
    #: A mesma conta por canal, para quem mostra so' alguns canais somar so' eles.
    por_canal: list[TendenciaDoCanal] = []


class HojePorHoraResponse(BaseModel):
    disponivel: bool
    motivo: Optional[str] = None
    dia: str
    #: H = vendas de 00:00 ate' H:00, nos canais do filtro. `None` sem dado.
    corte_hora: Optional[int] = None
    canais: list[CanalFonte]
    limitacoes: list[str]
    hoje: Optional[BlocoVendas] = None
    ontem: Optional[BlocoVendas] = None
    semana_passada: Optional[BlocoVendas] = None
    por_canal: list[CanalVendas] = []
    por_marca: list[MarcaTendencia] = []
