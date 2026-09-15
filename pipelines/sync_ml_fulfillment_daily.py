"""Gate FULL-1A — sync de marts.fact_ml_fulfillment_daily (+ _listing_daily).

Publica a superficie "Full Mercado Livre" a partir de `api.ml_orders`,
`api.ml_shipments` e `api.ml_order_line_items` no Data Mart. Contrato completo na
migration 016.

O QUE ESTE MODULO NAO E'
------------------------
Nao e' estoque. Nao ha aqui disponibilidade, cobertura em dias, ruptura nem
qualquer leitura de `api.ml_item_stock_history` -- o FULL-0R mediu correlacao de
-0,006 entre a variacao diaria de `available_quantity` e as unidades vendidas em
listings exclusivamente Full, o que derruba a tese de que o campo seja estoque
fisico. Nao e' margem (falta CMV), nao e' caixa (falta settlement).

UMA COORTE SO': O PEDIDO (corrigido no FULL-1A-R)
--------------------------------------------------
`ref_date` e' `date_created::date` do PEDIDO, em America/Sao_Paulo, e vale para
TODAS as metricas da linha -- GMV, pedidos, unidades, cancelamento, handling e
delivery.

A versao anterior media `handling`/`delivery` no grao do ENVIO, com a data do
ENVIO, na mesma `ref_date` das metricas de pedido. Isso dava DUAS semanticas a
uma coluna de chave: a mesma `ref_date` significava "pedidos criados no dia" para
GMV e "envios criados no dia" para tempo. Duas populacoes diferentes somadas na
mesma linha, sem nada que avisasse.

Agora a amostra e' o PEDIDO da coorte. Um pack com tres pedidos contribui tres
amostras -- e isso e' correto, porque a medida e' "quanto tempo ESTE pedido levou
para ser despachado", nao "quantos envios existiram". Se algum dia for preciso a
serie por data do evento logistico, ela e' OUTRA fato, nao uma segunda leitura
desta `ref_date`.

Medido em agosto/2026 com a coorte do pedido:

    classe     handling      delivery     cobertura handling / delivery
    full       28,67 h       2,65 d       97,3% / 96,5%
    non_full   71,83 h       4,89 d       97,1% / 96,0%
    unknown    --            --           0% (sem envio, por definicao)

Zero valores negativos, zero outliers acima de 30 d de handling ou 60 d de
entrega. `sample_count` mede a censura: os 2,7% sem handling sao pedidos que
ainda nao despacharam, nao pedidos com tempo zero.

A DIRECAO DO JOIN, E POR QUE ELA E' UMA SO'
--------------------------------------------
    api.ml_orders (brand, shipping_id) -> api.ml_shipments (brand, shipment_id)

PEDIDO -> ENVIO, nunca o contrario. Medido em agosto/2026: 4.821 packs carregam
de 2 a 8 pedidos no mesmo `shipping_id`. Ler `shipping_items` a partir de pedidos
pagos devolve 67.725 unidades contra 60.916 reais -- 11,2% de inflacao. Por isso
unidade vem de `api.ml_order_line_items`, que e' 1:1 com o pedido (257.681
pedidos de mai-ago/2026, todos com exatamente uma linha).

`brand` entra na chave porque 17 `shipment_id` de agosto/2026 aparecem em duas
marcas. Join so' por `shipment_id` duplicaria.

TODO STATUS TEM CASA (corrigido no FULL-1A-R)
-----------------------------------------------
    eligible_orders  = todos os status criados no dia
    paid_orders      = status 'paid'          -> GMV, unidades
    cancelled_orders = status 'cancelled'     -> taxa de cancelamento
    other_orders     = o resto                -> hoje so' 'partially_refunded'

    eligible = paid + cancelled + other        (invariante, com CHECK na 016)

`partially_refunded` fica FORA do GMV, e isso nao e' decisao pelo nome: e' o
contrato canonico ja' vigente na Torre. `db/seeds/03_status_canonico.sql` mapeia
o status para o canonico `returned`, e
`docs/MARKETPLACE_DATA_QUALITY_CHECKPOINT.md` registra a regra e a limitacao
conhecida -- o pedido INTEIRO sai, nao so' a parcela reembolsada (~0,1% do GMV).
Antes, esses pedidos existiam apenas como resto aritmetico; agora tem coluna.

LOCK DE SESSAO, NAO DE TRANSACAO (corrigido no FULL-1A-R)
----------------------------------------------------------
A versao anterior usava `pg_advisory_xact_lock` na conexao de publicacao e
mantinha essa transacao ABERTA durante a leitura do Data Mart -- ate' 600 s de
`idle in transaction` no Neon, segurando snapshot e impedindo VACUUM, por
trabalho que nem tocava o Neon.

Agora:

    conexao de lock   = autocommit, so' para o advisory lock de SESSAO
    leitura da fonte  = sem nenhuma transacao gravavel aberta
    transacao gravavel = aberta SO' depois de fonte lida e validada
    unlock            = no finally, seguido do close como ultima protecao

O lock de sessao sobrevive ao fim de cada statement, entao cobre decisao do modo,
leitura e publicacao inteiras -- exatamente o intervalo que precisa de exclusao
mutua -- sem manter transacao ociosa.

FAIL-FAST NA DISPUTA (corrigido no FULL-1B-L)
-----------------------------------------------
A aquisicao usa `pg_try_advisory_lock`, que devolve booleano na hora.

A variante bloqueante `pg_advisory_lock` esperaria INDEFINIDAMENTE: nao ha
`lock_timeout` em nenhum ponto deste caminho -- nem na conexao de lock, nem no
engine (`create_engine(url, pool_pre_ping=True)`, sem `connect_args`), nem no
DSN. O `auto` diario sobrepondo um `full` manual deixaria um processo pendurado
sem erro e sem log.

Quando o lock nao e' adquirido, a execucao termina em `ConcurrentRunError` com
exit code 1, ANTES de ler a fonte, abrir auditoria ou tocar no destino. Sem
espera, sem sleep, sem retry.

JANELA DE RELEITURA: DERIVADA DA MATURACAO MEDIDA
---------------------------------------------------
Cancelamento de pedido ML madura devagar. Medido em jun-ago/2026 (8.124
cancelamentos):

    p50   0,05 dia        p90   4,85 dias
    p99  14,62 dias       max  44,12 dias

Daqui saem os numeros, e nao de um palpite redondo:

    INCREMENTAL_DAYS_BACK = 15   cobre o p99 (14,62)
    BACKFILL_DAYS_BACK    = 45   cobre o maximo observado (44,12)

A cauda nao cabe no incremental: por isso ele e' COMBINADO com backfill semanal e
full mensal, com obrigacao durável em `audit.source_sync_run`.

PISO HISTORICO: 2025-08-01, POR COBERTURA DA FONTE (FULL-1C-H1)
-----------------------------------------------------------------
O modo `full` comeca em 01/08/2025, nao no primeiro envio. Antes disso a fonte
tem 1.330 pedidos PAGOS sem nenhuma linha em `api.ml_order_line_items` -- 338 em
maio, 190 em junho e 802 em julho de 2025 -- e zero em todos os 14 meses
seguintes.

Esses meses ficam INDISPONIVEIS por incompletude, e nao publicados como zero. A
regra "pedido pago exige unidade" continua BLOQUEANTE: ausencia de item e'
ausencia de medicao, nunca venda de zero unidade.

O piso volta a baixar quando a fonte for reparada e o diagnostico `full`
reconciliar na janela ampliada -- nunca antes.

Esta maturacao nao e' teorica. Entre a medicao do FULL-1A (15/09) e a revisao do
FULL-1A-R, um pedido de agosto migrou de `paid` para `cancelled` e o GMV Full de
agosto caiu R$ 66,00 sozinho. Um dia "fechado" continua se mexendo.

RECARGA INTEGRAL DA JANELA, NAO UPSERT
----------------------------------------
Um pedido que migra para `cancelled` precisa SAIR de `paid_gmv`. UPSERT por chave
deixaria a linha antiga intacta quando a combinacao (classe, rotulo) do dia
deixasse de existir. O DELETE e' limitado a `ref_date BETWEEN date_from AND
date_to`.

DRY-RUN E' READ-ONLY DE VERDADE
--------------------------------
Sem `--apply` nao existe conexao gravavel: nem para as fatos, nem para
`audit.source_sync_run`, nem para o lock.

NAO EXECUTADO: zero `--apply`, zero banco escrito, zero backfill.
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from pipelines.common.db import DataMartSession, LocalSession
from pipelines.common.logging import get_logger
from pipelines.common.operational_calendar import (
    OPERATIONAL_TZ,
    assert_closed_day,
    closed_window,
    last_closed_date,
)

logger = get_logger(__name__)


class MLFulfillmentSyncError(RuntimeError):
    """Falha de contrato deste sync. Mensagem ja' sanitizada no ponto de saida."""


