"""Contrato tipado da superficie "Full Mercado Livre" — Gate FULL-1A.

Bloco ADITIVO: nenhum schema existente e' alterado. A resposta e' servida por
`/api/v1/performance/ml-fulfillment` e le EXCLUSIVAMENTE as fatos ja'
materializadas em `marts` -- nunca o Data Mart, nunca `raw`, nunca a API do ML.

NENHUM CAMPO AQUI FALA DE ESTOQUE
----------------------------------
Nao existe `stock`, `available`, `coverage` nem `rupture` neste contrato, e a
ausencia e' decisao do gate FULL-0R: `available_quantity` foi medido e NAO se
comporta como estoque fisico. "Full" aqui significa modalidade logistica do
envio, jamais estoque no Full.

AUSENCIA NAO E' ZERO
--------------------
Todo share e' `float | None`. `None` significa denominador zero -- "nao ha o que
dividir" -- e nunca e' serializado como 0.0, que significaria "nenhum Full".
Mesma regra para as medias de tempo: `None` quando a amostra e' zero.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

FulfillmentClass = Literal["full", "non_full", "unknown"]

FreshnessStatus = Literal[
    "fresh",          # carga dentro da janela aceitavel
    "stale",          # fato existe, mas a ultima carga passou do limiar
    "never_loaded",   # tabela sem nenhuma linha
    "unknown",        # nao foi possivel medir o frescor
]


class TimeStat(BaseModel):
    """Media derivada de soma/amostra, com o tamanho da amostra exposto.

    O par existe para que o consumidor possa reagregar. A media nunca e'
    armazenada pronta na fato: guardada assim, somar dois dias exigiria
    reponderar, e quem consome normalmente esquece.
    """

    seconds_avg: Optional[float] = Field(
        None, description="Media em segundos. None quando sample_count = 0.")
    hours_avg: Optional[float] = Field(
        None, description="Mesma media em horas, por conveniencia de leitura.")
    days_avg: Optional[float] = Field(
        None, description="Mesma media em dias.")
    sample_count: int = Field(
        ..., description="PEDIDOS da coorte que ja tem o evento. Mede a censura: "
                         "pedido sem despacho/entrega fica fora da amostra em "
                         "vez de entrar como tempo zero.")


class ModalBreakdown(BaseModel):
    """Uma classe de fulfillment dentro do periodo."""

    fulfillment_class: FulfillmentClass
    paid_gmv: Decimal
    paid_orders: int
    paid_units: int
    eligible_orders: int
    cancelled_orders: int
    other_orders: int = Field(
        0, description="Status que nao e paid nem cancelled -- hoje apenas "
                       "partially_refunded, fora do GMV por contrato canonico. "
                       "paid + cancelled + other = eligible, sempre.")
    cancellation_rate: Optional[float] = Field(
        None, description="cancelled_orders / eligible_orders. None se o "
                          "denominador for zero.")
    handling: TimeStat
    delivery: TimeStat


class LogisticTypeBreakdown(BaseModel):
    """Composicao pelo rotulo BRUTO da fonte, inclusive extintos.

    `xd_drop_off`, `self_service` e `drop_off` sumiram da fonte em 2026 mas
    dominavam a serie em 2025: em novembro/2025 `xd_drop_off` tinha 14.696
    envios contra 794 de `cross_docking`. Descartar o rotulo apagaria 62% do mes.
    """

    logistic_type_original: str
    fulfillment_class: FulfillmentClass
    paid_gmv: Decimal
    paid_orders: int
    paid_units: int


class DailyPoint(BaseModel):
    ref_date: date
    fulfillment_class: FulfillmentClass
    paid_gmv: Decimal
    paid_orders: int
    paid_units: int
    eligible_orders: int
    cancelled_orders: int


class ListingClassification(BaseModel):
    """Contagem de listings por comportamento na janela.

    `mixed` e' a classe que importa comercialmente: o listing vendeu pelos dois
    modais no mesmo periodo, entao "Full" nao e' atributo dele. Em agosto/2026
    foram 223 de 589 listings.
    """

    full_only: int
    mixed: int
    non_full_only: int
    total: int


class MigrationOpportunity(BaseModel):
    """GMV que hoje NAO passa por Full, por listing.

    Publicavel no grao do listing porque a alocacao e' determinística:
    `api.ml_order_line_items` fecha com `total_amount` em 59.123/59.123 pedidos
    pagos de agosto/2026, diferenca R$ 0,00. Nao ha rateio.
    """

    item_id: str
    brand: str
    non_full_gmv: Decimal
    non_full_units: int
    full_gmv: Decimal
    listing_class: Literal["mixed", "non_full_only"]


class Quality(BaseModel):
    """Qualidade e cobertura do join, publicadas COM o dado.

    Ficam no mesmo payload de proposito: cobertura entregue em endpoint separado
    e' cobertura que ninguem consulta.
    """

    unmatched_orders: int = Field(
        ..., description="Pedidos sem envio correspondente. Entram em "
                         "fulfillment_class='unknown', nunca em non_full.")
    missing_shipping_items: int = Field(
        ..., description="Envios com shipping_items vazio. Nao afeta "
                         "paid_units, que vem de line_items.")
    unknown_logistic_type_orders: int = Field(
        ..., description="Pedidos elegiveis na classe unknown.")
    is_complete: bool = Field(
        ..., description="False quando ha dia sem linha na janela pedida, ou "
                         "quando a carga esta stale.")
    limitations: list[str] = Field(
        default_factory=list,
        description="Limitacoes conhecidas, em texto fixo e sanitizado.")


class Freshness(BaseModel):
    freshness_status: FreshnessStatus
    refreshed_at: Optional[datetime] = Field(
        None, description="MAX(ingested_at): quando a Torre publicou.")
    source_max_date: Optional[date] = Field(
        None, description="MAX(ref_date) presente na fato.")
    age_hours: Optional[float] = None
    load_mode: Literal["manual_snapshot", "scheduled"] = Field(
        "manual_snapshot",
        description="manual_snapshot ate' haver piloto e automacao. O gate "
                    "FULL-1A nao integrou Scheduler nem Airflow.")


class MLFulfillmentResponse(BaseModel):
    """Superficie Full do Mercado Livre. Somente modalidade logistica."""

    date_from: date
    date_to: date
    brands: list[str]

    share_full_gmv: Optional[float] = Field(
        None, description="KPI PRINCIPAL. paid_gmv(full) / paid_gmv(full + "
                          "non_full). `unknown` fica FORA do denominador: nao e "
                          "Full nem nao-Full. None se nao houver GMV.")
    share_full_orders: Optional[float] = None
    share_full_units: Optional[float] = None

    paid_gmv_total: Decimal
    paid_orders_total: int
    paid_units_total: int

    by_class: list[ModalBreakdown]
    by_logistic_type: list[LogisticTypeBreakdown]
    daily: list[DailyPoint]

    listings: ListingClassification
    migration_opportunities: list[MigrationOpportunity]

    quality: Quality
    freshness: Freshness
