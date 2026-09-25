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

from pydantic import BaseModel, Field

# Gate PMA-OPS-2 — o vocabulario de motivos vem do DOMINIO, nao redigitado.
# Redigitar foi a causa do incidente: o dominio ganhou tres valores, o schema
# nao, e a resposta inteira passou a falhar validacao. Ver `NonComparableReason`.
from app.services import pma_domain as dom

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

#: Gate PMA-2C1A — dimensoes do contrato multicanal.
MetricVersion = Literal["v1_all_active", "v2_product_type_aware"]
ObservationMode = Literal["daily_series", "snapshot_current"]
PromoContext = Literal["available", "unavailable"]
Availability = Literal["available", "unavailable"]
ProductType = Literal[
    "kit_confirmed", "kit_suspected", "no_kit_signal", "product_type_unknown"
]
BusinessScope = Literal["in_scope", "out_of_business_scope"]

#: Gate PMA-2C4A — POLITICA DE DATA do canal, declarada no payload.
#:   closed_day        Mercado Livre: serie diaria, teto D-1, D0 inconsistente;
#:   snapshot_current  Shopee/TikTok: fotografia do estado corrente, D0 normal.
DatePolicy = Literal["closed_day", "snapshot_current"]

#: O que a fotografia servida AINDA pode fazer.
#:   mutable_operational_snapshot  e' do dia corrente e pode mudar hoje se a
#:                                 origem recarregar. NAO e' periodo fechado,
#:                                 definitivo nem completo;
#:   settled_snapshot              o dia ja' virou; aquela fotografia nao muda.
SnapshotMutability = Literal["mutable_operational_snapshot", "settled_snapshot"]