class ConcurrentRunError(MLFulfillmentSyncError):
    """Outra execucao ja' detem o lock.

    Classe PROPRIA porque a acao do operador e' diferente das demais falhas:
    aqui nao ha nada quebrado -- ha outra execucao em andamento, e a resposta
    certa e' esperar ela terminar, nao investigar dado nem repetir na hora.
    """


class SourceUnavailableError(MLFulfillmentSyncError):
    """A fonte nao pode ser lida.

    Classe PROPRIA porque o tratamento e' oposto ao de uma janela vazia: fonte
    indisponivel NAO pode chegar perto do DELETE. Ausencia de dado e ausencia de
    fonte sao coisas diferentes, e confundi-las apagaria historico publicado.
    """


# ---------------------------------------------------------------------------
# Constantes de contrato
# ---------------------------------------------------------------------------

FACT_TABLE = "marts.fact_ml_fulfillment_daily"
LISTING_TABLE = "marts.fact_ml_fulfillment_listing_daily"

#: Espelha `SENTINELA_LT` da migration 016. Duplicado de proposito: a camada de
#: pipelines nao importa de `apps/api/alembic`, e um teste trava a igualdade.
SENTINELA_LT = "(sem envio)"

CLASSE_FULL = "full"
CLASSE_NON_FULL = "non_full"
CLASSE_UNKNOWN = "unknown"
CLASSES_VALIDAS = (CLASSE_FULL, CLASSE_NON_FULL, CLASSE_UNKNOWN)

#: O unico rotulo que significa Full. Todo o resto e' non_full -- inclusive
#: rotulo que o ML ainda nao criou.
ROTULO_FULL = "fulfillment"

#: Rotulos ja' observados na fonte. Serve APENAS para decidir se emitimos aviso
#: de "modalidade nova"; nao e' dominio fechado e nao bloqueia nada.
ROTULOS_CONHECIDOS = frozenset({
    ROTULO_FULL, "cross_docking", "xd_drop_off", "self_service", "drop_off",
})

MODE_DIAGNOSTIC = "diagnostic"
MODE_INCREMENTAL = "incremental"
MODE_BACKFILL = "backfill"
MODE_FULL = "full"
MODE_AUTO = "auto"
MODES = (MODE_DIAGNOSTIC, MODE_INCREMENTAL, MODE_BACKFILL, MODE_FULL, MODE_AUTO)

#: Derivados da maturacao medida -- ver docstring. Mudar sem nova medicao e'
#: regressao de contrato.
INCREMENTAL_DAYS_BACK = 15
BACKFILL_DAYS_BACK = 45

#: Piso do modo `full`: primeira data a partir da qual a FONTE tem cobertura
#: integral de `api.ml_order_line_items`.
#:
#: NAO e' a data do primeiro envio (22/05/2025) nem o inicio do negocio. E' o
#: ponto medido a partir do qual toda venda paga tem item, e portanto unidade.
#: Medido em 15/09/2026 sobre 550.239 pedidos:
#:
#:     2025-05    338 pedidos pagos sem line item
#:     2025-06    190
#:     2025-07    802   (ultimo dia com falha: 27/07/2025)
#:     2025-08+     0   -- zero em 14 meses consecutivos
#:
#: O buraco e' INTERMITENTE, nao um inicio de ingestao: 21 e 22/07 estao limpos,
#: 23 a 27/07 quebrados, 28/07 em diante limpos. Por isso o piso e' alinhado ao
#: mes seguinte (01/08), quatro dias depois da ultima falha -- margem
#: deliberada contra reaparecimento pontual.
#:
#: Mai-jul/2025 ficam INDISPONIVEIS por incompletude da fonte. Nao sao meses com
#: zero: sao meses que nao podem ser medidos. Publicar essa janela gravaria
#: venda paga com zero unidade, que `ck_fmfd_unidade_exige_pedido_pago` recusa --
#: e com razao, porque unidade ausente nao e' unidade nula.
#:
#: Este piso so' pode ser REDUZIDO depois que a fonte for reparada E o modo full
#: reconciliar em diagnostico para a janela ampliada. Baixa-lo sem isso faz o
#: full voltar a falhar na primeira execucao.
FULL_SOURCE_COMPLETE_FROM_DATE = date(2025, 8, 1)

#: `marketplace_id` do Mercado Livre em `dim.dim_marketplace`.
AUDIT_MARKETPLACE_ID = 1

AUDIT_SOURCE = "ml_fulfillment_daily"
AUDIT_SOURCE_BACKFILL = "ml_fulfillment_daily_backfill"
AUDIT_SOURCE_FULL = "ml_fulfillment_daily_full"

TARGET_STATEMENT_TIMEOUT_MS = 600_000
SOURCE_STATEMENT_TIMEOUT_MS = 600_000

#: Advisory lock de SESSAO. Chave distinta de todo outro sync do repositorio.
ADVISORY_LOCK_KEY = 916140016

PUBLICACAO_NAO_TENTADA = "nao_tentada"
PUBLICACAO_INDETERMINADA = "indeterminada"
PUBLICACAO_COMMIT_CONFIRMADO = "commit_confirmado"

STATUS_PAGO = "paid"
STATUS_CANCELADO = "cancelled"


# ---------------------------------------------------------------------------
# Sanitizacao
# ---------------------------------------------------------------------------

_PADROES_SENSIVEIS = (
    re.compile(r"postgres(?:ql)?://[^\s]*", re.I),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    re.compile(r"password=\S+", re.I),
    re.compile(r"\bhost=\S+", re.I),
)


def sanitizar(exc: BaseException | str) -> str:
    """Mensagem sem DSN, IP, host, senha ou corpo de SQL."""
    texto = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    for padrao in _PADROES_SENSIVEIS:
        texto = padrao.sub("[REDACTED]", texto)
    return " ".join(texto.split())[:500]


# ---------------------------------------------------------------------------
# Janela
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    date_from: date
    date_to: date

    def __post_init__(self) -> None:
        if self.date_from > self.date_to:
            raise MLFulfillmentSyncError(
                f"janela invertida: {self.date_from.isoformat()} > "
                f"{self.date_to.isoformat()}."
            )

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1


def resolve_window(mode: str, agora: datetime | None = None,
                   date_from: date | None = None,
                   date_to: date | None = None) -> Window:
    """Janela do modo, sempre com teto em D-1 (America/Sao_Paulo).

    Funcao PURA: nao toca banco. Zero `date.today()`.
    """
    if date_from is not None or date_to is not None:
        if date_from is None or date_to is None:
            raise MLFulfillmentSyncError(
                "janela explicita exige --date-from E --date-to."
            )
        assert_closed_day(date_to, agora, rotulo="--date-to")
        return Window(date_from, date_to)

    if mode in (MODE_INCREMENTAL, MODE_DIAGNOSTIC):
        inicio, fim = closed_window(INCREMENTAL_DAYS_BACK, agora)
    elif mode == MODE_BACKFILL:
        inicio, fim = closed_window(BACKFILL_DAYS_BACK, agora)
    elif mode == MODE_FULL:
        inicio, fim = FULL_SOURCE_COMPLETE_FROM_DATE, last_closed_date(agora)
    else:
        raise MLFulfillmentSyncError(f"modo sem janela definida: {mode!r}")
    return Window(inicio, fim)


