"""Gate PMA-1A / PMA-1A-R — servico read-only do monitoramento de precos proprios.

LE EXCLUSIVAMENTE `marts.*` NO NEON
-----------------------------------
Nenhuma consulta deste modulo menciona `gold.`, `raw.` ou `silver.`. O backend no
Render nao pode consultar o Data Mart, e a travessia Data Mart -> Neon acontece
somente no CLI `pipelines/sync_ml_listing_price_serving.py`.

SOMENTE LEITURA
---------------
Nao existe INSERT, UPDATE, DELETE, CREATE nem TRUNCATE neste modulo.

DOIS MODOS: `latest` e `selected_date`  (Gate PMA-H1)
----------------------------------------------------
O PMA-1A-R removeu `ref_date` porque ele casava um preco antigo com a referencia
de hoje SEM DIZER isso — devolvia um numero com aparencia de conclusao historica
que a fonte nao sustenta. O problema nunca foi consultar o passado: foi consultar
o passado sem declarar a base da comparacao.

Agora existe `observed_date=YYYY-MM-DD`, e o contrato e' EXPLICITO:

  - o preco anunciado consultado e' exatamente o daquele dia;
  - a referencia e' o snapshot PDV mais recente disponivel HOJE
    (`reference_basis = latest_available_snapshot`);
  - a resposta NAO afirma que essa referencia valia naquele dia
    (`validity_status = missing`), e diz isso no primeiro aviso e por linha;
  - o resultado pode MUDAR se uma nova referencia PDV for importada.

`ref_date` continua recusado com 422, agora por AMBIGUIDADE: o nome nao separa a
data observada da data de captura da referencia, e as duas viajam no payload.
`captured_at` continua NAO sendo vigencia e nao e' usado como tal.

FRESCOR E' QUALIDADE TRANSVERSAL, NAO CATEGORIA COMERCIAL  (Gate PMA-H1)
------------------------------------------------------------------------
`stale_observation` era um `comparison_status` e SUBSTITUIA a classificacao
comercial. Com o sync atrasado — situacao real, medida com o Data Mart fora do ar
— toda linha virava `stale_observation`, os cinco cartoes comerciais iam a zero e
a tela aparentava "nenhum desvio de preco" quando o que havia era atraso de
pipeline. Agora sao duas dimensoes ortogonais: `comparison_status` (particao
comercial que fecha em `monitored_count`) e `freshness_status`
(fresh/stale/historical/unavailable), sobreposto. `stale_observation` sobrevive
apenas como alias depreciado no filtro `status`, para nao quebrar link antigo.

A COMPARACAO NAO E' MATERIALIZADA
---------------------------------
O match roda em tempo de consulta, em `pma_match`, sobre as duas tabelas de
`marts`. A escala medida — 855 anuncios/dia e 221 referencias — cabe
folgadamente; `pma_match` recusa alto acima do teto em vez de truncar.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.services import pma_domain as dom
from app.services import pma_match as pm

TZ = ZoneInfo(pm.TIMEZONE_NAME)

LISTING_TABLE = "marts.fact_marketplace_listing_price_daily"
REFERENCE_TABLE = "marts.fact_suggested_price_reference_snapshot"

MAX_LIMIT = 500
DEFAULT_LIMIT = 100

#: Tetos de tamanho na borda do servico. A borda FastAPI tambem os aplica; aqui
#: existem para que uma chamada interna nao escape da regra.
MAX_BRAND_PARAM_CHARS = 120
MAX_STATUS_PARAM_CHARS = 240
MAX_PRODUCT_QUERY_CHARS = 120

#: Gate PMA-2C4A — tetos e recusas dos filtros NOVOS. Mensagens FIXAS, sem eco.
MAX_ACCOUNT_PARAM_CHARS = 120
MAX_PRODUCT_TYPE_PARAM_CHARS = 120

#: Conta de loja aceita letras, digitos, hifen e sublinhado. O formato e'
#: validado, mas nao ha allowlist fixa: contas sao cadastro da origem e mudam
#: sem release da API. O valor viaja por PARAMETRO ate o driver.
_ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

ERRO_ACCOUNT_INVALIDA = (
    "parametro shop_account invalido. Aceita 'all', ou contas separadas por "
    "virgula usando apenas letras minusculas, digitos, hifen e sublinhado. "
    "As contas presentes na fotografia vem em meta.account_clocks."
)
ERRO_ACCOUNT_TAMANHO = (
    f"parametro shop_account excede o tamanho maximo de "
    f"{MAX_ACCOUNT_PARAM_CHARS} caracteres."
)
ERRO_PRODUCT_TYPE_INVALIDO = (
    "parametro product_type invalido. Aceita 'all', ou valores separados por "
    "virgula entre: kit_confirmed, kit_suspected, no_kit_signal, "
    "product_type_unknown."
)
#: O ML nao tem conta de loja nem `product_type` materializado. Ignorar o
#: filtro devolveria um numero certo sob uma pergunta errada — a mesma falha
#: que `ref_date` tinha antes de virar 422.
ERRO_ACCOUNT_NAO_SUPORTADO = (
    "parametro shop_account nao se aplica ao Mercado Livre: a fato dele nao "
    "modela conta de loja. Use-o com marketplace=shopee ou marketplace=tiktok."
)
ERRO_PRODUCT_TYPE_NAO_SUPORTADO = (
    "parametro product_type nao se aplica ao Mercado Livre: o tipo de produto "
    "dele e' derivado em tempo de consulta, nao materializado. Use-o com "
    "marketplace=shopee ou marketplace=tiktok."
)
ERRO_PRODUCT_TYPE_TAMANHO = (
    f"parametro product_type excede o tamanho maximo de "
    f"{MAX_PRODUCT_TYPE_PARAM_CHARS} caracteres."
)
#: A recusa de data dos canais nomeia o teto CERTO. A mensagem do ML continua
#: intacta, palavra por palavra: ela e' contrato publicado.
ERRO_OBSERVED_DATE_FUTURA_SNAPSHOT = (
    "parametro observed_date fora do intervalo aceito: o teto e' o dia "
    "operacional corrente em America/Sao_Paulo. O futuro nao e' observavel. "
    "Veja meta.eligible_ref_date."
)

#: Rotulo da fotografia do proprio dia. Ela NAO e' periodo fechado, definitivo
#: nem completo: a conta pode recarregar e reescrever o escopo antes de o dia
#: virar. O nome diz isso para que a tela nao prometa conclusao.
SNAPSHOT_MUTABLE = "mutable_operational_snapshot"
SNAPSHOT_SETTLED = "settled_snapshot"

AVISO_SNAPSHOT_MUTAVEL = (
    "fotografia do dia corrente: e' o estado operacional observado ate o "
    "watermark de cada conta e AINDA PODE MUDAR hoje se a origem recarregar. "
    "Nao e' periodo fechado nem contagem definitiva do dia."
)

_ORDER_BY = "difference_pct"

# ---------------------------------------------------------------------------
# Mensagens FIXAS de recusa — NUNCA ecoam a entrada  (PMA-1A-R, F6)
# ---------------------------------------------------------------------------
# A versao anterior interpolava o valor recusado ("marca(s) invalida(s): {x}").
# Isso devolvia ao cliente o texto que ele mandou — HTML, quebra de linha, DSN
# falso, IP, payload longo — dentro de uma resposta de erro. Agora cada recusa
# tem uma mensagem constante que descreve o CONTRATO, lista o que E' aceito, e
# nao reflete um caractere do que veio.

ERRO_MARKETPLACE = (
    "marketplace nao reconhecido. Os canais do dominio sao 'ml', 'shopee' e "
    "'tiktok'. Um canal reconhecido cuja publicacao ainda nao foi liberada "
    "responde 200 com availability=unavailable, nao 422."
)

#: Aviso do envelope indisponivel. Diz o que NAO foi feito, para que a
#: ausencia de linhas nunca seja lida como "nenhum desvio encontrado".
AVISO_CANAL_INDISPONIVEL = (
    "Canal reconhecido, porem ainda nao publicado: nenhuma observacao foi "
    "consultada. Contagens zeradas significam ausencia de fonte, nao ausencia "
    "de desvio; coverage_rate vem nulo justamente para nao afirmar 0%."
)

ERRO_BRAND_INVALIDA = (
    "parametro brand invalido. Aceita 'all', ou marcas separadas por virgula "
    "entre: barbours, kokeshi, lescent, rituaria. As marcas apice e yenzah tem "
    "tabela de referencia B2B mas nao tem catalogo proprio no Mercado Livre "
    "(out_of_scope_no_ml_catalog), portanto nao ha anuncio a monitorar."
)
ERRO_BRAND_TAMANHO = (
    "parametro brand excede o tamanho maximo aceito."
)
ERRO_STATUS_INVALIDO = (
    "parametro status invalido. Aceita 'all', ou status separados por virgula "
    "entre: below_reference, at_or_above_reference, no_reference, "
    "non_comparable_reference_ambiguous, inactive_listing, stale_observation."
)
ERRO_STATUS_TAMANHO = (
    "parametro status excede o tamanho maximo aceito."
)
ERRO_PRODUCT_QUERY_TAMANHO = (
    "parametro product_query excede o tamanho maximo aceito."
)
ERRO_PAGINACAO_LIMIT = (
    f"parametro limit fora do intervalo aceito (1 a {MAX_LIMIT})."
)
ERRO_PAGINACAO_OFFSET = (
    "parametro offset invalido: precisa ser maior ou igual a zero."
)
#: PMA-1B + Gate PMA-H1 — `ref_date` RECUSADO, nunca ignorado.
#:
#: O FastAPI IGNORA query parameters desconhecidos por padrao: `?ref_date=...`
#: respondia 200 e fazia o consumidor crer que o filtro tinha sido aplicado.
#: Ignorar em silencio e' pior que nao existir — devolve um numero certo sob uma
#: pergunta errada. Continua 422, com mensagem fixa e sem ecoar o valor.
#:
#: A RAZAO da recusa mudou: nao e' mais "historico nao existe" (existe, via
#: `observed_date`), e' AMBIGUIDADE. O nome nao diz se se refere a data OBSERVADA
#: do preco ou a data de CAPTURA da referencia, e as duas viajam no payload.
ERRO_REF_DATE_NAO_SUPORTADO = (
    "parametro ref_date nao e' suportado porque e' ambiguo: nao distingue a data "
    "OBSERVADA do preco anunciado da data de CAPTURA da referencia PDV, e as duas "
    "existem nesta resposta. Use observed_date=YYYY-MM-DD para escolher o dia da "
    "observacao; a data efetivamente usada vem em meta.observed_ref_date e a da "
    "referencia em meta.reference_captured_at."
)

#: Gate PMA-H1 — recusas do novo parametro. Mensagens FIXAS: nenhuma ecoa o
#: valor recebido, nem quando ele e' HTML, DSN falso ou payload longo.
MAX_OBSERVED_DATE_CHARS = 10

ERRO_OBSERVED_DATE_FORMATO = (
    "parametro observed_date invalido: use exatamente o formato YYYY-MM-DD "
    "(exemplo: 2026-09-07). As datas disponiveis vem em "
    "meta.available_observed_dates."
)
ERRO_OBSERVED_DATE_FUTURA = (
    "parametro observed_date fora do intervalo aceito: o teto e' D-1 no fuso "
    "America/Sao_Paulo. O dia corrente e o futuro nao sao publicaveis pelo sync, "
    "portanto nao podem ser consultados. Veja meta.eligible_ref_date."
)

#: Teto defensivo da lista de datas disponiveis. Existe para que o payload nao
#: cresca sem limite conforme o historico acumula: com uma observacao por dia,
#: 180 entradas cobrem ~6 meses, muito acima da janela de uso da tela. Nao e'
#: calendario sintetico — somente datas MATERIALIZADAS entram.
MAX_AVAILABLE_DATES = 180


class MonitoramentoPrecoError(ValueError):
    """Recusa de CONTRATO na borda — erro do cliente, mensagem fixa.

    Distinta de `pma_match.PmaMatchError`, que sinaliza inconsistencia da nossa
    propria camada de dados e nao e' recuperavel mudando a requisicao.
    """


# ---------------------------------------------------------------------------
# Consultas — texto fixo, valores sempre por parametro nomeado
# ---------------------------------------------------------------------------

SQL_LATEST_REF_DATE = f"""
SELECT max(ref_date) AS ref_date
  FROM {LISTING_TABLE}
 WHERE marketplace = :marketplace
