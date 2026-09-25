"""Gate PMA-2C1A — dominio PURO do monitoramento multicanal.

Este modulo nao faz I/O: sem banco, sem rede, sem relogio, sem ambiente. Ele
existe para que as decisoes semanticas fechadas no PMA-2B-R2 sejam testaveis
isoladamente e nao se espalhem por servico, sync e schema em tres copias
divergentes.

O QUE FOI ARBITRADO E ESTA CONGELADO AQUI
-----------------------------------------
1. `kit_confirmed` SO existe onde ha flag nativa do canal. Hoje, so' a Shopee
   tem (`stg_shopee_products.is_kit`). ML e TikTok NUNCA podem produzi-lo:
   `classify_product_type` levanta `ProductTypeError` se receber flag nativa de
   um canal que nao a possui. Rotular suspeita como confirmacao foi listado como
   stop-loss do gate, entao aqui vira erro, nao convencao.

2. Titulo classifica SUSPEITA, e nunca casa produto. A regex vive neste modulo
   e nao e' exportada para o caminho de matching; `pma_match` nao a importa.

3. `no_kit_signal` exige uma autoridade ESTRUTURADA que tenha dito "nao": a flag
   do canal, ou o cadastro interno resolvido 1:1. A ausencia da palavra "kit"
   num titulo nao prova nada — sozinha produz `product_type_unknown`.

   CONSEQUENCIA POR CAMADA, e ela NAO e' a mesma  (Gate PMA-2C1A-R, fase 1)
   ---------------------------------------------------------------------
   A autoridade disponivel depende de QUEM classifica, e por isso existe um
   unico contrato publicado — o da camada que serve.

   - No SYNC (`channel_offer_sync`), que tem acesso ao Data Mart, o cadastro
     interno e a BOM estao disponiveis e `no_kit_signal` e' alcancavel.
   - Na API, NAO. O backend le exclusivamente `marts.*` no Neon e nao consulta
     `gold.`/`silver.`; sobram `seller_sku` e `listing_title`, que so' levantam
     SUSPEITA. Logo, para o ML — cuja fato nao carrega coluna de tipo de produto
     — o serving produz `no_kit_signal = 0` e `product_type_unknown = 339`.

   O numero publicado e' o do serving. Uma medicao offline com o cadastro
   interno chegou a 290 `no_kit_signal` / 49 `unknown`, mas esse recorte NAO e'
   servivel e foi descartado do contrato: manter as duas versoes lado a lado
   faria a mesma oferta ter dois rotulos conforme quem perguntasse.

   Consequencia honesta para a tela: `product_type_unknown` significa "sem sinal
   de kit", NUNCA "produto simples confirmado". A fila de revisao tem 339
   ofertas, das quais 135 sao comparaveis — nao 5.

4. `eligible = active - kit_confirmed - kit_suspected` (politica P2). Kits
   continuam VISIVEIS em `observed`/`active` e recebem
   `kit_composition_missing`: eles nao somem do monitoramento, apenas nao
   recebem conformidade enquanto a composicao nao estiver comprovada.

5. `coverage_rate` com denominador zero e' None, nunca 0.0. Zero afirmaria
   "medimos e deu zero por cento"; None diz "nao ha o que medir".
"""
from __future__ import annotations

import re
from datetime import date, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from zoneinfo import ZoneInfo

#: Fuso CANONICO da operacao. `observed_date` e' sempre o dia civil brasileiro,
#: nunca o dia UTC: `ingested_at` chega em UTC e uma carga as 00:30 BRT cairia
#: no dia seguinte se derivassemos a data direto do timestamp.
TIMEZONE_NAME = "America/Sao_Paulo"
TZ = ZoneInfo(TIMEZONE_NAME)

# ---------------------------------------------------------------------------
# Marketplaces
# ---------------------------------------------------------------------------
MARKETPLACE_ML = "ml"
MARKETPLACE_SHOPEE = "shopee"
MARKETPLACE_TIKTOK = "tiktok"