def audit_sources_for_mode(mode: str) -> tuple[str, ...]:
    """Nomes de auditoria do MODO EFETIVO.

    Backfill e full registram DUAS linhas: a canonica (que alimenta o frescor) e
    a da obrigacao durável. `auto` nunca aparece aqui -- quem chama ja' resolveu
    para o modo concreto, senao a auditoria registraria a intencao em vez do
    trabalho feito.
    """
    if mode == MODE_AUTO:
        raise MLFulfillmentSyncError(
            "auditoria exige modo efetivo, nunca 'auto': registrar a intencao "
            "em vez do trabalho feito tornaria a obrigacao durável mentirosa."
        )
    if mode == MODE_BACKFILL:
        return (AUDIT_SOURCE, AUDIT_SOURCE_BACKFILL)
    if mode == MODE_FULL:
        return (AUDIT_SOURCE, AUDIT_SOURCE_FULL)
    return (AUDIT_SOURCE,)


# ---------------------------------------------------------------------------
# Leitura da fonte -- UMA coorte, a do pedido
# ---------------------------------------------------------------------------

#: Classificacao em UM lugar so'. Repetir o CASE em cada SELECT seria a forma
#: mais barata de as duas tabelas discordarem sobre o que e' Full.
_CASE_CLASSE = f"""
    CASE
        WHEN e.logistic_type = '{ROTULO_FULL}'  THEN '{CLASSE_FULL}'
        WHEN e.logistic_type IS NULL            THEN '{CLASSE_UNKNOWN}'
        ELSE '{CLASSE_NON_FULL}'
    END
"""
_CASE_ROTULO = f"COALESCE(e.logistic_type, '{SENTINELA_LT}')"

#: UMA consulta, UMA coorte. Handling e delivery sao medidos do `date_created` do
#: PEDIDO ate' os eventos do envio dele -- a mesma populacao do GMV.
#:
#: O semi-join por EXISTS evita chutar margem de data: pega exatamente os envios
#: referenciados pelos pedidos da janela. Medido: identico ao recorte por janela
#: de datas, sem o risco de perder envio fora da margem.
#:
#: `DISTINCT ON` deduplica o envio por (brand, shipment_id) pela versao mais
#: recente. A fonte nao deveria ter duplicata; o preflight mede se tem.
SQL_AGREGADO = text(f"""
WITH pedidos AS (
    SELECT o.brand,
           o.order_id,
           o.date_created,
           o.date_created::date AS ref_date,
           o.status,
           o.total_amount,
           o.shipping_id,
           o.updated_at
      FROM api.ml_orders o
     WHERE o.date_created >= :date_from
       AND o.date_created <  :date_to_excl
),
envio AS (
    SELECT DISTINCT ON (s.brand, s.shipment_id)
           s.brand, s.shipment_id, s.logistic_type,
           s.date_shipped, s.date_delivered, s.shipping_items
      FROM api.ml_shipments s
     WHERE EXISTS (SELECT 1 FROM pedidos p
                    WHERE p.brand = s.brand AND p.shipping_id = s.shipment_id)
     ORDER BY s.brand, s.shipment_id, s.updated_at DESC NULLS LAST
),
unidades AS (
    SELECT l.brand, l.order_id, SUM(l.quantity)::bigint AS un
      FROM api.ml_order_line_items l
     WHERE EXISTS (SELECT 1 FROM pedidos p
                    WHERE p.brand = l.brand AND p.order_id = l.order_id)
     GROUP BY l.brand, l.order_id
),
classificado AS (
    SELECT p.ref_date,
           p.brand,
           {_CASE_CLASSE} AS fulfillment_class,
           {_CASE_ROTULO} AS logistic_type_original,
           p.status,
           p.total_amount,
           COALESCE(u.un, 0) AS un,
           (e.shipment_id IS NULL) AS sem_envio,
           (e.shipment_id IS NOT NULL
            AND (e.shipping_items IS NULL
                 OR jsonb_array_length(e.shipping_items) = 0)) AS sem_itens,
           -- Tempos do PEDIDO: da criacao DELE ate' o evento do envio dele.
           -- Eventos anteriores a criacao sao descartados como amostra
           -- invalida, nunca somados como negativo.
           CASE WHEN e.date_shipped IS NOT NULL
                 AND e.date_shipped >= p.date_created
                THEN EXTRACT(EPOCH FROM (e.date_shipped - p.date_created))
           END AS handling_s,
           CASE WHEN e.date_delivered IS NOT NULL
                 AND e.date_delivered >= p.date_created
                THEN EXTRACT(EPOCH FROM (e.date_delivered - p.date_created))
           END AS delivery_s,
           p.updated_at
      FROM pedidos p
      LEFT JOIN envio e
             ON e.brand = p.brand AND e.shipment_id = p.shipping_id
      LEFT JOIN unidades u
             ON u.brand = p.brand AND u.order_id = p.order_id
)
SELECT ref_date,
       brand,
       fulfillment_class,
       logistic_type_original,
       COUNT(*)::bigint                                             AS eligible_orders,
       COUNT(*) FILTER (WHERE status = :pago)::bigint               AS paid_orders,
       COUNT(*) FILTER (WHERE status = :cancelado)::bigint          AS cancelled_orders,
       COUNT(*) FILTER (WHERE status NOT IN (:pago, :cancelado))::bigint
                                                                    AS other_orders,
       COALESCE(SUM(total_amount) FILTER (WHERE status = :pago), 0) AS paid_gmv,
       COALESCE(SUM(un) FILTER (WHERE status = :pago), 0)::bigint   AS paid_units,
       COALESCE(SUM(handling_s), 0)::bigint                         AS handling_seconds_sum,
       COUNT(handling_s)::bigint                                    AS handling_sample_count,
       COALESCE(SUM(delivery_s), 0)::bigint                         AS delivery_seconds_sum,
       COUNT(delivery_s)::bigint                                    AS delivery_sample_count,
       COUNT(*) FILTER (WHERE sem_envio)::bigint                    AS unmatched_orders,
       COUNT(*) FILTER (WHERE sem_itens)::bigint                    AS missing_shipping_items,
       MAX(updated_at)                                              AS source_updated_at
  FROM classificado
 GROUP BY ref_date, brand, fulfillment_class, logistic_type_original
""")

#: Listing: somente pedidos PAGOS, valor alocado pelo item. Nunca rateio de
#: `total_amount` -- a alocacao e' determinística e o preflight mede se continua.
SQL_LISTING = text(f"""
WITH pedidos AS (
    SELECT o.brand, o.order_id, o.date_created::date AS ref_date, o.shipping_id
      FROM api.ml_orders o
     WHERE o.date_created >= :date_from
       AND o.date_created <  :date_to_excl
       AND o.status = :pago
),
envio AS (
    SELECT DISTINCT ON (s.brand, s.shipment_id)
           s.brand, s.shipment_id, s.logistic_type
      FROM api.ml_shipments s
     WHERE EXISTS (SELECT 1 FROM pedidos p
                    WHERE p.brand = s.brand AND p.shipping_id = s.shipment_id)
     ORDER BY s.brand, s.shipment_id, s.updated_at DESC NULLS LAST
)
SELECT p.ref_date,
       p.brand,
       l.item_id,
       {_CASE_CLASSE} AS fulfillment_class,
       {_CASE_ROTULO} AS logistic_type_original,
       COUNT(DISTINCT p.order_id)::bigint          AS paid_orders,
       SUM(l.quantity * l.unit_price)              AS paid_gmv,
       SUM(l.quantity)::bigint                     AS paid_units
  FROM pedidos p
  JOIN api.ml_order_line_items l
    ON l.brand = p.brand AND l.order_id = p.order_id
  LEFT JOIN envio e
         ON e.brand = p.brand AND e.shipment_id = p.shipping_id
 WHERE l.item_id IS NOT NULL AND BTRIM(l.item_id) <> ''
 GROUP BY p.ref_date, p.brand, l.item_id, e.logistic_type
""")