"""

SQL_LAST_SYNCED_AT = f"""
SELECT max(synced_at) AS synced_at
  FROM {LISTING_TABLE}
 WHERE marketplace = :marketplace
   AND ref_date = :ref_date
"""

SQL_LATEST_SNAPSHOT = f"""
SELECT snapshot_id, max(captured_at) AS captured_at
  FROM {REFERENCE_TABLE}
 GROUP BY snapshot_id
 ORDER BY max(captured_at) DESC
 LIMIT 1
"""

SQL_LISTINGS = f"""
SELECT ref_date, marketplace, brand, item_id,
       seller_sku, gtin, listing_title, permalink,
       advertised_price, original_price, currency, listing_status,
       catalog_listing, price_captured_at, listing_metadata_updated_at,
       synced_at
  FROM {LISTING_TABLE}
 WHERE marketplace = :marketplace
   AND ref_date = :ref_date
   AND (:brand_filter = FALSE OR brand = ANY(:brands))
   AND (:has_query = FALSE OR (
            listing_title ILIKE :query_like
         OR coalesce(seller_sku, '') ILIKE :query_like
         OR coalesce(gtin, '') ILIKE :query_like
         OR item_id ILIKE :query_like
   ))
 ORDER BY brand, item_id
"""

SQL_REFERENCES = f"""
SELECT brand, reference_row_id, source_sku, source_gtin, product_name,
       wholesale_amount, suggested_retail_amount,
       reference_type, validity_status, quality_status, captured_at
  FROM {REFERENCE_TABLE}
 WHERE snapshot_id = :snapshot_id
"""

#: Gate PMA-H1 — datas OBSERVADAS materializadas, ate D-1, mais recentes
#: primeiro. Sem `generate_series`, sem preencher buraco: se um dia nao foi
#: sincronizado, ele NAO aparece, e a UI nao pode oferecer o que nao existe.
SQL_AVAILABLE_OBSERVED_DATES = f"""
SELECT DISTINCT ref_date
  FROM {LISTING_TABLE}
 WHERE marketplace = :marketplace
   AND ref_date <= :eligible
 ORDER BY ref_date DESC
 LIMIT :max_dates
"""

#: Existencia de UMA data especifica. Separado da lista porque a lista tem teto:
#: uma data valida mais antiga que o teto ainda deve ser consultavel.
SQL_OBSERVED_DATE_EXISTS = f"""
SELECT 1 AS existe
  FROM {LISTING_TABLE}
 WHERE marketplace = :marketplace
   AND ref_date = :ref_date
 LIMIT 1
"""


# ---------------------------------------------------------------------------
# Gate PMA-2C4A — fonte fisica dos canais Shopee e TikTok
# ---------------------------------------------------------------------------
#: A fato publicada pelo `channel_offer_publisher` (migration 017). E' a UNICA
#: tabela consultada para `shopee` e `tiktok`; o Mercado Livre nunca a toca, e
#: nenhum canal cai para a tabela do outro. Um fallback silencioso entre elas
#: responderia sobre um canal a pergunta feita sobre outro.
CHANNEL_TABLE = "marts.fact_channel_offer_observation"

#: Colunas lidas, NOMEADAS uma a uma. Sem `SELECT *`: uma coluna nova na fato
#: nao deve vazar para o payload sem decisao. A fato nao tem comprador, pedido,
#: CPF, endereco nem telefone — as unicas colunas textuais livres sao titulo do
#: anuncio, SKU do vendedor e conta de loja.
SQL_CHANNEL_LATEST_OBSERVED_DATE = f"""
SELECT max(observed_date) AS observed_date
  FROM {CHANNEL_TABLE}
 WHERE marketplace = :marketplace
   AND observed_date <= :ceiling
"""

SQL_CHANNEL_AVAILABLE_DATES = f"""
SELECT DISTINCT observed_date
  FROM {CHANNEL_TABLE}
 WHERE marketplace = :marketplace
   AND observed_date <= :ceiling
 ORDER BY observed_date DESC
 LIMIT :max_dates
"""

SQL_CHANNEL_DATE_EXISTS = f"""
SELECT 1 AS existe
  FROM {CHANNEL_TABLE}
 WHERE marketplace = :marketplace
   AND observed_date = :observed_date
 LIMIT 1
"""

#: Ordenacao por `offer_key` dentro da marca. `offer_key` e' unico por
#: (observed_date, marketplace) — e' a PK da fato —, portanto a ordenacao e'
#: TOTAL e a paginacao nunca repete nem perde linha entre paginas.
SQL_CHANNEL_OFFERS = f"""
SELECT observed_date, marketplace, offer_key, parent_item_id, model_id,
       brand, shop_account, seller_sku, gtin, listing_title,
       observation_mode, observed_at, snapshot_status, account_watermark_at,
       is_active, product_type, product_type_source,
       observed_price, observed_price_source, list_price,
       promo_context, promo_id, promo_discount_pct, business_scope,
       batch_id, source_run_id, synced_at
  FROM {CHANNEL_TABLE}
 WHERE marketplace = :marketplace
   AND observed_date = :observed_date
   AND (:brand_filter = FALSE OR brand = ANY(:brands))
   AND (:account_filter = FALSE OR shop_account = ANY(:accounts))
   AND (:product_type_filter = FALSE OR product_type = ANY(:product_types))
   AND (:has_query = FALSE OR (
            coalesce(listing_title, '') ILIKE :query_like
         OR coalesce(seller_sku, '') ILIKE :query_like
         OR coalesce(gtin, '') ILIKE :query_like
         OR offer_key ILIKE :query_like
   ))
 ORDER BY brand, offer_key