#: Por que uma oferta elegivel nao pode ser comparada. Vive AO LADO de
#: `comparison_status`, nunca no lugar dele: a particao comercial tem cinco
#: valores e e' congelada por teste.
#:
#: Gate PMA-OPS-2 — INCIDENTE. Os tres valores do PMA-REF-LINK-1
#: (`brand_without_b2b_reference`, `kit_composition_missing`,
#: `offer_without_match_key`) foram adicionados ao dominio e ao servico e NAO
#: foram adicionados aqui. Como o campo e' `Literal`, o Pydantic recusou a
#: resposta inteira: `ResponseValidationError` -> HTTP 500 em qualquer pagina
#: que contivesse UMA linha com motivo novo. Na fotografia real isso e' a
#: maioria das linhas — a tela caia em 500 nos tres canais.
#:
#: O teste do gate anterior nao pegou porque chamava o servico DIRETO, sem
#: atravessar a serializacao; e um `limit=1` manual pegava a linha 0, que por
#: acaso carregava um motivo antigo. So' o carregamento real da pagina expos.
#:
#: Este `Literal` e' agora derivado de `dom.NON_COMPARABLE_REASONS`, nao
#: redigitado: duas listas do mesmo vocabulario divergem, e foi exatamente o
#: que aconteceu. Ver `test_schema_cobre_todo_o_vocabulario_do_dominio`.
NonComparableReason = Literal[tuple(dom.NON_COMPARABLE_REASONS)]  # type: ignore[valid-type]

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
    # Gate PMA-2C1A: era `Literal["ml"]`. Com o dominio aceitando tres canais,
    # o literal estreito fazia a resposta de `shopee`/`tiktok` — legitima, com
    # `availability=unavailable` — falhar na validacao do response_model e sair
    # como HTTP 500. Um 500 aqui seria exatamente o que o gate proibiu: erro no
    # lugar de estado estruturado.
    marketplace: Literal["ml", "shopee", "tiktok"]
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
    #: Gate PMA-2C4D3-H2 — as marcas que ESTA fotografia realmente contem,
    #: por `marketplace` + `observed_date`.
    #:
    #: Existe porque `monitored_brands` responde outra pergunta — o escopo de
    #: monitoramento do NEGOCIO — e volta igual para Shopee e TikTok, incluindo
    #: Kokeshi, que a fonte da Shopee nao devolve. Montar um filtro com aquela
    #: lista oferecia Kokeshi na Shopee e respondia "0", que se le como "Kokeshi
    #: nao tem anuncios" em vez de "nao observamos Kokeshi aqui".
    #:
    #: Calculado ANTES de marca, situacao, busca, conta, tipo e paginacao.
    #: Inclui marcas fora do escopo comercial que tenham observacao — e' o caso
    #: de gocase e denavita no TikTok. Ordenado pelo banco, sem duplicata.
    #:
    #: `default=[]` de proposito: o frontend e' publicado separado do backend e
    #: precisa tolerar a resposta antiga durante a janela entre os dois deploys.
    observed_brands: list[str] = Field(default_factory=list)
    #: `monitored_brands` menos `observed_brands`, preservando aquela ordem.
    #: NAO significa zero anuncio nem zero venda: significa que esta fotografia
    #: nao tem observacao da marca. Nenhum motivo e' inferido.
    monitored_unobserved_brands: list[str] = Field(default_factory=list)
    comparable_brands: list[str]
    no_reference_brands: list[str]
    #: marca -> rotulo de escopo (`out_of_scope_no_ml_catalog`).
    out_of_scope_brands: dict[str, str]
    order_by: str
    warnings: list[str]

    # ---- Gate PMA-2C1A: ADITIVOS. Nenhum campo acima mudou. ----------------
    #: Versao LOGICA da metrica. O ML publicado e' `v1_all_active`; a troca para
    #: `v2_product_type_aware` muda comparaveis de 139 para 135 e por isso viaja
    #: versionada em vez de mudar em silencio.
    metric_version: MetricVersion = "v1_all_active"
    #: A Shopee NUNCA devolve `daily_series`: e' estado corrente mutavel.
    observation_mode: ObservationMode = "daily_series"
    #: Se o canal permite AFIRMAR promocao. O TikTok nao permite, e isso nao
    #: impede comparar preco observado com PDV.
    promo_context: PromoContext = "available"
    #: `unavailable` quando o canal e' reconhecido mas ainda nao publicado.
    #: Nunca 500, nunca fallback para outro canal.
    availability: Availability = "available"
    unavailable_reason: Optional[str] = None
    #: Alias de `observed_ref_date` com o nome comum aos tres canais.
    observed_date: Optional[str] = None
    #: Instante da captura do PRECO. Nao e' a atualizacao cadastral.
    observed_at: Optional[str] = None
    #: Conceito da Shopee. Vem VAZIO no ML: a fato dele nao modela esse estado,
    #: e inventar "current: 862" afirmaria algo que a fonte nao diz.
    snapshot_status_counts: dict[str, int] = {}
    product_type_counts: dict[str, int] = {}
    #: Um relogio por CONTA. A Shopee carrega em quatro lotes distintos e um
    #: MAX() global marcaria as tres primeiras contas inteiras como atrasadas.
    account_clocks: list[dict] = []

    # ---------------- Gate PMA-2C4A: ADITIVOS --------------------------
    #: Politica de data em vigor nesta resposta. O ML continua `closed_day`.
    date_policy: DatePolicy = "closed_day"
    #: Nulo quando nao ha fotografia. `mutable_operational_snapshot` avisa que
    #: o numero e' verdadeiro para o instante, nao para o dia fechado.
    snapshot_mutability: Optional[SnapshotMutability] = None
    #: Ofertas de marca fora do escopo de beleza que aparecem na tabela e NAO
    #: entram em denominador nenhum. Explicito para que a diferenca entre
    #: `total_count` e `metrics.monitored_offers` seja reconciliavel.
    out_of_scope_offer_count: int = 0


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