#: Preflight de fonte: prova de que as invariantes valem ANTES de qualquer
#: escrita. Mede, nao presume.
SQL_PREFLIGHT_FONTE = text("""
WITH pedidos AS (
    SELECT o.brand, o.order_id, o.shipping_id, o.status, o.total_amount
      FROM api.ml_orders o
     WHERE o.date_created >= :date_from AND o.date_created < :date_to_excl
),
li AS (
    SELECT l.brand, l.order_id, COUNT(*) AS linhas,
           SUM(l.quantity * l.unit_price) AS soma
      FROM api.ml_order_line_items l
     WHERE EXISTS (SELECT 1 FROM pedidos p
                    WHERE p.brand = l.brand AND p.order_id = l.order_id)
     GROUP BY l.brand, l.order_id
),
envio_dup AS (
    SELECT s.brand, s.shipment_id
      FROM api.ml_shipments s
     WHERE EXISTS (SELECT 1 FROM pedidos p
                    WHERE p.brand = s.brand AND p.shipping_id = s.shipment_id)
     GROUP BY s.brand, s.shipment_id
    HAVING COUNT(*) > 1
)
SELECT (SELECT COUNT(*) FROM pedidos)                                AS pedidos,
       (SELECT COUNT(*) FROM pedidos WHERE status = :pago)           AS pedidos_pagos,
       (SELECT COUNT(*) FROM envio_dup)                              AS envios_duplicados,
       (SELECT COUNT(*) FROM pedidos p WHERE NOT EXISTS
            (SELECT 1 FROM li WHERE li.brand = p.brand
                                AND li.order_id = p.order_id))       AS pedidos_sem_line_item,
       (SELECT COUNT(*) FROM pedidos p
          JOIN li ON li.brand = p.brand AND li.order_id = p.order_id
         WHERE p.status = :pago
           AND ABS(p.total_amount - li.soma) > 0.01)                 AS pedidos_alocacao_divergente,
       (SELECT COUNT(DISTINCT status) FROM pedidos)                  AS status_distintos
""")

#: Prova de que a FONTE responde, independente de a janela ter pedidos. Separa
#: "consulta bem-sucedida com zero pedidos" de "fonte indisponivel": a primeira
#: devolve 1 aqui, a segunda levanta antes de chegar.
SQL_SONDA_FONTE = text("SELECT 1 AS viva FROM api.ml_orders LIMIT 1")


@dataclass
class Row:
    ref_date: date
    brand: str
    fulfillment_class: str
    logistic_type_original: str
    eligible_orders: int = 0
    paid_orders: int = 0
    cancelled_orders: int = 0
    other_orders: int = 0
    paid_gmv: object = 0
    paid_units: int = 0
    handling_seconds_sum: int = 0
    handling_sample_count: int = 0
    delivery_seconds_sum: int = 0
    delivery_sample_count: int = 0
    unmatched_orders: int = 0
    missing_shipping_items: int = 0
    source_updated_at: datetime | None = None

    @property
    def key(self) -> tuple:
        return (self.ref_date, self.brand, self.fulfillment_class,
                self.logistic_type_original)


@dataclass
class ListingRow:
    ref_date: date
    brand: str
    item_id: str
    fulfillment_class: str
    logistic_type_original: str
    paid_orders: int
    paid_gmv: object
    paid_units: int


@dataclass
class SourceSnapshot:
    window: Window
    rows: list[Row] = field(default_factory=list)
    listing_rows: list[ListingRow] = field(default_factory=list)
    preflight: dict = field(default_factory=dict)
    #: A fonte respondeu a sonda. False so' acontece se a leitura nem chegou --
    #: e nesse caso o fluxo ja' levantou `SourceUnavailableError`.
    source_alive: bool = False

    @property
    def janela_vazia(self) -> bool:
        """Fonte saudavel e janela legitimamente sem pedido."""
        return self.source_alive and not self.rows


def read_source(conn, window: Window) -> SourceSnapshot:
    """Le a fonte. Levanta `SourceUnavailableError` se ela nao responder.

    A sonda vem PRIMEIRO, de proposito: ela distingue "fonte viva, janela vazia"
    de "fonte morta". Sem ela, uma falha de conectividade que devolvesse zero
    linhas seria indistinguivel de um mes sem vendas -- e o DELETE apagaria a
    janela publicada.
    """
    try:
        conn.execute(text(f"SET LOCAL statement_timeout = "
                          f"{SOURCE_STATEMENT_TIMEOUT_MS}"))
        viva = conn.execute(SQL_SONDA_FONTE).scalar()
    except Exception as exc:
        raise SourceUnavailableError(
            f"fonte nao respondeu a sonda: {sanitizar(exc)}"
        ) from exc
    if viva is None:
        raise SourceUnavailableError(
            "api.ml_orders respondeu sem nenhuma linha em toda a tabela: isso "
            "e' fonte estruturalmente vazia, nao janela vazia. Publicar sobre "
            "isso apagaria historico."
        )

    params = {
        "date_from": window.date_from,
        "date_to_excl": window.date_to + timedelta(days=1),
        "pago": STATUS_PAGO,
        "cancelado": STATUS_CANCELADO,
    }
    try:
        preflight = dict(conn.execute(SQL_PREFLIGHT_FONTE, {
            k: v for k, v in params.items() if k != "cancelado"
        }).mappings().one())
        rows = [Row(**dict(m))
                for m in conn.execute(SQL_AGREGADO, params).mappings()]
        listing = [ListingRow(**dict(m)) for m in conn.execute(SQL_LISTING, {
            k: v for k, v in params.items() if k != "cancelado"
        }).mappings()]
    except Exception as exc:
        raise SourceUnavailableError(
            f"falha ao extrair da fonte: {sanitizar(exc)}"
        ) from exc

    return SourceSnapshot(window=window,
                          rows=sorted(rows, key=lambda r: r.key),
                          listing_rows=listing,
                          preflight=preflight,
                          source_alive=True)


# ---------------------------------------------------------------------------
# Validacao de contrato
# ---------------------------------------------------------------------------


