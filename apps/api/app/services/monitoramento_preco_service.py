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
