"""Gate PMA-1A — contrato de MATCH e de COMPARACAO do monitoramento de precos.

SO BIBLIOTECA PADRAO, DE PROPOSITO
----------------------------------
Nao importa sqlalchemy, pydantic nem fastapi. Este modulo e' a regra de negocio
do gate — quem casa com quem, e o que se pode afirmar do resultado — e mantendo-o
em stdlib ele roda e se prova sem banco e sem ambiente web montado. O acesso a
`marts.*` no Neon fica em `monitoramento_preco_service.py`.

UMA IMPLEMENTACAO, NAO DUAS
---------------------------
O match acontece AQUI, em Python, sobre linhas ja lidas das duas tabelas de
`marts`. Nao existe uma segunda versao em SQL. Duas implementacoes da mesma regra
divergiriam, e a que ficasse atrasada produziria um veredito de preco errado.

A escala permite: 855 anuncios monitorados e 221 linhas de referencia medidos em
2026-09-02. A indexacao e' O(n) por dicionario. O limite e' EXPLICITO
(`MAX_*_ROWS`) e falha alto — nunca trunca em silencio, porque truncar faria uma
tela de cobertura parcial parecer completa.

O QUE ESTE MODULO NAO FAZ
-------------------------
Nao ha severidade, limiar comercial, politica, sancao nem juizo. Sem limiar
aprovado, os unicos fatos sao `difference_amount` e `difference_pct`.
"advertised_price < suggested_retail_amount" e' `below_reference` — "abaixo da
referencia" — e nada mais que isso.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Contrato — DUPLICADO POR FRONTEIRA, com teste de identidade
# ---------------------------------------------------------------------------
# `apps/api` nao importa `pipelines` em nenhum ponto do repo, e esta task nao
# abre essa fronteira. Os literais abaixo repetem
# `pipelines/pma/reference_contract.py` de proposito, e
# `apps/api/tests/test_monitoramento_preco_contract.py` compara os dois modulos
# campo a campo: se um lado mudar sozinho, o teste reprova.
REFERENCE_TYPE = "suggested_retail_pdv"
POLICY_STATUS = "not_applicable_to_own_store_monitoring"
VALIDITY_STATUS = "missing"
COVERAGE_STATUS = "advertised_only"

MARKETPLACE_ML = "ml"
SUPPORTED_MARKETPLACES = (MARKETPLACE_ML,)
CURRENCY = "BRL"
TIMEZONE_NAME = "America/Sao_Paulo"

MONITORED_BRANDS = ("barbours", "kokeshi", "lescent", "rituaria")
REFERENCE_BRANDS = ("apice", "barbours", "kokeshi", "rituaria", "yenzah")
COMPARABLE_BRANDS = tuple(b for b in MONITORED_BRANDS if b in REFERENCE_BRANDS)
NO_REFERENCE_BRANDS = tuple(b for b in MONITORED_BRANDS if b not in REFERENCE_BRANDS)
OUT_OF_SCOPE_BRANDS = tuple(b for b in REFERENCE_BRANDS if b not in MONITORED_BRANDS)

QUALITY_MISSING_PRICE = "missing_suggested_price"

# --- status COMERCIAL: particao exclusiva que fecha em monitored_count -----
# Gate PMA-H1: `stale_observation` SAIU desta particao. Antes ele era um status
# comercial e SUBSTITUIA a classificacao: com o sync atrasado, toda linha virava
# `stale_observation`, os cinco contadores comerciais iam a zero e a tela
# aparentava "nenhum desvio" quando o que havia era atraso de pipeline. Atraso e'
# QUALIDADE do dado, nao veredito de preco — e as duas coisas sao ortogonais.
STATUS_BELOW = "below_reference"
STATUS_AT_OR_ABOVE = "at_or_above_reference"
STATUS_NO_REFERENCE = "no_reference"
STATUS_AMBIGUOUS = "non_comparable_reference_ambiguous"
STATUS_INACTIVE = "inactive_listing"

#: A particao comercial. Toda linha recebe EXATAMENTE um destes, e a soma dos
#: cinco contadores e' `monitored_count` — verificado em teste.
COMMERCIAL_STATUSES = (
    STATUS_BELOW, STATUS_AT_OR_ABOVE, STATUS_NO_REFERENCE,
    STATUS_AMBIGUOUS, STATUS_INACTIVE,
)

# --- frescor: QUALIDADE TRANSVERSAL, sobreposta a qualquer status comercial -
#: Observacao e' de D-1: sustenta leitura como "hoje".
FRESHNESS_FRESH = "fresh"
#: O modo `latest` caiu numa observacao anterior a D-1 porque o sync esta
#: atrasado. As comparacoes continuam validas PARA AQUELE DIA e continuam
#: visiveis; o que muda e' o aviso de defasagem.
FRESHNESS_STALE = "stale"
#: O consumidor ESCOLHEU uma data anterior. Nao e' atraso — e' consulta
#: retrospectiva deliberada, e por isso NAO se chama `stale`.
FRESHNESS_HISTORICAL = "historical"
#: Nao existe observacao para responder (serving vazio, ou data sem materializacao).
FRESHNESS_UNAVAILABLE = "unavailable"

FRESHNESS_STATUSES = (
    FRESHNESS_FRESH, FRESHNESS_STALE, FRESHNESS_HISTORICAL,
    FRESHNESS_UNAVAILABLE,
)

#: DEPRECIADO — alias de compatibilidade. Era um status comercial; agora e'
#: aceito no parametro `status` apenas como FILTRO DE FRESCOR
#: (`freshness_status == 'stale'`), para nao quebrar em silencio link antigo ou
#: bookmark. Nunca aparece em `row.comparison_status`.
STATUS_STALE = "stale_observation"

#: O que o parametro `status` aceita: a particao comercial + o alias depreciado.
COMPARISON_STATUSES = COMMERCIAL_STATUSES + (STATUS_STALE,)

#: Rotulo de escopo de MARCA (nao de linha): marca com referencia B2B mas sem
#: catalogo ML proprio. Vive em `meta.warnings`, nunca como status de linha —
#: nao existe anuncio para essas marcas, logo nao existe linha.
BRAND_SCOPE_OUT_OF_SCOPE = "out_of_scope_no_ml_catalog"

# --- metodo e qualidade do match ------------------------------------------
MATCH_GTIN = "brand_gtin_exact"
MATCH_SKU = "brand_sku_exact_unique"
MATCH_NONE = None

QUALITY_PRIMARY = "primary_gtin_exact"
QUALITY_SECONDARY = "secondary_sku_unique_in_brand"
QUALITY_AMBIGUOUS = "ambiguous_multiple_candidates"
QUALITY_UNMATCHED = "unmatched"

#: Tetos de seguranca. Estourar levanta — nunca trunca.
MAX_LISTING_ROWS = 50_000
MAX_REFERENCE_ROWS = 20_000

# --- frescor: SOMENTE D-1 sustenta comparacao  (PMA-1A-R, F4) ---------------
#: Observacao de D-1 (dia operacional America/Sao_Paulo) — elegivel.
OBS_ELIGIBLE = "eligible"
#: Observacao anterior a D-1 — `stale_observation`, sem diferenca e sem veredito.
OBS_STALE = "stale"
#: Observacao em D0 ou no futuro — ESTADO INVALIDO. O sync proibe
#: contratualmente publicar o dia corrente, portanto a presenca de uma linha
#: assim no serving e' inconsistencia da camada, nao dado tardio.
OBS_INVALID = "invalid"

#: A versao anterior tinha `STALE_TOLERANCE_DAYS = 1`, tratando D-2 como fresco.
#: Foi REMOVIDO: uma tolerancia converte atraso de pipeline em veredito de preco
#: com cara de atual. Sem ela, atraso aparece como `stale_observation`, que e' o
#: que ele e'.

_NON_DIGIT = re.compile(r"\D")

#: Tamanhos de EAN de CONSUMIDOR. 14 digitos e' DUN de caixa e NAO e' chave de
#: match — nem do lado da referencia, nem do lado da observacao.
CONSUMER_EAN_LENGTHS = (8, 12, 13)


class PmaMatchError(RuntimeError):
    """INCONSISTENCIA DA FONTE/SERVING — nunca erro do cliente.  (PMA-1A-R, F7)

    Era `ValueError` e o router a devolvia como 422, o que dizia ao consumidor
    "voce errou" quando o defeito era do nosso dado: NaN num preco, escala acima
    do teto, `ref_date` em D0 que o sync proibiu publicar, formato interno
    invalido. Nada disso e' recuperavel mudando a requisicao.

    Agora e' `RuntimeError` e o router a traduz para uma mensagem FIXA de erro
    interno, sem detalhe. Excecao de programacao desconhecida continua
    propagando — nao ha `except Exception` em nenhum ponto do caminho HTTP.
    """


def consumer_ean_or_none(raw: object) -> str | None:
    """Codigo de barras -> EAN de consumidor, ou `None`.

    14 digitos devolve `None`: e' DUN de caixa. O gate proibe tratar DUN como EAN
    silenciosamente, e casar caixa com unidade compararia precos de coisas
    diferentes. A linha continua elegivel ao match secundario por SKU.
    """
    if raw is None:
        return None
    digitos = _NON_DIGIT.sub("", str(raw).strip())
    if len(digitos) in CONSUMER_EAN_LENGTHS:
        return digitos
    return None


def normalize_sku_key(raw: object) -> str | None:
    """SKU -> chave de match. Igualdade EXATA sobre texto maiusculo. Zero fuzzy."""
    if raw is None:
        return None
    texto = str(raw).strip().upper()
    return texto or None


def normalize_brand_key(raw: object) -> str | None:
    if raw is None:
        return None
    texto = str(raw).strip().lower()
    return texto or None


def last_eligible_date(today: date) -> date:
    """O UNICO dia que sustenta comparacao: D-1 em America/Sao_Paulo."""
    return today - timedelta(days=1)


def classify_freshness(ref_date: date | None, today: date,
                       *, selected: bool) -> str:
    """Frescor de uma resposta. ORTOGONAL ao status comercial (Gate PMA-H1).

    `selected` diz se o consumidor PEDIU aquela data. E' o que separa atraso de
    consulta retrospectiva: a mesma `ref_date` de tres dias atras e' `stale`
    quando o modo `latest` caiu nela por falta de dado mais novo, e `historical`
    quando foi escolhida de proposito. Chamar as duas de `stale` misturaria uma
    falha de pipeline com um uso legitimo da tela.

    D0/futuro NAO e' classificado aqui: `classify_observation_date` continua
    tratando isso como inconsistencia do serving, fail-closed.
    """
    if ref_date is None:
        return FRESHNESS_UNAVAILABLE
    if selected:
        return (FRESHNESS_FRESH if ref_date == last_eligible_date(today)
                else FRESHNESS_HISTORICAL)
    return (FRESHNESS_FRESH if ref_date == last_eligible_date(today)
            else FRESHNESS_STALE)


def lag_days(ref_date: date | None, today: date) -> int | None:
    """Dias entre a observacao e D-1. `None` quando nao ha observacao.

    Zero significa em dia. Nunca negativo: D0/futuro e' barrado antes daqui.
    """
    if ref_date is None:
        return None
    return (last_eligible_date(today) - ref_date).days


def classify_observation_date(ref_date: date, today: date) -> str:
    """Classifica a data observada em elegivel / vencida / INVALIDA.

    Fronteiras exatas, sem tolerancia:
        ref_date == D-1  -> `eligible`
        ref_date <  D-1  -> `stale`     (atraso e' atraso, nao veredito)
        ref_date >= D0   -> `invalid`   (o sync proibe D0 e futuro; se apareceu,
                                         a camada de serving esta inconsistente)
    """
    limite = last_eligible_date(today)
    if ref_date == limite:
        return OBS_ELIGIBLE
    if ref_date < limite:
        return OBS_STALE
    return OBS_INVALID


@dataclass(frozen=True)
class ReferenceIndex:
    """Indice de referencias escopado por MARCA.

    As chaves sao tuplas `(brand, valor)`. Match cross-brand e' impossivel por
    CONSTRUCAO, nao por filtro adicional: nao existe chave sem marca. Isso
    importa porque os SKU do Apice sao numericos de 5 digitos e Barbours, Kokeshi
    e Lescent tem 100, 188 e 94 itens ML com SELLER_SKU no mesmo formato — um
    indice global casaria produto de marca errada.
    """

    by_gtin: dict[tuple[str, str], list[dict]]
    by_sku: dict[tuple[str, str], list[dict]]
    captured_at: object | None

    @staticmethod
    def build(references: list[dict]) -> "ReferenceIndex":
        if len(references) > MAX_REFERENCE_ROWS:
            raise PmaMatchError(
                f"snapshot de referencia com {len(references)} linhas excede o teto "
                f"de {MAX_REFERENCE_ROWS}: o match em memoria foi dimensionado para "
                f"a escala medida (221 linhas). Recusado em vez de truncado."
            )
        by_gtin: dict[tuple[str, str], list[dict]] = {}
        by_sku: dict[tuple[str, str], list[dict]] = {}
        captured: object | None = None
        for ref in references:
            # Linha sem preco de referencia NAO entra em nenhum indice: nao e'
            # candidata a nada, e deixa-la entrar criaria ambiguidade artificial
            # (dois candidatos, um deles inutilizavel). Ela permanece no snapshot,
            # auditavel, com `quality_status = missing_suggested_price` e
            # `suggested_retail_amount` NULO — ver F1 do PMA-1A-R.
            if ref.get("quality_status") == QUALITY_MISSING_PRICE:
                continue
            if ref.get("suggested_retail_amount") is None:
                continue
            marca = normalize_brand_key(ref.get("brand"))
            if marca is None:
                continue
            gtin = consumer_ean_or_none(ref.get("source_gtin"))
            if gtin is not None:
                by_gtin.setdefault((marca, gtin), []).append(ref)
            sku = normalize_sku_key(ref.get("source_sku"))
            if sku is not None:
                by_sku.setdefault((marca, sku), []).append(ref)
            if ref.get("captured_at") is not None:
                atual = ref["captured_at"]
                captured = atual if captured is None or atual > captured else captured
        return ReferenceIndex(by_gtin, by_sku, captured)


@dataclass(frozen=True)
class MatchResult:
    reference: dict | None
    method: str | None
    quality: str
    ambiguous: bool
    candidate_count: int


def resolve_match(listing: dict, index: ReferenceIndex) -> MatchResult:
    """Resolve a referencia de UM anuncio. Ordem obrigatoria do gate.

    1. marca normalizada + GTIN exato (chave PRIMARIA);
    2. sem match por GTIN, marca normalizada + SKU exato (chave SECUNDARIA);
    3. o SKU secundario so vale se for UNICO dentro da marca;
    4. SKU global nunca — toda chave carrega a marca;
    5. zero fuzzy — somente igualdade exata, nenhuma distancia de texto;
    6. mais de um candidato = ambiguo, e ambiguo NAO cai para a chave seguinte.

    O item 6 e' a razao de `ambiguous` existir separado de `reference is None`:
    quando o GTIN aponta para duas referencias com PDV divergente — a Rituaria
    tem exatamente isso, `7901128300047` com R$ 109,90 e R$ 109,01 — tentar o SKU
    em seguida seria escolher um dos dois por acidente de ordenacao. A autoridade
    de ambiguidade e' a CONTAGEM de candidatos, aqui; `quality_status` gravado no
    snapshot e' o diagnostico do importador, e nao e' consultado nesta decisao,
    para que nao existam dois mecanismos capazes de discordar.
    """
    marca = normalize_brand_key(listing.get("brand"))
    if marca is None:
        return MatchResult(None, MATCH_NONE, QUALITY_UNMATCHED, False, 0)

    gtin = consumer_ean_or_none(listing.get("gtin"))
    if gtin is not None:
        candidatos = index.by_gtin.get((marca, gtin), [])
        if len(candidatos) == 1:
            return MatchResult(candidatos[0], MATCH_GTIN, QUALITY_PRIMARY, False, 1)
        if len(candidatos) > 1:
            return MatchResult(
                None, MATCH_GTIN, QUALITY_AMBIGUOUS, True, len(candidatos)
            )

    sku = normalize_sku_key(listing.get("seller_sku"))
    if sku is not None:
        candidatos = index.by_sku.get((marca, sku), [])
        if len(candidatos) == 1:
            return MatchResult(candidatos[0], MATCH_SKU, QUALITY_SECONDARY, False, 1)
        if len(candidatos) > 1:
            return MatchResult(
                None, MATCH_SKU, QUALITY_AMBIGUOUS, True, len(candidatos)
            )

    return MatchResult(None, MATCH_NONE, QUALITY_UNMATCHED, False, 0)


def _dec(valor: object) -> Decimal | None:
    """Converte para Decimal preservando NULO. NULO NUNCA VIRA ZERO."""
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        if valor.is_nan():
            raise PmaMatchError("valor NaN recusado: NaN nao e' um preco.")
        return valor
    d = Decimal(str(valor))
    if d.is_nan():
        raise PmaMatchError("valor NaN recusado: NaN nao e' um preco.")
    return d


def compare_listing(listing: dict, index: ReferenceIndex, today: date,
                    freshness: str = FRESHNESS_FRESH) -> dict:
    """Uma linha do payload: anuncio + referencia resolvida + diferenca.

    PRECEDENCIA DO STATUS COMERCIAL, do mais fundamental ao mais especifico:
      0. data em D0/futuro   — FAIL-CLOSED: levanta `PmaMatchError`, porque o
         sync proibe contratualmente publicar o dia corrente (F4);
      1. `inactive_listing`   — anuncio nao exibido publicamente; comparar
         geraria ruido sobre uma vitrine que nao existe;
      2. `non_comparable_reference_ambiguous` — referencia nao resolvivel;
      3. `no_reference`       — nenhuma referencia encontrada;
      4. `below_reference` / `at_or_above_reference`.

    Nos casos 1 a 3 os campos de referencia e de diferenca ficam NULOS, nunca
    zero: zero afirmaria "diferenca medida igual a zero", que e' falso.

    FRESCOR NAO ENTRA NESTA PRECEDENCIA  (Gate PMA-H1)
    --------------------------------------------------
    `freshness` chega decidido pelo servico e e' apenas ESTAMPADO na linha, em
    `freshness_status`. Antes, uma observacao anterior a D-1 devolvia
    `comparison_status = stale_observation` e voltava CEDO, apagando a
    classificacao comercial: com o sync atrasado a tela mostrava zero em todos os
    cartoes comerciais, como se nao houvesse desvio. A comparacao de um dia
    anterior continua VALIDA para aquele dia — o que ela nao sustenta e' ser lida
    como "hoje", e isso e' dito no aviso, nao apagando o dado.
    """
    ref_date = listing.get("ref_date")
    if not isinstance(ref_date, date):
        raise PmaMatchError("ref_date ausente ou invalido na observacao.")

    anunciado = _dec(listing.get("advertised_price"))
    if anunciado is None:
        raise PmaMatchError("advertised_price ausente: observacao invalida.")

    situacao = classify_observation_date(ref_date, today)
    if situacao == OBS_INVALID:
        # Fail-closed. Nao existe caminho que apresente D0 como observacao
        # valida: o sync recusa publicar o dia corrente, entao uma linha assim
        # so pode ter vindo de escrita fora do contrato.
        raise PmaMatchError(
            "observacao com data igual ou posterior ao dia operacional corrente: "
            "o sync proibe publicar o dia corrente e o futuro, portanto a camada "
            "de serving esta inconsistente."
        )

    match = resolve_match(listing, index)
    ref = match.reference

    linha = {
        "product_name": None,
        "brand": listing.get("brand"),
        "marketplace": listing.get("marketplace"),
        "item_id": listing.get("item_id"),
        "seller_sku": listing.get("seller_sku"),
        "gtin": listing.get("gtin"),
        "listing_title": listing.get("listing_title"),
        "permalink": listing.get("permalink"),
        "listing_status": listing.get("listing_status"),
        "currency": listing.get("currency"),
        "advertised_price": anunciado,
        "original_price": _dec(listing.get("original_price")),
        "ref_date": ref_date,

        # `observed_at` = instante em que o PRECO foi capturado
        # (`price_captured_at`, de `stg_ml_item_price_history.extracted_at`).
        # NAO usa `stg_ml_items.updated_at`, que descreve o estado CADASTRAL
        # corrente do item e nao a captura do preco historico — era o defeito F2.
        # O timestamp cadastral viaja separado, com nome inequivoco, e nao e'
        # apresentado como horario de preco.
        "observed_at": listing.get("price_captured_at"),
        "listing_metadata_updated_at": listing.get("listing_metadata_updated_at"),

        # `observed_effective_amount = advertised_price` para o ML. E' APROXIMACAO
        # INCOMPLETA e esta declarada como tal: os quatro componentes que
        # faltariam para o preco de checkout ficam NULOS abaixo, nunca zero.
        "observed_effective_amount": anunciado,
        "shipping_amount": None,
        "seller_coupon_amount": None,
        "platform_subsidy_amount": None,
        "checkout_price": None,
        "coverage_status": COVERAGE_STATUS,

        "suggested_retail_amount": None,
        "reference_type": REFERENCE_TYPE,
        "validity_status": VALIDITY_STATUS,
        "policy_status": POLICY_STATUS,
        "reference_captured_at": None,
        "reference_row_id": None,
        "difference_amount": None,
        "difference_pct": None,
        "match_method": match.method,
        "match_quality": match.quality,
        "reference_candidate_count": match.candidate_count,
        "comparison_status": None,
        # Gate PMA-H1: qualidade TRANSVERSAL, na propria linha. Fica ao lado do
        # status comercial em vez de substitui-lo, e permite a UI marcar a linha
        # sem perder o veredito.
        "freshness_status": freshness,
        "limitations": [],
    }

    if ref is not None:
        linha["product_name"] = ref.get("product_name")
        linha["reference_row_id"] = ref.get("reference_row_id")
        linha["reference_captured_at"] = ref.get("captured_at")

    limites = [
        # PMA-1A-R, F8 — direcao INDETERMINADA, nunca "conservadora".
        "Comparacao parcial baseada apenas no preco anunciado do produto; nao "
        "representa o preco final de checkout e sua diferenca pode mudar quando "
        "frete ou cupom forem considerados",
        "referencia e' preco sugerido de revenda (PDV), nao preco minimo "
        "anunciado, e nao tem vigencia declarada",
    ]

    # Gate PMA-H1: a defasagem/retrospectividade entra como LIMITACAO da linha,
    # nunca como status comercial. A classificacao comercial abaixo roda sempre.
    if freshness == FRESHNESS_STALE:
        atraso = lag_days(ref_date, today)
        limites = limites + [
            f"observacao de {ref_date.isoformat()}, {atraso} dia(s) atras de "
            f"{last_eligible_date(today).isoformat()} (D-1 do dia operacional "
            f"{today.isoformat()}): a comparacao vale para aquele dia e NAO "
            f"descreve o preco de hoje. Verifique a ultima execucao do sync."
        ]
    elif freshness == FRESHNESS_HISTORICAL:
        limites = limites + [
            f"consulta retrospectiva: preco anunciado de {ref_date.isoformat()} "
            f"comparado a referencia PDV mais recente disponivel HOJE. A origem "
            f"nao declara a vigencia historica dessa referencia, portanto este "
            f"resultado pode mudar se uma nova referencia for importada."
        ]

    if listing.get("listing_status") != "active":
        linha["comparison_status"] = STATUS_INACTIVE
        linha["limitations"] = limites + [
            "anuncio nao esta ativo: nao ha vitrine publica a comparar"
        ]
        return linha

    if match.ambiguous:
        linha["comparison_status"] = STATUS_AMBIGUOUS
        linha["limitations"] = limites + [
            f"{match.candidate_count} linhas de referencia disputam a mesma chave "
            f"dentro da marca: ambiguidade marcada, nunca resolvida por escolha "
            f"arbitraria. Revisao humana necessaria na tabela de origem"
        ]
        return linha

    if ref is None:
        marca = normalize_brand_key(listing.get("brand"))
        detalhe = (
            f"marca {marca} nao possui tabela de referencia B2B"
            if marca in NO_REFERENCE_BRANDS
            else "nenhuma linha de referencia casou por GTIN nem por SKU unico na marca"
        )
        linha["comparison_status"] = STATUS_NO_REFERENCE
        linha["limitations"] = limites + [detalhe]
        return linha

    sugerido = _dec(ref.get("suggested_retail_amount"))
    if sugerido is None or sugerido == 0:
        linha["comparison_status"] = STATUS_NO_REFERENCE
        linha["limitations"] = limites + [
            "referencia sem valor utilizavel: ausencia nao e' zero"
        ]
        return linha

    linha["suggested_retail_amount"] = sugerido
    diferenca = anunciado - sugerido
    linha["difference_amount"] = diferenca.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Quantizado AQUI, no contrato, e nao na serializacao: uma segunda casa
    # decimal escolhida na borda HTTP divergiria do numero que o teste verifica.
    linha["difference_pct"] = ((diferenca / sugerido) * Decimal("100")).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    linha["comparison_status"] = (
        STATUS_BELOW if anunciado < sugerido else STATUS_AT_OR_ABOVE
    )
    linha["limitations"] = limites
    return linha


def compare_all(listings: list[dict], references: list[dict], today: date,
                freshness: str = FRESHNESS_FRESH) -> list[dict]:
    """Compara o conjunto inteiro. Recusa escala acima do teto, nunca trunca."""
    if len(listings) > MAX_LISTING_ROWS:
        raise PmaMatchError(
            f"{len(listings)} observacoes excedem o teto de {MAX_LISTING_ROWS}: "
            f"o match em memoria foi dimensionado para a escala medida "
            f"(855 anuncios). Recusado em vez de truncado."
        )
    index = ReferenceIndex.build(references)
    return [compare_listing(li, index, today, freshness) for li in listings]


def build_kpis(rows: list[dict]) -> dict:
    """KPIs das linhas comparadas. Cada um com denominador explicito.

    DUAS DIMENSOES INDEPENDENTES  (Gate PMA-H1)
    -------------------------------------------
    Comercial: os cinco contadores de `COMMERCIAL_STATUSES` formam PARTICAO e
    somam exatamente `monitored_count`. `stale_count` saiu dessa soma.

    Qualidade: `fresh_count`/`stale_count`/`historical_count` contam FRESCOR e
    tambem somam `monitored_count`, mas SOBREPOSTOS a particao comercial — uma
    linha `below_reference` num dia atrasado conta nos dois lados. Antes,
    `stale_count` competia com os contadores comerciais e zerava todos eles.
    """
    def n(*status: str) -> int:
        alvo = set(status)
        return sum(1 for r in rows if r["comparison_status"] in alvo)

    def f(*status: str) -> int:
        alvo = set(status)
        return sum(1 for r in rows if r.get("freshness_status") in alvo)

    return {
        "monitored_count": len(rows),
        # comparable_count = below + at_or_above, por definicao.
        "comparable_count": n(STATUS_BELOW, STATUS_AT_OR_ABOVE),
        "below_reference_count": n(STATUS_BELOW),
        "at_or_above_reference_count": n(STATUS_AT_OR_ABOVE),
        "no_reference_count": n(STATUS_NO_REFERENCE),
        "ambiguous_reference_count": n(STATUS_AMBIGUOUS),
        "inactive_count": n(STATUS_INACTIVE),
        # --- qualidade transversal, sobreposta ---
        "fresh_count": f(FRESHNESS_FRESH),
        "stale_count": f(FRESHNESS_STALE),
        "historical_count": f(FRESHNESS_HISTORICAL),
    }


def comparison_basis_text(observed: date | None,
                          reference_captured_at: object | None) -> str | None:
    """A frase OBRIGATORIA que declara as duas datas e a ausencia de vigencia.

    Gate PMA-H1, Fase B0. Texto fixo, sem vocabulario de politica e sem afirmar
    que a referencia valia na data observada. `None` quando falta uma das duas
    datas — sem data nao existe afirmacao a fazer.
    """
    if observed is None or reference_captured_at is None:
        return None
    cap = reference_captured_at
    if isinstance(cap, str):
        # `captured_at` chega como `datetime` do Postgres, mas tambem como
        # string ISO quando o valor ja passou por uma camada de serializacao.
        # Aceitar as duas formas evita que a frase obrigatoria desapareca por
        # um detalhe de tipo — o texto e' contrato, nao enfeite.
        try:
            cap = datetime.fromisoformat(cap)
        except ValueError:
            return None
    if isinstance(cap, datetime):
        if cap.tzinfo is not None:
            cap = cap.astimezone(ZoneInfo(TIMEZONE_NAME))
        cap = cap.date()
    elif not isinstance(cap, date):
        return None
    return (
        f"Preco anunciado em {observed.strftime('%d/%m/%Y')} comparado a "
        f"referencia sugerida ao consumidor (PDV) capturada em "
        f"{cap.strftime('%d/%m/%Y')}. A origem nao declara a vigencia historica "
        f"dessa referencia."
    )


def build_warnings(rows: list[dict], today: date,
                   *, freshness: str = FRESHNESS_FRESH,
                   observed: date | None = None,
                   reference_captured_at: object | None = None) -> list[str]:
    """Avisos de escopo e cobertura. Texto observacional, sem vocabulario de politica."""
    avisos = []
    base = comparison_basis_text(observed, reference_captured_at)
    if base:
        # PRIMEIRO aviso: e' a base da leitura, nao uma nota de pe de pagina.
        avisos.append(base)
    avisos += [
        "MVP observacional: compara preco anunciado das lojas PROPRIAS contra o "
        "preco sugerido de revenda (PDV) das tabelas B2B. Nao e' fiscalizacao de "
        "revendedor e nao aplica politica de preco.",
        "A referencia e' preco sugerido de revenda (PDV), medido como markup "
        "aritmetico sobre o preco de atacado, com razao que varia por marca. Nao "
        "e' preco minimo anunciado.",
        # Gate PMA-H1: a consulta retrospectiva passou a EXISTIR, com contrato
        # explicito. O que continua verdade e' que a referencia nao tem vigencia
        # — logo o resultado historico e' "preco daquele dia contra a referencia
        # de hoje", e nunca "a referencia valia naquele dia".
        "Sem vigencia declarada na origem (validity_status=missing): a unica nocao "
        "de tempo da referencia e' a data de captura do snapshot, e ela NAO e' "
        "vigencia. Numa data historica, a comparacao usa a referencia mais "
        "recente disponivel hoje (reference_basis=latest_available_snapshot) e "
        "pode mudar se uma nova referencia PDV for importada.",
        # PMA-1A-R, F8 — a direcao liquida e' indeterminada, nao conservadora.
        "Cobertura advertised_only: sem frete, cupom de vitrine, subsidio de "
        "plataforma ou preco de checkout. Esses campos vem nulos, nunca zero. "
        "Como o preco de checkout se compoe de produto + frete - cupom, e o frete "
        "eleva enquanto o cupom reduz, a direcao liquida do desvio e' "
        "INDETERMINADA: a diferenca pode mudar de valor e de sinal quando esses "
        "componentes forem considerados.",
        "Sem limiar comercial aprovado nao ha severidade: os unicos fatos sao "
        "difference_amount e difference_pct.",
        "Frescor e' qualidade TRANSVERSAL, nao categoria comercial: uma "
        "observacao atrasada ou historica continua classificada em abaixo/ "
        "no-ou-acima/sem-referencia/inativo, e o atraso viaja em "
        "freshness_status e lag_days.",
    ]
    if NO_REFERENCE_BRANDS:
        avisos.append(
            "Marcas monitoradas sem tabela de referencia B2B (no_reference): "
            + ", ".join(NO_REFERENCE_BRANDS)
        )
    if OUT_OF_SCOPE_BRANDS:
        avisos.append(
            f"Marcas com referencia B2B mas sem catalogo proprio no Mercado Livre "
            f"({BRAND_SCOPE_OUT_OF_SCOPE}), fora do escopo desta tela: "
            + ", ".join(OUT_OF_SCOPE_BRANDS)
        )
    atraso = lag_days(observed, today)
    if freshness == FRESHNESS_STALE and atraso:
        avisos.append(
            f"DEFASAGEM: a observacao mais recente disponivel e' de "
            f"{observed.isoformat()}, {atraso} dia(s) atras de "
            f"{last_eligible_date(today).isoformat()} (D-1 do dia operacional "
            f"{today.isoformat()}). As comparacoes abaixo valem para aquele dia "
            f"e NAO descrevem o preco de hoje. Verifique a ultima execucao do "
            f"sync de precos."
        )
    elif freshness == FRESHNESS_HISTORICAL:
        avisos.append(
            f"CONSULTA RETROSPECTIVA: data observada {observed.isoformat()} "
            f"escolhida deliberadamente. Nao e' defasagem do pipeline."
        )
    elif freshness == FRESHNESS_UNAVAILABLE:
        avisos.append(
            "Nao existe observacao materializada para a data pedida: nenhum "
            "numero e' apresentado. Ausencia nao e' zero."
        )
    return avisos