def validate_contract(snapshot: SourceSnapshot) -> list[str]:
    """Invariantes que BLOQUEIAM e invariantes que apenas avisam."""
    avisos: list[str] = []
    pf = snapshot.preflight

    if not snapshot.source_alive:
        raise SourceUnavailableError(
            "snapshot sem prova de fonte viva: recusado antes de qualquer "
            "escrita."
        )

    if not snapshot.rows:
        # Janela legitimamente vazia e' PUBLICAVEL (a guarda contra apagar
        # janela ja' publicada mora em `publish_in_transaction`). O que nao
        # existe mais e' o bloqueio cego que tratava vazio como erro.
        avisos.append(
            f"janela {snapshot.window.date_from.isoformat()}..."
            f"{snapshot.window.date_to.isoformat()} sem nenhum pedido; a fonte "
            "respondeu normalmente, entao a ausencia e' medicao, nao falha."
        )
        return avisos

    if pf.get("envios_duplicados"):
        avisos.append(
            f"{pf['envios_duplicados']} shipment_id duplicado(s) na fonte; a "
            "leitura deduplica por (brand, shipment_id) antes de classificar."
        )

    if pf.get("pedidos_alocacao_divergente"):
        raise MLFulfillmentSyncError(
            f"{pf['pedidos_alocacao_divergente']} pedido(s) pago(s) com "
            "SUM(quantity*unit_price) divergindo de total_amount em mais de "
            "R$ 0,01. A alocacao por listing deixou de ser determinística: "
            "publicar GMV por listing agora seria rateio, nao medicao."
        )

    if pf.get("pedidos_sem_line_item"):
        avisos.append(
            f"{pf['pedidos_sem_line_item']} pedido(s) sem line item: as unidades "
            "desses pedidos entram como ausencia, nunca como zero de venda."
        )

    for r in snapshot.rows:
        if r.fulfillment_class not in CLASSES_VALIDAS:
            raise MLFulfillmentSyncError(
                f"classe desconhecida {r.fulfillment_class!r} em {r.key}."
            )
        eh_unknown = r.fulfillment_class == CLASSE_UNKNOWN
        tem_sentinela = r.logistic_type_original == SENTINELA_LT
        if eh_unknown != tem_sentinela:
            raise MLFulfillmentSyncError(
                f"classe e rotulo incoerentes em {r.key}: unknown e sentinela "
                "tem de andar juntos."
            )
        if r.fulfillment_class == CLASSE_FULL and \
                r.logistic_type_original != ROTULO_FULL:
            raise MLFulfillmentSyncError(
                f"classe full com rotulo {r.logistic_type_original!r} em {r.key}."
            )
        # Fechamento EXATO das populacoes: nenhum status pode sumir.
        if r.paid_orders + r.cancelled_orders + r.other_orders != r.eligible_orders:
            raise MLFulfillmentSyncError(
                f"populacoes nao fecham em {r.key}: pagos({r.paid_orders}) + "
                f"cancelados({r.cancelled_orders}) + outros({r.other_orders}) "
                f"!= elegiveis({r.eligible_orders}). Algum status ficou sem casa."
            )
        if (r.paid_orders == 0) != (r.paid_units == 0):
            raise MLFulfillmentSyncError(
                f"pedido pago e unidade discordam em {r.key}: "
                f"paid_orders={r.paid_orders}, paid_units={r.paid_units}."
            )
        if r.unmatched_orders and r.fulfillment_class != CLASSE_UNKNOWN:
            raise MLFulfillmentSyncError(
                f"{r.unmatched_orders} pedido(s) sem envio classificados como "
                f"{r.fulfillment_class!r} em {r.key}: ausencia de envio so' pode "
                "produzir unknown."
            )
        # A amostra de tempo nunca pode exceder a coorte: se exceder, o grao
        # escorregou de volta para o envio.
        for nome, n in (("handling", r.handling_sample_count),
                        ("delivery", r.delivery_sample_count)):
            if n > r.eligible_orders:
                raise MLFulfillmentSyncError(
                    f"amostra de {nome} ({n}) maior que a coorte "
                    f"({r.eligible_orders}) em {r.key}: a medida escapou do "
                    "grao do pedido."
                )
        if r.handling_sample_count == 0 and r.handling_seconds_sum != 0:
            raise MLFulfillmentSyncError(
                f"handling com soma sem amostra em {r.key}.")
        if r.delivery_sample_count == 0 and r.delivery_seconds_sum != 0:
            raise MLFulfillmentSyncError(
                f"delivery com soma sem amostra em {r.key}.")
        if r.fulfillment_class == CLASSE_UNKNOWN and (
                r.handling_sample_count or r.delivery_sample_count):
            raise MLFulfillmentSyncError(
                f"classe unknown com amostra de tempo em {r.key}: sem envio nao "
                "existe despacho nem entrega."
            )

    rotulos = {r.logistic_type_original for r in snapshot.rows} - {SENTINELA_LT}
    desconhecidos = rotulos - ROTULOS_CONHECIDOS
    if desconhecidos:
        # AVISO, nunca bloqueio: modalidade nova do ML tem de entrar publicada
        # como non_full, com o rotulo preservado.
        avisos.append(
            "modalidade(s) logistica(s) nao catalogada(s), publicada(s) como "
            f"{CLASSE_NON_FULL} com rotulo preservado: {sorted(desconhecidos)}."
        )

    return avisos


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------

_COLS = ("ref_date", "brand", "fulfillment_class", "logistic_type_original",
         "eligible_orders", "paid_orders", "cancelled_orders", "other_orders",
         "paid_gmv", "paid_units", "handling_seconds_sum",
         "handling_sample_count", "delivery_seconds_sum",
         "delivery_sample_count", "unmatched_orders", "missing_shipping_items",
         "source_updated_at", "source_run_id")

_COLS_L = ("ref_date", "brand", "item_id", "fulfillment_class",
           "logistic_type_original", "paid_orders", "paid_gmv", "paid_units",
           "source_run_id")

_LISTA = ", ".join(_COLS)
_BINDS = ", ".join(f":{c}" for c in _COLS)
_LISTA_L = ", ".join(_COLS_L)
_BINDS_L = ", ".join(f":{c}" for c in _COLS_L)

SQL_STAGING_CREATE = text(f"""
    CREATE TEMP TABLE stg_fmfd (LIKE {FACT_TABLE} INCLUDING DEFAULTS)
        ON COMMIT DROP
""")
SQL_STAGING_CREATE_L = text(f"""
    CREATE TEMP TABLE stg_fmfld (LIKE {LISTING_TABLE} INCLUDING DEFAULTS)
        ON COMMIT DROP
""")
SQL_STAGING_INSERT = text(f"INSERT INTO stg_fmfd ({_LISTA}) VALUES ({_BINDS})")
SQL_STAGING_INSERT_L = text(
    f"INSERT INTO stg_fmfld ({_LISTA_L}) VALUES ({_BINDS_L})")

SQL_DELETE_JANELA = text(
    f"DELETE FROM {FACT_TABLE} WHERE ref_date BETWEEN :date_from AND :date_to")
SQL_DELETE_JANELA_L = text(
    f"DELETE FROM {LISTING_TABLE} WHERE ref_date BETWEEN :date_from AND :date_to")
SQL_INSERT_DO_STAGING = text(
    f"INSERT INTO {FACT_TABLE} ({_LISTA}) SELECT {_LISTA} FROM stg_fmfd")
SQL_INSERT_DO_STAGING_L = text(
    f"INSERT INTO {LISTING_TABLE} ({_LISTA_L}) SELECT {_LISTA_L} FROM stg_fmfld")

#: EXCEPT bidirecional: prova que o destino ficou identico ao staging na janela.
#: Roda SEMPRE dentro da transacao, ANTES do commit -- e' o unico momento em que
#: a divergencia ainda pode ser desfeita. Depois do commit, o que sobraria seria
#: um relatorio de estrago, nao uma reconciliacao.
#: `ingested_at` fica FORA da comparacao: e' o unico campo que muda a cada
#: execucao por construcao.
SQL_EXCEPT = text(f"""
    WITH destino AS (
        SELECT {_LISTA} FROM {FACT_TABLE}
         WHERE ref_date BETWEEN :date_from AND :date_to
    ),
    so_staging AS (SELECT {_LISTA} FROM stg_fmfd EXCEPT SELECT {_LISTA} FROM destino),
    so_destino AS (SELECT {_LISTA} FROM destino EXCEPT SELECT {_LISTA} FROM stg_fmfd)
    SELECT (SELECT COUNT(*) FROM so_staging) AS so_staging,
           (SELECT COUNT(*) FROM so_destino) AS so_destino
""")
SQL_EXCEPT_L = text(f"""
    WITH destino AS (
        SELECT {_LISTA_L} FROM {LISTING_TABLE}
         WHERE ref_date BETWEEN :date_from AND :date_to
    ),
    so_staging AS (SELECT {_LISTA_L} FROM stg_fmfld EXCEPT SELECT {_LISTA_L} FROM destino),
    so_destino AS (SELECT {_LISTA_L} FROM destino EXCEPT SELECT {_LISTA_L} FROM stg_fmfld)
    SELECT (SELECT COUNT(*) FROM so_staging) AS so_staging,
           (SELECT COUNT(*) FROM so_destino) AS so_destino
""")