"""

#: Um relogio por CONTA. A Shopee carrega em quatro lotes distintos e um MAX()
#: global marcaria as tres primeiras contas inteiras como atrasadas.
SQL_CHANNEL_ACCOUNT_CLOCKS = f"""
SELECT shop_account,
       max(account_watermark_at) AS account_watermark_at,
       max(observed_at)          AS observed_at,
       max(synced_at)            AS refreshed_at,
       count(*)                  AS offers,
       count(*) FILTER (WHERE snapshot_status = :status_current) AS current_offers,
       count(*) FILTER (WHERE snapshot_status = :status_stale)   AS stale_offers
  FROM {CHANNEL_TABLE}
 WHERE marketplace = :marketplace
   AND observed_date = :observed_date
 GROUP BY shop_account
 ORDER BY shop_account
"""

#: Todo o texto de consulta deste modulo. Os testes de contrato varrem esta
#: tupla — nao um regex sobre o arquivo —, de modo que uma consulta nova nao
#: escapa da varredura por ficar fora do padrao textual.
ALL_QUERIES = (
    SQL_LATEST_REF_DATE, SQL_LAST_SYNCED_AT, SQL_LATEST_SNAPSHOT,
    SQL_LISTINGS, SQL_REFERENCES,
    SQL_AVAILABLE_OBSERVED_DATES, SQL_OBSERVED_DATE_EXISTS,
    # Gate PMA-2C4A — as consultas dos canais entram na MESMA varredura.
    SQL_CHANNEL_LATEST_OBSERVED_DATE, SQL_CHANNEL_AVAILABLE_DATES,
    SQL_CHANNEL_DATE_EXISTS, SQL_CHANNEL_OFFERS, SQL_CHANNEL_ACCOUNT_CLOCKS,
)

#: Gate PMA-H1 — os dois modos publicos.
MODE_LATEST = "latest"
MODE_SELECTED = "selected_date"

#: Base da referencia. Literal e' contrato: declara que a comparacao usa o
#: snapshot mais recente disponivel, nunca um snapshot "vigente na data".
REFERENCE_BASIS = "latest_available_snapshot"

#: Formato aceito em `observed_date`. Estrito de proposito: `date.fromisoformat`
#: no Python 3.11+ aceita variantes como "20260907", e aceitar mais de uma forma
#: faria a mesma consulta ter duas grafias na URL e no cache.
_OBSERVED_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today_operacional(agora: datetime | None = None) -> date:
    """Dia corrente em America/Sao_Paulo, nao no fuso do processo."""
    return (agora or datetime.now(timezone.utc)).astimezone(TZ).date()


def normalize_marketplace(valor: str | None) -> str:
    """Valida o canal na borda. Nao ecoa a entrada.

    Gate PMA-2C1A: o dominio passou a conhecer `shopee` e `tiktok`. Conhecer
    NAO e' servir — um canal conhecido cuja flag esteja desligada devolve 200
    com estado `unavailable` estruturado, e nao 422. A distincao importa: 422
    diz "voce pediu algo que nao existe" e 200+unavailable diz "existe, mas
    ainda nao esta publicado". Um canal desconhecido continua 422.
    """
    if valor is None or valor == "":
        return pm.MARKETPLACE_ML
    escolhido = str(valor).strip().lower()
    if escolhido not in dom.ALL_MARKETPLACES:
        raise MonitoramentoPrecoError(ERRO_MARKETPLACE)
    return escolhido


def channel_enabled(marketplace: str) -> bool:
    """Flags do PMA-2C1A. O ML nao tem flag: ja' esta publicado."""
    if marketplace == pm.MARKETPLACE_ML:
        return True
    if marketplace == dom.MARKETPLACE_SHOPEE:
        return bool(settings.pma_shopee_enabled)
    if marketplace == dom.MARKETPLACE_TIKTOK:
        return bool(settings.pma_tiktok_enabled)
    return False


def active_metric_version(marketplace: str) -> str:
    """Versao logica da metrica em vigor para o canal.

    O ML publicado e' `v1_all_active` e so' muda por decisao explicita. Os
    canais novos ja' nascem em `v2_product_type_aware`: nao ha metrica
    publicada deles para preservar.
    """
    if marketplace == pm.MARKETPLACE_ML:
        return (dom.METRIC_VERSION_V2 if settings.pma_ml_metric_v2_enabled
                else dom.METRIC_VERSION_V1)
    return dom.METRIC_VERSION_V2


def unavailable_response(marketplace: str) -> dict:
    """Envelope publico de canal desligado. Nao recebe sessao de banco.

    Existe como funcao PUBLICA para que a rota possa responder antes de exigir
    a dependencia de banco: um canal desligado nao precisa de conexao, e fazer
    `_require_db` primeiro transformaria o estado `unavailable` num 503.
    """
    return _unavailable_envelope(marketplace, dom.UNAVAILABLE_CHANNEL_DISABLED)


def _metrics_zeradas() -> dict:
    """Bloco `metrics` sem nenhuma observacao.

    As CONTAGENS sao zero — e' verdade, nao ha linha alguma. As TAXAS sao None:
    `coverage_rate = 0.0` afirmaria "medimos a cobertura e ela e' 0%", que e'
    falso quando nada foi medido.
    """
    return {
        "monitored_offers": 0, "active_offers": 0, "inactive_offers": 0,
        "kit_confirmed": 0, "kit_suspected": 0, "no_kit_signal": 0,
        "product_type_unknown": 0, "eligible_offers": 0, "comparable_offers": 0,
        "below_reference": 0, "at_or_above_reference": 0,
        "non_comparable_reasons": {r: 0 for r in dom.NON_COMPARABLE_REASONS},
        "coverage_rate": None, "distinct_b2b_products": 0, "b2b_reach": None,
    }


def _metrics_do_ml(comparadas: list[dict], contagem_tipos: dict,
                   versao: str, tipo_por_linha: dict) -> dict:
    """Traduz a particao COMERCIAL do ML para o vocabulario multicanal.

    Reusa as linhas ja' comparadas por `pma_match` — nao ha segundo matcher que
    possa divergir do primeiro.

    NUMERADOR E DENOMINADOR VEM DA MESMA VERSAO
    -------------------------------------------
    Este e' o ponto delicado. Em `v1_all_active` toda oferta ativa e' elegivel e
    os comparaveis sao os 139 publicados. Em `v2_product_type_aware` os kits
    saem do denominador (339) E do numerador (135). Misturar os dois — 139 sobre
    339 — produziria 41%, uma taxa que nao pertence a nenhuma das versoes e que
    ninguem conseguiria reconciliar depois.
    """
    ativos = [r for r in comparadas if r["comparison_status"] != pm.STATUS_INACTIVE]
    inativos = len(comparadas) - len(ativos)

    if versao == dom.METRIC_VERSION_V2:
        elegiveis_linhas = [
            r for r in ativos
            if not dom.is_excluded_from_comparison(
                tipo_por_linha.get(id(r), dom.PRODUCT_TYPE_UNKNOWN))
        ]
        elegiveis = dom.eligible_offers(
            active=len(ativos),
            kit_confirmed=contagem_tipos[dom.PRODUCT_KIT_CONFIRMED],
            kit_suspected=contagem_tipos[dom.PRODUCT_KIT_SUSPECTED],
        )
    else:
        # v1 publicado: nenhuma exclusao por tipo de produto.
        elegiveis_linhas = ativos
        elegiveis = len(ativos)

    abaixo = sum(1 for r in elegiveis_linhas
                 if r["comparison_status"] == pm.STATUS_BELOW)
    acima = sum(1 for r in elegiveis_linhas
                if r["comparison_status"] == pm.STATUS_AT_OR_ABOVE)
    ambiguas = sum(1 for r in elegiveis_linhas
                   if r["comparison_status"] == pm.STATUS_AMBIGUOUS)
    sem_ref = sum(1 for r in elegiveis_linhas
                  if r["comparison_status"] == pm.STATUS_NO_REFERENCE)
    referencias = {r.get("reference_row_id") for r in elegiveis_linhas
                   if r.get("reference_row_id") is not None}
    return {
        "monitored_offers": len(comparadas),
        "active_offers": len(ativos),
        "inactive_offers": inativos,
        "kit_confirmed": contagem_tipos[dom.PRODUCT_KIT_CONFIRMED],
        "kit_suspected": contagem_tipos[dom.PRODUCT_KIT_SUSPECTED],
        "no_kit_signal": contagem_tipos[dom.PRODUCT_NO_KIT_SIGNAL],
        "product_type_unknown": contagem_tipos[dom.PRODUCT_TYPE_UNKNOWN],
        "eligible_offers": elegiveis,
        "comparable_offers": abaixo + acima,
        "below_reference": abaixo,
        "at_or_above_reference": acima,
        "non_comparable_reasons": {
            dom.REASON_REFERENCE_MISSING: sem_ref,
            dom.REASON_AMBIGUOUS: ambiguas,
        },
        "coverage_rate": dom.coverage_rate(abaixo + acima, elegiveis),
        "distinct_b2b_products": len(referencias),
        "b2b_reach": None,
    }


