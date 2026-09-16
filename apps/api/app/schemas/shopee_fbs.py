"""Contrato tipado do desempenho FBS da Shopee — Gate FULL-SH-1C.

Bloco ADITIVO: nenhum schema existente e' alterado. A resposta e' servida por
`/api/v1/performance/shopee-fbs` e le EXCLUSIVAMENTE
`marts.fact_shopee_fbs_daily` -- nunca o Data Mart, nunca `silver`, nunca a
API da Shopee.

O VALOR E' GMV BRUTO, E O NOME IMPORTA
--------------------------------------
`gross_gmv` = soma de `item_total` dos itens de pedidos com
`order_status <> 'cancelled'`.

    to_return  ESTA DENTRO do bruto, e aparece tambem em coluna propria
    unpaid     ESTA DENTRO do bruto, e aparece tambem em coluna propria
    cancelled  FICA FORA do GMV, mas conta no denominador do cancelamento

Isto NAO e' receita liquida nem realizada, e nenhum campo deste contrato pode
ser chamado apenas de "receita". O rotulo viaja no proprio payload
(`meta.gmv_definition`) para que a tela nao precise lembrar.

DUAS CLASSES, E NAO EXISTE `unknown`
-------------------------------------
    fbs     <- fulfillment_flag = 'fulfilled_by_shopee'
    seller  <- fulfillment_flag = 'fulfilled_by_local_seller'

Ao contrario do Full ML, aqui nao ha terceira classe: em 262.411 pedidos a
flag tem exatamente dois valores e ZERO nulos. Classe fora do dominio e'
contrato quebrado e falha fechada, nunca vira categoria silenciosa.

AUSENCIA NAO E' ZERO -- E ZERO NAO E' AUSENCIA
-----------------------------------------------
    denominador > 0 e numerador FBS zero  -> share 0.0    (mediu, deu zero)
    denominador = 0                        -> share None   (nada a dividir)
    conta/marca fora da cobertura          -> nem aparece em `by_account`,
                                              e sim em `missing_accounts` /
                                              `brands_not_covered`

Apice em agosto/2026 e' o caso real do primeiro: vendeu R$ 275.234,00 e nao
teve um unico pedido FBS. Isso e' 0%, nao "sem dado". Kokeshi e' o caso do
terceiro: nao existe na esteira API e NAO pode ser suprida pelo XLSX aqui.

COBERTURA PARCIAL, DECLARADA NO PROPRIO PAYLOAD
------------------------------------------------
`scope_label` e' "Shopee -- cobertura API", nunca "Shopee total". A esteira
cobre quatro contas; Kokeshi, a maior marca Shopee por volume, esta fora.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

#: Dominio FECHADO. Sem `unknown`: a fonte nao produz ausencia de classe.
FbsClass = Literal["fbs", "seller"]

FreshnessStatus = Literal[
    "fresh",          # publicacao e fonte dentro da janela aceitavel
    "stale",          # existe, mas passou do limiar
    "never_loaded",   # tabela sem nenhuma linha na janela consultada
    "unknown",        # nao foi possivel medir
]

#: Politica de data desta superficie. `closed_day` = a serie so' publica dias
#: FECHADOS; D0 nunca e' materializado nem projetado.
DatePolicy = Literal["closed_day"]


class HandlingStat(BaseModel):
    """Tempo de PAGAMENTO ate' COLETA. Nao e' entrega.

    A API da Shopee nao expoe data real de entrega -- `delivered_date` so'
    existe no export XLSX, que nao entra neste contrato. Nao ha campo de
    entrega aqui, e a ausencia e' deliberada: um campo vazio convidaria a
    preenche-lo com coleta e chama-lo de entrega.

    A media e' derivada DEPOIS da agregacao (`SUM(sum) / SUM(count)`), nunca
    pela media das medias diarias.
    """

    seconds_avg: Optional[float] = Field(
        None, description="Media em segundos. None quando sample_count = 0.")
    hours_avg: Optional[float] = Field(
        None, description="Mesma media em horas.")
    days_avg: Optional[float] = Field(
        None, description="Mesma media em dias.")
    seconds_sum: int = Field(
        ..., description="Soma bruta, exposta para permitir reagregacao.")
    sample_count: int = Field(
        ..., description="PEDIDOS com pay_time E pickup_done_time. Mede a "
                         "censura: pedido sem coleta fica FORA da amostra, "
                         "nunca entra como tempo zero.")
    coverage_ratio: Optional[float] = Field(
        None, description="sample_count / eligible_orders. Quanto da coorte a "
                          "media cobre. None quando nao ha pedido elegivel.")


class Totals(BaseModel):
    """Medidas ADITIVAS do periodo. Razoes ficam em `shares` e `rates`."""

    gross_gmv: Decimal = Field(
        ..., description="GMV BRUTO de pedidos nao cancelados. Inclui "
                         "to_return e unpaid. NAO e' receita liquida.")
    gross_units: int
    created_orders: int = Field(
        ..., description="TODOS os pedidos criados. Denominador do cancelamento.")
    eligible_orders: int = Field(
        ..., description="order_status <> 'cancelled'. Populacao do GMV.")
    cancelled_orders: int
    to_return_orders: int
    to_return_gmv: Decimal = Field(
        ..., description="Parcela de to_return DENTRO do bruto. Nao e' deducao.")
    unpaid_orders: int
    unpaid_gmv: Decimal = Field(
        ..., description="Parcela de unpaid DENTRO do bruto. Nao e' deducao.")
    handling: HandlingStat


class Rates(BaseModel):
    cancellation_rate: Optional[float] = Field(
        None, description="cancelled_orders / created_orders -- denominador "
                          "PROPRIO, calculado APOS a agregacao. Nunca a media "
                          "das taxas diarias. None quando nao houve pedido.")


class Shares(BaseModel):
    """Participacao do FBS. Denominador = fbs + seller (nao ha terceira classe).

    Calculados SOMENTE apos somar as medidas aditivas do periodo inteiro.
    Media de shares diarios daria peso igual a um dia de R$ 300 e a um de
    R$ 300.000.
    """

    share_fbs_gmv: Optional[float] = None
    share_fbs_orders: Optional[float] = None
    share_fbs_units: Optional[float] = None


class ClassBreakdown(BaseModel):
    fbs_class: FbsClass
    gross_gmv: Decimal
    gross_units: int
    created_orders: int
    eligible_orders: int
    cancelled_orders: int
    to_return_gmv: Decimal
    unpaid_gmv: Decimal
    handling: HandlingStat


class BrandBreakdown(BaseModel):
    brand: str
    gross_gmv: Decimal
    gross_units: int
    eligible_orders: int
    cancelled_orders: int
    created_orders: int
    #: None APENAS quando o denominador da marca e' zero. Marca coberta que
    #: nao vendeu FBS recebe 0.0.
    share_fbs_gmv: Optional[float] = None
    share_fbs_orders: Optional[float] = None


class AccountBreakdown(BaseModel):
    shop_account: str
    brand: str
    gross_gmv: Decimal
    eligible_orders: int
    created_orders: int
    share_fbs_gmv: Optional[float] = None
    #: Watermark da FONTE para esta conta. Uma conta pode estar atrasada sem
    #: que o total pareca atrasado.
    source_watermark_at: Optional[datetime] = None


class DailyPoint(BaseModel):
    """Um dia, uma classe. Medidas ADITIVAS apenas.

    A tendencia NAO carrega share diario pronto: quem precisar dele divide a
    medida da classe pela soma do dia. Publicar share por dia aqui convidaria
    a media-las, que e' exatamente o erro que este contrato evita.
    """

    ref_date: date
    fbs_class: FbsClass
    gross_gmv: Decimal
    gross_units: int
    eligible_orders: int
    created_orders: int
    cancelled_orders: int


class Freshness(BaseModel):
    """Tres relogios distintos, e nenhum mascara o outro.

    `refreshed_at` (nossa publicacao) recente NAO torna o dado fresco se
    `source_max_date` for antigo: uma republicacao do mesmo periodo atualiza o
    primeiro sem mover o segundo. Por isso `freshness` olha os DOIS.
    """

    freshness_status: FreshnessStatus
    #: Relogio da FONTE: maior `ingested_at` da silver que entrou nesta fato.
    source_watermark_at: Optional[datetime] = None
    #: Relogio da nossa PUBLICACAO no Neon.
    refreshed_at: Optional[datetime] = None
    #: Ultimo dia FECHADO efetivamente materializado.
    source_max_date: Optional[date] = None
    source_min_date: Optional[date] = None
    source_age_hours: Optional[float] = Field(
        None, description="Idade do watermark da FONTE.")
    snapshot_age_hours: Optional[float] = Field(
        None, description="Idade da nossa publicacao.")
    #: Dias entre `source_max_date` e o ultimo dia fechado. > 0 = a serie esta
    #: atrasada em relacao ao que ja' poderia estar publicado.
    closed_days_behind: Optional[int] = None
    load_mode: Literal["manual_snapshot"] = "manual_snapshot"
    no_automation: bool = True


class Meta(BaseModel):
    marketplace: Literal["shopee"] = "shopee"
    #: NUNCA "Shopee total": a esteira cobre quatro contas e Kokeshi esta fora.
    scope_label: str
    date_policy: DatePolicy = "closed_day"
    #: Politica de data explicita, em texto, para a tela nao ter de inferir.
    d0_materialized: bool = False
    last_closed_date: date = Field(
        ..., description="D-1 em America/Sao_Paulo. Teto do que e' consultavel.")
    requested_date_from: date
    requested_date_to: date
    #: Intervalo EFETIVAMENTE servido (menor dia e maior dia com linha). None
    #: quando a janela nao tem nenhuma linha publicada.
    effective_date_from: Optional[date] = None
    effective_date_to: Optional[date] = None

    expected_accounts: list[str]
    observed_accounts: list[str]
    missing_accounts: list[str] = Field(
        ..., description="Conta esperada SEM linha na janela. Fora da "
                         "cobertura medida -- nao e' 0%.")
    unexpected_accounts: list[str] = Field(
        ..., description="Conta observada fora da allowlist. Deve ser sempre "
                         "vazia; se nao for, o contrato da fonte mudou.")
    covered_brands: list[str]
    brands_not_covered: list[str] = Field(
        ..., description="Marcas Shopee fora da esteira API. Contem 'kokeshi'.")

    gmv_definition: str
    freshness: Freshness
    warnings: list[str]
    limitations: list[str]


class ShopeeFbsResponse(BaseModel):
    meta: Meta
    totals: Totals
    rates: Rates
    shares: Shares
    by_class: list[ClassBreakdown]
    by_brand: list[BrandBreakdown]
    by_account: list[AccountBreakdown]
    daily: list[DailyPoint]


class ShopeeFbsUnavailableResponse(BaseModel):
    """Resposta ESTRUTURADA quando a superficie esta desligada por flag.

    200 com estado explicito, nao 404 nem 500: a rota existe, o contrato e'
    conhecido, e o consumidor precisa distinguir "desligado" de "quebrado".
    NENHUMA consulta ao banco acontece neste caminho.
    """

    marketplace: Literal["shopee"] = "shopee"
    status: Literal["unavailable"] = "unavailable"
    scope_label: str
    unavailable_reason: str
    date_policy: DatePolicy = "closed_day"