SQL_RECONCILIA_DESTINO = text(f"""
    SELECT COUNT(*)::bigint                        AS linhas,
           COALESCE(SUM(paid_gmv), 0)              AS gmv,
           COALESCE(SUM(paid_orders), 0)::bigint   AS pedidos,
           COALESCE(SUM(paid_units), 0)::bigint    AS unidades
      FROM {FACT_TABLE}
     WHERE ref_date BETWEEN :date_from AND :date_to
""")


def publish_in_transaction(conn, snapshot: SourceSnapshot, run_id: str) -> int:
    """Staging -> DELETE da janela -> INSERT -> EXCEPT. UMA transacao.

    Ordem obrigatoria, e a razao de cada passo:

      1. staging     -- monta o alvo antes de tocar no destino
      2. DELETE      -- limitado a janela; recarga integral, nao UPSERT, porque
                        um pedido que virou cancelled precisa SAIR de paid_gmv
      3. INSERT      -- do staging
      4. EXCEPT x2   -- ANTES do commit: e' o unico ponto em que divergencia
                        ainda pode ser desfeita
    """
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    conn.execute(SQL_STAGING_CREATE)
    conn.execute(SQL_STAGING_CREATE_L)

    janela = {"date_from": snapshot.window.date_from,
              "date_to": snapshot.window.date_to}

    if not snapshot.rows:
        # Janela vazia: so' pode apagar o destino se ele TAMBEM estiver vazio.
        # Caso contrario estariamos deixando uma leitura sem dado destruir uma
        # janela ja' publicada -- o modo de falha que a Fase 6 existe para
        # impedir.
        atual = conn.execute(SQL_RECONCILIA_DESTINO, janela).mappings().one()
        if atual["linhas"]:
            raise MLFulfillmentSyncError(
                f"fonte devolveu zero linhas para a janela, mas o destino tem "
                f"{atual['linhas']} linha(s) publicada(s). Recusado: uma leitura "
                "vazia nao apaga historico. Investigue a fonte antes de repetir."
            )
        return 0

    conn.execute(SQL_STAGING_INSERT, [{
        "ref_date": r.ref_date, "brand": r.brand,
        "fulfillment_class": r.fulfillment_class,
        "logistic_type_original": r.logistic_type_original,
        "eligible_orders": r.eligible_orders, "paid_orders": r.paid_orders,
        "cancelled_orders": r.cancelled_orders, "other_orders": r.other_orders,
        "paid_gmv": r.paid_gmv, "paid_units": r.paid_units,
        "handling_seconds_sum": r.handling_seconds_sum,
        "handling_sample_count": r.handling_sample_count,
        "delivery_seconds_sum": r.delivery_seconds_sum,
        "delivery_sample_count": r.delivery_sample_count,
        "unmatched_orders": r.unmatched_orders,
        "missing_shipping_items": r.missing_shipping_items,
        "source_updated_at": r.source_updated_at, "source_run_id": run_id,
    } for r in snapshot.rows])

    if snapshot.listing_rows:
        conn.execute(SQL_STAGING_INSERT_L, [{
            "ref_date": r.ref_date, "brand": r.brand, "item_id": r.item_id,
            "fulfillment_class": r.fulfillment_class,
            "logistic_type_original": r.logistic_type_original,
            "paid_orders": r.paid_orders, "paid_gmv": r.paid_gmv,
            "paid_units": r.paid_units, "source_run_id": run_id,
        } for r in snapshot.listing_rows])

    conn.execute(SQL_DELETE_JANELA, janela)
    conn.execute(SQL_INSERT_DO_STAGING)
    conn.execute(SQL_DELETE_JANELA_L, janela)
    conn.execute(SQL_INSERT_DO_STAGING_L)

    for sql, rotulo in ((SQL_EXCEPT, FACT_TABLE), (SQL_EXCEPT_L, LISTING_TABLE)):
        diff = conn.execute(sql, janela).mappings().one()
        if diff["so_staging"] or diff["so_destino"]:
            raise MLFulfillmentSyncError(
                f"EXCEPT bidirecional falhou em {rotulo}: {diff['so_staging']} "
                f"linha(s) so' no staging, {diff['so_destino']} so' no destino. "
                "Transacao inteira desfeita."
            )
    return len(snapshot.rows)


# ---------------------------------------------------------------------------
# Auditoria -- conexao INDEPENDENTE da publicacao
# ---------------------------------------------------------------------------

SQL_AUDIT_START = text("""
    INSERT INTO audit.source_sync_run
        (source_name, marketplace_id, status, source_min_date, source_max_date)
    VALUES (:fonte, :mkt, 'running', :min_date, :max_date)
    RETURNING sync_run_id
""")
SQL_AUDIT_FINISH = text("""
    UPDATE audit.source_sync_run
       SET status = :status, finished_at = NOW(),
           rows_extracted = :extracted, rows_loaded = :loaded,
           error_message = :erro
     WHERE sync_run_id = :sync_run_id
""")
SQL_ULTIMO_SUCESSO = text("""
    SELECT MAX(finished_at) AS ultimo
      FROM audit.source_sync_run
     WHERE source_name = :fonte AND status = 'success'
       AND finished_at IS NOT NULL AND finished_at >= :desde
""")

#: Lock de SESSAO e FAIL-FAST.
#:
#: SESSAO (nao transacional): sobrevive ao fim de cada statement, entao cobre
#: decisao, leitura da fonte e publicacao sem manter transacao ociosa no Neon.
#:
#: FAIL-FAST (`try`, nao a variante bloqueante): `pg_advisory_lock` espera
#: INDEFINIDAMENTE quando outra execucao detem a chave -- sem erro, sem log e
#: sem limite, porque `lock_timeout` nao esta configurado em lugar nenhum deste
#: caminho. O `auto` diario sobrepondo um `full` manual deixaria um processo
#: pendurado ate' alguem mata-lo.
#:
#: `pg_try_advisory_lock` devolve BOOLEANO na hora: `true` adquiriu, `false`
#: alguem mais tem. A segunda execucao nao tem nada de util a fazer esperando --
#: a janela dela sera recalculada do zero na proxima tentativa.
SQL_TRY_LOCK = text("SELECT pg_try_advisory_lock(:chave) AS obtido")
SQL_UNLOCK = text("SELECT pg_advisory_unlock(:chave)")


def audit_start(conn, sources: tuple[str, ...], window: Window) -> dict[str, int]:
    """Abre uma linha `running` por nome, ANTES da leitura e da publicacao.

    `source_min_date`/`source_max_date` sao os limites da JANELA pedida -- nao
    os dados encontrados. Semantica honesta: dizem o que foi varrido, nao o que
    existia.
    """
    return {f: conn.execute(SQL_AUDIT_START, {
        "fonte": f, "mkt": AUDIT_MARKETPLACE_ID,
        "min_date": window.date_from, "max_date": window.date_to,
    }).scalar_one() for f in sources}


def audit_finish(conn, ids: dict[str, int], status: str,
                 extracted: int, loaded: int, erro: str | None) -> None:
    """Fecha TODAS as linhas com o mesmo resultado factual.

    `rows_extracted` = pedidos ELEGIVEIS lidos da fonte (grao do pedido).
    `rows_loaded`    = linhas da fato AGREGADA (grao do dia/marca/classe).
    Os dois nao se comparam, e as linhas de listing NAO entram em nenhum dos
    dois: somar graos diferentes num inteiro so' produziria um numero que nao
    significa nada.
    """
    if status not in ("success", "failed"):
        raise MLFulfillmentSyncError(f"status de auditoria invalido: {status!r}")
    if status == "success" and erro is not None:
        raise MLFulfillmentSyncError(
            "execucao success nao pode gravar error_message."
        )
    for sync_run_id in ids.values():
        res = conn.execute(SQL_AUDIT_FINISH, {
            "status": status, "extracted": extracted, "loaded": loaded,
            "erro": erro, "sync_run_id": sync_run_id,
        })
        if res.rowcount != 1:
            raise MLFulfillmentSyncError(
                f"UPDATE de auditoria afetou {res.rowcount} linha(s); "
                "esperado exatamente 1."
            )