class MonitoramentoPrecoMetrics(BaseModel):
    """Metricas multicanal, SEPARADAS de `kpis`.  (Gate PMA-2C1A, fase 7)

    `kpis` continua sendo o bloco PUBLICADO do ML e nao muda de forma: renomear
    seus campos quebraria a tela em producao. As metricas novas — que falam de
    OFERTAS, tipo de produto e elegibilidade — vivem aqui, num bloco proprio.

    A particao e' exaustiva e verificada em teste:
        observed_offers = active_offers + inactive_offers
        active_offers   = kit_confirmed + kit_suspected + no_kit_signal
                          + product_type_unknown
        eligible_offers = active_offers - kit_confirmed - kit_suspected
        comparable_offers + soma(non_comparable_reasons) = eligible_offers
        below_reference + at_or_above_reference = comparable_offers

    `coverage_rate` e `b2b_reach` sao `Optional[float]` e vem NULOS quando o
    denominador e' zero. Zero por cento afirmaria uma medicao que nao existe.

    `distinct_b2b_products` e `b2b_reach` sao leitura de CATALOGO: dizem quantos
    produtos B2B o canal alcanca, e nunca sao denominador de conformidade.
    """

    monitored_offers: int
    active_offers: int
    inactive_offers: int
    kit_confirmed: int
    kit_suspected: int
    no_kit_signal: int
    product_type_unknown: int
    eligible_offers: int
    comparable_offers: int
    below_reference: int
    at_or_above_reference: int
    #: Motivo -> contagem. Soma com `comparable_offers` em `eligible_offers`.
    non_comparable_reasons: dict[str, int] = {}
    #: Motivo -> contagem sobre as ofertas ATIVAS sem referencia, kits e marcas
    #: fora do escopo INCLUSIVE. Soma exatamente `kpis.no_reference_count`.
    #:
    #: Gate PMA-OPS-2 — este campo FALTAVA AQUI. O servico o calculava desde o
    #: PMA-REF-LINK-1 e o Pydantic o descartava silenciosamente na borda HTTP,
    #: porque um modelo sem o campo declarado simplesmente nao o serializa. Os
    #: testes do gate anterior chamavam o servico DIRETO e por isso viam o
    #: valor certo; a resposta real chegava com `{}`. Foi o QA em navegador que
    #: pegou — nenhum teste de unidade cruzava a fronteira do schema.
    no_reference_breakdown: dict[str, int] = {}
    coverage_rate: Optional[float] = None
    distinct_b2b_products: int = 0
    b2b_reach: Optional[float] = None


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
    #: Gate PMA-2C4A — passou a admitir NULO. A fato dos canais permite
    #: `observed_price IS NULL` ("nao observamos preco"), e o Mercado Livre
    #: NUNCA produz nulo aqui: nenhuma resposta de ML muda de valor. Descartar a
    #: linha esconderia a oferta; devolver 0.0 afirmaria um preco que ninguem
    #: observou.
    advertised_price: Optional[float] = None
    original_price: Optional[float] = None

    # --- aproximacao do preco efetivo, declarada como incompleta ---
    #: Segue `advertised_price`, inclusive na ausencia.
    observed_effective_amount: Optional[float] = None
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
    #: Por que a oferta elegivel nao foi comparada. Nulo quando ela FOI
    #: comparada, ou quando o motivo nao se aplica.
    non_comparable_reason: Optional[NonComparableReason] = None
    #: Frescor da linha, ao LADO do status comercial. Todas as linhas de uma
    #: resposta compartilham a mesma `ref_date`, logo o mesmo frescor; viaja na
    #: linha para que a UI possa marca-la sem perder o veredito comercial.
    freshness_status: FreshnessStatus
    limitations: list[str]

    # ---------------- Gate PMA-2C4A: campos de CANAL -------------------
    # Todos MATERIALIZADOS na fato. Vem nulos no Mercado Livre, que nao os
    # modela — e nulo aqui significa "este canal nao tem esse conceito", nao
    # "o valor faltou".
    #: Identidade da oferta no canal: `item_id` na Shopee sem variacao,
    #: `item_id:model_id` no modelo, `sku_id` no TikTok. Igual a `item_id`.
    offer_key: Optional[str] = None
    #: Conta de loja na origem, e ESCOPO da substituicao na publicacao.
    shop_account: Optional[str] = None
    parent_item_id: Optional[str] = None
    model_id: Optional[str] = None
    observed_date: Optional[str] = None
    date_policy: Optional[DatePolicy] = None
    #: Decidido pelo publisher com autoridades que a API nao alcanca (flag
    #: nativa do canal, cadastro interno, BOM). Nunca reclassificado aqui.
    product_type: Optional[ProductType] = None
    product_type_source: Optional[str] = None
    #: Frescor da OFERTA, como o publisher o carimbou na carga.
    snapshot_status: Optional[str] = None
    #: Fim da carga da PROPRIA conta.
    account_watermark_at: Optional[str] = None
    #: Coluna de ORIGEM do preco: `current_price` na Shopee, `sale_price` no
    #: TikTok. Viaja com o dado para que uma troca de fonte nao passe despercebida.
    observed_price_source: Optional[str] = None
    list_price: Optional[float] = None
    promo_context: Optional[PromoContext] = None
    promo_id: Optional[str] = None
    promo_discount_pct: Optional[float] = None
    business_scope: Optional[BusinessScope] = None
    batch_id: Optional[str] = None
    source_run_id: Optional[str] = None


class MonitoramentoPrecoResponse(BaseModel):
    meta: MonitoramentoPrecoMeta
    #: Bloco PUBLICADO do ML — forma inalterada.
    kpis: MonitoramentoPrecoKpis
    #: Bloco multicanal NOVO, separado. Ver `MonitoramentoPrecoMetrics`.
    metrics: MonitoramentoPrecoMetrics
    rows: list[MonitoramentoPrecoRow]
    returned_count: int
    total_count: int
    truncated: bool
