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
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

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

# --- status de comparacao -------------------------------------------------
STATUS_BELOW = "below_reference"
STATUS_AT_OR_ABOVE = "at_or_above_reference"
STATUS_NO_REFERENCE = "no_reference"
STATUS_AMBIGUOUS = "non_comparable_reference_ambiguous"
STATUS_INACTIVE = "inactive_listing"
STATUS_STALE = "stale_observation"

COMPARISON_STATUSES = (
    STATUS_BELOW, STATUS_AT_OR_ABOVE, STATUS_NO_REFERENCE,
    STATUS_AMBIGUOUS, STATUS_INACTIVE, STATUS_STALE,
)

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


def compare_listing(listing: dict, index: ReferenceIndex, today: date) -> dict:
    """Uma linha do payload: anuncio + referencia resolvida + diferenca.

    PRECEDENCIA DOS STATUS, do mais fundamental ao mais especifico:
      0. data em D0/futuro   — FAIL-CLOSED: levanta `PmaMatchError`, porque o
         sync proibe contratualmente publicar o dia corrente (F4);
      1. `stale_observation`  — observacao anterior a D-1; nenhum veredito de
         preco pode ser apresentado como fato sobre ela;
      2. `inactive_listing`   — anuncio nao exibido publicamente; comparar
         geraria ruido sobre uma vitrine que nao existe;
      3. `non_comparable_reference_ambiguous` — referencia nao resolvivel;
      4. `no_reference`       — nenhuma referencia encontrada;
      5. `below_reference` / `at_or_above_reference`.

    Nos casos 1 a 4 os campos de referencia e de diferenca ficam NULOS, nunca
    zero: zero afirmaria "diferenca medida igual a zero", que e' falso.
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

    if situacao == OBS_STALE:
        linha["comparison_status"] = STATUS_STALE
        linha["limitations"] = limites + [
            f"observacao de {ref_date.isoformat()} anterior a "
            f"{last_eligible_date(today).isoformat()} (D-1 do dia operacional "
            f"{today.isoformat()}): nenhum veredito de preco e' afirmado"
        ]
        return linha

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


def compare_all(listings: list[dict], references: list[dict], today: date) -> list[dict]:
    """Compara o conjunto inteiro. Recusa escala acima do teto, nunca trunca."""
    if len(listings) > MAX_LISTING_ROWS:
        raise PmaMatchError(
            f"{len(listings)} observacoes excedem o teto de {MAX_LISTING_ROWS}: "
            f"o match em memoria foi dimensionado para a escala medida "
            f"(855 anuncios). Recusado em vez de truncado."
        )
    index = ReferenceIndex.build(references)
    return [compare_listing(li, index, today) for li in listings]


def build_kpis(rows: list[dict]) -> dict:
    """KPIs a partir das linhas ja comparadas. Cada um com denominador explicito.

    `monitored_count` conta TODAS as observacoes da janela; `comparable_count`
    conta somente as que produziram veredito. A soma dos demais nao e' obrigada a
    fechar com `monitored_count` por acidente: ela fecha por construcao, e o
    teste verifica.
    """
    def n(*status: str) -> int:
        alvo = set(status)
        return sum(1 for r in rows if r["comparison_status"] in alvo)

    comparaveis = n(STATUS_BELOW, STATUS_AT_OR_ABOVE)
    return {
        "monitored_count": len(rows),
        "comparable_count": comparaveis,
        "below_reference_count": n(STATUS_BELOW),
        "at_or_above_reference_count": n(STATUS_AT_OR_ABOVE),
        "no_reference_count": n(STATUS_NO_REFERENCE),
        "ambiguous_reference_count": n(STATUS_AMBIGUOUS),
        "stale_count": n(STATUS_STALE),
        "inactive_count": n(STATUS_INACTIVE),
    }


def build_warnings(rows: list[dict], today: date) -> list[str]:
    """Avisos de escopo e cobertura. Texto observacional, sem vocabulario de politica."""
    avisos = [
        "MVP observacional: compara preco anunciado das lojas PROPRIAS contra o "
        "preco sugerido de revenda (PDV) das tabelas B2B. Nao e' fiscalizacao de "
        "revendedor e nao aplica politica de preco.",
        "A referencia e' preco sugerido de revenda (PDV), medido como markup "
        "aritmetico sobre o preco de atacado, com razao que varia por marca. Nao "
        "e' preco minimo anunciado.",
        "Sem vigencia declarada na origem (validity_status=missing): a unica nocao "
        "de tempo da referencia e' a data de captura do snapshot, e ela NAO e' "
        "vigencia. Por isso a comparacao roda somente sobre a ultima observacao "
        "elegivel, nunca retrospectivamente.",
        # PMA-1A-R, F8 — a direcao liquida e' indeterminada, nao conservadora.
        "Cobertura advertised_only: sem frete, cupom de vitrine, subsidio de "
        "plataforma ou preco de checkout. Esses campos vem nulos, nunca zero. "
        "Como o preco de checkout se compoe de produto + frete - cupom, e o frete "
        "eleva enquanto o cupom reduz, a direcao liquida do desvio e' "
        "INDETERMINADA: a diferenca pode mudar de valor e de sinal quando esses "
        "componentes forem considerados.",
        "Sem limiar comercial aprovado nao ha severidade: os unicos fatos sao "
        "difference_amount e difference_pct.",
        "Somente observacoes de D-1 (America/Sao_Paulo) sustentam comparacao; "
        "datas anteriores aparecem como stale_observation.",
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
    vencidas = sum(1 for r in rows if r["comparison_status"] == STATUS_STALE)
    if vencidas:
        avisos.append(
            f"{vencidas} observacoes anteriores a "
            f"{last_eligible_date(today).isoformat()} (D-1 do dia operacional "
            f"{today.isoformat()}): verifique a ultima execucao do sync antes de "
            f"ler os numeros."
        )
    return avisos