def _inicio_mes_brt(agora: datetime | None = None) -> datetime:
    hoje = (agora or datetime.now(timezone.utc)).astimezone(OPERATIONAL_TZ).date()
    return datetime(hoje.year, hoje.month, 1, tzinfo=OPERATIONAL_TZ)


def decide_effective_mode(conn, agora: datetime | None = None) -> str:
    """Resolve `auto` -> full | backfill | incremental. SOB O LOCK.

    Decidir antes do lock abriria corrida: duas execucoes leriam a mesma
    ausencia de `_full` no mes e ambas escolheriam full, a segunda reconstruindo
    o destino em cima da primeira.

    A obrigacao so' e' considerada cumprida por execucao `success` FINALIZADA --
    `running` e `failed` nao contam, senao uma tentativa que morreu no meio
    faria o full do mes parecer feito.
    """
    instante = agora or datetime.now(timezone.utc)
    if conn.execute(SQL_ULTIMO_SUCESSO, {
        "fonte": AUDIT_SOURCE_FULL, "desde": _inicio_mes_brt(instante),
    }).scalar() is None:
        return MODE_FULL
    if conn.execute(SQL_ULTIMO_SUCESSO, {
        "fonte": AUDIT_SOURCE_BACKFILL,
        "desde": instante - timedelta(days=7),
    }).scalar() is None:
        return MODE_BACKFILL
    return MODE_INCREMENTAL


# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------


def summarize(snapshot: SourceSnapshot) -> dict:
    """Agregados da janela. Funcao PURA -- testavel sem banco.

    Shares vem como fracao ou `None`; NUNCA como zero quando o denominador e'
    zero. `unknown` NAO entra no denominador dos shares de Full: ele nao e' nem
    Full nem nao-Full, e inclui-lo faria o share cair por causa de pedidos cuja
    modalidade simplesmente nao conhecemos.
    """
    def soma(campo, classes=None):
        return sum(getattr(r, campo) for r in snapshot.rows
                   if classes is None or r.fulfillment_class in classes)

    classificadas = (CLASSE_FULL, CLASSE_NON_FULL)
    gmv_full = soma("paid_gmv", (CLASSE_FULL,))
    gmv_class = soma("paid_gmv", classificadas)
    ped_full = soma("paid_orders", (CLASSE_FULL,))
    ped_class = soma("paid_orders", classificadas)
    un_full = soma("paid_units", (CLASSE_FULL,))
    un_class = soma("paid_units", classificadas)

    def share(num, den):
        return float(num) / float(den) if den else None

    return {
        "rows": len(snapshot.rows),
        "listing_rows": len(snapshot.listing_rows),
        "empty_window": snapshot.janela_vazia,
        "paid_gmv_total": soma("paid_gmv"),
        "paid_gmv_full": gmv_full,
        "share_full_gmv": share(gmv_full, gmv_class),
        "share_full_orders": share(ped_full, ped_class),
        "share_full_units": share(un_full, un_class),
        "paid_orders_total": soma("paid_orders"),
        "paid_units_total": soma("paid_units"),
        "eligible_orders": soma("eligible_orders"),
        "cancelled_orders": soma("cancelled_orders"),
        "other_orders": soma("other_orders"),
        "unmatched_orders": soma("unmatched_orders"),
        "missing_shipping_items": soma("missing_shipping_items"),
        "unknown_orders": soma("eligible_orders", (CLASSE_UNKNOWN,)),
        "handling_sample_count": soma("handling_sample_count"),
        "delivery_sample_count": soma("delivery_sample_count"),
        "logistic_types": sorted({r.logistic_type_original
                                  for r in snapshot.rows}),
    }


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------


def _default_lock_connection():
    """Conexao dedicada ao advisory lock, em AUTOCOMMIT.

    Autocommit de proposito: o lock de SESSAO nao precisa de transacao, e abrir
    uma so' para segura-lo recriaria o `idle in transaction` que esta revisao
    veio eliminar.
    """
    engine = LocalSession.kw.get("bind")
    if engine is None:
        raise MLFulfillmentSyncError("destino nao configurado (sem engine).")
    return engine.connect().execution_options(isolation_level="AUTOCOMMIT")


def run_dry(mode: str, agora: datetime | None = None,
            date_from: date | None = None, date_to: date | None = None,
            *, dm_factory=None) -> dict:
    """Diagnostico SEM NENHUMA conexao gravavel -- nem para o lock."""
    if mode == MODE_AUTO:
        raise MLFulfillmentSyncError(
            "--mode auto exige --apply: a decisao durável le a auditoria no "
            "destino, e dry-run nao abre conexao ao destino."
        )
    instante = agora or datetime.now(timezone.utc)
    window = resolve_window(mode, instante, date_from, date_to)
    fabrica = dm_factory or DataMartSession
    if fabrica is None:
        raise MLFulfillmentSyncError("Data Mart nao configurado.")

    sessao = fabrica()
    try:
        snapshot = read_source(sessao.connection(), window)
    finally:
        sessao.rollback()
        sessao.close()

    avisos = validate_contract(snapshot)
    resumo = summarize(snapshot)
    resumo.update({
        "mode": mode, "applied": False,
        "date_from": window.date_from, "date_to": window.date_to,
        "preflight": dict(snapshot.preflight),
        "warnings": avisos,
    })
    return resumo