def _unavailable_envelope(marketplace: str, reason: str) -> dict:
    """Resposta ESTRUTURADA quando nao ha o que servir.

    Nenhuma consulta e' emitida antes de chegar aqui: com a flag desligada, a
    tabela `marts.fact_channel_offer_observation` — que ainda NAO existe, pois
    o head Alembic e' 015 — nunca e' tocada. Sem erro SQL, sem 500, e sem cair
    para o ML: responder sobre outro canal seria responder outra pergunta.

    Os KPIs vem ZERADOS e `coverage_rate` vem NULO. Zero em contagem e' honesto
    ("nao ha linha alguma"); zero em taxa afirmaria "medimos e deu 0%", que e'
    falso — por isso a taxa e' None.
    """
    # `kpis` mantem a FORMA PUBLICADA do ML mesmo aqui: o consumidor nao deve
    # precisar de dois parsers conforme o canal esteja ligado ou nao.
    kpis = {k: 0 for k in (
        "monitored_count", "comparable_count", "below_reference_count",
        "at_or_above_reference_count", "no_reference_count",
        "ambiguous_reference_count", "inactive_count", "fresh_count",
        "stale_count", "historical_count",
    )}
    return {
        "meta": {
            "timezone": pm.TIMEZONE_NAME,
            "currency": pm.CURRENCY,
            "marketplace": marketplace,
            "metric_version": active_metric_version(marketplace),
            "observation_mode": dom.observation_mode_for(marketplace),
            "promo_context": dom.promo_context_for(marketplace),
            "availability": "unavailable",
            "unavailable_reason": reason,
            "mode": MODE_LATEST,
            "observed_date": None,
            "observed_at": None,
            "requested_observed_date": None,
            "observed_ref_date": None,
            "available_observed_dates": [],
            "available_observed_dates_limit": MAX_AVAILABLE_DATES,
            "snapshot_status_counts": {},
            "product_type_counts": {},
            "account_clocks": [],
            "reference_snapshot_id": None,
            "reference_captured_at": None,
            "reference_basis": REFERENCE_BASIS,
            "comparison_basis_text": None,
            # Frescor DESCONHECIDO, nao "fresco": nada foi observado.
            "freshness_status": pm.FRESHNESS_UNAVAILABLE,
            "lag_days": None,
            "refreshed_at": None,
            "eligible_ref_date": None,
            "reference_type": pm.REFERENCE_TYPE,
            "policy_status": pm.POLICY_STATUS,
            "validity_status": pm.VALIDITY_STATUS,
            "coverage_status": pm.COVERAGE_STATUS,
            # Escopo de BELEZA. Gocase e Denavita aparecem no TikTok mas nao
            # entram em denominador nenhum — nem como "sem referencia", o que as
            # faria parecer falha de casamento em vez de fora do produto.
            "monitored_brands": list(dom.BEAUTY_SCOPE_BRANDS),
            "comparable_brands": [],
            "no_reference_brands": [],
            "out_of_scope_brands": {},
            "order_by": _ORDER_BY,
            "date_policy": date_policy_for(marketplace),
            "snapshot_mutability": None,
            "out_of_scope_offer_count": 0,
            "warnings": [AVISO_CANAL_INDISPONIVEL],
        },
        "kpis": kpis,
        "metrics": _metrics_zeradas(),
        "rows": [],
        "returned_count": 0,
        "total_count": 0,
        "truncated": False,
    }


def normalize_brands(valor: str | None) -> list[str]:
    """Marcas pedidas -> allowlist. Recusa com mensagem FIXA, sem eco."""
    if valor is None or str(valor).strip() in ("", "all"):
        return []
    bruto = str(valor)
    if len(bruto) > MAX_BRAND_PARAM_CHARS:
        raise MonitoramentoPrecoError(ERRO_BRAND_TAMANHO)
    pedidas = [b.strip().lower() for b in bruto.split(",") if b.strip()]
    if not pedidas or set(pedidas) - set(pm.MONITORED_BRANDS):
        raise MonitoramentoPrecoError(ERRO_BRAND_INVALIDA)
    return pedidas


def normalize_status(valor: str | None) -> list[str]:
    """Status pedidos -> allowlist. Recusa com mensagem FIXA, sem eco."""
    if valor is None or str(valor).strip() in ("", "all"):
        return []
    bruto = str(valor)
    if len(bruto) > MAX_STATUS_PARAM_CHARS:
        raise MonitoramentoPrecoError(ERRO_STATUS_TAMANHO)
    pedidos = [s.strip().lower() for s in bruto.split(",") if s.strip()]
    if not pedidos or set(pedidos) - set(pm.COMPARISON_STATUSES):
        raise MonitoramentoPrecoError(ERRO_STATUS_INVALIDO)
    return pedidos


def normalize_product_query(valor: str | None) -> str:
    if valor is None:
        return ""
    bruto = str(valor)
    if len(bruto) > MAX_PRODUCT_QUERY_CHARS:
        raise MonitoramentoPrecoError(ERRO_PRODUCT_QUERY_TAMANHO)
    return bruto.strip()


