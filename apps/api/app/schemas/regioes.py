from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.performance import FiltersEcho

CoverageLevel = Literal["ok", "partial", "low", "not_applicable"]


class RegioesSummaryResponse(BaseModel):
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


class RegioesByUfResponse(BaseModel):
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


class RegioesByBrandResponse(BaseModel):
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


class RegioesTrendResponse(BaseModel):
    granularity: Literal["day", "month"]
    data: list[RegiaoTrendPoint]
    date_from: date
    date_to: date
    filters: FiltersEcho
    refreshed_at: Optional[str] = None
    channels_sem_cobertura_regional: list[str] = []
