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

#: Todo o texto de consulta deste modulo. Os testes de contrato varrem esta
#: tupla — nao um regex sobre o arquivo —, de modo que uma consulta nova nao
#: escapa da varredura por ficar fora do padrao textual.
ALL_QUERIES = (
    SQL_LATEST_REF_DATE, SQL_LAST_SYNCED_AT, SQL_LATEST_SNAPSHOT,
    SQL_LISTINGS, SQL_REFERENCES,
    SQL_AVAILABLE_OBSERVED_DATES, SQL_OBSERVED_DATE_EXISTS,
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


def normalize_observed_date(valor: str | None, hoje: date) -> date | None:
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
    if pedida > pm.last_eligible_date(hoje):
        # D0 e futuro. O sync proibe publica-los, logo nao ha o que consultar.
        raise MonitoramentoPrecoError(ERRO_OBSERVED_DATE_FUTURA)
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


def get_monitoramento_preco(
    db: Session,
    *,
    marketplace: str | None = None,
    brand: str | None = None,
    status: str | None = None,
    product_query: str | None = None,
    observed_date: str | None = None,
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

    marcas = normalize_brands(brand)
    filtros_status = normalize_status(status)
    consulta = normalize_product_query(product_query)
    lim, off = normalize_pagination(limit, offset)
    dia = today or today_operacional()
    pedida = normalize_observed_date(observed_date, dia)

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
