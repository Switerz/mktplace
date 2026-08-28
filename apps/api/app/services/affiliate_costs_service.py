"""Bloco "Impacto de afiliados no resultado" para /canais — UE3 Task 2/3.

Implementa o contrato §23 de docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md. Modulo
separado de proposito: `performance_service` ja e' grande, e este bloco tem
regras proprias (competencia mensal, quatro dimensoes de estado, isolamento de
falha) que nao se misturam com as do resto de /canais.

O QUE ESTE MODULO NAO FAZ, E POR QUE
-----------------------------------
- **Nao soma os tres componentes.** Qual subconjunto constitui "custo de
  afiliado" e' o ponto aberto P2, e a ausencia de sobreposicao entre eles NAO
  esta provada (§23.3): a soma nao e' so decisao comercial pendente, e'
  aritmeticamente nao validada.
- **Nao agrega competencias.** Varios meses devolvem uma linha por marca x
  competencia, cada uma auditavel isoladamente.
- **Nao calcula razao com GMV.** A Fase C do contrato comercial TikTok esta
  aberta (auditoria da fonte BLOCKED).
- **Nao aplica `abs()`.** O valor sai assinado como esta na fonte; os campos se
  chamam `*_signed` para dizer isso no nome.
- **Nao rateia mes para dias** e **nao completa marca ausente com zero**.
- **Nao produz numero para periodo parcial ou desalinhado** — devolve `rows=[]`.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.deps.period import today_brt
from app.services.performance_service import ML_ID, SHOPEE_ID, TIKTOK_ID

logger = logging.getLogger(__name__)

FACT_TABLE = "marts.fact_tiktok_affiliate_cost_order_monthly"
SYNC_STATE_TABLE = FACT_TABLE + "_sync_state"

TIKTOK_CHANNEL = "tiktok"
#: Canais que a pagina conhece, na ordem FIXA de exibicao. Nao e' ranking:
#: ordenar por valor colocaria o unico canal com dado sempre no topo.
CHANNEL_ORDER = (TIKTOK_CHANNEL, "mercadolivre", "shopee")

#: Quantas marcas uma competencia completa deve ter. Espelha
#: `BRANDS_IN_SCOPE` do conector TikTok; divergencia aqui vira
#: `incomplete_brand_coverage`, nunca preenchimento com zero.
EXPECTED_BRANDS_PER_MONTH = 5

# --- Notas fixas e sanitizadas. Nunca interpolam mensagem de driver, SQL,
# --- DSN, host ou credencial.
RETURN_NOTE = (
    "Retorno de afiliados indisponível: não há receita atribuída a afiliados "
    "no grão necessário, então não existe numerador para calcular retorno."
)
SOURCE_NOTE = (
    "Competência mensal do pedido (mês de order_create_time), a partir de "
    "marts.fact_tiktok_affiliate_cost_order_monthly."
)
LIMITATION_NOTE = (
    "Os três lançamentos são exibidos separadamente e não somados: qual "
    "subconjunto constitui custo de afiliado é decisão aberta, e a ausência de "
    "sobreposição entre eles não está provada. Valores com o sinal da fonte."
)
ERROR_NOTE = (
    "Não foi possível ler os custos de afiliados nesta consulta. O restante "
    "da página não foi afetado."
)
NO_SOURCE_NOTE = "Fonte equivalente não confirmada para este canal."
PARTIAL_NOTE = (
    "Competência em aberto ou período não alinhado ao mês: os valores ainda "
    "maturam na fonte e não são exibidos, porque um número parcial pareceria "
    "comparável a um mês fechado."
)
ABSENT_MONTHS_NOTE = (
    "Nenhuma competência do período tem lançamento de afiliado na fonte. "
    "Ausência de registro não é o mesmo que custo zero."
)

#: Categoria FIXA registrada em log quando a consulta falha. Nao interpola
#: mensagem do driver, SQL, host, DSN nem credencial — ver `safe_...`.
LOG_QUERY_FAILURE = "affiliate_costs_block: consulta indisponivel"


# ---------------------------------------------------------------------------
# Classificacao de periodo — funcao PURA, sem banco
# ---------------------------------------------------------------------------

def _is_month_start(d: date) -> bool:
    return d.day == 1


def _is_month_end(d: date) -> bool:
    if d.month == 12:
        proximo = date(d.year + 1, 1, 1)
    else:
        proximo = date(d.year, d.month + 1, 1)
    return (proximo - d).days == 1


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def months_between(start: date, end: date) -> list[str]:
    """Competências `YYYY-MM` cobertas pelo intervalo, INCLUSIVO nas pontas."""
    out: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def classify_period(start: date, end: date, today: date) -> tuple[str, list[str]]:
    """Classifica o período e devolve `(period_status, competências elegíveis)`.

    PURA e testável sem banco — é a regra que decide se existe consulta de
    valores, e por isso não pode depender de I/O.

    Só produz competências quando o intervalo fecha mês(es) calendário
    **completo(s)** e **passado(s)**:

      - início no dia 1 e fim no último dia do mês;
      - o último mês do intervalo já terminou (`end < início do mês atual`).

    Qualquer outro recorte devolve lista vazia, e o chamador **não consulta
    valores**. Não há rateio em nenhuma direção: o grão da fact é mensal
    (§18.8.2), e dividir mês por dias inventaria medição que a fonte não tem.
    """
    if start > end:
        return "not_month_aligned", []

    alinhado = _is_month_start(start) and _is_month_end(end)
    if not alinhado:
        # Cabe dentro de um único mês? É "parcial"; senão, "desalinhado".
        mesmo_mes = (start.year, start.month) == (end.year, end.month)
        return ("partial_month" if mesmo_mes else "not_month_aligned"), []

    inicio_mes_atual = date(today.year, today.month, 1)
    if end >= inicio_mes_atual:
        # O intervalo alcança o mês corrente: ele ainda está maturando.
        return "partial_month", []

    meses = months_between(start, end)
    status = "complete_month" if len(meses) == 1 else "complete_months"
    return status, meses


# ---------------------------------------------------------------------------
# Montagem do bloco
# ---------------------------------------------------------------------------

def _channel_statuses(selected: list[int], tiktok_status: str,
                      tiktok_reason: str) -> list[dict]:
    """Status POR CANAL, autoritativo.

    Disponibilidade do TikTok jamais torna ML/Shopee disponíveis (§23.6.1):
    cada entrada carrega o seu próprio `availability_status`, e o agregado do
    bloco nunca o sobrescreve.
    """
    por_id = {TIKTOK_ID: TIKTOK_CHANNEL, ML_ID: "mercadolivre", SHOPEE_ID: "shopee"}
    out: list[dict] = []
    for canal in CHANNEL_ORDER:
        ids = [i for i, nome in por_id.items() if nome == canal]
        if not ids or ids[0] not in selected:
            continue
        if canal == TIKTOK_CHANNEL:
            out.append({"channel": canal, "availability_status": tiktok_status,
                        "reason_note": tiktok_reason})
        else:
            out.append({"channel": canal,
                        "availability_status": "unavailable_no_source",
                        "reason_note": NO_SOURCE_NOTE})
    return out


def _float_or_none(v) -> float | None:
    """Converte para float preservando o SINAL e mantendo `None` como `None`.

    `None` significa ausência de medição; zero significa medição igual a zero.
    Converter um no outro inventaria ou apagaria informação.
    """
    if v is None:
        return None
    return float(v) if isinstance(v, Decimal) else float(v)


def _iso(v) -> str | None:
    return None if v is None else v.isoformat()


def _empty_block(period_status: str, channels: list[dict],
                 months: list[str], availability: str,
                 limitation: str, *,
                 coverage: str = "unknown") -> dict:
    """Bloco sem linha monetaria.

    `freshness_status` e' SEMPRE `unknown` aqui: nenhuma fotografia do TikTok
    foi consultada, entao afirmar "carga manual" — ou qualquer coisa sobre a
    idade do dado — seria falar de um dado que nao foi lido (F5).
    """
    return {
        "availability_status": availability,
        "period_status": period_status,
        "coverage_status": coverage,
        "freshness_status": "unknown",
        "rows": [],
        "channels": channels,
        "months_included": months,
        "affiliate_refreshed_at": None,
        "source_watermark": None,
        "return_availability": "unavailable_no_attributed_revenue",
        "return_note": RETURN_NOTE,
        "source_note": SOURCE_NOTE,
        "limitation_note": limitation,
    }


def build_affiliate_costs_block(
    db: Session, mkt_ids: list[int], start: date, end: date, *,
    brand_keys: list[str] | None = None,
    today: date | None = None,
) -> dict:
    """Monta o bloco. Falha de banco e' tratada em `safe_...` abaixo.

    `today` resolve em **America/Sao_Paulo** (`today_brt`), nunca no fuso do
    SO nem em UTC: entre 21h e 00h BRT o UTC ja virou o dia — e no dia 1 do
    mes, o mes. Isso decidiria que a competencia anterior "ainda esta em
    aberto" um dia inteiro antes da hora, escondendo dados fechados.
    """
    today = today or today_brt()
    period_status, meses = classify_period(start, end, today)
    tiktok_selecionado = TIKTOK_ID in mkt_ids

    # TikTok fora do filtro: nenhuma linha de TikTok, e o canal nao aparece.
    if not tiktok_selecionado:
        canais = _channel_statuses(mkt_ids, "unavailable_no_source", NO_SOURCE_NOTE)
        return _empty_block(period_status, canais, meses,
                            "unavailable_no_source", LIMITATION_NOTE)

    # Periodo parcial/desalinhado: ZERO consulta de valores (§23.6 itens 3-4).
    if not meses:
        canais = _channel_statuses(mkt_ids, "available", PARTIAL_NOTE)
        return _empty_block(period_status, canais, meses, "available",
                            PARTIAL_NOTE)

    params: dict = {"meses": [f"{m}-01" for m in meses], "alvo": FACT_TABLE}
    brand_sql = ""
    if brand_keys is not None:
        if not brand_keys:
            canais = _channel_statuses(mkt_ids, "no_eligible_brand",
                                       "Nenhuma marca elegível no filtro.")
            return _empty_block(period_status, canais, meses,
                                "no_eligible_brand", LIMITATION_NOTE)
        params["brands"] = list(brand_keys)
        brand_sql = " AND f.brand = ANY(:brands)"

    registros = db.execute(_scope_sql(brand_sql), params).mappings().all()

    rows: list[dict] = []
    cobertura_por_mes: dict[str, int] = {}
    synced: list = []
    watermark = None
    for r in registros:
        mes = _month_key(r["ref_month"])
        presentes = int(r["brands_present_in_month"] or 0)
        cobertura_por_mes[mes] = presentes
        if watermark is None:
            watermark = r["source_watermark"]

        # `brand IS NULL` e' a linha SO de metainformacao produzida pelo LEFT
        # JOIN quando a competencia pedida nao casou nenhum registro. NUNCA
        # vira linha monetaria: fabricar `R$ 0,00` para um mes que a fonte nao
        # tem afirmaria que nao houve custo de afiliado.
        if r["brand"] is None:
            continue

        if r["synced_at"] is not None:
            synced.append(r["synced_at"])
        rows.append({
            "channel": TIKTOK_CHANNEL,
            "brand": r["brand"],
            "ref_month": mes,
            "creator_commission_signed":
                _float_or_none(r["affiliate_creator_commission"]),
            "partner_commission_signed":
                _float_or_none(r["affiliate_partner_commission"]),
            "affiliate_ads_commission_signed":
                _float_or_none(r["affiliate_ads_commission"]),
            "coverage_status": _coverage_of(presentes),
            "brands_present_in_month": presentes,
        })

    # CONSERVADOR (§23.6.1) e sobre TODAS as competencias PEDIDAS, nao so' as
    # presentes: um mes inteiramente ausente conta como `0` marcas e torna o
    # agregado incompleto. Sem isso, um mes que sumiu da fonte desapareceria
    # tambem da analise e o bloco se declararia `complete` (F3).
    presentes_por_mes = {m: cobertura_por_mes.get(m, 0) for m in meses}
    cobertura_geral = (
        "incomplete_brand_coverage"
        if any(_coverage_of(n) == "incomplete_brand_coverage"
               for n in presentes_por_mes.values())
        else "complete"
    )

    if not rows:
        # Nenhuma linha monetaria. Duas causas DISTINTAS:
        #  - todas as competencias pedidas estao ausentes da fonte;
        #  - as competencias existem, mas o filtro de marca nao casou nada.
        todas_ausentes = all(n == 0 for n in presentes_por_mes.values())
        if todas_ausentes:
            canais = _channel_statuses(mkt_ids, "available", ABSENT_MONTHS_NOTE)
            bloco = _empty_block(period_status, canais, meses, "available",
                                 ABSENT_MONTHS_NOTE,
                                 coverage="incomplete_brand_coverage")
        else:
            canais = _channel_statuses(mkt_ids, "no_eligible_brand",
                                       "Sem marca elegível nas competências do período.")
            bloco = _empty_block(period_status, canais, meses,
                                 "no_eligible_brand", LIMITATION_NOTE,
                                 coverage=cobertura_geral)
        return bloco

    # Frescor PROPRIO do bloco. `MAX(synced_at)` do escopo retornado — nunca o
    # `refreshed_at` geral de /canais, que diria "recente" para um dado
    # congelado so' porque a rota respondeu agora.
    canais = _channel_statuses(mkt_ids, "available", "Dados disponíveis.")
    return {
        "availability_status": "available",
        "period_status": period_status,
        "coverage_status": cobertura_geral,
        # `manual_snapshot` so' aqui, onde uma fotografia foi de fato lida
        # (F5). `fresh`/`stale` seguem inatribuiveis antes da UE2-C definir
        # rotina e SLA (§23.5.2): a carga e' manual e qualquer limiar seria
        # inventado.
        "freshness_status": "manual_snapshot",
        "rows": rows,
        "channels": canais,
        "months_included": meses,
        "affiliate_refreshed_at": _iso(max(synced)) if synced else None,
        "source_watermark": _iso(watermark),
        "return_availability": "unavailable_no_attributed_revenue",
        "return_note": RETURN_NOTE,
        "source_note": SOURCE_NOTE,
        "limitation_note": LIMITATION_NOTE,
    }


def _coverage_of(brands_present: int) -> str:
    return ("complete" if brands_present >= EXPECTED_BRANDS_PER_MONTH
            else "incomplete_brand_coverage")


def _scope_sql(brand_sql: str):
    """Consulta ÚNICA do bloco: valores, cobertura e watermark.

    Um round-trip só. A versão anterior fazia dois (fact + `sync_state`), e o
    segundo era um `SELECT` de uma linha — custo de rede sem ganho algum.

    Três decisões estruturais:

    1. `pedidas` materializa as competências **solicitadas**, e o `LEFT JOIN`
       garante que um mês sem nenhuma linha ainda apareça, com
       `brands_present_in_month = 0` e `brand IS NULL` (F3).
    2. `cobertura` conta `DISTINCT brand` **sem** o filtro de marca: a
       cobertura é da COMPETÊNCIA, não do recorte pedido. Com filtro de uma
       marca, contar dentro do filtro diria "1 de 5" sempre.
    3. O watermark entra por subconsulta escalar — mesmo round-trip, e
       continua sendo grandeza distinta de `synced_at`.

    Colunas EXPLÍCITAS, nunca `SELECT *`.
    """
    return text(f"""
        WITH pedidas AS (
            SELECT UNNEST(CAST(:meses AS date[])) AS ref_month
        ),
        cobertura AS (
            SELECT p.ref_month, COUNT(DISTINCT f.brand) AS marcas
            FROM pedidas p
            LEFT JOIN {FACT_TABLE} f ON f.ref_month = p.ref_month
            GROUP BY p.ref_month
        ),
        recorte AS (
            SELECT
                f.ref_month,
                f.brand,
                f.affiliate_creator_commission,
                f.affiliate_partner_commission,
                f.affiliate_ads_commission,
                f.synced_at
            FROM {FACT_TABLE} f
            WHERE f.ref_month = ANY(CAST(:meses AS date[])){brand_sql}
        )
        SELECT
            c.ref_month,
            r.brand,
            r.affiliate_creator_commission,
            r.affiliate_partner_commission,
            r.affiliate_ads_commission,
            r.synced_at,
            c.marcas AS brands_present_in_month,
            (SELECT s.last_successful_upper_bound
               FROM {SYNC_STATE_TABLE} s
              WHERE s.target_table = :alvo) AS source_watermark
        FROM cobertura c
        LEFT JOIN recorte r ON r.ref_month = c.ref_month
        ORDER BY c.ref_month, r.brand
    """)


def safe_affiliate_costs_block(
    db: Session, mkt_ids: list[int], start: date, end: date, *,
    brand_keys: list[str] | None = None,
    today: date | None = None,
) -> dict:
    """Isolamento de falha: captura SOMENTE erro esperado da camada de banco.

    `SQLAlchemyError` cobre indisponibilidade, timeout, tabela ausente e erro
    de programação de SQL — as falhas que este bloco pode legitimamente sofrer
    sem que o resto de /canais deva cair.

    Um `except Exception` amplo esconderia bug arbitrário do próprio bloco
    (`KeyError`, `TypeError`, `AttributeError`) sob um estado de "erro de
    fonte", e o defeito viveria em produção parecendo indisponibilidade. Esses
    sobem e falham alto.

    A nota devolvida é FIXA e sanitizada: nada de SQL, DSN, host, credencial ou
    mensagem bruta do driver chega ao payload.

    O log também é sanitizado: uma CATEGORIA fixa, sem `exc_info`. O traceback
    de `SQLAlchemyError` carrega o SQL e, dependendo do driver, host e
    parâmetros de conexão — e log de aplicação costuma sair do perímetro
    (agregador, ticket, captura de tela). Como esta é uma falha ESPERADA e já
    classificada, o traceback não acrescenta diagnóstico que justifique o
    risco. Bug de programação continua subindo com traceback completo, porque
    não é capturado aqui.
    """
    try:
        return build_affiliate_costs_block(
            db, mkt_ids, start, end, brand_keys=brand_keys, today=today)
    except SQLAlchemyError:
        logger.warning(LOG_QUERY_FAILURE)
        period_status, meses = classify_period(
            start, end, today or today_brt())
        # `channels` segue autoritativo mesmo em erro: ML/Shopee continuam
        # `unavailable_no_source` (nao ficaram indisponiveis por causa desta
        # falha), e so' o TikTok entra em `error`.
        canais = _channel_statuses(mkt_ids, "error", ERROR_NOTE)
        return _empty_block(period_status, canais, meses, "error", ERROR_NOTE)