def run_apply(mode: str, agora: datetime | None = None,
              *, dm_factory=None, neon_factory=None, lock_factory=None) -> dict:
    """Execucao real. Ordem travada:

        lock de sessao (autocommit)
          -> decide modo -> auditoria running
          -> LE FONTE (sem transacao gravavel aberta)
          -> valida
          -> abre transacao gravavel
          -> reconcilia antes -> publica -> EXCEPT -> reconcilia depois
          -> marca indeterminada -> commit
          -> auditoria success
        finally: unlock -> close

    A transacao gravavel do Neon so' nasce DEPOIS de a fonte estar lida e
    validada: nada de `idle in transaction` enquanto se espera o Data Mart.
    """
    fabrica_dm = dm_factory or DataMartSession
    fabrica_neon = neon_factory or LocalSession
    fabrica_lock = lock_factory or _default_lock_connection
    if fabrica_dm is None:
        raise MLFulfillmentSyncError("Data Mart nao configurado.")

    instante = agora or datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex[:32]
    publicacao = PUBLICACAO_NAO_TENTADA
    ids: dict[str, int] = {}
    extracted = loaded = 0
    modo_efetivo = mode
    lock_obtido = False

    conn_lock = fabrica_lock()
    sessao_audit = None
    sessao_pub = None

    try:
        # PRIMEIRA coisa da execucao, e a unica antes da decisao de seguir.
        # `scalar()` le o booleano de verdade: disparar o SELECT sem inspecionar
        # o retorno daria a mesma aparencia de sucesso com ou sem o lock.
        obtido = conn_lock.execute(
            SQL_TRY_LOCK, {"chave": ADVISORY_LOCK_KEY}).scalar()

        # `is not True` de proposito, nao `if not obtido`: qualquer coisa que
        # nao seja exatamente True -- None de conexao encerrada, driver que
        # devolva 0, resultado vazio -- e' tratada como NAO adquirido. Nunca
        # presumir posse do lock a partir de um valor ambiguo.
        if obtido is not True:
            raise ConcurrentRunError(
                "outra execucao de ml_fulfillment_daily ja' detem o lock "
                f"{ADVISORY_LOCK_KEY}. Nada foi lido, auditado ou publicado. "
                "Sem espera e sem retry: aguarde a execucao em andamento "
                "terminar e rode de novo."
            )
        lock_obtido = True

        # Recursos do destino so' depois do lock: criar a sessao de auditoria
        # antes abriria conexao para uma execucao que talvez nem comece.
        sessao_audit = fabrica_neon()

        if mode == MODE_AUTO:
            modo_efetivo = decide_effective_mode(conn_lock, instante)
        window = resolve_window(modo_efetivo, instante)

        ids = audit_start(sessao_audit.connection(),
                          audit_sources_for_mode(modo_efetivo), window)
        sessao_audit.commit()

        # --- Leitura da fonte: nenhuma transacao gravavel aberta aqui ---
        sessao_dm = fabrica_dm()
        try:
            snapshot = read_source(sessao_dm.connection(), window)
        finally:
            sessao_dm.rollback()
            sessao_dm.close()

        avisos = validate_contract(snapshot)
        resumo = summarize(snapshot)
        extracted = resumo["eligible_orders"]

        # --- So' agora abre a transacao gravavel ---
        sessao_pub = fabrica_neon()
        conn_pub = sessao_pub.connection()

        antes = dict(conn_pub.execute(SQL_RECONCILIA_DESTINO, {
            "date_from": window.date_from, "date_to": window.date_to,
        }).mappings().one())

        loaded = publish_in_transaction(conn_pub, snapshot, run_id)

        depois = dict(conn_pub.execute(SQL_RECONCILIA_DESTINO, {
            "date_from": window.date_from, "date_to": window.date_to,
        }).mappings().one())

        # A partir daqui NINGUEM sabe se o servidor efetivou.
        publicacao = PUBLICACAO_INDETERMINADA
        sessao_pub.commit()
        publicacao = PUBLICACAO_COMMIT_CONFIRMADO

        resultado = dict(resumo)
        resultado.update({
            "mode": mode, "effective_mode": modo_efetivo, "applied": True,
            "date_from": window.date_from, "date_to": window.date_to,
            "run_id": run_id, "warnings": avisos,
            "preflight": dict(snapshot.preflight),
            "reconciliation": {"before": antes, "after": depois},
            "no_op": bool(antes == depois),
            "rows_loaded": loaded,
            "rows_extracted": extracted,
        })

        try:
            audit_finish(sessao_audit.connection(), ids, "success",
                         extracted, loaded, None)
            sessao_audit.commit()
        except Exception as aud:
            # A publicacao ESTA COMMITADA. Auditoria que falha aqui e' perda de
            # rastro, nao perda de dado -- e marcar `failed` diria ao proximo
            # run que nada foi publicado, o que e' falso e faria o `auto`
            # refazer trabalho sobre dado bom.
            logger.error(
                "publicacao COMMITADA mas auditoria nao registrou success: %s",
                sanitizar(aud))
            try:
                sessao_audit.rollback()
            except Exception:   # pragma: no cover
                pass
            resultado["audit_status"] = "nao_registrada_apos_commit"
        return resultado

    except Exception as exc:
        msg = sanitizar(exc)
        if publicacao == PUBLICACAO_INDETERMINADA:
            # Commit levantou: o servidor pode ter efetivado. Sem rollback cego
            # e sem retry -- reexecutar sem saber o estado poderia republicar
            # sobre uma janela ja' correta.
            msg = (f"ESTADO INDETERMINADO apos commit: {msg}. Verifique "
                   f"{FACT_TABLE} na janela antes de qualquer reexecucao; nao "
                   "reexecute automaticamente.")
        elif publicacao == PUBLICACAO_NAO_TENTADA and sessao_pub is not None:
            try:
                sessao_pub.rollback()
            except Exception as rb:   # pragma: no cover
                logger.error("rollback falhou: %s", sanitizar(rb))
        logger.error("sync ml_fulfillment_daily falhou: %s", msg)

        # `failed` afirma que NADA foi publicado. So' e' verdade quando a
        # publicacao nem foi tentada. `sessao_audit is None` significa que a
        # execucao morreu antes do lock: nao ha linha de auditoria para fechar.
        #
        # Em PUBLICACAO_INDETERMINADA a linha fica em `running` de proposito:
        # esse E' o estado honesto -- ninguem sabe se o servidor efetivou. Um
        # `running` preso e' um alarme visivel que exige inspecao humana;
        # `failed` seria uma afirmacao falsa que ainda faria o `auto` refazer o
        # trabalho por cima de dado possivelmente bom.
        if ids and sessao_audit is not None and \
                publicacao == PUBLICACAO_NAO_TENTADA:
            try:
                audit_finish(sessao_audit.connection(), ids, "failed",
                             extracted, loaded, msg)
                sessao_audit.commit()
            except Exception as aud:
                logger.error("auditoria pos-falha nao registrada: %s",
                             sanitizar(aud))
                try:
                    sessao_audit.rollback()
                except Exception:   # pragma: no cover
                    pass
        # Preserva a SUBCLASSE EXATA: cada uma pede uma acao diferente do
        # operador -- `ConcurrentRunError` pede esperar, `SourceUnavailableError`
        # pede tentar mais tarde, o erro base pede investigar. Rebaixar todas
        # para a classe base apagaria justamente essa informacao.
        #
        # `type(exc)` em vez de uma cadeia de `isinstance`: subclasse nova criada
        # depois deste ponto continua preservada sem ninguem lembrar de vir aqui.
        classe_erro = (type(exc) if isinstance(exc, MLFulfillmentSyncError)
                       else MLFulfillmentSyncError)
        raise classe_erro(msg) from exc
    finally:
        # `finally`, nao `except`: KeyboardInterrupt e SystemExit nao passam
        # pelo `except Exception` acima, mas passam por aqui. Ctrl+C libera o
        # lock e a excecao segue propagando -- sem isso, um Ctrl+C no meio da
        # carga deixaria a chave presa ate' a conexao morrer.
        #
        # Unlock explicito e depois close, nesta ordem: o close sozinho ja'
        # liberaria o lock ao devolver a conexao, mas depender disso deixaria o
        # lock preso caso a conexao fosse mantida viva por um pool.
        #
        # `if lock_obtido` e' obrigatorio: `pg_advisory_unlock` de uma chave que
        # esta sessao nunca adquiriu emite WARNING e devolve false -- e, pior,
        # esconderia um bug de contagem se algum dia o lock virasse reentrante.
        if lock_obtido:
            try:
                conn_lock.execute(SQL_UNLOCK, {"chave": ADVISORY_LOCK_KEY})
            except Exception as ul:   # pragma: no cover
                logger.error("unlock falhou: %s", sanitizar(ul))
        for recurso in (sessao_pub, sessao_audit, conn_lock):
            if recurso is None:
                continue
            try:
                recurso.close()
            except Exception:   # pragma: no cover
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sync de marts.fact_ml_fulfillment_daily (Gate FULL-1A). "
                    "Sem --apply nao escreve nada, em lugar nenhum.")
    p.add_argument("--mode", choices=MODES, default=MODE_DIAGNOSTIC)
    p.add_argument("--apply", action="store_true",
                   help="executa de verdade; sem isso e' read-only.")
    p.add_argument("--date-from", type=date.fromisoformat,
                   help="somente em dry-run.")
    p.add_argument("--date-to", type=date.fromisoformat,
                   help="somente em dry-run.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.apply:
            if args.date_from is not None or args.date_to is not None:
                raise MLFulfillmentSyncError(
                    "janela explicita nao e' permitida com --apply: os modos "
                    "tem obrigacao durável registrada em auditoria, e uma "
                    "janela arbitraria a consumiria sem fazer o trabalho "
                    "correspondente. Use --date-from/--date-to em dry-run."
                )
            if args.mode == MODE_DIAGNOSTIC:
                raise MLFulfillmentSyncError(
                    "--mode diagnostic nao escreve: escolha incremental, "
                    "backfill, full ou auto."
                )
            saida = run_apply(args.mode)
        else:
            saida = run_dry(args.mode, date_from=args.date_from,
                            date_to=args.date_to)
    except MLFulfillmentSyncError as exc:
        print(json.dumps({"status": "failed", "error": sanitizar(exc)},
                         ensure_ascii=False))
        return 1
    print(json.dumps(saida, ensure_ascii=False, default=_json_default,
                     sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