#: Canais que o dominio conhece. O que a API EXPOE depende das feature flags;
#: um canal listado aqui e' apenas modelavel, nao necessariamente servivel.
ALL_MARKETPLACES = (MARKETPLACE_ML, MARKETPLACE_SHOPEE, MARKETPLACE_TIKTOK)

#: Canais que vivem na fato nova. O ML permanece exclusivamente em
#: `marts.fact_marketplace_listing_price_daily` — uma oferta nunca existe nas
#: duas fatos, e esta tupla e' a fronteira que garante isso.
CHANNEL_OFFER_MARKETPLACES = (MARKETPLACE_SHOPEE, MARKETPLACE_TIKTOK)

#: Unico canal com flag NATIVA de kit.
MARKETPLACES_WITH_NATIVE_KIT_FLAG = (MARKETPLACE_SHOPEE,)

# ---------------------------------------------------------------------------
# Modo de observacao
# ---------------------------------------------------------------------------
#: ML e TikTok tem serie diaria real; a Shopee tem estado corrente mutavel.
OBSERVATION_MODE_DAILY_SERIES = "daily_series"
OBSERVATION_MODE_SNAPSHOT_CURRENT = "snapshot_current"
OBSERVATION_MODES = (OBSERVATION_MODE_DAILY_SERIES, OBSERVATION_MODE_SNAPSHOT_CURRENT)

_OBSERVATION_MODE_BY_MARKETPLACE = MappingProxyType({
    MARKETPLACE_ML: OBSERVATION_MODE_DAILY_SERIES,
    MARKETPLACE_TIKTOK: OBSERVATION_MODE_DAILY_SERIES,
    MARKETPLACE_SHOPEE: OBSERVATION_MODE_SNAPSHOT_CURRENT,
})

# ---------------------------------------------------------------------------
# Estado da fotografia
# ---------------------------------------------------------------------------
SNAPSHOT_CURRENT = "current"
#: Linha que sobreviveu a uma carga da PROPRIA conta sem ser revista.
SNAPSHOT_STALE = "stale"
#: Item que sumiu da fonte. So' e' afirmavel com fotografia COMPLETA da conta.
SNAPSHOT_ABSENT = "absent"
#: A conta nao executou. Diferente de `absent`: `absent` afirma remocao,
#: `account_did_not_run` afirma desconhecimento.
SNAPSHOT_ACCOUNT_DID_NOT_RUN = "account_did_not_run"
#: Carga parcial: nunca pode produzir `absent`.
SNAPSHOT_PARTIAL_LOAD = "partial_load"
SNAPSHOT_STATUSES = (
    SNAPSHOT_CURRENT,
    SNAPSHOT_STALE,
    SNAPSHOT_ABSENT,
    SNAPSHOT_ACCOUNT_DID_NOT_RUN,
    SNAPSHOT_PARTIAL_LOAD,
)

# ---------------------------------------------------------------------------
# Tipo de produto  (politica P2 do PMA-2B-R2)
# ---------------------------------------------------------------------------
PRODUCT_KIT_CONFIRMED = "kit_confirmed"
PRODUCT_KIT_SUSPECTED = "kit_suspected"
PRODUCT_NO_KIT_SIGNAL = "no_kit_signal"
PRODUCT_TYPE_UNKNOWN = "product_type_unknown"
PRODUCT_TYPES = (
    PRODUCT_KIT_CONFIRMED,
    PRODUCT_KIT_SUSPECTED,
    PRODUCT_NO_KIT_SIGNAL,
    PRODUCT_TYPE_UNKNOWN,
)

#: Os dois estados que saem de `eligible_for_comparison`.
PRODUCT_TYPES_EXCLUDED_FROM_COMPARISON = (PRODUCT_KIT_CONFIRMED, PRODUCT_KIT_SUSPECTED)

SOURCE_CHANNEL_FLAG = "channel_flag"
SOURCE_INTERNAL_CATALOG = "internal_catalog"
SOURCE_INTERNAL_BOM = "internal_bom"
SOURCE_SKU_PREFIX = "sku_prefix"
SOURCE_TITLE = "title"
SOURCE_NONE = "none"

