"""Gate PMA-1A — schema do endpoint de monitoramento de precos proprios.

TODO CAMPO MONETARIO E' `Optional[float]`, DE PROPOSITO
------------------------------------------------------
`None` significa "nao disponivel" e NUNCA e' substituido por `0.0`. Zero
afirmaria uma medicao — "frete medido igual a zero", "diferenca medida igual a
zero" — que nao existe. Os quatro campos que faltariam para um preco de checkout
(`shipping_amount`, `seller_coupon_amount`, `platform_subsidy_amount`,
`checkout_price`) sao SEMPRE nulos neste MVP, porque a fonte do Mercado Livre nao
os fornece, e estao no schema justamente para que a ausencia seja explicita em
vez de invisivel.

SEM SEVERIDADE
--------------
Nao existe campo de severidade, gravidade ou prioridade. Sem limiar comercial
aprovado, os unicos fatos sao `difference_amount` e `difference_pct`.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

#: PARTICAO COMERCIAL — cinco valores que somam `monitored_count`.
#: Gate PMA-H1: `stale_observation` SAIU daqui. Era um status comercial e
#: substituia a classificacao, de modo que um sync atrasado zerava os cinco
#: cartoes comerciais e a tela aparentava "nenhum desvio". Frescor virou
#: dimensao propria, em `FreshnessStatus`.
ComparisonStatus = Literal[
    "below_reference",
    "at_or_above_reference",
    "no_reference",
    "non_comparable_reference_ambiguous",
    "inactive_listing",
]

#: QUALIDADE TRANSVERSAL — sobreposta a `ComparisonStatus`, nunca no lugar dele.
#:   fresh       observacao de D-1;
#:   stale       o modo `latest` caiu num dia anterior porque o sync atrasou;
#:   historical  o consumidor ESCOLHEU um dia anterior (nao e' atraso);
#:   unavailable nao ha observacao materializada para responder.
FreshnessStatus = Literal["fresh", "stale", "historical", "unavailable"]

#: Modos publicos do endpoint.
QueryMode = Literal["latest", "selected_date"]

#: Base da referencia usada na comparacao. Um unico valor, e' contrato: a
#: comparacao SEMPRE usa o snapshot PDV mais recente disponivel, inclusive numa
#: data historica. Nao existe "snapshot vigente na data" porque a origem nao
#: declara vigencia.
ReferenceBasis = Literal["latest_available_snapshot"]

MatchMethod = Literal["brand_gtin_exact", "brand_sku_exact_unique"]

MatchQuality = Literal[
    "primary_gtin_exact",
    "secondary_sku_unique_in_brand",
    "ambiguous_multiple_candidates",
    "unmatched",
]

ReferenceType = Literal["suggested_retail_pdv"]
PolicyStatus = Literal["not_applicable_to_own_store_monitoring"]

#: SO `missing`.  (PMA-1A-R, F5)
#: A tabela de referencia nao tem `valid_from` nem `valid_to`, portanto nao
#: consegue PROVAR nenhum outro estado de vigencia. Expor `declared`/`expired`
#: no contrato HTTP sugeriria uma capacidade que o banco nao tem. Quando uma
#: referencia vigente existir, ela vem com migration e contrato proprios.
ValidityStatus = Literal["missing"]
CoverageStatus = Literal["advertised_only"]


class MonitoramentoPrecoMeta(BaseModel):
    timezone: str
    currency: str
    marketplace: Literal["ml"]
    #: `latest` (maior observacao <= D-1) ou `selected_date` (dia pedido).
    mode: QueryMode
    #: Ultima publicacao do sync para `observed_ref_date`.
    refreshed_at: Optional[str] = None
    #: O que o cliente PEDIU em `observed_date`. Nulo no modo `latest`.
    requested_observed_date: Optional[str] = None
    #: A data efetivamente USADA. Nula quando a data pedida nao tem observacao
    #: materializada — nunca substituida pela mais proxima, porque aproximar
    #: responderia outra pergunta.
    observed_ref_date: Optional[str] = None
    #: D-1 do dia operacional: o teto do que e' consultavel.
    eligible_ref_date: Optional[str] = None
    #: Datas observadas MATERIALIZADAS, <= D-1, mais recentes primeiro. Sem
    #: calendario sintetico: dia que o sync nao publicou nao aparece.
    available_observed_dates: list[str] = []
    #: Teto defensivo aplicado a lista acima.
    available_observed_dates_limit: int
    #: Dias entre `observed_ref_date` e `eligible_ref_date`. Zero = em dia.
    #: Nulo quando nao ha observacao. Nunca negativo.
    lag_days: Optional[int] = None
    freshness_status: FreshnessStatus
    reference_snapshot_id: Optional[str] = None
    reference_captured_at: Optional[str] = None
    reference_basis: ReferenceBasis
    #: A frase que declara as DUAS datas e a ausencia de vigencia historica.
    #: Nula quando falta uma das duas — sem data nao ha afirmacao a fazer.
    comparison_basis_text: Optional[str] = None
    reference_type: ReferenceType
    policy_status: PolicyStatus
    validity_status: ValidityStatus
    coverage_status: CoverageStatus
    monitored_brands: list[str]
    comparable_brands: list[str]
    no_reference_brands: list[str]
    #: marca -> rotulo de escopo (`out_of_scope_no_ml_catalog`).
    out_of_scope_brands: dict[str, str]
    order_by: str
    warnings: list[str]


class MonitoramentoPrecoKpis(BaseModel):
    """Duas dimensoes independentes  (Gate PMA-H1).

    COMERCIAL — particao: `below + at_or_above + no_reference + ambiguous +
    inactive == monitored_count`, sempre, verificado em teste.
    `comparable_count == below + at_or_above`, por definicao.

    QUALIDADE — sobreposta: `fresh + stale + historical == monitored_count`
    tambem, mas cruzando a particao comercial. Uma linha `below_reference` num
    dia atrasado conta nas duas dimensoes. Antes, `stale_count` competia com os
    contadores comerciais e os zerava.
    """

    monitored_count: int
    comparable_count: int
    below_reference_count: int
    at_or_above_reference_count: int
    no_reference_count: int
    ambiguous_reference_count: int
    inactive_count: int
    fresh_count: int
    stale_count: int
    historical_count: int


class MonitoramentoPrecoRow(BaseModel):
    # --- produto e anuncio ---
    product_name: Optional[str] = None
    brand: str
    marketplace: str
    item_id: str
    seller_sku: Optional[str] = None
    gtin: Optional[str] = None
    listing_title: Optional[str] = None
    permalink: Optional[str] = None
    listing_status: Optional[str] = None
    currency: Optional[str] = None

    # --- observacao ---
    ref_date: str
    #: Instante em que o PRECO foi capturado
    #: (`fact_marketplace_listing_price_daily.price_captured_at`, de
    #: `stg_ml_item_price_history.extracted_at`). NAO e' a atualizacao cadastral
    #: do anuncio — esse era o defeito F2 do PMA-1A. Sem fuso, porque a origem
    #: tambem e' sem fuso e nao declara seu relogio: nao renderizar como
    #: instante absoluto.
    observed_at: Optional[str] = None
    #: Ultima alteracao do CADASTRO do anuncio. Viaja com nome inequivoco e
    #: NUNCA e' apresentada como horario do preco.
    listing_metadata_updated_at: Optional[str] = None
    advertised_price: float
    original_price: Optional[float] = None

    # --- aproximacao do preco efetivo, declarada como incompleta ---
    observed_effective_amount: float
    shipping_amount: Optional[float] = None
    seller_coupon_amount: Optional[float] = None
    platform_subsidy_amount: Optional[float] = None
    checkout_price: Optional[float] = None
    coverage_status: CoverageStatus

    # --- referencia ---
    suggested_retail_amount: Optional[float] = None
    reference_type: ReferenceType
    validity_status: ValidityStatus
    policy_status: PolicyStatus
    reference_captured_at: Optional[str] = None
    reference_row_id: Optional[str] = None

    # --- comparacao ---
    difference_amount: Optional[float] = None
    difference_pct: Optional[float] = None
    match_method: Optional[MatchMethod] = None
    match_quality: MatchQuality
    reference_candidate_count: int
    comparison_status: ComparisonStatus
    #: Frescor da linha, ao LADO do status comercial. Todas as linhas de uma
    #: resposta compartilham a mesma `ref_date`, logo o mesmo frescor; viaja na
    #: linha para que a UI possa marca-la sem perder o veredito comercial.
    freshness_status: FreshnessStatus
    limitations: list[str]


class MonitoramentoPrecoResponse(BaseModel):
    meta: MonitoramentoPrecoMeta
    kpis: MonitoramentoPrecoKpis
    rows: list[MonitoramentoPrecoRow]
    returned_count: int
    total_count: int
    truncated: bool
