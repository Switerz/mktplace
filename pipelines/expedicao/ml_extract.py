"""Extracao da Expedicao do Mercado Livre (Gate EXP-3B1).

CONTRATO DA FONTE
-----------------
    raw.ml_shipments  JOIN  raw.ml_orders  ON (brand, order_id)

    unidade:  SHIPMENT (nao pedido)
    chave:    (brand, shipment_id)  -- UNIQUE na fonte, 0 duplicatas
    abre:     shipment.status = 'ready_to_ship'
              AND order.status = 'paid'
              AND logistic_type NA ALLOWLIST seller-managed
              AND extracted_at dentro da coorte confiavel
    fecha:    date_shipped, date_cancelled, ou status deixar de ser ready_to_ship

O QUE O MERCADO LIVRE **NAO** DA
--------------------------------
Nao existe prazo operacional de despacho materializado no armazem. Medido em
17/09/2026: `estimated_handling_hours` 0% preenchido em 190.025 linhas,
`estimated_delivery_limit` 0% na janela de 30 dias, e os JSONB `carrier_info`,
`quotation` e `threshold_cancellation` estao VAZIOS. `date_handling` e' inicio
de manuseio (media 0,30h APOS a criacao), nao prazo; `estimated_delivery_date`
e' promessa de ENTREGA ao comprador, outra coisa.

Entao `dispatch_deadline` sai NULO e `deadline_status` sai `unavailable`.
Nenhum prazo e derivado de `date_ready_to_ship`, de `date_created` nem de SLA
fixo: inventar prazo produziria "vencido" onde o marketplace nunca prometeu
nada, e a Torre perderia o direito de ser acreditada. Idade, faixa de 48h e
sinal de `stalled` continuam disponiveis porque dependem so' do relogio.

POR QUE HA UMA COORTE DE CONFIABILIDADE
---------------------------------------
O extrator do ML e' externo a este repositorio. A janela nao pode ser provada
por codigo; o COMPORTAMENTO pode, linha a linha, por `extracted_at`. Um
shipment `ready_to_ship` que nao e' relido ha' meses nao e' backlog vivo — e'
uma fotografia congelada. Medimos 1.958 registros assim, contra um vale de
apenas 21 registros na faixa de 20 a 40 dias: as duas populacoes sao
separaveis. Ver `ML_SOURCE_COHORT_MAX_AGE` no contrato.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from pipelines.expedicao.contract import (
    ML_ALLOWED_SOURCE_COLUMNS,
    ML_BACKLOG_ORDER_STATUS,
    ML_BACKLOG_SHIPMENT_STATUS,
    ML_FULFILLMENT_LOGISTIC_TYPE,
    ML_KNOWN_LOGISTIC_TYPES,
    ML_SELLER_MANAGED_LOGISTIC_TYPES,
    ML_SOURCE_COHORT_MAX_AGE,
    PII_FORBIDDEN_TOKENS,
    REGISTRY_SQL,
    ExtractionResult,
    SourceHealth,
    SourceUnhealthy,
    SourceWatermark,
)
from pipelines.expedicao.shopee_extract import _require_aware, assert_no_pii_in_sql
from pipelines.expedicao.transform import normalizar_ingestao_ml

#: Colunas extraidas, na ordem do SELECT. Espelha a lista fechada do contrato.
ML_SELECT_COLUMNS = (
    "seller_id",
    "brand",
    "shipment_id",
    "order_id",
    "shipment_status",
    "substatus",
    "logistic_type",
    "order_status",
    "order_created_at",
    "date_created",
    "date_ready_to_ship",
    "date_shipped",
    "date_cancelled",
    "tracking_method",
    "extracted_at",
)

#: Backlog seller-managed dentro da coorte confiavel.
#:
#: `s.status` vira `shipment_status` no resultado para nao colidir com
#: `o.status`. Colunas explicitas — nunca `SELECT *`, que traria
#: `receiver_address`, `receiver_name` e o resto do destinatario.
#:
#: `tracking_method` (a MODALIDADE de rastreio, ex. "Normal") entra;
#: `tracking_number` NAO entra: e' identificador de remessa rastreavel
#: publicamente e a API da Torre nao tem autenticacao.
ML_BACKLOG_SQL = """
SELECT
    o.seller_id                AS seller_id,
    s.brand                    AS brand,
    s.shipment_id              AS shipment_id,
    s.order_id                 AS order_id,
    s.status                   AS shipment_status,
    s.substatus                AS substatus,
    s.logistic_type            AS logistic_type,
    o.status                   AS order_status,
    o.date_created             AS order_created_at,
    s.date_created             AS date_created,
    s.date_ready_to_ship       AS date_ready_to_ship,
    s.date_shipped             AS date_shipped,
    s.date_cancelled           AS date_cancelled,
    s.tracking_method          AS tracking_method,
    s.extracted_at             AS extracted_at
