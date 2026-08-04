"""
Schemas do resumo executivo da Gerencial (Gate 2, Fase 1 — ver
docs/sections/gerencial_audit.md secao 11). Cobre apenas Health/Changes/
Risks/DataWarnings; Opportunities e Matriz Marca x Canal ficam para a Fase 2.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.performance import FiltersEcho

Severity = Literal["info", "warning", "critical"]
# Categoria semantica (Gate G1) — separa desempenho comercial, eficiencia/
# operacao e confianca no dado. Aditivo e Optional: payloads/clientes antigos
# que nao a declaram continuam validos.
InsightCategory = Literal["performance", "efficiency_ops", "data_confidence"]
ReferenceKind = Literal["median", "p75", "threshold", "previous_period"]


class ExecutivePeriod(BaseModel):
    date_from: date
    date_to: date
    compare_date_from: Optional[date] = None
    compare_date_to: Optional[date] = None
    refreshed_at: Optional[str] = None


class ExecutiveHealth(BaseModel):
    status: Literal["ok", "attention", "critical"]
    gmv: float
    gmv_mom_pct: Optional[float] = None
    orders: int
    avg_ticket: float
    summary: str


class ExecutiveChange(BaseModel):
    type: Literal["growth", "drop"]
    severity: Severity
    title: str
    description: str
    brand: Optional[str] = None
    marketplace: Optional[str] = None
    metric_value: Optional[float] = None
    href: str
    # Campos aditivos (Gate G1) — categorizacao + referencia/diferenca para o
    # drill-down. Todos Optional: retrocompativel com o contrato anterior.
    category: Optional[InsightCategory] = None
    reference_value: Optional[float] = None
    reference_kind: Optional[ReferenceKind] = None
    delta_abs: Optional[float] = None
    delta_pct: Optional[float] = None
    confidence_note: Optional[str] = None


class ExecutiveRisk(BaseModel):
    type: Literal["high_cancel_rate", "high_cost", "low_regional_coverage", "stale_data", "missing_data"]
    severity: Severity
    title: str
    description: str
    brand: Optional[str] = None
    marketplace: Optional[str] = None
    metric_value: Optional[float] = None
    href: str
    # Campos aditivos (Gate 2 Fase 1, correcao da regra stale_data — ver
    # docs/sections/gerencial_audit.md secao 11): so' preenchidos em riscos
    # type=="stale_data"; None em qualquer outro tipo. Aditivo e compativel
    # com o frontend atual, que nao os declara nem os exige.
    source: Optional[str] = None
    last_date: Optional[str] = None
    threshold_days: Optional[int] = None
    staleness_days: Optional[int] = None
    # Campos aditivos (Gate G1) — categorizacao + referencia/diferenca/
    # confianca para o drill-down. Todos Optional: retrocompativel.
    category: Optional[InsightCategory] = None
    reference_value: Optional[float] = None
    reference_kind: Optional[ReferenceKind] = None
    delta_abs: Optional[float] = None
    delta_pct: Optional[float] = None
    confidence_note: Optional[str] = None


class ExecutiveDataWarning(BaseModel):
    type: Literal["stale", "partial_coverage", "not_applicable"]
    severity: Severity
    message: str
    href: Optional[str] = None
    # Aditivo (Gate G1): sempre "data_confidence" — os warnings nunca sao
    # risco comercial. Optional para retrocompatibilidade.
    category: Optional[InsightCategory] = None


class ExecutiveSummaryResponse(BaseModel):
    period: ExecutivePeriod
    health: ExecutiveHealth
    changes: list[ExecutiveChange] = Field(default_factory=list)
    risks: list[ExecutiveRisk] = Field(default_factory=list)
    data_warnings: list[ExecutiveDataWarning] = Field(default_factory=list)
    filters: Optional[FiltersEcho] = None