#: PRECEDENCIA DETERMINISTICA. A ordem e' contrato: a primeira autoridade que
#: dispara vence, e o resultado nao depende de ordem de dicionario nem de
#: iteracao. Autoridade estruturada (canal, cadastro, BOM) sempre antes de
#: sinal textual (prefixo, titulo).
PRODUCT_TYPE_SOURCE_PRECEDENCE = (
    SOURCE_CHANNEL_FLAG,
    SOURCE_INTERNAL_CATALOG,
    SOURCE_INTERNAL_BOM,
    SOURCE_SKU_PREFIX,
    SOURCE_TITLE,
    SOURCE_NONE,
)

#: Autoridades que podem afirmar a AUSENCIA de kit. Prefixo e titulo nao estao
#: aqui de proposito: eles so' levantam suspeita.
_STRUCTURED_SOURCES = (SOURCE_CHANNEL_FLAG, SOURCE_INTERNAL_CATALOG)

#: Prefixo de SKU que levanta suspeita.
KIT_SKU_PREFIX = "KIT"

#: Sinal textual de kit. Fica NESTE modulo e nao viaja para o matching.
#: `\b` dos dois lados evita casar "kitchen"; `s?` cobre o plural.
_KIT_TITLE_RE = re.compile(r"\bkits?\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Contexto promocional
# ---------------------------------------------------------------------------
#: Independente da conformidade: dizer "ha promocao" exige preco anterior.
PROMO_AVAILABLE = "available"
PROMO_UNAVAILABLE = "unavailable"
PROMO_CONTEXTS = (PROMO_AVAILABLE, PROMO_UNAVAILABLE)

#: TikTok tem SOMENTE `sale_price`: sem preco de tabela, sem flag de campanha,
#: sem `promotion_id`. Medido no PMA-2A-R. Nao ha como afirmar promocao la',
#: e isso NAO impede comparar o preco observado com o PDV.
_PROMO_CONTEXT_BY_MARKETPLACE = MappingProxyType({
    MARKETPLACE_ML: PROMO_AVAILABLE,
    MARKETPLACE_SHOPEE: PROMO_AVAILABLE,
    MARKETPLACE_TIKTOK: PROMO_UNAVAILABLE,
})

# ---------------------------------------------------------------------------
# Preco ANUNCIADO  — rotulos semanticos  (Gate PMA-2C1A-R, fase 3)
# ---------------------------------------------------------------------------
# ESTA SUPERFICIE MONITORA PRECO ANUNCIADO CORRENTE, NAO PRECO REALIZADO.
#
# A distincao nao e' terminologia: e' o limite do que a fonte sustenta. Nenhum
# dos tres canais entrega, neste catalogo, o valor que o comprador efetivamente
# pagou. Por isso os nomes abaixo sao fixos e as expressoes "preco efetivamente
# pago" e "preco liquido da venda" estao BANIDAS do contrato — usa-las
# prometeria uma medicao de checkout que nao existe aqui.
#
#   observed_price        preco ANUNCIADO corrente, exibido na vitrine
#   list_price            preco original / de tabela exibido pelo canal
#   realized_order_price  INDISPONIVEL nesta superficie (vive no pedido)
#   checkout_price        INDISPONIVEL
#   coupon_effect         INDISPONIVEL quando o canal nao fornece
#   shipping_effect       INDISPONIVEL
#
# Por canal, o que fica de fora:
#   ML      — pode haver cupom ou beneficio de checkout invisivel nesta fonte;
#             `original_price` existe em apenas 283 de 862 anuncios (33%).
#   TikTok  — a fonte de catalogo nao fornece contexto promocional suficiente:
#             so' `sale_price`, sem preco de tabela e sem id de campanha.
#   Shopee  — ha promocao DO ANUNCIO (`has_promotion`, `promotion_id`,
#             `discount_pct`, no item pai), mas ela nao prova cupom individual
#             aplicado no checkout de um comprador.
PRICE_LABEL_OBSERVED = "observed_price"
PRICE_LABEL_LIST = "list_price"
PRICE_LABEL_REALIZED = "realized_order_price"
PRICE_LABEL_CHECKOUT = "checkout_price"
PRICE_LABEL_COUPON = "coupon_effect"
PRICE_LABEL_SHIPPING = "shipping_effect"

#: Rotulos que esta superficie NAO mede. Vem sempre nulos e nunca sao inferidos.
UNAVAILABLE_PRICE_LABELS = (
    PRICE_LABEL_REALIZED,
    PRICE_LABEL_CHECKOUT,
    PRICE_LABEL_COUPON,
    PRICE_LABEL_SHIPPING,
)

#: Expressoes proibidas em contrato, log e documentacao: afirmam checkout.
BANNED_PRICE_PHRASES = (
    "preco efetivamente pago",
    "preco liquido da venda",
    "preco liquido realizado",
    "valor pago pelo comprador",
)

#: Coluna de onde sai `observed_price` em cada canal. NUNCA ha fallback para o
#: preco cheio: se a coluna de preco anunciado for nula, a oferta vira
#: `invalid_channel_price`, e nao uma comparacao contra `list_price`.
#: `MappingProxyType` impede mutacao acidental de um mapa de contrato.
OBSERVED_PRICE_SOURCE = MappingProxyType({
    MARKETPLACE_ML: "advertised_price",
    MARKETPLACE_SHOPEE: "current_price",
    MARKETPLACE_TIKTOK: "sale_price",
})

#: Preco anterior / de tabela. `None` onde a fonte nao fornece.
LIST_PRICE_SOURCE = MappingProxyType({
    MARKETPLACE_ML: "original_price",
    MARKETPLACE_SHOPEE: "original_price",
    MARKETPLACE_TIKTOK: None,
})

#: Medidos identicos aos campos simples em 371/371 modelos (PMA-2A-R e
#: reconfirmado na fase 2 do PMA-2C1A-R): nao carregam informacao e estao
#: PROIBIDOS no contrato.
FORBIDDEN_PRICE_FIELDS = ("inflated_current_price", "inflated_original_price")

# ---------------------------------------------------------------------------
# Escopo de negocio
# ---------------------------------------------------------------------------
BUSINESS_SCOPE_IN = "in_scope"
BUSINESS_SCOPE_OUT = "out_of_business_scope"
BUSINESS_SCOPES = (BUSINESS_SCOPE_IN, BUSINESS_SCOPE_OUT)

#: Escopo padrao do PMA de beleza. Gocase e Denavita aparecem no TikTok, sao
#: MEDIDAS e visiveis na auditoria de cobertura, mas nao entram em nenhum
#: denominador — nem como "sem referencia", o que as faria parecer falha de
#: casamento quando na verdade estao fora do produto.
BEAUTY_SCOPE_BRANDS = ("apice", "barbours", "kokeshi", "lescent", "rituaria")

# ---------------------------------------------------------------------------
# Versao da metrica
# ---------------------------------------------------------------------------
#: `v1_all_active` e' o comportamento PUBLICADO: todo anuncio ativo e' elegivel,
#: kits inclusive. `v2_product_type_aware` aplica a politica P2. A troca muda o
#: numero do ML de 139 para 135 comparaveis — e' evolucao DELIBERADA da metrica,
#: nao correcao de bug, entao viaja versionada em vez de mudar em silencio.
METRIC_VERSION_V1 = "v1_all_active"
METRIC_VERSION_V2 = "v2_product_type_aware"
METRIC_VERSIONS = (METRIC_VERSION_V1, METRIC_VERSION_V2)

# ---------------------------------------------------------------------------
# Comparacao e motivos de nao comparabilidade
# ---------------------------------------------------------------------------
COMPARISON_BELOW = "below_reference"
COMPARISON_AT_OR_ABOVE = "at_or_above_reference"
COMPARISON_NO_REFERENCE = "no_reference"
COMPARISON_AMBIGUOUS = "non_comparable_reference_ambiguous"
COMPARISON_INACTIVE = "inactive_listing"
#: Onde kit_confirmed e kit_suspected caem. Nome diz o que FALTA (composicao),
#: nao o que o produto e'.
COMPARISON_KIT_MISSING = "kit_composition_missing"
COMPARISON_STATUSES = (
    COMPARISON_BELOW,
    COMPARISON_AT_OR_ABOVE,
    COMPARISON_NO_REFERENCE,
    COMPARISON_AMBIGUOUS,
    COMPARISON_INACTIVE,
    COMPARISON_KIT_MISSING,
)

#: Os dois unicos estados que contam como conformidade medida.
COMPARABLE_STATUSES = (COMPARISON_BELOW, COMPARISON_AT_OR_ABOVE)

REASON_REFERENCE_MISSING = "reference_missing_for_product"
REASON_SKU_NOT_INTERNAL = "sku_not_in_internal_catalog"
REASON_PRODUCT_WITHOUT_EAN = "product_without_ean"
REASON_EAN_NOT_CONSUMER = "product_ean_not_consumer"
REASON_AMBIGUOUS = "ambiguous_multiple_candidates"
REASON_INVALID_REFERENCE_PRICE = "invalid_reference_price"
REASON_INVALID_CHANNEL_PRICE = "invalid_channel_price"

# --- Gate PMA-REF-LINK-1: tres causas que o serving SEMPRE soube distinguir --
#
# Medido em 2026-09-25 sobre a fotografia publicada dos tres canais: as 1.817
# ofertas sem referencia recebiam UM unico motivo,
# `reference_missing_for_product`, que se le como "o produto nao esta na tabela
# B2B". Para 879 delas isso e' FALSO — e falso de tres formas diferentes, cada
# uma com um dono e uma acao distintos:
#
#   279  a MARCA nao tem tabela B2B publicada (Lescent nos tres canais, Gocase e
#        Denavita no TikTok). Nao ha o que procurar: a acao e' de Trade, publicar
#        a tabela, e nao de cadastro, corrigir um codigo.
#   600  a oferta e' KIT e a composicao nao esta confirmada. O proprio
#        `pma_domain` ja' prometia este rotulo no item 4 do cabecalho deste
#        modulo ("kits ... recebem `kit_composition_missing`") e o serving nunca
#        o emitiu. A acao e' cadastral: registrar a BOM.
#     3  a oferta nao tem NENHUMA chave de casamento — nem GTIN, nem SKU. Nao
#        e' que a busca falhou: nao houve busca possivel.
#
# Nenhum deles exige dado novo. A marca sem tabela sai das linhas de referencia
# que o servico ja' carrega; o tipo de produto e a ausencia de chave saem da
# propria fato. O que faltava era a PRECEDENCIA, nao a informacao.
#
#: A marca da oferta nao aparece no snapshot de referencia carregado. Difere de
#: `reference_missing_for_product`, que afirma que a marca TEM tabela e o
#: produto nao esta nela.
REASON_BRAND_WITHOUT_REFERENCE = "brand_without_b2b_reference"
#: Kit sem composicao confirmada. MESMO literal de `COMPARISON_KIT_MISSING`, de
#: proposito: e' a mesma afirmacao vista de dois vocabularios, e duas grafias
#: fariam a tela e a metrica discordarem sobre o mesmo fato.
REASON_KIT_COMPOSITION_MISSING = COMPARISON_KIT_MISSING
#: Oferta sem GTIN e sem SKU: nao ha chave com que procurar. Nao e' o mesmo que
#: "procuramos e nao achamos", e chamar as duas de ausencia de referencia
#: mandaria conferir a tabela B2B quando o defeito esta no anuncio.
REASON_NO_MATCH_KEY = "offer_without_match_key"

#: Particao dos ELEGIVEIS: comparavel + estes motivos == eligible, sempre.
NON_COMPARABLE_REASONS = (
    REASON_REFERENCE_MISSING,
    REASON_BRAND_WITHOUT_REFERENCE,
    REASON_KIT_COMPOSITION_MISSING,
    REASON_NO_MATCH_KEY,
    REASON_SKU_NOT_INTERNAL,
    REASON_PRODUCT_WITHOUT_EAN,
    REASON_EAN_NOT_CONSUMER,
    REASON_AMBIGUOUS,
    REASON_INVALID_REFERENCE_PRICE,
    REASON_INVALID_CHANNEL_PRICE,
)

# ---------------------------------------------------------------------------
# Indisponibilidade
# ---------------------------------------------------------------------------
#: Estado ESTRUTURADO devolvido quando nao ha o que servir. Nunca 500, nunca
#: fallback para outro canal, nunca a observacao mais proxima.
UNAVAILABLE_CHANNEL_DISABLED = "channel_disabled_by_feature_flag"
UNAVAILABLE_TABLE_MISSING = "serving_table_not_created"
UNAVAILABLE_NO_OBSERVATION = "no_observation_for_requested_date"
UNAVAILABLE_NO_REFERENCE = "no_reference_snapshot_published"
UNAVAILABLE_HISTORY_NOT_STARTED = "history_not_started_for_channel"
UNAVAILABLE_REASONS = (
    UNAVAILABLE_CHANNEL_DISABLED,
    UNAVAILABLE_TABLE_MISSING,
    UNAVAILABLE_NO_OBSERVATION,
    UNAVAILABLE_NO_REFERENCE,
    UNAVAILABLE_HISTORY_NOT_STARTED,
)


class ProductTypeError(ValueError):
    """Violacao de INVARIANTE do dominio, nao entrada malformada do usuario.

    Levantada quando um canal sem flag nativa tenta produzir `kit_confirmed`.
    E' erro de programacao: significa que alguem ligou um canal novo a uma
    autoridade que ele nao tem. Falhar alto e' deliberado — o stop-loss do gate
    e' exatamente "suspeito apresentado como confirmado".
    """


class GrainError(ValueError):
    """Chave de oferta impossivel de formar com os campos recebidos."""


# ---------------------------------------------------------------------------
# Funcoes puras
# ---------------------------------------------------------------------------
def observed_date_from(instant) -> "date | None":
    """Dia CIVIL brasileiro de um instante. Nunca o dia UTC.

    Um `ingested_at` de 2026-09-15T02:30Z e' 2026-09-14 em Sao Paulo. Derivar a
    data direto do timestamp jogaria a observacao para o dia seguinte e criaria
    um dia fantasma na serie.

    Instante ingenuo (sem tzinfo) e' tratado como UTC: e' o que o driver devolve
    para `timestamp without time zone`, e assumir o fuso local silenciosamente
    mudaria o resultado conforme a maquina que roda o sync.
    """
    if instant is None:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(TZ).date()


def observation_mode_for(marketplace: str) -> str:
    """Modo de observacao do canal. A Shopee JAMAIS devolve `daily_series`."""
    try:
        return _OBSERVATION_MODE_BY_MARKETPLACE[marketplace]
    except KeyError:
        raise ValueError("marketplace fora do dominio conhecido") from None


def promo_context_for(marketplace: str) -> str:
    """Se o canal permite AFIRMAR contexto promocional. Nao afeta conformidade."""
    try:
        return _PROMO_CONTEXT_BY_MARKETPLACE[marketplace]
    except KeyError:
        raise ValueError("marketplace fora do dominio conhecido") from None


def supports_native_kit_flag(marketplace: str) -> bool:
    return marketplace in MARKETPLACES_WITH_NATIVE_KIT_FLAG


def title_has_kit_signal(title: object) -> bool:
    """Sinal TEXTUAL de kit. Serve para suspeita; nunca para casar produto."""
    if not isinstance(title, str) or not title:
        return False
    return _KIT_TITLE_RE.search(title) is not None


def sku_has_kit_prefix(seller_sku: object) -> bool:
    if not isinstance(seller_sku, str):
        return False
    return seller_sku.strip().upper().startswith(KIT_SKU_PREFIX)


def classify_product_type(
    marketplace: str,
    *,
    channel_kit_flag: bool | None = None,
    internal_is_kit: bool | None = None,
    internal_has_bom: bool | None = None,
    seller_sku: object = None,
    title: object = None,
) -> tuple[str, str]:
    """Classifica a oferta e devolve `(product_type, product_type_source)`.

    `None` em `channel_kit_flag` / `internal_is_kit` / `internal_has_bom`
    significa AUTORIDADE INDISPONIVEL — e' diferente de `False`, que e' a
    autoridade dizendo "nao e' kit". Essa distincao e' o que separa
    `no_kit_signal` de `product_type_unknown`.
    """
    if marketplace not in ALL_MARKETPLACES:
        raise ValueError("marketplace fora do dominio conhecido")

    if channel_kit_flag is not None and not supports_native_kit_flag(marketplace):
        # Deixar passar produziria `kit_confirmed` num canal que nao tem como
        # prova-lo — exatamente o stop-loss do gate.
        raise ProductTypeError(
            "canal sem flag nativa de kit nao pode receber channel_kit_flag"
        )

    # 1. channel_flag
    if channel_kit_flag is True:
        return PRODUCT_KIT_CONFIRMED, SOURCE_CHANNEL_FLAG
    # 2. internal_catalog
    if internal_is_kit is True:
        return PRODUCT_KIT_SUSPECTED, SOURCE_INTERNAL_CATALOG
    # 3. internal_bom
    if internal_has_bom is True:
        return PRODUCT_KIT_SUSPECTED, SOURCE_INTERNAL_BOM
    # 4. sku_prefix
    if sku_has_kit_prefix(seller_sku):
        return PRODUCT_KIT_SUSPECTED, SOURCE_SKU_PREFIX
    # 5. title
    if title_has_kit_signal(title):
        return PRODUCT_KIT_SUSPECTED, SOURCE_TITLE
    # 6. autoridade estruturada que disse "nao"
    if channel_kit_flag is False:
        return PRODUCT_NO_KIT_SIGNAL, SOURCE_CHANNEL_FLAG
    if internal_is_kit is False:
        return PRODUCT_NO_KIT_SIGNAL, SOURCE_INTERNAL_CATALOG
    # 7. nenhuma autoridade estruturada disponivel
    return PRODUCT_TYPE_UNKNOWN, SOURCE_NONE


def is_excluded_from_comparison(product_type: str) -> bool:
    """Politica P2: kit confirmado e suspeito saem do denominador elegivel."""
    return product_type in PRODUCT_TYPES_EXCLUDED_FROM_COMPARISON


def in_beauty_scope(brand: object) -> bool:
    if not isinstance(brand, str):
        return False
    return brand.strip().lower() in BEAUTY_SCOPE_BRANDS


def business_scope_for(brand: object) -> str:
    return BUSINESS_SCOPE_IN if in_beauty_scope(brand) else BUSINESS_SCOPE_OUT


def build_offer_key(
    marketplace: str,
    *,
    item_id: object = None,
    model_id: object = None,
    sku_id: object = None,
    has_model: bool | None = None,
) -> str:
    """Chave da OFERTA no grao nativo de cada canal.

    Shopee: um pai SEM variacao e' uma oferta (`item_id`); um pai COM variacao
    NAO e' oferta nenhuma — e' container — e cada modelo dele vira uma oferta
    (`item_id:model_id`). E' essa regra que impede contar pai e filho juntos:
    na fotografia de referencia sao 321 pais simples + 371 modelos = 692
    ofertas, nunca 659 + 371 = 1.030.
    """
    def _txt(v, campo):
        if v is None or (isinstance(v, str) and not v.strip()):
            raise GrainError(f"campo obrigatorio do grao ausente: {campo}")
        return str(v).strip()

    if marketplace == MARKETPLACE_ML:
        return _txt(item_id, "item_id")
    if marketplace == MARKETPLACE_TIKTOK:
        return _txt(sku_id, "sku_id")
    if marketplace == MARKETPLACE_SHOPEE:
        if has_model is None:
            raise GrainError("shopee exige has_model para decidir o grao")
        base = _txt(item_id, "item_id")
        if not has_model:
            if model_id is not None:
                raise GrainError("item sem variacao nao pode carregar model_id")
            return base
        return f"{base}:{_txt(model_id, 'model_id')}"
    raise ValueError("marketplace fora do dominio conhecido")


def shopee_offer_is_container(has_model: bool) -> bool:
    """Pai COM variacao nao e' oferta comercial: seus modelos e' que sao."""
    return bool(has_model)


def _positive_decimal(value: object) -> Decimal | None:
    """Decimal estritamente positivo, ou None. Nunca converte ausencia em zero."""
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not dec.is_finite() or dec <= 0:
        return None
    return dec


def compare_to_reference(observed_price: object,
                         suggested_price: object) -> tuple[str | None, str | None]:
    """Devolve `(comparison_status, reason)`; exatamente um dos dois e' None.

    Preco nulo, zero, negativo, NaN ou infinito NUNCA vira conformidade: sai
    como motivo explicito. Um zero aqui afirmaria "medimos e deu zero".
    """
    ref = _positive_decimal(suggested_price)
    if ref is None:
        return None, REASON_INVALID_REFERENCE_PRICE
    obs = _positive_decimal(observed_price)
    if obs is None:
        return None, REASON_INVALID_CHANNEL_PRICE
    if obs < ref:
        return COMPARISON_BELOW, None
    return COMPARISON_AT_OR_ABOVE, None


def difference(observed_price: object,
               suggested_price: object) -> tuple[Decimal | None, Decimal | None]:
    """`(diferenca_absoluta, diferenca_percentual)` ou `(None, None)`."""
    ref = _positive_decimal(suggested_price)
    obs = _positive_decimal(observed_price)
    if ref is None or obs is None:
        return None, None
    delta = obs - ref
    return delta, (delta / ref) * Decimal(100)


def eligible_offers(active: int, kit_confirmed: int, kit_suspected: int) -> int:
    """`eligible = active - kit_confirmed - kit_suspected` (P2)."""
    valor = active - kit_confirmed - kit_suspected
    if valor < 0:
        raise ValueError("elegiveis negativos: particao inconsistente")
    return valor


def coverage_rate(comparable: int, eligible: int) -> float | None:
    """Denominador zero devolve None. Zero por cento seria uma medicao falsa."""
    if eligible <= 0:
        return None
    return comparable / eligible


def b2b_reach(distinct_b2b_products: int, reference_rows: int) -> float | None:
    """Leitura de CATALOGO. Nunca e' denominador de conformidade."""
    if reference_rows <= 0:
        return None
    return distinct_b2b_products / reference_rows


def assert_partition(
    *,
    observed: int,
    active: int,
    inactive: int,
    product_type_counts: dict,
    eligible: int,
    excluded_by_product_type: int,
    comparable: int,
    reason_counts: dict,
) -> None:
    """As quatro provas do gate. Falha ALTO: numero errado e' pior que erro."""
    if active + inactive != observed:
        raise ValueError("particao: active + inactive != observed")
    soma_tipos = sum(product_type_counts.get(t, 0) for t in PRODUCT_TYPES)
    if soma_tipos != active:
        raise ValueError("particao: soma dos product_type != active")
    if eligible + excluded_by_product_type != active:
        raise ValueError("particao: eligible + excluded_by_product_type != active")
    soma_motivos = sum(reason_counts.get(r, 0) for r in NON_COMPARABLE_REASONS)
    if comparable + soma_motivos != eligible:
        raise ValueError("particao: comparable + motivos != eligible")