FROM raw.ml_shipments s
JOIN raw.ml_orders o
  ON o.brand = s.brand AND o.order_id = s.order_id
WHERE s.status = %(shipment_status)s
  AND s.date_shipped IS NULL
  AND s.date_cancelled IS NULL
  AND o.status = %(order_status)s
"""

#: Watermark POR CONTA. `seller_id` e a identidade do registry; a marca e
#: atributo. Como no Shopee, o maximo GLOBAL esconderia uma conta parada.
ML_WATERMARK_SQL = """
SELECT
    o.seller_id                AS seller_id,
    s.brand                    AS brand,
    MAX(s.extracted_at)        AS max_extracted_at
FROM raw.ml_shipments s
JOIN raw.ml_orders o
  ON o.brand = s.brand AND o.order_id = s.order_id
GROUP BY o.seller_id, s.brand
"""


def fetch_watermarks(conn) -> list[SourceWatermark]:
    """Watermark por conta (`seller_id`), com a marca como nome da conta.

    O carimbo sai daqui JA NORMALIZADO para UTC. Esta e a fronteira entre a
    linha crua do banco e o dominio: `SourceWatermark` alimenta
    `build_account_summaries` (que publica `source_watermark_at`) e o alerta
    de frescor, e os dois comparam com `effective_at`, que e aware.

    A Shopee entrega `timestamptz` e sempre foi aware. O ML entregava naive,
    e a comparacao morria com `can't compare offset-naive and offset-aware
    datetimes` DEPOIS do commit: publicacao feita, auditoria incompleta,
    exit 5. Medido no ensaio ponta a ponta do EXP-3B2-H2.
    """
    with conn.cursor() as cur:
        cur.execute(ML_WATERMARK_SQL)
        return [
            SourceWatermark(
                external_seller_id=str(row["seller_id"]),
                shop_account=str(row["brand"]),
                max_ingested_at=normalizar_ingestao_ml(row["max_extracted_at"]),
            )
            for row in cur.fetchall()
        ]


def fetch_candidates(conn) -> list[dict]:
    """Todos os candidatos, ANTES de excluir Full e registro congelado.

    Devolve o conjunto bruto de proposito: os filtros sao aplicados em Python,
    em `classify_candidates`, para que cada exclusao seja CONTADA. Filtrar no
    SQL faria o Full e o congelado desaparecerem sem deixar numero.
    """
    with conn.cursor() as cur:
        cur.execute(
            ML_BACKLOG_SQL,
            {
                "shipment_status": ML_BACKLOG_SHIPMENT_STATUS,
                "order_status": ML_BACKLOG_ORDER_STATUS,
            },
        )
        return [dict(row) for row in cur.fetchall()]


def is_stale_source_record(
    extracted_at: datetime | None,
    effective_at: datetime,
    max_age: timedelta = ML_SOURCE_COHORT_MAX_AGE,
) -> bool:
    """O registro esta fora da janela ativa do extrator?

    Carimbo ausente conta como FORA: sem evidencia de releitura nao ha' como
    afirmar que o estado gravado ainda vale.

    O carimbo chega NAIVE da fonte e passa pelo mesmo helper que o resto do
    canal. Comparar naive com aware levantaria `TypeError`; pior, assumir UTC
    aqui e -04:00 na transformacao daria duas idades diferentes para o mesmo
    shipment — uma decidindo a coorte, outra decidindo a faixa de 48h.
    """
    _require_aware(effective_at, "effective_at")
    carimbo = normalizar_ingestao_ml(extracted_at)
    if carimbo is None:
        return True
    return (effective_at - carimbo) > max_age



def _fora_da_coorte_utc(
    carimbo: datetime | None, effective_at: datetime, max_age: timedelta
) -> bool:
    """Idem `is_stale_source_record`, mas para carimbo JA em UTC.

    O watermark ja passou pela normalizacao na fronteira; reprocessa-lo faria
    `normalizar_ingestao_ml` levantar, porque a funcao recusa aware de
    proposito (EXP-3B2-H1)."""
    if carimbo is None:
        return True
    return (effective_at - carimbo) > max_age

def classify_candidates(
    linhas: list[dict],
    effective_at: datetime,
    *,
    max_age: timedelta = ML_SOURCE_COHORT_MAX_AGE,
) -> tuple[list[dict], dict[str, int]]:
    """Aplica a allowlist e a coorte, devolvendo `(fila, diagnostico)`.

    Falha FECHADA em modalidade desconhecida: um `logistic_type` novo do
    Mercado Livre nao entra sozinho na fila nem some dela em silencio — bloqueia
    a publicacao inteira ate alguem classificar. E' a mesma disciplina do
    `UNEXPECTED_ACCOUNT` do Shopee.
    """
    _require_aware(effective_at, "effective_at")

    desconhecidas = sorted(
        {
            str(linha.get("logistic_type"))
            for linha in linhas
            if linha.get("logistic_type") not in ML_KNOWN_LOGISTIC_TYPES
        }
    )
    if desconhecidas:
        raise SourceUnhealthy(
            "logistic_type fora do dominio conhecido do Mercado Livre: "
            f"{desconhecidas}. A fila NAO e publicada ate a modalidade ser "
            "classificada como operada pelo vendedor ou como Full."
        )

    fila: list[dict] = []
    excluidos_full = 0
    excluidos_congelados = 0

    for linha in linhas:
        if linha.get("logistic_type") == ML_FULFILLMENT_LOGISTIC_TYPE:
            excluidos_full += 1
            continue
        if linha.get("logistic_type") not in ML_SELLER_MANAGED_LOGISTIC_TYPES:
            # Inalcancavel enquanto o dominio conhecido = allowlist + Full; fica
            # como rede caso o dominio cresca sem a allowlist crescer junto.
            raise SourceUnhealthy(
                f"logistic_type {linha.get('logistic_type')!r} conhecido mas "
                "nao classificado como seller-managed nem como Full."
            )
        if is_stale_source_record(linha.get("extracted_at"), effective_at, max_age):
            excluidos_congelados += 1
            continue
        fila.append(linha)

    diagnostico = {
        "candidate_count": len(linhas),
        "seller_managed_count": len(linhas) - excluidos_full,
        "fulfillment_excluded_count": excluidos_full,
        "stale_source_record_count": excluidos_congelados,
        "unmapped_logistic_type_count": 0,
        "queue_count": len(fila),
    }
    return fila, diagnostico


def extract(
    conn,
    effective_at: datetime,
    expected_accounts: frozenset[str],
    *,
    registry_problems: list[str] | None = None,
    max_age: timedelta = ML_SOURCE_COHORT_MAX_AGE,
) -> ExtractionResult:
    """Extracao com SAUDE, TAMANHO e EXCLUSOES separados.

    Mesma maquina de saude do Shopee — `expected_accounts` vem do REGISTRY do
    Neon, nunca da propria fonte, e a comparacao e de IGUALDADE de conjuntos.
    A diferenca do ML esta depois: o conjunto lido ainda passa pela allowlist de
    modalidade e pela coorte de confiabilidade, e as duas exclusoes sao contadas
    em `diagnostics`.
    """
    _require_aware(effective_at, "effective_at")

    if registry_problems:
        return ExtractionResult(
            source_health=SourceHealth.REGISTRY_AMBIGUOUS,
            expected_accounts=expected_accounts,
            observed_accounts=frozenset(),
            account_watermarks={},
            backlog_rows=[],
            detail="; ".join(registry_problems),
        )

    if not expected_accounts:
        return ExtractionResult(
            source_health=SourceHealth.SOURCE_UNAVAILABLE,
            expected_accounts=frozenset(),
            observed_accounts=frozenset(),
            account_watermarks={},
            backlog_rows=[],
            detail=(
                "registry sem contas ativas para o Mercado Livre "
                "(marts.dim_seller_account nao tem marketplace_id = 2)"
            ),
        )

    watermarks = fetch_watermarks(conn)
    observadas = frozenset(w.external_seller_id for w in watermarks)
    carimbos = {w.external_seller_id: w.max_ingested_at for w in watermarks}
    nomes = {w.external_seller_id: w.shop_account for w in watermarks}

    def _resultado(health: SourceHealth, detalhe: str) -> ExtractionResult:
        return ExtractionResult(
            source_health=health,
            expected_accounts=expected_accounts,
            observed_accounts=observadas,
            account_watermarks=carimbos,
            backlog_rows=[],
            detail=detalhe,
            account_shop_names=nomes,
        )

    faltando = expected_accounts - observadas
    if faltando:
        return _resultado(
            SourceHealth.ACCOUNT_MISSING,
            f"contas esperadas ausentes na fonte: {sorted(faltando)}",
        )

    inesperadas = observadas - expected_accounts
    if inesperadas:
        return _resultado(
            SourceHealth.UNEXPECTED_ACCOUNT,
            f"contas observadas sem cadastro no registry: {sorted(inesperadas)}",
        )

    sem_carimbo = sorted(c for c in expected_accounts if carimbos.get(c) is None)
    if sem_carimbo:
        return _resultado(
            SourceHealth.WATERMARK_MISSING,
            f"contas sem carimbo de extracao: {sem_carimbo}",
        )

    # PRECONDICAO DE PUBLICACAO, nao metadado de frescor.
    #
    # No Shopee o watermark so' descreve a idade do dado: fonte parada ainda
    # devolve as linhas do ultimo estado conhecido. Aqui NAO — o filtro de
    # coorte remove justamente as linhas que o extrator deixou de reler. Uma
    # conta parada sairia com `backlog = 0` e o publisher apagaria a fila
    # anterior por silencio, confundindo "tudo foi despachado" com "a fonte
    # sumiu". Bloquear aqui preserva a fotografia anterior.
    paradas = sorted(
        c
        for c in expected_accounts
        if _fora_da_coorte_utc(carimbos.get(c), effective_at, max_age)
    )
    if paradas:
        return _resultado(
            SourceHealth.SOURCE_STALE,
            f"contas fora da coorte de {max_age.days}d (extracao parada): "
            f"{paradas}. Publicar apagaria a fila anterior com base em "
            "silencio da fonte.",
        )

    fila, diagnostico = classify_candidates(
        fetch_candidates(conn), effective_at, max_age=max_age
    )
    return ExtractionResult(
        source_health=SourceHealth.HEALTHY,
        expected_accounts=expected_accounts,
        observed_accounts=observadas,
        account_watermarks=carimbos,
        backlog_rows=fila,
        account_shop_names=nomes,
        diagnostics=diagnostico,
    )


# Barreira aplicada no import: se alguem colar uma coluna de comprador num dos
# SELECTs, o modulo deixa de importar e os testes falham na coleta.
for _sql in (ML_BACKLOG_SQL, ML_WATERMARK_SQL, REGISTRY_SQL):
    assert_no_pii_in_sql(_sql, PII_FORBIDDEN_TOKENS)

assert set(ML_SELECT_COLUMNS) == set(ML_ALLOWED_SOURCE_COLUMNS), (
    "ML_SELECT_COLUMNS divergiu da lista fechada do contrato"
)
