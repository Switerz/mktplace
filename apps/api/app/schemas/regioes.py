from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.performance import FiltersEcho

CoverageLevel = Literal["ok", "partial", "low", "not_applicable"]
CanalCoberturaStatus = Literal["atualizada", "atraso_leve", "defasada", "sem_carga", "sem_cobertura"]


class CoberturaCanal(BaseModel):
    """Cobertura regional de UM canal pedido. `dados_ate` e' a ultima data
    carregada (nunca o instante de ingestao); `null` = nada carregado ou canal
    sem fonte regional. Canal nao `atualizada` NAO esta (inteiro) nos totais."""
    channel: str
    marketplace_id: int
    status: CanalCoberturaStatus
    dados_ate: Optional[date] = None
    esperado_ate: date
    motivo: Optional[Literal["carga_atrasada", "carga_defasada", "sem_carga", "sem_fonte_regional"]] = None
    motivo_detalhe: Optional[str] = None


class _CoberturaCampos(BaseModel):
    """Campos de cobertura comuns a todas as respostas de /regioes.
    `dados_completos_ate` e' a data ate a qual o total cobre TODOS os canais
    com fonte regional pedidos — diferente de `refreshed_at`, que e' o
    instante da carga mais recente de qualquer canal."""
    cobertura_por_canal: list[CoberturaCanal] = []
    #: defasada ou sem_carga — o canal falta no total por mais de um dia.
    canais_defasados: list[str] = []
    #: qualquer status que impede chamar o total de atualizado (inclui D-2).
    canais_com_atraso: list[str] = []
    dados_completos_ate: Optional[date] = None


class RegioesSummaryResponse(_CoberturaCampos):
    gmv: float
    orders: int
    units_sold: int
    ufs_com_venda: int
    uf_known_orders: int
    uf_eligible_orders: int
    uf_fill_pct: Optional[float] = None
    shipping_cost_covered_orders: int
    shipping_cost_eligible_orders: int
    shipping_cost_coverage_pct: Optional[float] = None
    seller_shipping_cost: Optional[float] = None
    coverage_level: CoverageLevel
    coverage_warning: bool
    date_from: date
    date_to: date
    filters: FiltersEcho
    refreshed_at: Optional[str] = None
    channels_sem_cobertura_regional: list[str] = []


class RegiaoUfRow(BaseModel):
    uf: str
    gmv: float
    orders: int
    units_sold: int
    canceled_orders: int
    returned_orders: int
    seller_shipping_cost: Optional[float] = None
    uf_known_orders: int
    uf_eligible_orders: int
    shipping_cost_covered_orders: int
    shipping_cost_eligible_orders: int
    uf_fill_pct: Optional[float] = None
    shipping_cost_coverage_pct: Optional[float] = None
    coverage_level: CoverageLevel
    coverage_warning: bool


class RegioesByUfResponse(_CoberturaCampos):
    data: list[RegiaoUfRow]
    date_from: date
    date_to: date
    filters: FiltersEcho
    refreshed_at: Optional[str] = None
    channels_sem_cobertura_regional: list[str] = []


class RegiaoBrandRow(BaseModel):
    brand: str
    label: str
    marketplace_id: int
    marketplace: str
    gmv: float
    orders: int
    units_sold: int
    uf_known_orders: int
    uf_eligible_orders: int
    uf_fill_pct: Optional[float] = None
    shipping_cost_covered_orders: int
    shipping_cost_eligible_orders: int
    shipping_cost_coverage_pct: Optional[float] = None
    coverage_level: CoverageLevel
    coverage_warning: bool


class RegioesByBrandResponse(_CoberturaCampos):
    data: list[RegiaoBrandRow]
    date_from: date
    date_to: date
    filters: FiltersEcho
    refreshed_at: Optional[str] = None
    channels_sem_cobertura_regional: list[str] = []


class RegiaoTrendPoint(BaseModel):
    date: str
    label: str
    gmv: float
    orders: int
    uf_fill_pct: Optional[float] = None
    #: Canais com fonte regional cuja carga para antes do fim do ponto: o
    #: ponto NAO os inclui (por inteiro ou em parte). Ausencia, nao zero.
    canais_ausentes: list[str] = []


class RegioesTrendResponse(_CoberturaCampos):
    granularity: Literal["day", "month"]
    data: list[RegiaoTrendPoint]
    date_from: date
    date_to: date
    filters: FiltersEcho
    refreshed_at: Optional[str] = None
    channels_sem_cobertura_regional: list[str] = []
