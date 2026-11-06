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
