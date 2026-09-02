"""Gate PMA-1A / PMA-1A-R — servico read-only do monitoramento de precos proprios.

LE EXCLUSIVAMENTE `marts.*` NO NEON
-----------------------------------
Nenhuma consulta deste modulo menciona `gold.`, `raw.` ou `silver.`. O backend no
Render nao pode consultar o Data Mart, e a travessia Data Mart -> Neon acontece
somente no CLI `pipelines/sync_ml_listing_price_serving.py`.

SOMENTE LEITURA
---------------
Nao existe INSERT, UPDATE, DELETE, CREATE nem TRUNCATE neste modulo.

SOMENTE MODO `latest` — SEM COMPARACAO HISTORICA  (PMA-1A-R, F3)
----------------------------------------------------------------
A versao anterior aceitava `ref_date` arbitrario e sempre casava contra o
snapshot de referencia MAIS RECENTE. Como as planilhas nao tem `valid_from`/
`valid_to`, isso comparava um preco de semanas atras com a referencia de hoje e
devolvia um numero com aparencia de conclusao historica que a fonte nao sustenta.

O filtro publico `ref_date` foi REMOVIDO. O endpoint opera so na ultima
observacao elegivel, com D-1 como teto. O historico diario continua armazenado
para evolucao futura, e comparacao historica so existira quando houver vigencia
de verdade — ou um contrato explicito de "referencia atual aplicada
retrospectivamente", que ninguem aprovou. `captured_at` NAO e' vigencia e nao e'
usado como tal.

A COMPARACAO NAO E' MATERIALIZADA
---------------------------------
O match roda em tempo de consulta, em `pma_match`, sobre as duas tabelas de
`marts`. A escala medida — 855 anuncios/dia e 221 referencias — cabe
folgadamente; `pma_match` recusa alto acima do teto em vez de truncar.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

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
    "marketplace fora do escopo deste MVP. O unico canal aceito e' 'ml': e' o "
    "unico com fonte de preco anunciado. Shopee tem somente preco transacional "
    "de export de pedido, TikTok nao tem preco no catalogo e Amazon nao tem "
    "fonte na Torre."
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
#: PMA-1B — `ref_date` RECUSADO, nunca ignorado.
#:
#: O PMA-1A-R removeu o parametro da assinatura, mas o FastAPI IGNORA query
#: parameters desconhecidos por padrao: `?ref_date=2026-08-01` respondia 200 e
#: fazia o consumidor crer que o filtro historico tinha sido aplicado. Ignorar em
#: silencio e' pior que nao existir — devolve um numero certo sob uma pergunta
#: errada. Agora e' 422 com esta mensagem, sem interpretar nem ecoar o valor.
ERRO_REF_DATE_NAO_SUPORTADO = (
    "parametro ref_date nao e' suportado: este endpoint opera exclusivamente no "
    "modo latest, sobre a ultima observacao elegivel (D-1). Comparacao historica "
    "exigiria vigencia na referencia, que a origem nao possui. Remova o "
    "parametro; a data efetivamente usada vem em meta.observed_ref_date."
)


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

#: Todo o texto de consulta deste modulo. Os testes de contrato varrem esta
#: tupla — nao um regex sobre o arquivo —, de modo que uma consulta nova nao
#: escapa da varredura por ficar fora do padrao textual.
ALL_QUERIES = (
    SQL_LATEST_REF_DATE, SQL_LAST_SYNCED_AT, SQL_LATEST_SNAPSHOT,
    SQL_LISTINGS, SQL_REFERENCES,
)


def today_operacional(agora: datetime | None = None) -> date:
    """Dia corrente em America/Sao_Paulo, nao no fuso do processo."""
    return (agora or datetime.now(timezone.utc)).astimezone(TZ).date()


def normalize_marketplace(valor: str | None) -> str:
    """Valida o canal na borda. O MVP aceita somente `ml`. Nao ecoa a entrada."""
    if valor is None or valor == "":
        return pm.MARKETPLACE_ML
    if str(valor).strip().lower() not in pm.SUPPORTED_MARKETPLACES:
        raise MonitoramentoPrecoError(ERRO_MARKETPLACE)
    return str(valor).strip().lower()


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


def get_monitoramento_preco(
    db: Session,
    *,
    marketplace: str | None = None,
    brand: str | None = None,
    status: str | None = None,
    product_query: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    today: date | None = None,
) -> dict:
    """Envelope do endpoint. Somente leitura de `marts.*`, somente modo `latest`.

    NAO existe parametro de data: comparacao historica esta fora do MVP (F3).
    `today` existe apenas para fixar o dia operacional em teste.
    """
    canal = normalize_marketplace(marketplace)
    marcas = normalize_brands(brand)
    filtros_status = normalize_status(status)
    consulta = normalize_product_query(product_query)
    lim, off = normalize_pagination(limit, offset)
    dia = today or today_operacional()

    observado = resolve_observed_ref_date(db, canal, dia)

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

    comparadas = pm.compare_all(listings, referencias, dia)

    # KPIs SEMPRE do conjunto completo do filtro estrutural (canal/marca/busca),
    # antes do filtro de status: um KPI que respondesse ao filtro de status
    # mostraria "abaixo da referencia = N de N" e destruiria o denominador.
    kpis = pm.build_kpis(comparadas)
    avisos = pm.build_warnings(comparadas, dia)

    if filtros_status:
        alvo = set(filtros_status)
        visiveis = [r for r in comparadas if r["comparison_status"] in alvo]
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

    return {
        "meta": {
            "timezone": pm.TIMEZONE_NAME,
            "currency": pm.CURRENCY,
            "marketplace": canal,
            "mode": "latest",
            "refreshed_at": _serialize(refreshed_at),
            "observed_ref_date": _serialize(observado),
            "eligible_ref_date": _serialize(pm.last_eligible_date(dia)),
            "reference_snapshot_id": snapshot_id,
            "reference_captured_at": _serialize(reference_captured_at),
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
            "order_by": _ORDER_BY,
            "warnings": avisos,
        },
        "kpis": kpis,
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