def normalize_pagination(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = DEFAULT_LIMIT if limit is None else int(limit)
    off = 0 if offset is None else int(offset)
    if lim < 1 or lim > MAX_LIMIT:
        raise MonitoramentoPrecoError(ERRO_PAGINACAO_LIMIT)
    if off < 0:
        raise MonitoramentoPrecoError(ERRO_PAGINACAO_OFFSET)
    return lim, off


def _rows(db: Session, sql: str, params: dict) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


def _serialize(valor: object) -> object:
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def resolve_observed_ref_date(db: Session, canal: str, hoje: date) -> date | None:
    """A ultima observacao publicada, com D-1 como TETO. Fail-closed acima dele.

    Nao existe parametro para escolher a data: ver F3 no docstring do modulo.

    Se a maior `ref_date` publicada for D0 ou futura, isto NAO e' tratado como
    "usa a anterior": o sync recusa contratualmente publicar o dia corrente,
    logo uma linha assim significa escrita fora do contrato, e o caminho correto
    e' falhar fechado — nao servir um numero sobre uma camada inconsistente.
    """
    linhas = _rows(db, SQL_LATEST_REF_DATE, {"marketplace": canal})
    maior = linhas[0]["ref_date"] if linhas else None
    if maior is None:
        return None
    if not isinstance(maior, date):
        raise pm.PmaMatchError("ref_date do serving nao e' uma data.")
    if pm.classify_observation_date(maior, hoje) == pm.OBS_INVALID:
        raise pm.PmaMatchError(
            "a maior ref_date publicada e' igual ou posterior ao dia operacional "
            "corrente: o sync proibe publicar o dia corrente e o futuro, "
            "portanto a camada de serving esta inconsistente."
        )
    return maior


def normalize_observed_date(valor: str | None, hoje: date,
                           policy: str = pm.POLICY_CLOSED_DAY) -> date | None:
    """Valida `observed_date` na borda. Recusa com mensagem FIXA, SEM eco.

    Recebido como TEXTO de proposito: se fosse tipado como `date` no FastAPI, o
    validador nativo devolveria um 422 proprio com `{"input": "<o payload>"}` —
    o mesmo eco que o PMA-1A-R eliminou. Aqui nada e' interpolado na resposta.

    Ordem das recusas importa: TAMANHO antes de regex antes de `fromisoformat`
    antes do teto D-1. Um payload de 10 KB nao chega a ser compilado por regex,
    e uma data sintaticamente valida mas em D0 recebe a mensagem de INTERVALO,
    nao a de formato — sao problemas diferentes.
    """
    if valor is None or str(valor).strip() == "":
        return None
    bruto = str(valor).strip()
    if len(bruto) != MAX_OBSERVED_DATE_CHARS or not _OBSERVED_DATE_RE.match(bruto):
        raise MonitoramentoPrecoError(ERRO_OBSERVED_DATE_FORMATO)
    try:
        pedida = date.fromisoformat(bruto)
    except ValueError:
        # Sintaxe certa, calendario errado: 2026-02-30, 2026-13-01.
        raise MonitoramentoPrecoError(ERRO_OBSERVED_DATE_FORMATO) from None
    if pedida > pm.date_ceiling(hoje, policy):
        # Sob `closed_day`: D0 e futuro — o sync proibe publica-los. Sob
        # `snapshot_current`: somente o futuro, que nenhuma fonte observa.
        raise MonitoramentoPrecoError(
            ERRO_OBSERVED_DATE_FUTURA_SNAPSHOT
            if policy == pm.POLICY_SNAPSHOT_CURRENT
            else ERRO_OBSERVED_DATE_FUTURA
        )
    return pedida


def available_observed_dates(db: Session, canal: str, hoje: date) -> list[date]:
    """Datas observadas MATERIALIZADAS, ate D-1, mais recentes primeiro.

    Sem calendario sintetico: um dia que o sync nao publicou nao aparece, e a UI
    nao pode oferecer ao usuario uma data que devolveria vazio.
    """
    linhas = _rows(db, SQL_AVAILABLE_OBSERVED_DATES, {
        "marketplace": canal,
        "eligible": pm.last_eligible_date(hoje),
        "max_dates": MAX_AVAILABLE_DATES,
    })
    return [r["ref_date"] for r in linhas if isinstance(r["ref_date"], date)]


def observed_date_exists(db: Session, canal: str, quando: date) -> bool:
    """A data pedida tem observacao materializada? Sem isto, 200 com estado vazio."""
    return bool(_rows(db, SQL_OBSERVED_DATE_EXISTS,
                      {"marketplace": canal, "ref_date": quando}))



# ---------------------------------------------------------------------------
# Gate PMA-2C4A — politica de data POR CANAL
# ---------------------------------------------------------------------------
def date_policy_for(marketplace: str) -> str:
    """A politica vem do CONTRATO DA FONTE, nunca de uma chave global.

    O Mercado Livre e' serie diaria: so' um dia fechado sustenta comparacao, e
    D0 continua sendo inconsistencia detectavel. Shopee e TikTok sao fotografia
    do estado corrente, derivada do watermark da conta no proprio dia: para eles
    D0 e' o caso normal.

    Trocar isto por um teto unico destruiria uma das duas garantias — ou o ML
    passaria a servir D0 como se fosse dia fechado, ou os canais novos nunca
    serviriam a fotografia que acabaram de publicar.
    """
    if marketplace in dom.CHANNEL_OFFER_MARKETPLACES:
        return pm.POLICY_SNAPSHOT_CURRENT
    return pm.POLICY_CLOSED_DAY


def channel_brand_allowlist(marketplace: str) -> tuple:
    """Marcas aceitas no filtro `brand`, por canal.

    O ML mantem as quatro de `pm.MONITORED_BRANDS` — mexer nisso mudaria uma
    recusa publicada. Shopee e TikTok tem catalogo proprio com marcas que o ML
    nao tem (`apice` entre elas), e recusar 422 numa marca que a fato REALMENTE
    contem seria negar um filtro legitimo.
    """
    if marketplace in dom.CHANNEL_OFFER_MARKETPLACES:
        return dom.BEAUTY_SCOPE_BRANDS
    return pm.MONITORED_BRANDS


def normalize_brands_for(valor, marketplace: str) -> list:
    """`normalize_brands` com a allowlist do canal. Recusa sem ecoar a entrada."""
    if valor is None or str(valor).strip() in ("", "all"):
        return []
    bruto = str(valor)
    if len(bruto) > MAX_BRAND_PARAM_CHARS:
        raise MonitoramentoPrecoError(ERRO_BRAND_TAMANHO)
    permitidas = channel_brand_allowlist(marketplace)
    pedidas = [b.strip().lower() for b in bruto.split(",") if b.strip()]
    if not pedidas or set(pedidas) - set(permitidas):
        raise MonitoramentoPrecoError(ERRO_BRAND_INVALIDA)
    return pedidas


def normalize_accounts(valor) -> list:
    """Filtro por conta de loja. Aceita apenas o formato, nunca uma allowlist
    fixa: as contas sao cadastro da origem e mudam sem release da API.

    O valor vai por PARAMETRO para o driver — nunca interpolado no texto SQL.
    """
    if valor is None or str(valor).strip() in ("", "all"):
        return []
    bruto = str(valor)
    if len(bruto) > MAX_ACCOUNT_PARAM_CHARS:
        raise MonitoramentoPrecoError(ERRO_ACCOUNT_TAMANHO)
    pedidas = [a.strip().lower() for a in bruto.split(",") if a.strip()]
    if not pedidas or any(not _ACCOUNT_RE.match(a) for a in pedidas):
        raise MonitoramentoPrecoError(ERRO_ACCOUNT_INVALIDA)
    return pedidas


def normalize_product_types(valor) -> list:
    """Filtro pelos quatro estados de `product_type`, do dominio versionado."""
    if valor is None or str(valor).strip() in ("", "all"):
        return []
    bruto = str(valor)
    if len(bruto) > MAX_PRODUCT_TYPE_PARAM_CHARS:
        raise MonitoramentoPrecoError(ERRO_PRODUCT_TYPE_TAMANHO)
    pedidos = [t.strip().lower() for t in bruto.split(",") if t.strip()]
    if not pedidos or set(pedidos) - set(dom.PRODUCT_TYPES):
        raise MonitoramentoPrecoError(ERRO_PRODUCT_TYPE_INVALIDO)
    return pedidos


def resolve_channel_observed_date(db, canal: str, hoje: date):
    """Maior `observed_date` <= teto operacional. D0 permitido, futuro nunca.

    Nao ha fail-closed em D0 aqui — sob `snapshot_current` ele e' o caso normal.
    O filtro `<= :ceiling` vive no SQL, entao uma linha futura (que a 017 nao
    impede fisicamente) jamais e' escolhida, mesmo que exista.
    """
    linhas = _rows(db, SQL_CHANNEL_LATEST_OBSERVED_DATE, {
        "marketplace": canal,
        "ceiling": pm.date_ceiling(hoje, pm.POLICY_SNAPSHOT_CURRENT),
    })
    maior = linhas[0]["observed_date"] if linhas else None
    return maior if isinstance(maior, date) else None


def _channel_listing(linha: dict) -> dict:
    """Traduz UMA linha da fato para a forma que `pma_match` consome.

    MAPEAMENTO EXPLICITO, campo a campo — nada inferido por nome:

        offer_key            -> item_id      (identidade da oferta no canal)
        observed_date        -> ref_date     (dia da fotografia)
        observed_price       -> advertised_price  (preco ANUNCIADO na vitrine)
        observed_at          -> price_captured_at (instante da observacao)
        is_active            -> listing_status ('active' / 'inactive')
        list_price           -> original_price     (preco "de", quando o canal o publica)

    `is_active` e' booleano na fato e string no matcher: a traducao e' feita
    aqui, uma vez, em vez de o matcher aprender um segundo formato.

    NAO ha permalink: a fato nao guarda URL, e inventar uma a partir do
    `offer_key` produziria link quebrado com aparencia de link bom.
    """
    return {
        "marketplace": linha["marketplace"],
        "brand": linha["brand"],
        "item_id": linha["offer_key"],
        "seller_sku": linha["seller_sku"],
        "gtin": linha["gtin"],
        "listing_title": linha["listing_title"],
        "permalink": None,
        "listing_status": "active" if linha["is_active"] else "inactive",
        "currency": pm.CURRENCY,
        "ref_date": linha["observed_date"],
        "advertised_price": linha["observed_price"],
        "original_price": linha["list_price"],
        "price_captured_at": linha["observed_at"],
        "listing_metadata_updated_at": None,
    }


#: Campos da fato que viajam ADITIVAMENTE na linha do payload. Todos existem
#: materializados; nenhum e' recalculado aqui.
_CHANNEL_ROW_EXTRA = (
    "offer_key", "shop_account", "parent_item_id", "model_id",
    "product_type", "product_type_source", "snapshot_status",
    "account_watermark_at", "observed_price_source", "list_price",
    "promo_context", "promo_id", "promo_discount_pct", "business_scope",
    "batch_id", "source_run_id",
)


def _metrics_do_canal(linhas: list, tipo_por_linha: dict, contagem: dict,
                      fora_de_escopo: set) -> dict:
    """KPIs multicanal a partir dos valores MATERIALIZADOS na fato.

    `product_type` NAO e' reclassificado: o publisher ja' decidiu com as
    autoridades que so' ele alcanca (flag nativa do canal, cadastro interno,
    BOM). Reclassificar aqui, com apenas SKU e titulo, produziria um segundo
    rotulo para a mesma oferta conforme quem pergunta.

    O denominador e' `v2_product_type_aware`: elegiveis = ativas menos kits
    confirmados e suspeitos. Numerador e denominador saem da MESMA versao.

    ESCOPO DE NEGOCIO SAI DO DENOMINADOR, NAO DO MONITORAMENTO
    ----------------------------------------------------------
    Gocase e Denavita aparecem no TikTok e continuam CONTADAS em
    `monitored_offers`, `active_offers` e na particao de `product_type` — sao
    ofertas reais e a auditoria de cobertura precisa ve-las. O que elas nao
    fazem e' entrar em `eligible_offers` nem na taxa de cobertura: nao sao do
    produto, e infla-las no denominador faria a cobertura parecer pior do que e'.
    """
    ativos = [r for r in linhas if r["comparison_status"] != pm.STATUS_INACTIVE]
    inativos = len(linhas) - len(ativos)
    elegiveis_linhas = [
        r for r in ativos
        if id(r) not in fora_de_escopo
        and not dom.is_excluded_from_comparison(
            tipo_por_linha.get(id(r), dom.PRODUCT_TYPE_UNKNOWN))
    ]
    elegiveis = len(elegiveis_linhas)

    abaixo = sum(1 for r in elegiveis_linhas
                 if r["comparison_status"] == pm.STATUS_BELOW)
    acima = sum(1 for r in elegiveis_linhas
                if r["comparison_status"] == pm.STATUS_AT_OR_ABOVE)

    # Motivos CONTADOS do campo que a linha ja' carrega, nunca re-deduzidos do
    # status: `no_reference` cobre tres causas distintas e colapsa-las apagaria
    # a diferenca entre "nao casou" e "preco nao observado".
    motivos = {r: 0 for r in dom.NON_COMPARABLE_REASONS}
    for r in elegiveis_linhas:
        motivo = r.get("non_comparable_reason")
        if motivo in motivos:
            motivos[motivo] += 1

    referencias = {r.get("reference_row_id") for r in elegiveis_linhas
                   if r.get("reference_row_id") is not None}
    return {
        "monitored_offers": len(linhas),
        "active_offers": len(ativos),
        "inactive_offers": inativos,
        "kit_confirmed": contagem[dom.PRODUCT_KIT_CONFIRMED],
        "kit_suspected": contagem[dom.PRODUCT_KIT_SUSPECTED],
        "no_kit_signal": contagem[dom.PRODUCT_NO_KIT_SIGNAL],
        "product_type_unknown": contagem[dom.PRODUCT_TYPE_UNKNOWN],
        "eligible_offers": elegiveis,
        "comparable_offers": abaixo + acima,
        "below_reference": abaixo,
        "at_or_above_reference": acima,
        "non_comparable_reasons": motivos,
        "coverage_rate": dom.coverage_rate(abaixo + acima, elegiveis),
        "distinct_b2b_products": len(referencias),
        "b2b_reach": None,
    }

def _serve_channel(db, canal: str, *, hoje, pedida, marcas, contas,
                   tipos_pedidos, filtros_status, consulta, lim, off) -> dict:
    """Serving de Shopee e TikTok. LE SOMENTE `CHANNEL_TABLE`.

    Nenhuma consulta deste caminho toca `LISTING_TABLE`: o ML nao e' consultado
    para responder sobre outro canal, e a ausencia de dado aqui NUNCA cai para
    la'. O unico cruzamento e' com a tabela de REFERENCIA B2B, que e' comum aos
    tres canais por definicao — a referencia e' do produto, nao do canal.
    """
    politica = pm.POLICY_SNAPSHOT_CURRENT
    teto = pm.date_ceiling(hoje, politica)

    disponiveis = [
        r["observed_date"] for r in _rows(db, SQL_CHANNEL_AVAILABLE_DATES, {
            "marketplace": canal, "ceiling": teto,
            "max_dates": MAX_AVAILABLE_DATES,
        }) if isinstance(r["observed_date"], date)
    ]

    if pedida is None:
        modo = MODE_LATEST
        observado = resolve_channel_observed_date(db, canal, hoje)
    else:
        modo = MODE_SELECTED
        # NUNCA cair para o dia anterior: a pergunta era sobre ESTE dia.
        existe = bool(_rows(db, SQL_CHANNEL_DATE_EXISTS, {
            "marketplace": canal, "observed_date": pedida}))
        observado = pedida if existe else None

    if observado is None:
        # Estado vazio HONESTO: o canal esta ligado, a tabela foi consultada e
        # nao ha fotografia para esta pergunta. Diferente de flag desligada, e
        # por isso o motivo e' outro.
        motivo = (dom.UNAVAILABLE_NO_OBSERVATION if pedida is not None
                  else dom.UNAVAILABLE_HISTORY_NOT_STARTED)
        vazio = _unavailable_envelope(canal, motivo)
        vazio["meta"]["mode"] = modo
        vazio["meta"]["requested_observed_date"] = _serialize(pedida)
        vazio["meta"]["available_observed_dates"] = [
            _serialize(d) for d in disponiveis]
        vazio["meta"]["eligible_ref_date"] = _serialize(teto)
        vazio["meta"]["date_policy"] = politica
        vazio["meta"]["snapshot_mutability"] = None
        return vazio

    linhas = _rows(db, SQL_CHANNEL_OFFERS, {
        "marketplace": canal,
        "observed_date": observado,
        "brand_filter": bool(marcas),
        "brands": marcas or list(channel_brand_allowlist(canal)),
        "account_filter": bool(contas),
        "accounts": contas or [""],
        "product_type_filter": bool(tipos_pedidos),
        "product_types": tipos_pedidos or [""],
        "has_query": bool(consulta),
        "query_like": f"%{consulta}%",
    })

    relogios_brutos = _rows(db, SQL_CHANNEL_ACCOUNT_CLOCKS, {
        "marketplace": canal, "observed_date": observado,
        "status_current": dom.SNAPSHOT_CURRENT, "status_stale": dom.SNAPSHOT_STALE,
    })

    snapshot = _rows(db, SQL_LATEST_SNAPSHOT, {})
    snapshot_id = snapshot[0]["snapshot_id"] if snapshot else None
    reference_captured_at = snapshot[0]["captured_at"] if snapshot else None
    referencias = (_rows(db, SQL_REFERENCES, {"snapshot_id": snapshot_id})
                   if snapshot_id is not None else [])

    # ---- frescor: pelo CONTRATO e pelo WATERMARK, nao pela igualdade de data.
    # Estar em D0 nao basta para dizer `fresh`: se a fotografia daquele dia nao
    # tem nenhuma oferta com `snapshot_status = current`, o que existe e' uma
    # carga inteiramente carimbada de antes, e chama-la de fresca mentiria.
    contagem_snapshot: dict = {}
    for r in linhas:
        chave = r["snapshot_status"]
        contagem_snapshot[chave] = contagem_snapshot.get(chave, 0) + 1
    algum_corrente = contagem_snapshot.get(dom.SNAPSHOT_CURRENT, 0) > 0

    if pedida is not None and observado != teto:
        frescor = pm.FRESHNESS_HISTORICAL
    elif observado == teto and algum_corrente:
        frescor = pm.FRESHNESS_FRESH
    else:
        frescor = pm.FRESHNESS_STALE
    atraso = pm.lag_days(observado, hoje, politica)
    mutavel = pm.is_mutable_snapshot(observado, hoje, politica)

    # ---- comparacao: MESMO matcher do ML, com a politica do canal ----------
    listings = [_channel_listing(r) for r in linhas]
    por_linha = {}
    for origem, destino in zip(linhas, listings):
        por_linha[id(destino)] = (
            pm.FRESHNESS_HISTORICAL if frescor == pm.FRESHNESS_HISTORICAL
            else pm.channel_row_freshness(origem["snapshot_status"])
        )
    comparadas = pm.compare_all(
        listings, referencias, hoje,
        policy=politica, allow_missing_price=True, row_freshness=por_linha,
    )

    # ---- campos aditivos + tipo de produto MATERIALIZADO -------------------
    contagem_tipos = {t: 0 for t in dom.PRODUCT_TYPES}
    tipo_por_linha = {}
    ids_fora_de_escopo = set()
    for origem, linha in zip(linhas, comparadas):
        for campo in _CHANNEL_ROW_EXTRA:
            linha[campo] = origem[campo]
        linha["observed_date"] = origem["observed_date"]
        linha["date_policy"] = politica
        tipo = origem["product_type"]
        tipo_por_linha[id(linha)] = tipo
        # A particao de `product_type` conta as ATIVAS, como no Mercado Livre:
        # o mesmo campo precisa significar a mesma coisa nos tres canais. E' o
        # que mantem `eligible = active - kit_confirmed - kit_suspected`
        # aritmeticamente verdadeiro — contar kit inativo aqui quebraria essa
        # identidade e o denominador deixaria de reconciliar.
        if linha["comparison_status"] != pm.STATUS_INACTIVE:
            contagem_tipos[tipo] += 1
        if origem["business_scope"] != dom.BUSINESS_SCOPE_IN:
            ids_fora_de_escopo.add(id(linha))
    fora_de_escopo = len(ids_fora_de_escopo)

    kpis = pm.build_kpis(comparadas)
    metricas = _metrics_do_canal(comparadas, tipo_por_linha, contagem_tipos,
                                 ids_fora_de_escopo)

    avisos = pm.build_warnings(
        comparadas, hoje, freshness=frescor, observed=observado,
        reference_captured_at=reference_captured_at,
    )
    if mutavel:
        avisos.insert(0, AVISO_SNAPSHOT_MUTAVEL)
    if fora_de_escopo:
        avisos.append(
            f"{fora_de_escopo} oferta(s) de marca fora do escopo de beleza sao "
            f"monitoradas e aparecem na tabela, mas NAO entram em "
            f"eligible_offers nem na taxa de cobertura."
        )

    # ---- filtro de situacao: altera SOMENTE a tabela, nunca os KPIs --------
    if filtros_status:
        alvo = set(filtros_status)
        quer_stale = pm.STATUS_STALE in alvo
        comerciais = alvo - {pm.STATUS_STALE}
        visiveis = [
            r for r in comparadas
            if (r["comparison_status"] in comerciais)
            or (quer_stale and r.get("freshness_status") == pm.FRESHNESS_STALE)
        ]
    else:
        visiveis = comparadas

    # Ordenacao TOTAL: diferenca, depois marca, depois `offer_key` — que e'
    # unico dentro de (observed_date, marketplace) por ser parte da PK. Sem o
    # desempate final, duas linhas de mesma diferenca poderiam trocar de lugar
    # entre paginas e a paginacao perderia ou repetiria uma delas.
    visiveis.sort(
        key=lambda r: (r["difference_pct"] is None,
                       r["difference_pct"] if r["difference_pct"] is not None else 0,
                       r["brand"] or "", r["offer_key"] or "")
    )
    total = len(visiveis)
    pagina = visiveis[off:off + lim]

    relogios = [{
        "account": r["shop_account"],
        "observed_at": _serialize(r["observed_at"]),
        "refreshed_at": _serialize(r["refreshed_at"]),
        "account_watermark_at": _serialize(r["account_watermark_at"]),
        "offers": r["offers"],
        "current_offers": r["current_offers"],
        "stale_offers": r["stale_offers"],
        "snapshot_status": (dom.SNAPSHOT_CURRENT if r["current_offers"]
                            else dom.SNAPSHOT_STALE),
    } for r in relogios_brutos]

    refreshed_at = max((r["refreshed_at"] for r in relogios_brutos
                        if r["refreshed_at"] is not None), default=None)
    observado_em = max((r["observed_at"] for r in relogios_brutos
                        if r["observed_at"] is not None), default=None)

    return {
        "meta": {
            "timezone": pm.TIMEZONE_NAME,
            "currency": pm.CURRENCY,
            "marketplace": canal,
            "mode": modo,
            "refreshed_at": _serialize(refreshed_at),
            "requested_observed_date": _serialize(pedida),
            "observed_ref_date": _serialize(observado),
            "eligible_ref_date": _serialize(teto),
            "available_observed_dates": [_serialize(d) for d in disponiveis],
            "available_observed_dates_limit": MAX_AVAILABLE_DATES,
            "lag_days": atraso,
            "freshness_status": frescor,
            "reference_snapshot_id": snapshot_id,
            "reference_captured_at": _serialize(reference_captured_at),
            "reference_basis": REFERENCE_BASIS,
            "comparison_basis_text": pm.comparison_basis_text(
                observado, reference_captured_at),
            "reference_type": pm.REFERENCE_TYPE,
            "policy_status": pm.POLICY_STATUS,
            "validity_status": pm.VALIDITY_STATUS,
            "coverage_status": pm.COVERAGE_STATUS,
            "monitored_brands": list(channel_brand_allowlist(canal)),
            "comparable_brands": list(pm.COMPARABLE_BRANDS),
            "no_reference_brands": list(pm.NO_REFERENCE_BRANDS),
            "out_of_scope_brands": {
                b: pm.BRAND_SCOPE_OUT_OF_SCOPE for b in pm.OUT_OF_SCOPE_BRANDS
            },
            "metric_version": active_metric_version(canal),
            "observation_mode": dom.observation_mode_for(canal),
            "promo_context": dom.promo_context_for(canal),
            "availability": "available",
            "unavailable_reason": None,
            "observed_date": _serialize(observado),
            "observed_at": _serialize(observado_em),
            "snapshot_status_counts": contagem_snapshot,
            "product_type_counts": contagem_tipos,
            "account_clocks": relogios,
            "order_by": _ORDER_BY,
            # ---- Gate PMA-2C4A: campos ADITIVOS -------------------------
            "date_policy": politica,
            "snapshot_mutability": (SNAPSHOT_MUTABLE if mutavel
                                    else SNAPSHOT_SETTLED),
            "out_of_scope_offer_count": fora_de_escopo,
            "warnings": avisos,
        },
        "kpis": kpis,
        "metrics": metricas,
        "rows": [_serialize_row(r) for r in pagina],
        "returned_count": len(pagina),
        "total_count": total,
        "truncated": (off + len(pagina)) < total,
    }


def get_monitoramento_preco(
    db: Session,
    *,
    marketplace: str | None = None,
    brand: str | None = None,
    status: str | None = None,
    product_query: str | None = None,
    observed_date: str | None = None,
    shop_account: str | None = None,
    product_type: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    today: date | None = None,
) -> dict:
    """Envelope do endpoint. Somente leitura de `marts.*`.

    Sem `observed_date`: modo `latest` — a maior observacao publicada <= D-1.
    Se ela estiver atrasada, os dados APARECEM e o atraso e' declarado; nunca se
    escolhe silenciosamente o dia anterior nem se esvaziam os cartoes.

    Com `observed_date`: modo `selected_date` — exatamente aquele dia. Data
    valida sem observacao devolve 200 com estado vazio tipado, nao 404 e nao
    fallback para outro dia.

    `today` existe apenas para fixar o dia operacional em teste.
    """
    canal = normalize_marketplace(marketplace)

    # Gate PMA-2C1A — PORTAO. Fica aqui, ANTES de normalizar o resto e antes de
    # `db` ser tocado, porque a garantia que interessa e' negativa: com a flag
    # desligada nenhuma consulta e' emitida. `marts.fact_channel_offer_observation`
    # nao existe (head Alembic 015), e uma consulta a ela viraria erro SQL.
    if canal != pm.MARKETPLACE_ML and not channel_enabled(canal):
        return _unavailable_envelope(canal, dom.UNAVAILABLE_CHANNEL_DISABLED)

    politica = date_policy_for(canal)
    marcas = normalize_brands_for(brand, canal)
    contas = normalize_accounts(shop_account)
    tipos_pedidos = normalize_product_types(product_type)
    filtros_status = normalize_status(status)
    consulta = normalize_product_query(product_query)
    lim, off = normalize_pagination(limit, offset)
    dia = today or today_operacional()
    pedida = normalize_observed_date(observed_date, dia, politica)

    # Gate PMA-2C4A — BIFURCACAO EXPLICITA, sem fallback. Shopee e TikTok leem
    # `CHANNEL_TABLE` e nada mais; o ML continua no caminho abaixo, palavra por
    # palavra como estava. Nenhum dos dois consulta a tabela do outro.
    if canal in dom.CHANNEL_OFFER_MARKETPLACES:
        return _serve_channel(
            db, canal, hoje=dia, pedida=pedida, marcas=marcas, contas=contas,
            tipos_pedidos=tipos_pedidos, filtros_status=filtros_status,
            consulta=consulta, lim=lim, off=off,
        )

    if contas:
        raise MonitoramentoPrecoError(ERRO_ACCOUNT_NAO_SUPORTADO)
    if tipos_pedidos:
        raise MonitoramentoPrecoError(ERRO_PRODUCT_TYPE_NAO_SUPORTADO)

    disponiveis = available_observed_dates(db, canal, dia)

    if pedida is None:
        modo = MODE_LATEST
        observado = resolve_observed_ref_date(db, canal, dia)
    else:
        modo = MODE_SELECTED
        # NUNCA cair para o dia anterior: a pergunta era sobre ESTE dia.
        observado = pedida if observed_date_exists(db, canal, pedida) else None

    frescor = pm.classify_freshness(observado, dia, selected=(pedida is not None))
    atraso = pm.lag_days(observado, dia)

    snapshot = _rows(db, SQL_LATEST_SNAPSHOT, {})
    snapshot_id = snapshot[0]["snapshot_id"] if snapshot else None
    reference_captured_at = snapshot[0]["captured_at"] if snapshot else None

    refreshed_at = None
    if observado is not None:
        s = _rows(db, SQL_LAST_SYNCED_AT, {"marketplace": canal, "ref_date": observado})
        refreshed_at = s[0]["synced_at"] if s else None

    listings: list[dict] = []
    if observado is not None:
        listings = _rows(db, SQL_LISTINGS, {
            "marketplace": canal,
            "ref_date": observado,
            "brand_filter": bool(marcas),
            "brands": marcas or list(pm.MONITORED_BRANDS),
            "has_query": bool(consulta),
            "query_like": f"%{consulta}%",
        })

    referencias: list[dict] = []
    if snapshot_id is not None:
        referencias = _rows(db, SQL_REFERENCES, {"snapshot_id": snapshot_id})

    comparadas = pm.compare_all(listings, referencias, dia, frescor)
    versao_metrica = active_metric_version(canal)

    # KPIs SEMPRE do conjunto completo do filtro estrutural (canal/marca/busca),
    # antes do filtro de status: um KPI que respondesse ao filtro de status
    # mostraria "abaixo da referencia = N de N" e destruiria o denominador.
    kpis = pm.build_kpis(comparadas)
    avisos = pm.build_warnings(
        comparadas, dia, freshness=frescor, observed=observado,
        reference_captured_at=reference_captured_at,
    )
    if filtros_status and pm.STATUS_STALE in filtros_status:
        avisos.append(
            "O filtro status=stale_observation esta DEPRECIADO: frescor deixou de "
            "ser categoria comercial. Ele agora seleciona linhas com "
            "freshness_status=stale. Use o seletor de data observada."
        )

    if filtros_status:
        alvo = set(filtros_status)
        # `stale_observation` virou filtro de FRESCOR, nao de status comercial:
        # o alias sobrevive para nao quebrar link antigo em silencio.
        quer_stale = pm.STATUS_STALE in alvo
        comerciais = alvo - {pm.STATUS_STALE}
        visiveis = [
            r for r in comparadas
            if (r["comparison_status"] in comerciais)
            or (quer_stale and r.get("freshness_status") == pm.FRESHNESS_STALE)
        ]
    else:
        visiveis = comparadas

    # Ordena por diferenca percentual crescente: o mais abaixo da referencia
    # primeiro. Linha sem diferenca medida (nulo) vai para o fim — nunca tratada
    # como zero.
    visiveis.sort(
        key=lambda r: (r["difference_pct"] is None,
                       r["difference_pct"] if r["difference_pct"] is not None else 0,
                       r["brand"] or "", r["item_id"] or "")
    )

    total = len(visiveis)
    pagina = visiveis[off:off + lim]

    # ---- Gate PMA-2C1A: dimensao de tipo de produto -------------------------
    # Classifica com as autoridades REALMENTE disponiveis nesta camada. A API le
    # exclusivamente `marts.*`; o cadastro interno e a BOM vivem no Data Mart e
    # o backend nao os consulta. Sobram `seller_sku` e `listing_title`, que so'
    # levantam SUSPEITA — por isso o ML nao produz `no_kit_signal` aqui, e o que
    # nao tem sinal cai em `product_type_unknown`, que permanece ELEGIVEL.
    #
    # CONTRATO PUBLICADO (Gate PMA-2C1A-R, fase 1). Para o ML, hoje:
    #
    #     kit_confirmed        0     impossivel: nao ha flag nativa
    #     kit_suspected      354     prefixo de SKU ou titulo
    #     no_kit_signal        0     nenhuma autoridade estruturada nesta camada
    #     product_type_unknown 339   elegiveis, porem SEM SINAL de kit
    #
    # Os 339 `unknown` sao a fila de revisao, e 135 deles sao comparaveis. Uma
    # medicao offline com o cadastro interno dava 290/49 e apenas 5 comparaveis
    # `unknown`; esse recorte NAO e' servivel — a API nao consulta o Data Mart —
    # e foi descartado do contrato para que a mesma oferta nao tenha dois
    # rotulos conforme quem pergunta.
    #
    # A tela deve rotular `product_type_unknown` como "sem sinal de kit", jamais
    # como "produto simples confirmado": a diferenca e' entre nao ter encontrado
    # evidencia e ter evidencia de ausencia.
    contagem_tipos: dict[str, int] = {t: 0 for t in dom.PRODUCT_TYPES}
    tipo_por_linha: dict[int, str] = {}
    for linha in comparadas:
        if linha["comparison_status"] == pm.STATUS_INACTIVE:
            continue
        tipo, _ = dom.classify_product_type(
            canal,
            seller_sku=linha.get("seller_sku"),
            title=linha.get("listing_title"),
        )
        contagem_tipos[tipo] += 1
        tipo_por_linha[id(linha)] = tipo

    observado_em = None
    for linha in comparadas:
        atual = linha.get("observed_at")
        if atual is not None and (observado_em is None or atual > observado_em):
            observado_em = atual

    relogios = []
    if observado is not None:
        relogios.append({
            "account": canal,
            "observed_at": _serialize(observado_em),
            "refreshed_at": _serialize(refreshed_at),
            "snapshot_status": None,
        })

    return {
        "meta": {
            "timezone": pm.TIMEZONE_NAME,
            "currency": pm.CURRENCY,
            "marketplace": canal,
            "mode": modo,
            "refreshed_at": _serialize(refreshed_at),
            # `requested_observed_date` e' o que o cliente PEDIU; `observed_ref_date`
            # e' o que foi efetivamente usado. Diferem quando a data pedida nao
            # tem observacao — e nesse caso a segunda e' nula, nao "a mais
            # proxima", porque aproximar seria responder outra pergunta.
            "requested_observed_date": _serialize(pedida),
            "observed_ref_date": _serialize(observado),
            "eligible_ref_date": _serialize(pm.last_eligible_date(dia)),
            "available_observed_dates": [_serialize(d) for d in disponiveis],
            "available_observed_dates_limit": MAX_AVAILABLE_DATES,
            "lag_days": atraso,
            "freshness_status": frescor,
            "reference_snapshot_id": snapshot_id,
            "reference_captured_at": _serialize(reference_captured_at),
            "reference_basis": REFERENCE_BASIS,
            "comparison_basis_text": pm.comparison_basis_text(
                observado, reference_captured_at),
            "reference_type": pm.REFERENCE_TYPE,
            "policy_status": pm.POLICY_STATUS,
            "validity_status": pm.VALIDITY_STATUS,
            "coverage_status": pm.COVERAGE_STATUS,
            "monitored_brands": list(pm.MONITORED_BRANDS),
            "comparable_brands": list(pm.COMPARABLE_BRANDS),
            "no_reference_brands": list(pm.NO_REFERENCE_BRANDS),
            "out_of_scope_brands": {
                b: pm.BRAND_SCOPE_OUT_OF_SCOPE for b in pm.OUT_OF_SCOPE_BRANDS
            },
            # ---- Gate PMA-2C1A: campos ADITIVOS -------------------------
            # Nenhum campo acima mudou de nome, tipo ou valor. Estes sao novos
            # e o teste de regressao do payload v1 verifica exatamente isso:
            # cada chave preexistente mantem o valor que ja' tinha.
            "metric_version": versao_metrica,
            "observation_mode": dom.observation_mode_for(canal),
            "promo_context": dom.promo_context_for(canal),
            "availability": "available",
            "unavailable_reason": None,
            # Alias explicito de `observed_ref_date` com o nome que os canais
            # novos usam, para que a tela leia um campo so' nos tres canais.
            "observed_date": _serialize(observado),
            # Instante em que o PRECO foi capturado — o maior entre as linhas.
            # NAO e' a atualizacao cadastral do anuncio.
            "observed_at": _serialize(observado_em),
            # `snapshot_status` e' conceito da Shopee (carga por conta,
            # sobrescrita, sem tombstone). A fato do ML nao carrega essa coluna,
            # entao o dicionario vem VAZIO em vez de inventar "current: 862":
            # afirmar um estado que a fonte nao modela seria pior que omiti-lo.
            "snapshot_status_counts": {},
            "product_type_counts": contagem_tipos,
            # Relogios por CONTA. O ML tem uma unica travessia diaria, entao
            # publica um relogio so'; a Shopee publicara um por conta, porque
            # suas quatro contas terminam em lotes distintos e um MAX() global
            # marcaria as tres primeiras como atrasadas.
            "account_clocks": relogios,
            "order_by": _ORDER_BY,
            # ---- Gate PMA-2C4A: politica DECLARADA, nao presumida --------
            # O ML segue em `closed_day`. Explicitar no dicionario evita que o
            # valor dependa do default do schema: o servico e' consumido
            # diretamente em teste, sem passar pelo `response_model`.
            "date_policy": pm.POLICY_CLOSED_DAY,
            "snapshot_mutability": (
                SNAPSHOT_SETTLED if observado is not None else None),
            "out_of_scope_offer_count": 0,
            "warnings": avisos,
        },
        "kpis": kpis,
        "metrics": _metrics_do_ml(comparadas, contagem_tipos,
                                  versao_metrica, tipo_por_linha),
        "rows": [_serialize_row(r) for r in pagina],
        "returned_count": len(pagina),
        "total_count": total,
        "truncated": (off + len(pagina)) < total,
    }


def _serialize_row(r: dict) -> dict:
    """Serializa uma linha comparada. NULO permanece NULO — nunca vira zero."""
    saida = {}
    for chave, valor in r.items():
        if isinstance(valor, list):
            saida[chave] = list(valor)
        else:
            saida[chave] = _serialize(valor)
    return saida
