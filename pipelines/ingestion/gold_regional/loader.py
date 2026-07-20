"""
Loader operacional da Gold regional (`gold.marketplace_region_daily`) —
carga inicial (Gate 6A.3) e refresh incremental (Gate 6C).

`execute_first_load()` é a carga ÚNICA original: recalcula o histórico
INTEIRO da fonte sem filtro de data e faz um INSERT simples (sem TRUNCATE)
numa tabela com `UNIQUE (date, marketplace_id, loja_id, uf)`. Chamá-la de
novo depois de qualquer carga bem-sucedida SEMPRE falha por violação dessa
constraint (rollback completo, seguro, mas inútil) — não é um comando de
refresh. Mantida intacta, sem alterações de comportamento.

`execute_incremental_load()` (Gate 6C) é o caminho de refresh recorrente:
para cada marketplace suportado (ML, Shopee — TikTok nunca tem regional),
calcula o `MAX(date)` já carregado em `gold.marketplace_region_daily` e só
recalcula/insere linhas com `date` posterior a esse valor. Nunca usa
TRUNCATE/DELETE/UPDATE — só INSERT das linhas novas. Se nenhum marketplace
tiver data nova na fonte, retorna `no_op` sem tocar em staging/insert. Um
marketplace sem novidade (ex.: Shopee, cuja fonte `silver.stg_shopee_
order_item_snapshots` está parada em 2026-05-31 — ver docs/
regional_design_draft.md) nunca bloqueia o refresh dos demais.

`diagnose_incremental_load()` é o modo somente-leitura equivalente: nunca
abre conexão de escrita, calcula o mesmo `MAX(date)` por marketplace (gold
vs. fonte) e uma estimativa de linhas novas, para decidir se vale a pena
rodar `--incremental`.

A carga inteira (em ambos os modos) roda em UMA transação: staging temporário (`TEMP TABLE ...
ON COMMIT DROP`, nunca fica para trás) → validações RECALCULADAS a partir
da fonte (nunca contra uma constante histórica fixa — a fonte cresce todo
dia) → INSERT final em `gold.marketplace_region_daily` → validação
pós-insert → commit. Qualquer falha de validação levanta
`LoadValidationError`, que aciona ROLLBACK completo (nenhuma linha fica
parcialmente inserida). Sem retry automático.

Não há um arquivo `.sql` separado para a carga (diferente de `ddl.py`): a
validação precisa RAMIFICAR em Python entre os passos (abortar se
duplicidade > 0, se GMV não reconciliar, etc.) — isso não se encaixa no
modelo linear "parse e execute todos os statements" de `ddl.py`. As
queries ficam como constantes Python neste módulo, cada uma isolada e
nomeada, para ficarem revisáveis e testáveis com cursor falso.

Decisões de transform implementadas aqui (ver docs/regional_design_draft.md
para a auditoria e evidências completas):
  - Shopee: dedup em 2 passos (`file_id` vencedor por `(brand, order_id)`,
    depois JOIN de volta para trazer TODAS as linhas de SKU desse arquivo —
    um `DISTINCT ON` de passo único perderia unidades de pedidos
    multi-item). UF via mapa fixo de 27 nomes→sigla (os nomes acentuados
    são UTF-8 correto — o que parecia "corrupção de encoding" em sessões
    anteriores era só a exibição do terminal Windows, confirmado nesta
    sessão via codepoints reais: á=225, í=237, ã=227, ô=244). GMV/orders só
    para `order_status NOT ILIKE '%cancel%'`. `seller_shipping_cost` fica
    NULL (sem equivalente). Cobertura de custo de frete sempre 0/0 (conceito
    não existe para Shopee).
  - ML: pedidos de `raw.ml_orders` (`status='paid'` para GMV/orders, mirror
    exato de `gold.ml_gestao_diaria`), shipments/custos de
    `raw.ml_shipments`/`raw.ml_shipment_costs` (não silver — decisão da
    seção 1.2c). UF via `receiver_state` sem prefixo `BR-`; `shipment`/UF
    ausente → `uf='XX'`. Custo só quando `sender_cost` resolvido.
  - TikTok: nenhuma linha inserida — validado explicitamente após o INSERT.
  - Barbours nov/2025–mar/2026: nenhuma exceção no transform — a baixa
    cobertura aparece pelos próprios numerador/denominador (Opção A).

Limitações conhecidas, não bloqueantes (documentadas nos comentários de
coluna da DDL operacional): `units_sold` sempre 0 para ML (precisaria de
`raw.ml_order_line_items`, fora do escopo desta carga); `returned_orders`
sempre 0 para ML (sem sinal limpo identificado na auditoria).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import psycopg2

from pipelines.common.config import settings
from pipelines.ingestion.gold_regional import window_write_conn
from pipelines.ingestion.gold_regional import write_conn
from pipelines.ingestion.gold_regional.write_conn import (
    ADVISORY_LOCK_KEY,
    WritePreflightBlocked,
    release_advisory_lock,
    sanitize_error_message,
    try_acquire_advisory_lock,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WRITE_SECRET_PATH = REPO_ROOT / ".env.gold-write.local"
DEFAULT_WINDOW_WRITE_SECRET_PATH = REPO_ROOT / ".env.gold-window-write.local"

TIKTOK_MARKETPLACE_ID = 1
ML_MARKETPLACE_ID = 2
SHOPEE_MARKETPLACE_ID = 3

# Mesmo mapa de pipelines/transforms/ml_gestao_diaria.py:BRAND_TO_LOJA —
# duplicado deliberadamente (valor literal usado dentro de SQL, não
# importável de um módulo Python) para não criar uma dependência cruzada
# entre pacotes de ingestão distintos.
_BRAND_LOJA_VALUES = "('apice',1),('barbours',2),('kokeshi',3),('lescent',4),('rituaria',5)"

# 27 nomes de UF -> sigla. Os nomes acentuados são UTF-8 correto (não um
# workaround de encoding corrompido) — confirmado nesta sessão via
# codepoints reais lidos direto da fonte (read-only).
_SHOPEE_UF_MAP_VALUES = """
    ('Acre','AC'),('Alagoas','AL'),('Amapá','AP'),('Amazonas','AM'),
    ('Bahia','BA'),('Ceará','CE'),('Distrito Federal','DF'),
    ('Espírito Santo','ES'),('Goiás','GO'),('Maranhão','MA'),
    ('Mato Grosso','MT'),('Mato Grosso do Sul','MS'),('Minas Gerais','MG'),
    ('Pará','PA'),('Paraíba','PB'),('Paraná','PR'),('Pernambuco','PE'),
    ('Piauí','PI'),('Rio de Janeiro','RJ'),('Rio Grande do Norte','RN'),
    ('Rio Grande do Sul','RS'),('Rondônia','RO'),('Roraima','RR'),
    ('Santa Catarina','SC'),('São Paulo','SP'),('Sergipe','SE'),('Tocantins','TO')
"""

# Tolerância de arredondamento na reconciliação de GMV (NUMERIC(14,2) —
# diferenças só de centavo por acúmulo de arredondamento são aceitáveis;
# qualquer coisa maior indica um transform incorreto, não arredondamento).
GMV_RECONCILIATION_TOLERANCE = Decimal("0.01")


class LoadValidationError(RuntimeError):
    """Uma validação dentro da transação de carga falhou — aciona rollback
    completo. Mensagens contêm só contagens/diffs numéricos, nunca
    PII/order_id/valores de linha."""


class NothingToLoadError(LoadValidationError):
    """Staging vazio após o transform — carga abortada antes de tocar a
    tabela final."""


class InvalidWindowError(ValueError):
    """Janela [date_from, date_to] inválida para diagnose/refresh de Shopee
    por janela (Gate S2/S3) — data invertida, no futuro, ou maior que o
    teto de sanidade. Nunca chega a abrir conexão nenhuma."""


# Teto de sanidade para --diagnose-shopee-window / --refresh-shopee-window
# (Gate S3): Shopee é janela móvel recente, não deveria precisar reprocessar
# vários meses de uma vez. Existe para pegar erro de digitação/uso indevido
# antes de rodar qualquer coisa contra o banco — não é um limite técnico do
# Postgres nem da fonte.
MAX_SHOPEE_WINDOW_DAYS = 180


@dataclass
class LoadResult:
    rows_inserted: int = 0
    shopee_gmv_staging: Optional[Decimal] = None
    shopee_gmv_source: Optional[Decimal] = None
    ml_gmv_staging: Optional[Decimal] = None
    ml_gmv_source: Optional[Decimal] = None
    tiktok_rows: int = 0


@dataclass
class MarketplaceFreshness:
    """Frescor de um marketplace suportado pela Gold regional (ML, Shopee —
    nunca TikTok, que não tem cobertura regional em nenhuma fonte)."""
    marketplace: str  # "ml" | "shopee"
    marketplace_id: int
    max_date_gold: Optional[date]
    max_date_source: Optional[date]
    estimated_new_rows: int
    will_update: bool


@dataclass
class DiagnoseReport:
    marketplaces: list = field(default_factory=list)  # list[MarketplaceFreshness]
    any_update_needed: bool = False


@dataclass
class IncrementalLoadResult:
    no_op: bool = False
    rows_inserted: int = 0
    marketplaces_updated: list = field(default_factory=list)  # list[str], ex.: ["ml"]
    shopee_gmv_staging: Optional[Decimal] = None
    shopee_gmv_source: Optional[Decimal] = None
    ml_gmv_staging: Optional[Decimal] = None
    ml_gmv_source: Optional[Decimal] = None
    tiktok_rows: int = 0


@dataclass
class ShopeeWindowDiagnoseReport:
    """Resultado de `diagnose_shopee_window` (Gate S2) — somente leitura.
    `rows_to_delete`/`rows_to_insert` descrevem o impacto de um FUTURO
    `--refresh-shopee-window` (Gate S3, ainda não implementado): como esse
    refresh substituiria TODA a janela (delete completo + insert do
    recálculo), `rows_to_delete` é sempre `gold_rows` e `rows_to_insert` é
    sempre `recalculated_rows` — não há diff linha-a-linha nesta camada."""
    date_from: date
    date_to: date
    gold_rows: int = 0
    gold_gmv: Decimal = Decimal("0")
    gold_orders: int = 0
    recalculated_rows: int = 0
    recalculated_gmv: Decimal = Decimal("0")
    recalculated_orders: int = 0
    rows_to_delete: int = 0
    rows_to_insert: int = 0
    gmv_delta: Decimal = Decimal("0")
    orders_delta: int = 0
    overlaps_existing_gold_data: bool = False
    zero_source_risk: bool = False  # recalculado==0 linhas E gold>0 linhas na janela
    duplicate_key_count: int = 0
    null_required_count: int = 0
    numerator_over_denominator_count: int = 0
    # Comparação exata por chave (Gate S2.1) — FULL OUTER JOIN Gold vs. fonte
    # no grão (date, marketplace_id, loja_id, uf), campos comparados com
    # IS DISTINCT FROM. Detecta redistribuição entre chaves invisível aos
    # agregados.
    gold_only_key_count: int = 0
    source_only_key_count: int = 0
    changed_key_count: int = 0

    @property
    def would_change_data(self) -> bool:
        """A janela NÃO está reconciliada — um refresh mudaria os dados.
        `False` não é erro: significa que Gold e fonte já batem exatamente
        chave a chave e campo a campo."""
        return (
            self.gold_only_key_count > 0
            or self.source_only_key_count > 0
            or self.changed_key_count > 0
        )

    @property
    def structurally_safe_for_refresh(self) -> bool:
        """A fonte recalculada é estruturalmente sã para servir de base a um
        futuro refresh (Gate S3): sem risco de zerar a Gold, sem duplicidade
        de chave, sem nulos obrigatórios, sem numerador > denominador.
        Independe de `would_change_data` — uma janela pode estar reconciliada
        (nada a mudar) E ser estruturalmente sã ao mesmo tempo."""
        return (
            not self.zero_source_risk
            and self.duplicate_key_count == 0
            and self.null_required_count == 0
            and self.numerator_over_denominator_count == 0
        )


# ---------------------------------------------------------------------------
# SQL — staging (TEMP TABLE, ON COMMIT DROP — nunca sobrevive à transação)
# ---------------------------------------------------------------------------

SQL_CREATE_STAGING = """
CREATE TEMP TABLE stg_marketplace_region_daily (
    date DATE NOT NULL,
    marketplace_id INT NOT NULL,
    loja_id INT NOT NULL,
    uf CHAR(2) NOT NULL,
    gmv NUMERIC(14,2) NOT NULL DEFAULT 0,
    orders INT NOT NULL DEFAULT 0,
    units_sold INT NOT NULL DEFAULT 0,
    canceled_orders INT NOT NULL DEFAULT 0,
    returned_orders INT NOT NULL DEFAULT 0,
    seller_shipping_cost NUMERIC(14,2),
    buyer_shipping_fee NUMERIC(14,2),
    estimated_shipping_fee NUMERIC(14,2),
    reverse_shipping_fee NUMERIC(14,2),
    uf_known_orders INT NOT NULL DEFAULT 0,
    uf_eligible_orders INT NOT NULL DEFAULT 0,
    shipping_cost_covered_orders INT NOT NULL DEFAULT 0,
    shipping_cost_eligible_orders INT NOT NULL DEFAULT 0
) ON COMMIT DROP
"""

_STAGING_INSERT_COLUMNS = """
    date, marketplace_id, loja_id, uf, gmv, orders, units_sold, canceled_orders,
    returned_orders, seller_shipping_cost, buyer_shipping_fee, estimated_shipping_fee,
    reverse_shipping_fee, uf_known_orders, uf_eligible_orders,
    shipping_cost_covered_orders, shipping_cost_eligible_orders
"""

# Shopee: dedup em 2 passos —
#   (1) shopee_winning_file: so o file_id vencedor por (brand, order_id);
#   (2) shopee_all_lines_of_winner: JOIN de volta em
#       silver.stg_shopee_order_item_snapshots para trazer TODAS as linhas
#       de SKU daquele arquivo (preserva unidades de pedidos multi-item).
SQL_INSERT_SHOPEE_STAGING = f"""
WITH shopee_winning_file AS (
    SELECT DISTINCT ON (brand, order_id) brand, order_id, file_id
    FROM silver.stg_shopee_order_item_snapshots
    ORDER BY brand, order_id, file_id DESC
),
shopee_all_lines_of_winner AS (
    SELECT s.*
    FROM silver.stg_shopee_order_item_snapshots s
    JOIN shopee_winning_file w
      ON w.brand = s.brand AND w.order_id = s.order_id AND w.file_id = s.file_id
),
shopee_per_order AS (
    SELECT
        brand,
        order_id,
        MAX(order_created_at)::date AS order_date,
        MAX(order_amount) AS order_amount,
        MAX(order_status) AS order_status,
        MAX(return_refund_status) AS return_refund_status,
        MAX(delivery_state) AS delivery_state,
        MAX(buyer_paid_shipping_fee) AS buyer_paid_shipping_fee,
        MAX(estimated_shipping_fee) AS estimated_shipping_fee,
        MAX(reverse_shipping_fee) AS reverse_shipping_fee,
        SUM(quantity) AS units
    FROM shopee_all_lines_of_winner
    GROUP BY brand, order_id
),
shopee_uf_map(delivery_state, uf) AS (VALUES {_SHOPEE_UF_MAP_VALUES}),
shopee_brand_loja(brand, loja_id) AS (VALUES {_BRAND_LOJA_VALUES}),
shopee_final AS (
    SELECT
        o.order_date AS date,
        {SHOPEE_MARKETPLACE_ID} AS marketplace_id,
        bl.loja_id,
        COALESCE(m.uf, 'XX') AS uf,
        o.order_amount, o.order_status, o.return_refund_status,
        o.buyer_paid_shipping_fee, o.estimated_shipping_fee, o.reverse_shipping_fee,
        o.units
    FROM shopee_per_order o
    JOIN shopee_brand_loja bl ON bl.brand = o.brand
    LEFT JOIN shopee_uf_map m ON m.delivery_state = o.delivery_state
)
INSERT INTO stg_marketplace_region_daily ({_STAGING_INSERT_COLUMNS})
SELECT
    date, marketplace_id, loja_id, uf,
    SUM(CASE WHEN order_status NOT ILIKE '%cancel%' THEN order_amount ELSE 0 END),
    COUNT(*) FILTER (WHERE order_status NOT ILIKE '%cancel%'),
    SUM(CASE WHEN order_status NOT ILIKE '%cancel%' THEN units ELSE 0 END),
    COUNT(*) FILTER (WHERE order_status ILIKE '%cancel%'),
    COUNT(*) FILTER (WHERE return_refund_status IS NOT NULL),
    NULL,
    SUM(buyer_paid_shipping_fee),
    SUM(estimated_shipping_fee),
    SUM(reverse_shipping_fee),
    COUNT(*) FILTER (WHERE uf <> 'XX'),
    COUNT(*),
    0,
    0
FROM shopee_final
GROUP BY date, marketplace_id, loja_id, uf
"""

# ML: pedidos de raw.ml_orders, shipments/custos de raw.ml_shipments/
# raw.ml_shipment_costs (decisao secao 1.2c — nao silver.stg_ml_*).
SQL_INSERT_ML_STAGING = f"""
WITH ml_orders AS (
    SELECT brand, order_id, status, shipping_id, total_amount,
           date_created::date AS order_date
    FROM raw.ml_orders
    WHERE status IN ('paid', 'cancelled')
),
ml_shipments AS (
    SELECT brand, shipment_id, receiver_state FROM raw.ml_shipments
),
ml_costs AS (
    SELECT brand, shipment_id, sender_cost FROM raw.ml_shipment_costs
),
ml_brand_loja(brand, loja_id) AS (VALUES {_BRAND_LOJA_VALUES}),
ml_joined AS (
    SELECT
        o.brand, o.order_date, o.status, o.total_amount,
        CASE
            WHEN o.shipping_id IS NULL THEN 'XX'
            WHEN sh.shipment_id IS NULL THEN 'XX'
            WHEN sh.receiver_state IS NULL THEN 'XX'
            ELSE upper(regexp_replace(sh.receiver_state, '^BR-', ''))
        END AS uf,
        (o.shipping_id IS NOT NULL AND sh.shipment_id IS NOT NULL AND sh.receiver_state IS NOT NULL) AS uf_resolved,
        c.sender_cost,
        (c.sender_cost IS NOT NULL) AS cost_resolved
    FROM ml_orders o
    LEFT JOIN ml_shipments sh ON sh.brand = o.brand AND sh.shipment_id = o.shipping_id
    LEFT JOIN ml_costs c ON c.brand = o.brand AND c.shipment_id = o.shipping_id
),
ml_final AS (
    SELECT j.*, bl.loja_id
    FROM ml_joined j
    JOIN ml_brand_loja bl ON bl.brand = j.brand
)
INSERT INTO stg_marketplace_region_daily ({_STAGING_INSERT_COLUMNS})
SELECT
    order_date, {ML_MARKETPLACE_ID}, loja_id, uf,
    SUM(CASE WHEN status = 'paid' THEN total_amount ELSE 0 END),
    COUNT(*) FILTER (WHERE status = 'paid'),
    0,
    COUNT(*) FILTER (WHERE status = 'cancelled'),
    0,
    SUM(CASE WHEN status = 'paid' AND cost_resolved THEN sender_cost ELSE 0 END),
    NULL, NULL, NULL,
    COUNT(*) FILTER (WHERE status = 'paid' AND uf_resolved),
    COUNT(*) FILTER (WHERE status = 'paid'),
    COUNT(*) FILTER (WHERE status = 'paid' AND cost_resolved),
    COUNT(*) FILTER (WHERE status = 'paid')
FROM ml_final
GROUP BY order_date, loja_id, uf
"""

# ---------------------------------------------------------------------------
# SQL — validações (todas recalculadas a partir da fonte na hora; NENHUMA
# compara contra uma constante histórica fixa)
# ---------------------------------------------------------------------------

SQL_VALIDATE_ROWCOUNT = "SELECT COUNT(*) FROM stg_marketplace_region_daily"

SQL_VALIDATE_DUPLICATES = """
    SELECT COUNT(*) FROM (
        SELECT date, marketplace_id, loja_id, uf FROM stg_marketplace_region_daily
        GROUP BY date, marketplace_id, loja_id, uf HAVING COUNT(*) > 1
    ) t
"""

SQL_VALIDATE_NULLS = """
    SELECT COUNT(*) FROM stg_marketplace_region_daily
    WHERE date IS NULL OR marketplace_id IS NULL OR loja_id IS NULL OR uf IS NULL
"""

SQL_VALIDATE_NUMERATOR_DENOMINATOR = """
    SELECT COUNT(*) FROM stg_marketplace_region_daily
    WHERE uf_known_orders > uf_eligible_orders
       OR shipping_cost_covered_orders > shipping_cost_eligible_orders
"""

SQL_SHOPEE_GMV_STAGING = f"SELECT COALESCE(SUM(gmv), 0) FROM stg_marketplace_region_daily WHERE marketplace_id = {SHOPEE_MARKETPLACE_ID}"

# Recalcula o GMV Shopee dedupicado DIRETO DA FONTE, na hora — nunca uma
# constante histórica (a fonte cresce todo dia; comparar contra um número
# fixo dias depois sempre divergiria por crescimento normal, não por bug).
SQL_SHOPEE_GMV_SOURCE_RECALC = """
    WITH shopee_winning_file AS (
        SELECT DISTINCT ON (brand, order_id) brand, order_id, file_id
        FROM silver.stg_shopee_order_item_snapshots
        ORDER BY brand, order_id, file_id DESC
    ),
    shopee_per_order AS (
        SELECT w.brand, w.order_id, MAX(s.order_amount) AS order_amount, MAX(s.order_status) AS order_status
        FROM shopee_winning_file w
        JOIN silver.stg_shopee_order_item_snapshots s
          ON s.brand = w.brand AND s.order_id = w.order_id AND s.file_id = w.file_id
        GROUP BY w.brand, w.order_id
    )
    SELECT COALESCE(SUM(CASE WHEN order_status NOT ILIKE '%cancel%' THEN order_amount ELSE 0 END), 0)
    FROM shopee_per_order
"""

SQL_ML_GMV_STAGING = f"SELECT COALESCE(SUM(gmv), 0) FROM stg_marketplace_region_daily WHERE marketplace_id = {ML_MARKETPLACE_ID}"

# Idem — recalculado da fonte, nunca constante fixa.
SQL_ML_GMV_SOURCE_RECALC = "SELECT COALESCE(SUM(total_amount), 0) FROM raw.ml_orders WHERE status = 'paid'"

SQL_TIKTOK_ROWS_CHECK = f"SELECT COUNT(*) FROM gold.marketplace_region_daily WHERE marketplace_id = {TIKTOK_MARKETPLACE_ID}"

SQL_INSERT_FINAL = f"""
    INSERT INTO gold.marketplace_region_daily ({_STAGING_INSERT_COLUMNS})
    SELECT {_STAGING_INSERT_COLUMNS}
    FROM stg_marketplace_region_daily
"""


def execute_first_load(write_url: str) -> LoadResult:
    """Executa a primeira carga em UMA transação: advisory lock →
    staging+validações (recalculadas) → insert final → validação
    pós-insert → commit. Sem retry automático; qualquer exceção aciona
    ROLLBACK completo antes de subir."""
    conn = psycopg2.connect(write_url, connect_timeout=15)
    conn.autocommit = False
    try:
        if not try_acquire_advisory_lock(conn):
            raise WritePreflightBlocked(
                f"advisory lock {ADVISORY_LOCK_KEY} já está em uso — outra execução da "
                "carga da Gold regional pode estar em andamento. Abortando sem tentar novamente."
            )
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                cur.execute("SET LOCAL statement_timeout = '600s'")

                # 1. Staging (grão pedido -> grão date x marketplace x loja x uf)
                cur.execute(SQL_CREATE_STAGING)
                cur.execute(SQL_INSERT_SHOPEE_STAGING)
                cur.execute(SQL_INSERT_ML_STAGING)

                # 2. Validações pré-insert (todas recalculadas)
                cur.execute(SQL_VALIDATE_ROWCOUNT)
                (row_count,) = cur.fetchone()
                if row_count == 0:
                    raise NothingToLoadError("staging vazio após o transform — abortando sem inserir nada")

                cur.execute(SQL_VALIDATE_DUPLICATES)
                (dup_count,) = cur.fetchone()
                if dup_count > 0:
                    raise LoadValidationError(
                        f"{dup_count} combinação(ões) (date,marketplace_id,loja_id,uf) duplicadas no staging"
                    )

                cur.execute(SQL_VALIDATE_NULLS)
                (null_count,) = cur.fetchone()
                if null_count > 0:
                    raise LoadValidationError(f"{null_count} linha(s) com coluna obrigatória nula no staging")

                cur.execute(SQL_VALIDATE_NUMERATOR_DENOMINATOR)
                (bad_count,) = cur.fetchone()
                if bad_count > 0:
                    raise LoadValidationError(f"{bad_count} linha(s) com numerador > denominador de cobertura")

                cur.execute(SQL_SHOPEE_GMV_STAGING)
                (shopee_gmv_staging,) = cur.fetchone()
                cur.execute(SQL_SHOPEE_GMV_SOURCE_RECALC)
                (shopee_gmv_source,) = cur.fetchone()
                if abs(shopee_gmv_staging - shopee_gmv_source) > GMV_RECONCILIATION_TOLERANCE:
                    raise LoadValidationError(
                        f"GMV Shopee do staging diverge da fonte recalculada na hora "
                        f"(diff={abs(shopee_gmv_staging - shopee_gmv_source)})"
                    )

                cur.execute(SQL_ML_GMV_STAGING)
                (ml_gmv_staging,) = cur.fetchone()
                cur.execute(SQL_ML_GMV_SOURCE_RECALC)
                (ml_gmv_source,) = cur.fetchone()
                if abs(ml_gmv_staging - ml_gmv_source) > GMV_RECONCILIATION_TOLERANCE:
                    raise LoadValidationError(
                        f"GMV ML do staging diverge da fonte recalculada na hora "
                        f"(diff={abs(ml_gmv_staging - ml_gmv_source)})"
                    )

                # 3. Insert final
                cur.execute(SQL_INSERT_FINAL)
                rows_inserted = cur.rowcount

                # 4. Validação pós-insert
                cur.execute(SQL_TIKTOK_ROWS_CHECK)
                (tiktok_rows,) = cur.fetchone()
                if tiktok_rows != 0:
                    raise LoadValidationError(
                        f"{tiktok_rows} linha(s) TikTok encontrada(s) na Gold — não deveria haver nenhuma"
                    )

            conn.commit()
            return LoadResult(
                rows_inserted=rows_inserted,
                shopee_gmv_staging=shopee_gmv_staging,
                shopee_gmv_source=shopee_gmv_source,
                ml_gmv_staging=ml_gmv_staging,
                ml_gmv_source=ml_gmv_source,
                tiktok_rows=tiktok_rows,
            )
        except LoadValidationError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise RuntimeError(f"Carga falhou, rollback completo executado: {sanitize_error_message(exc)}") from exc
        finally:
            release_advisory_lock(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Refresh incremental (Gate 6C) — só linhas com `date` posterior ao que já
# está em gold.marketplace_region_daily, por marketplace. Duplica
# deliberadamente a lógica de transform de SQL_INSERT_SHOPEE_STAGING/
# SQL_INSERT_ML_STAGING (que ficam INTACTAS, usadas só por
# execute_first_load) — reaproveitar via refactor acoplaria o caminho de
# carga inicial (auditado, congelado) ao caminho incremental novo. Mesma
# escolha já feita neste arquivo para `_BRAND_LOJA_VALUES` (duplicado entre
# pacotes de ingestão distintos, de propósito).
#
# `min_date` é sempre um `date` computado internamente (MAX(date) já
# carregado, ou date.min quando o marketplace ainda não tem nenhuma linha)
# — nunca entrada de usuário. Interpolado como literal de data (mesmo
# padrão já usado neste arquivo para marketplace_id/UF/brand, nenhum dos
# quais vem de input externo livre), não como bind parameter — evita o
# risco de escapar mal os `%` literais de `ILIKE '%cancel%'` ao misturar
# parâmetros nomeados do psycopg2 nesta mesma string.
# ---------------------------------------------------------------------------

SQL_MAX_DATE_GOLD_BY_MARKETPLACE = (
    "SELECT marketplace_id, MAX(date) AS max_date FROM gold.marketplace_region_daily GROUP BY marketplace_id"
)
SQL_MAX_DATE_SHOPEE_SOURCE = "SELECT MAX(order_created_at::date) FROM silver.stg_shopee_order_item_snapshots"
SQL_MAX_DATE_ML_SOURCE = "SELECT MAX(date_created::date) FROM raw.ml_orders WHERE status IN ('paid', 'cancelled')"


def _shopee_incremental_select(min_date: date) -> str:
    """SELECT puro (sem INSERT) das linhas de staging Shopee, restrito a
    `order_date > min_date`. Mesma lógica de dedup/UF/GMV de
    SQL_INSERT_SHOPEE_STAGING — revisar as duas juntas se uma mudar."""
    min_date_literal = min_date.isoformat()
    return f"""
    WITH shopee_winning_file AS (
        SELECT DISTINCT ON (brand, order_id) brand, order_id, file_id
        FROM silver.stg_shopee_order_item_snapshots
        ORDER BY brand, order_id, file_id DESC
    ),
    shopee_all_lines_of_winner AS (
        SELECT s.*
        FROM silver.stg_shopee_order_item_snapshots s
        JOIN shopee_winning_file w
          ON w.brand = s.brand AND w.order_id = s.order_id AND w.file_id = s.file_id
    ),
    shopee_per_order AS (
        SELECT
            brand,
            order_id,
            MAX(order_created_at)::date AS order_date,
            MAX(order_amount) AS order_amount,
            MAX(order_status) AS order_status,
            MAX(return_refund_status) AS return_refund_status,
            MAX(delivery_state) AS delivery_state,
            MAX(buyer_paid_shipping_fee) AS buyer_paid_shipping_fee,
            MAX(estimated_shipping_fee) AS estimated_shipping_fee,
            MAX(reverse_shipping_fee) AS reverse_shipping_fee,
            SUM(quantity) AS units
        FROM shopee_all_lines_of_winner
        GROUP BY brand, order_id
    ),
    shopee_uf_map(delivery_state, uf) AS (VALUES {_SHOPEE_UF_MAP_VALUES}),
    shopee_brand_loja(brand, loja_id) AS (VALUES {_BRAND_LOJA_VALUES}),
    shopee_final AS (
        SELECT
            o.order_date AS date,
            {SHOPEE_MARKETPLACE_ID} AS marketplace_id,
            bl.loja_id,
            COALESCE(m.uf, 'XX') AS uf,
            o.order_amount, o.order_status, o.return_refund_status,
            o.buyer_paid_shipping_fee, o.estimated_shipping_fee, o.reverse_shipping_fee,
            o.units
        FROM shopee_per_order o
        JOIN shopee_brand_loja bl ON bl.brand = o.brand
        LEFT JOIN shopee_uf_map m ON m.delivery_state = o.delivery_state
        WHERE o.order_date > '{min_date_literal}'::date
    )
    SELECT
        date, marketplace_id, loja_id, uf,
        SUM(CASE WHEN order_status NOT ILIKE '%cancel%' THEN order_amount ELSE 0 END) AS gmv,
        COUNT(*) FILTER (WHERE order_status NOT ILIKE '%cancel%') AS orders,
        SUM(CASE WHEN order_status NOT ILIKE '%cancel%' THEN units ELSE 0 END) AS units_sold,
        COUNT(*) FILTER (WHERE order_status ILIKE '%cancel%') AS canceled_orders,
        COUNT(*) FILTER (WHERE return_refund_status IS NOT NULL) AS returned_orders,
        NULL::numeric AS seller_shipping_cost,
        SUM(buyer_paid_shipping_fee) AS buyer_shipping_fee,
        SUM(estimated_shipping_fee) AS estimated_shipping_fee,
        SUM(reverse_shipping_fee) AS reverse_shipping_fee,
        COUNT(*) FILTER (WHERE uf <> 'XX') AS uf_known_orders,
        COUNT(*) AS uf_eligible_orders,
        0 AS shipping_cost_covered_orders,
        0 AS shipping_cost_eligible_orders
    FROM shopee_final
    GROUP BY date, marketplace_id, loja_id, uf
    """


def _ml_incremental_select(min_date: date) -> str:
    """SELECT puro (sem INSERT) das linhas de staging ML, restrito a
    `order_date > min_date`. Mesma lógica de SQL_INSERT_ML_STAGING."""
    min_date_literal = min_date.isoformat()
    return f"""
    WITH ml_orders AS (
        SELECT brand, order_id, status, shipping_id, total_amount,
               date_created::date AS order_date
        FROM raw.ml_orders
        WHERE status IN ('paid', 'cancelled')
          AND date_created::date > '{min_date_literal}'::date
    ),
    ml_shipments AS (
        SELECT brand, shipment_id, receiver_state FROM raw.ml_shipments
    ),
    ml_costs AS (
        SELECT brand, shipment_id, sender_cost FROM raw.ml_shipment_costs
    ),
    ml_brand_loja(brand, loja_id) AS (VALUES {_BRAND_LOJA_VALUES}),
    ml_joined AS (
        SELECT
            o.brand, o.order_date, o.status, o.total_amount,
            CASE
                WHEN o.shipping_id IS NULL THEN 'XX'
                WHEN sh.shipment_id IS NULL THEN 'XX'
                WHEN sh.receiver_state IS NULL THEN 'XX'
                ELSE upper(regexp_replace(sh.receiver_state, '^BR-', ''))
            END AS uf,
            (o.shipping_id IS NOT NULL AND sh.shipment_id IS NOT NULL AND sh.receiver_state IS NOT NULL) AS uf_resolved,
            c.sender_cost,
            (c.sender_cost IS NOT NULL) AS cost_resolved
        FROM ml_orders o
        LEFT JOIN ml_shipments sh ON sh.brand = o.brand AND sh.shipment_id = o.shipping_id
        LEFT JOIN ml_costs c ON c.brand = o.brand AND c.shipment_id = o.shipping_id
    ),
    ml_final AS (
        SELECT j.*, bl.loja_id
        FROM ml_joined j
        JOIN ml_brand_loja bl ON bl.brand = j.brand
    )
    SELECT
        order_date AS date, {ML_MARKETPLACE_ID} AS marketplace_id, loja_id, uf,
        SUM(CASE WHEN status = 'paid' THEN total_amount ELSE 0 END) AS gmv,
        COUNT(*) FILTER (WHERE status = 'paid') AS orders,
        0 AS units_sold,
        COUNT(*) FILTER (WHERE status = 'cancelled') AS canceled_orders,
        0 AS returned_orders,
        SUM(CASE WHEN status = 'paid' AND cost_resolved THEN sender_cost ELSE 0 END) AS seller_shipping_cost,
        NULL::numeric AS buyer_shipping_fee, NULL::numeric AS estimated_shipping_fee, NULL::numeric AS reverse_shipping_fee,
        COUNT(*) FILTER (WHERE status = 'paid' AND uf_resolved) AS uf_known_orders,
        COUNT(*) FILTER (WHERE status = 'paid') AS uf_eligible_orders,
        COUNT(*) FILTER (WHERE status = 'paid' AND cost_resolved) AS shipping_cost_covered_orders,
        COUNT(*) FILTER (WHERE status = 'paid') AS shipping_cost_eligible_orders
    FROM ml_final
    GROUP BY order_date, loja_id, uf
    """


def _shopee_gmv_source_recalc_incremental(min_date: date) -> str:
    """Reconciliação de GMV Shopee escopada à MESMA janela incremental do
    staging (`order_date > min_date`) — nunca a fonte inteira (que sempre
    divergiria do staging incremental, que só tem as linhas novas)."""
    min_date_literal = min_date.isoformat()
    return f"""
    WITH shopee_winning_file AS (
        SELECT DISTINCT ON (brand, order_id) brand, order_id, file_id
        FROM silver.stg_shopee_order_item_snapshots
        ORDER BY brand, order_id, file_id DESC
    ),
    shopee_per_order AS (
        SELECT w.brand, w.order_id, MAX(s.order_amount) AS order_amount, MAX(s.order_status) AS order_status,
               MAX(s.order_created_at)::date AS order_date
        FROM shopee_winning_file w
        JOIN silver.stg_shopee_order_item_snapshots s
          ON s.brand = w.brand AND s.order_id = w.order_id AND s.file_id = w.file_id
        GROUP BY w.brand, w.order_id
    )
    SELECT COALESCE(SUM(CASE WHEN order_status NOT ILIKE '%cancel%' THEN order_amount ELSE 0 END), 0)
    FROM shopee_per_order
    WHERE order_date > '{min_date_literal}'::date
    """


def _ml_gmv_source_recalc_incremental(min_date: date) -> str:
    """Reconciliação de GMV ML escopada à mesma janela incremental."""
    min_date_literal = min_date.isoformat()
    return (
        "SELECT COALESCE(SUM(total_amount), 0) FROM raw.ml_orders "
        f"WHERE status = 'paid' AND date_created::date > '{min_date_literal}'::date"
    )


def _wrap_count(select_sql: str) -> str:
    return f"SELECT COUNT(*) FROM ({select_sql}) t"


_SUPPORTED_MARKETPLACES = (
    ("ml", ML_MARKETPLACE_ID, SQL_MAX_DATE_ML_SOURCE, _ml_incremental_select),
    ("shopee", SHOPEE_MARKETPLACE_ID, SQL_MAX_DATE_SHOPEE_SOURCE, _shopee_incremental_select),
)


def diagnose_incremental_load(read_url: str) -> DiagnoseReport:
    """Somente leitura — NUNCA abre conexão de escrita, nunca cria staging,
    nunca insere. Sessão explicitamente `readonly=True` (mesmo padrão de
    `write_conn._connect_readonly`), então mesmo um bug aqui não conseguiria
    escrever. Para cada marketplace suportado, calcula o `MAX(date)` já
    carregado em `gold.marketplace_region_daily` vs. o `MAX(date)`
    disponível na fonte, e uma estimativa de quantas linhas de grão
    (date x marketplace x loja x uf) a carga incremental inseriria."""
    conn = psycopg2.connect(read_url, connect_timeout=15)
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_MAX_DATE_GOLD_BY_MARKETPLACE)
            max_date_gold_by_mkt = {row[0]: row[1] for row in cur.fetchall()}

            marketplaces: list[MarketplaceFreshness] = []
            for marketplace, marketplace_id, max_date_source_sql, incremental_select_fn in _SUPPORTED_MARKETPLACES:
                cur.execute(max_date_source_sql)
                (max_date_source,) = cur.fetchone()
                max_date_gold = max_date_gold_by_mkt.get(marketplace_id)

                estimated_new_rows = 0
                will_update = max_date_source is not None and (max_date_gold is None or max_date_source > max_date_gold)
                if will_update:
                    min_date = max_date_gold or date.min
                    cur.execute(_wrap_count(incremental_select_fn(min_date)))
                    (estimated_new_rows,) = cur.fetchone()
                    will_update = estimated_new_rows > 0

                marketplaces.append(MarketplaceFreshness(
                    marketplace=marketplace, marketplace_id=marketplace_id,
                    max_date_gold=max_date_gold, max_date_source=max_date_source,
                    estimated_new_rows=estimated_new_rows, will_update=will_update,
                ))
    finally:
        conn.close()

    return DiagnoseReport(marketplaces=marketplaces, any_update_needed=any(m.will_update for m in marketplaces))


def execute_incremental_load(write_url: str) -> IncrementalLoadResult:
    """Carga incremental (Gate 6C): para cada marketplace com dado novo na
    fonte, insere SOMENTE as linhas novas (`date` > `MAX(date)` já
    carregado para aquele marketplace). Um marketplace sem novidade (ex.:
    Shopee parado) nunca bloqueia os demais. Se NENHUM marketplace tiver
    novidade, retorna `no_op=True` sem staging/insert algum.

    Mesma disciplina transacional de `execute_first_load`: 1 transação, 1
    advisory lock, validações recalculadas antes do insert (duplicidade,
    nulos, numerador<=denominador, reconciliação de GMV — só para os
    marketplaces efetivamente incluídos nesta rodada), validação pós-
    insert (zero TikTok), commit só no final, ROLLBACK completo em
    qualquer exceção, sem retry automático. NUNCA usa TRUNCATE/DELETE/
    UPDATE — só INSERT das linhas novas."""
    conn = psycopg2.connect(write_url, connect_timeout=15)
    conn.autocommit = False
    try:
        if not try_acquire_advisory_lock(conn):
            raise WritePreflightBlocked(
                f"advisory lock {ADVISORY_LOCK_KEY} já está em uso — outra execução da "
                "carga da Gold regional pode estar em andamento. Abortando sem tentar novamente."
            )
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                cur.execute("SET LOCAL statement_timeout = '600s'")

                cur.execute(SQL_MAX_DATE_GOLD_BY_MARKETPLACE)
                max_date_gold_by_mkt = {row[0]: row[1] for row in cur.fetchall()}

                to_load: list[tuple[str, str]] = []
                min_dates: dict[str, date] = {}
                for marketplace, marketplace_id, max_date_source_sql, incremental_select_fn in _SUPPORTED_MARKETPLACES:
                    cur.execute(max_date_source_sql)
                    (max_date_source,) = cur.fetchone()
                    min_date = max_date_gold_by_mkt.get(marketplace_id) or date.min
                    if max_date_source is not None and max_date_source > min_date:
                        min_dates[marketplace] = min_date
                        to_load.append((marketplace, incremental_select_fn(min_date)))

                if not to_load:
                    conn.commit()  # nada foi alterado; só fecha a transação de leitura limpa
                    return IncrementalLoadResult(no_op=True)

                # 1. Staging (só das linhas novas dos marketplaces com novidade)
                cur.execute(SQL_CREATE_STAGING)
                for _, select_sql in to_load:
                    cur.execute(f"INSERT INTO stg_marketplace_region_daily ({_STAGING_INSERT_COLUMNS}) {select_sql}")

                # 2. Validações pré-insert (recalculadas, escopo incremental)
                cur.execute(SQL_VALIDATE_ROWCOUNT)
                (row_count,) = cur.fetchone()
                if row_count == 0:
                    raise NothingToLoadError("staging vazio após o transform incremental — abortando sem inserir nada")

                cur.execute(SQL_VALIDATE_DUPLICATES)
                (dup_count,) = cur.fetchone()
                if dup_count > 0:
                    raise LoadValidationError(
                        f"{dup_count} combinação(ões) (date,marketplace_id,loja_id,uf) duplicadas no staging incremental"
                    )

                cur.execute(SQL_VALIDATE_NULLS)
                (null_count,) = cur.fetchone()
                if null_count > 0:
                    raise LoadValidationError(f"{null_count} linha(s) com coluna obrigatória nula no staging incremental")

                cur.execute(SQL_VALIDATE_NUMERATOR_DENOMINATOR)
                (bad_count,) = cur.fetchone()
                if bad_count > 0:
                    raise LoadValidationError(f"{bad_count} linha(s) com numerador > denominador de cobertura no staging incremental")

                marketplaces_updated = [name for name, _ in to_load]

                shopee_gmv_staging = shopee_gmv_source = None
                if "shopee" in marketplaces_updated:
                    cur.execute(SQL_SHOPEE_GMV_STAGING)
                    (shopee_gmv_staging,) = cur.fetchone()
                    cur.execute(_shopee_gmv_source_recalc_incremental(min_dates["shopee"]))
                    (shopee_gmv_source,) = cur.fetchone()
                    if abs(shopee_gmv_staging - shopee_gmv_source) > GMV_RECONCILIATION_TOLERANCE:
                        raise LoadValidationError(
                            f"GMV Shopee do staging incremental diverge da fonte recalculada na hora "
                            f"(diff={abs(shopee_gmv_staging - shopee_gmv_source)})"
                        )

                ml_gmv_staging = ml_gmv_source = None
                if "ml" in marketplaces_updated:
                    cur.execute(SQL_ML_GMV_STAGING)
                    (ml_gmv_staging,) = cur.fetchone()
                    cur.execute(_ml_gmv_source_recalc_incremental(min_dates["ml"]))
                    (ml_gmv_source,) = cur.fetchone()
                    if abs(ml_gmv_staging - ml_gmv_source) > GMV_RECONCILIATION_TOLERANCE:
                        raise LoadValidationError(
                            f"GMV ML do staging incremental diverge da fonte recalculada na hora "
                            f"(diff={abs(ml_gmv_staging - ml_gmv_source)})"
                        )

                # 3. Insert final — só linhas novas, nunca TRUNCATE/DELETE/UPDATE
                cur.execute(SQL_INSERT_FINAL)
                rows_inserted = cur.rowcount

                # 4. Validação pós-insert
                cur.execute(SQL_TIKTOK_ROWS_CHECK)
                (tiktok_rows,) = cur.fetchone()
                if tiktok_rows != 0:
                    raise LoadValidationError(
                        f"{tiktok_rows} linha(s) TikTok encontrada(s) na Gold — não deveria haver nenhuma"
                    )

            conn.commit()
            return IncrementalLoadResult(
                no_op=False,
                rows_inserted=rows_inserted,
                marketplaces_updated=marketplaces_updated,
                shopee_gmv_staging=shopee_gmv_staging,
                shopee_gmv_source=shopee_gmv_source,
                ml_gmv_staging=ml_gmv_staging,
                ml_gmv_source=ml_gmv_source,
                tiktok_rows=tiktok_rows,
            )
        except LoadValidationError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise RuntimeError(f"Carga incremental falhou, rollback completo executado: {sanitize_error_message(exc)}") from exc
        finally:
            release_advisory_lock(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Diagnose de janela Shopee (Gate S2) — somente leitura, sem staging, sem
# DDL/DML. Recalcula o MESMO dedup "arquivo vencedor" de
# SQL_INSERT_SHOPEE_STAGING/_shopee_incremental_select, escopado a uma
# janela [date_from, date_to] arbitrária (não só "> MAX(date)"), e compara
# com o que já está em gold.marketplace_region_daily para Shopee na mesma
# janela. Existe para decidir SE um `--refresh-shopee-window` (Gate S3,
# ainda não implementado — sem DELETE/INSERT neste gate) seria seguro.
#
# date_from/date_to vêm de --date-from/--date-to na CLI, ou seja, são
# ENTRADA DE USUÁRIO — diferente de `min_date` em _shopee_incremental_select
# (sempre computado internamente, nunca do usuário). Por isso aqui as datas
# são SEMPRE bind parameters do psycopg2 (%(date_from)s/%(date_to)s), nunca
# interpoladas como literal de string na query. Isso obriga escapar como
# `%%` os `%` literais de `ILIKE '%cancel%'` na mesma string (psycopg2 faz
# substituição estilo printf quando há qualquer %(nome)s na query) — mesma
# armadilha já documentada no comentário de _shopee_incremental_select,
# resolvida aqui com bind parameter em vez de literal porque a entrada,
# desta vez, não é confiável por construção.
#
# Sem TEMP TABLE: uma sessão readonly=True bloqueia até `CREATE TEMP TABLE`
# (mexe em catálogo do sistema mesmo sem persistir dados) — por isso as 4
# validações (agregados/duplicidade/nulos/numerador-denominador) reexecutam
# o recálculo completo cada uma via CTE (`WITH shopee_window_recalc AS
# (...)”), em vez de materializar uma vez como execute_first_load/
# execute_incremental_load fazem em staging. Aceitável para um comando de
# diagnose usado sob demanda; o refresh real (Gate S3) volta a materializar
# em staging dentro de uma transação de escrita.
# ---------------------------------------------------------------------------

SQL_SHOPEE_WINDOW_GOLD_AGGREGATES = f"""
    SELECT COUNT(*), COALESCE(SUM(gmv), 0), COALESCE(SUM(orders), 0)
    FROM gold.marketplace_region_daily
    WHERE marketplace_id = {SHOPEE_MARKETPLACE_ID}
      AND date BETWEEN %(date_from)s AND %(date_to)s
"""

# Mesmo dedup de SQL_INSERT_SHOPEE_STAGING/_shopee_incremental_select
# (DISTINCT ON (brand, order_id) ORDER BY file_id DESC + join de volta para
# preservar multi-item), só que filtrado por uma janela [date_from, date_to]
# via bind parameter em vez de "> min_date" literal.
SQL_SHOPEE_WINDOW_RECALC_ROWS = f"""
WITH shopee_winning_file AS (
    SELECT DISTINCT ON (brand, order_id) brand, order_id, file_id
    FROM silver.stg_shopee_order_item_snapshots
    ORDER BY brand, order_id, file_id DESC
),
shopee_all_lines_of_winner AS (
    SELECT s.*
    FROM silver.stg_shopee_order_item_snapshots s
    JOIN shopee_winning_file w
      ON w.brand = s.brand AND w.order_id = s.order_id AND w.file_id = s.file_id
),
shopee_per_order AS (
    SELECT
        brand,
        order_id,
        MAX(order_created_at)::date AS order_date,
        MAX(order_amount) AS order_amount,
        MAX(order_status) AS order_status,
        MAX(return_refund_status) AS return_refund_status,
        MAX(delivery_state) AS delivery_state,
        MAX(buyer_paid_shipping_fee) AS buyer_paid_shipping_fee,
        MAX(estimated_shipping_fee) AS estimated_shipping_fee,
        MAX(reverse_shipping_fee) AS reverse_shipping_fee,
        SUM(quantity) AS units
    FROM shopee_all_lines_of_winner
    GROUP BY brand, order_id
),
shopee_uf_map(delivery_state, uf) AS (VALUES {_SHOPEE_UF_MAP_VALUES}),
shopee_brand_loja(brand, loja_id) AS (VALUES {_BRAND_LOJA_VALUES}),
shopee_final AS (
    SELECT
        o.order_date AS date,
        {SHOPEE_MARKETPLACE_ID} AS marketplace_id,
        bl.loja_id,
        COALESCE(m.uf, 'XX') AS uf,
        o.order_amount, o.order_status, o.return_refund_status,
        o.buyer_paid_shipping_fee, o.estimated_shipping_fee, o.reverse_shipping_fee,
        o.units
    FROM shopee_per_order o
    JOIN shopee_brand_loja bl ON bl.brand = o.brand
    LEFT JOIN shopee_uf_map m ON m.delivery_state = o.delivery_state
    WHERE o.order_date BETWEEN %(date_from)s AND %(date_to)s
)
SELECT
    date, marketplace_id, loja_id, uf,
    SUM(CASE WHEN order_status NOT ILIKE '%%cancel%%' THEN order_amount ELSE 0 END) AS gmv,
    COUNT(*) FILTER (WHERE order_status NOT ILIKE '%%cancel%%') AS orders,
    SUM(CASE WHEN order_status NOT ILIKE '%%cancel%%' THEN units ELSE 0 END) AS units_sold,
    COUNT(*) FILTER (WHERE order_status ILIKE '%%cancel%%') AS canceled_orders,
    COUNT(*) FILTER (WHERE return_refund_status IS NOT NULL) AS returned_orders,
    NULL::numeric AS seller_shipping_cost,
    SUM(buyer_paid_shipping_fee) AS buyer_shipping_fee,
    SUM(estimated_shipping_fee) AS estimated_shipping_fee,
    SUM(reverse_shipping_fee) AS reverse_shipping_fee,
    COUNT(*) FILTER (WHERE uf <> 'XX') AS uf_known_orders,
    COUNT(*) AS uf_eligible_orders,
    0 AS shipping_cost_covered_orders,
    0 AS shipping_cost_eligible_orders
FROM shopee_final
GROUP BY date, marketplace_id, loja_id, uf
"""

SQL_SHOPEE_WINDOW_RECALC_AGGREGATES = f"""
WITH shopee_window_recalc AS (
    {SQL_SHOPEE_WINDOW_RECALC_ROWS}
)
SELECT COUNT(*), COALESCE(SUM(gmv), 0), COALESCE(SUM(orders), 0) FROM shopee_window_recalc
"""

SQL_SHOPEE_WINDOW_RECALC_DUPLICATES = f"""
WITH shopee_window_recalc AS (
    {SQL_SHOPEE_WINDOW_RECALC_ROWS}
)
SELECT COUNT(*) FROM (
    SELECT date, marketplace_id, loja_id, uf FROM shopee_window_recalc
    GROUP BY date, marketplace_id, loja_id, uf HAVING COUNT(*) > 1
) t
"""

SQL_SHOPEE_WINDOW_RECALC_NULLS = f"""
WITH shopee_window_recalc AS (
    {SQL_SHOPEE_WINDOW_RECALC_ROWS}
)
SELECT COUNT(*) FROM shopee_window_recalc
WHERE date IS NULL OR marketplace_id IS NULL OR loja_id IS NULL OR uf IS NULL
"""

SQL_SHOPEE_WINDOW_RECALC_NUMERATOR_DENOMINATOR = f"""
WITH shopee_window_recalc AS (
    {SQL_SHOPEE_WINDOW_RECALC_ROWS}
)
SELECT COUNT(*) FROM shopee_window_recalc
WHERE uf_known_orders > uf_eligible_orders
   OR shipping_cost_covered_orders > shipping_cost_eligible_orders
"""

# Colunas de negócio comparadas campo a campo (Gate S2.1). Chave técnica
# (id) e carimbos (ingested_at, source_updated_at) são EXCLUÍDOS de
# propósito: o diagnose responde "os DADOS mudariam", não "a linha foi
# reingerida". A ordem/lista espelha exatamente as colunas produzidas pelo
# recálculo Shopee (SQL_SHOPEE_WINDOW_RECALC_ROWS) e presentes em
# gold.marketplace_region_daily.
_WINDOW_BUSINESS_COLUMNS = (
    "gmv", "orders", "units_sold", "canceled_orders", "returned_orders",
    "seller_shipping_cost", "buyer_shipping_fee", "estimated_shipping_fee", "reverse_shipping_fee",
    "uf_known_orders", "uf_eligible_orders",
    "shipping_cost_covered_orders", "shipping_cost_eligible_orders",
)

_WINDOW_KEY_COLUMNS = ("date", "marketplace_id", "loja_id", "uf")

# `IS DISTINCT FROM` (não `<>`): trata NULL como um valor comparável, então
# NULL-vs-0 e NULL-vs-NULL são classificados corretamente — um `<>` retorna
# NULL (não TRUE) quando um lado é NULL e a mudança passaria despercebida.
_WINDOW_CHANGED_PREDICATE = " OR ".join(
    f"g.{c} IS DISTINCT FROM s.{c}" for c in _WINDOW_BUSINESS_COLUMNS
)

_WINDOW_GOLD_SELECT_COLUMNS = ", ".join(_WINDOW_KEY_COLUMNS + _WINDOW_BUSINESS_COLUMNS)

# Comparação exata por chave (date, marketplace_id, loja_id, uf) via FULL
# OUTER JOIN entre a Gold Shopee atual (na janela) e o recálculo da fonte
# (na mesma janela). Detecta REDISTRIBUIÇÃO entre chaves — ex.: pedidos que
# saem de uf='XX' para uf='SP' sem alterar GMV/orders/linhas totais — que
# uma comparação só de agregados jamais pegaria. As colunas de chave são
# NOT NULL nos dois lados (constraint na Gold; COALESCE/GROUP BY no
# recálculo), então `s.date IS NULL` / `g.date IS NULL` são sinais
# confiáveis de "sem correspondência do outro lado" no FULL OUTER JOIN.
SQL_SHOPEE_WINDOW_KEY_DIFF = f"""
WITH shopee_window_recalc AS (
    {SQL_SHOPEE_WINDOW_RECALC_ROWS}
),
gold_window AS (
    SELECT {_WINDOW_GOLD_SELECT_COLUMNS}
    FROM gold.marketplace_region_daily
    WHERE marketplace_id = {SHOPEE_MARKETPLACE_ID}
      AND date BETWEEN %(date_from)s AND %(date_to)s
)
SELECT
    COUNT(*) FILTER (WHERE s.date IS NULL) AS gold_only_key_count,
    COUNT(*) FILTER (WHERE g.date IS NULL) AS source_only_key_count,
    COUNT(*) FILTER (
        WHERE g.date IS NOT NULL AND s.date IS NOT NULL
          AND ({_WINDOW_CHANGED_PREDICATE})
    ) AS changed_key_count
FROM gold_window g
FULL OUTER JOIN shopee_window_recalc s
  ON g.date = s.date
 AND g.marketplace_id = s.marketplace_id
 AND g.loja_id = s.loja_id
 AND g.uf = s.uf
"""


def _validate_shopee_window(date_from: date, date_to: date) -> None:
    """Validações de negócio da janela — rodam ANTES de qualquer conexão.
    Formato ISO já é garantido por `type=date.fromisoformat` no argparse
    (CLI) ou pela responsabilidade do chamador (uso como função)."""
    if date_from > date_to:
        raise InvalidWindowError(f"date_from ({date_from.isoformat()}) é posterior a date_to ({date_to.isoformat()})")
    today = date.today()
    if date_to > today:
        raise InvalidWindowError(f"date_to ({date_to.isoformat()}) está no futuro (hoje: {today.isoformat()})")
    window_days = (date_to - date_from).days + 1
    if window_days > MAX_SHOPEE_WINDOW_DAYS:
        raise InvalidWindowError(
            f"janela de {window_days} dia(s) excede o máximo permitido de {MAX_SHOPEE_WINDOW_DAYS} dias"
        )


def diagnose_shopee_window(read_url: str, date_from: date, date_to: date) -> ShopeeWindowDiagnoseReport:
    """Somente leitura — NUNCA abre conexão de escrita, nunca lê o secret de
    escrita dedicado (o mesmo arquivo local usado por `--incremental`),
    nunca cria staging/temp table, nunca insere/deleta. Sessão
    explicitamente `readonly=True` (mesmo padrão de
    `diagnose_incremental_load`/`write_conn._connect_readonly`). Recalcula,
    a partir de `silver.stg_shopee_order_item_snapshots`, o mesmo dedup
    "arquivo vencedor" da carga, escopado à janela [date_from, date_to], e
    compara com o que já está em `gold.marketplace_region_daily` para
    Shopee na mesma janela — para avaliar se um `--refresh-shopee-window`
    (Gate S3, ainda NÃO implementado) seria seguro."""
    _validate_shopee_window(date_from, date_to)
    params = {"date_from": date_from, "date_to": date_to}

    # Snapshot consistente (Gate S2.1): TODAS as consultas na MESMA conexão,
    # numa ÚNICA transação read-only REPEATABLE READ (autocommit desligado).
    # REPEATABLE READ fixa o snapshot no primeiro comando e o mantém para
    # todas as consultas seguintes — sem isso (READ COMMITTED/autocommit),
    # cada SELECT poderia ver um estado diferente se houvesse ingestão
    # concorrente entre eles, e o key-diff (Gold vs. fonte) compararia dois
    # instantes distintos. `readonly=True` garante que, mesmo com um bug
    # aqui, nada consegue escrever. Rollback explícito ao final INCLUSIVE no
    # sucesso (é uma transação só de leitura — não há nada a commitar; o
    # rollback só fecha o snapshot de forma limpa).
    #
    # `conn.close()` precisa acontecer em QUALQUER caminho depois que
    # `connect()` retornar uma conexão — inclusive se `set_session()` (que já
    # fala com o servidor para configurar isolation level/readonly) falhar.
    # Por isso `set_session()` entra DENTRO do `try`, não antes dele.
    conn = psycopg2.connect(read_url, connect_timeout=15)
    try:
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ", autocommit=False)
        with conn.cursor() as cur:
            cur.execute(SQL_SHOPEE_WINDOW_GOLD_AGGREGATES, params)
            gold_rows, gold_gmv, gold_orders = cur.fetchone()

            cur.execute(SQL_SHOPEE_WINDOW_RECALC_AGGREGATES, params)
            recalc_rows, recalc_gmv, recalc_orders = cur.fetchone()

            cur.execute(SQL_SHOPEE_WINDOW_RECALC_DUPLICATES, params)
            (dup_count,) = cur.fetchone()

            cur.execute(SQL_SHOPEE_WINDOW_RECALC_NULLS, params)
            (null_count,) = cur.fetchone()

            cur.execute(SQL_SHOPEE_WINDOW_RECALC_NUMERATOR_DENOMINATOR, params)
            (bad_count,) = cur.fetchone()

            cur.execute(SQL_SHOPEE_WINDOW_KEY_DIFF, params)
            gold_only, source_only, changed = cur.fetchone()
        conn.rollback()  # sucesso: transação só de leitura, fecha o snapshot limpo
    except Exception:
        # Best-effort: se o rollback em si falhar (ex.: conexão já caída
        # porque foi isso que quebrou o try), nunca deixar essa falha
        # mascarar a exceção original que estamos prestes a propagar.
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        conn.close()

    gold_rows = int(gold_rows)
    gold_orders = int(gold_orders)
    recalc_rows = int(recalc_rows)
    recalc_orders = int(recalc_orders)

    return ShopeeWindowDiagnoseReport(
        date_from=date_from,
        date_to=date_to,
        gold_rows=gold_rows,
        gold_gmv=gold_gmv,
        gold_orders=gold_orders,
        recalculated_rows=recalc_rows,
        recalculated_gmv=recalc_gmv,
        recalculated_orders=recalc_orders,
        rows_to_delete=gold_rows,
        rows_to_insert=recalc_rows,
        gmv_delta=recalc_gmv - gold_gmv,
        orders_delta=recalc_orders - gold_orders,
        overlaps_existing_gold_data=gold_rows > 0,
        zero_source_risk=(recalc_rows == 0 and gold_rows > 0),
        duplicate_key_count=int(dup_count),
        null_required_count=int(null_count),
        numerator_over_denominator_count=int(bad_count),
        gold_only_key_count=int(gold_only),
        source_only_key_count=int(source_only),
        changed_key_count=int(changed),
    )


# =============================================================================
# Gate S3 — refresh e restore transacionais da Gold regional Shopee POR
# JANELA. PRIMEIRO caminho de escrita deste módulo que faz DELETE (todos os
# outros — execute_first_load, execute_incremental_load, DDL — só fazem
# INSERT/DDL). Secret e preflight DEDICADOS em
# pipelines/ingestion/gold_regional/window_write_conn.py
# (.env.gold-window-write.local — NUNCA .env.gold-write.local, o secret do
# --incremental). Escopo do DELETE/INSERT sempre e só:
#     marketplace_id = SHOPEE_MARKETPLACE_ID AND date BETWEEN date_from AND date_to
# Nenhuma linha ML, TikTok ou Shopee fora da janela é tocada — garantido
# tanto pelo WHERE explícito quanto por um fingerprint agregado de "tudo
# fora do escopo" conferido antes e depois de qualquer escrita (defesa em
# profundidade contra um bug no WHERE, não só confiança nele).
#
# `execute_shopee_window_refresh`/`execute_shopee_window_restore` NUNCA
# reaproveitam `diagnose_shopee_window`: recalculam tudo (inclusive
# structurally_safe_for_refresh) sob o table lock, na hora — um diagnose
# executado minutos antes pode estar desatualizado.
# =============================================================================

_GOLD_TABLE_QUALIFIED = "gold.marketplace_region_daily"
_RE_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

WINDOW_BACKUP_SCHEMA_VERSION = 1

# Lista ordenada das 17 colunas de _STAGING_INSERT_COLUMNS — fonte única
# (derivada da mesma constante já usada pela carga/incremental/diagnose,
# nunca redigitada à mão) para o formato dos registros de backup/restore.
_STAGING_INSERT_COLUMNS_LIST = [c.strip() for c in _STAGING_INSERT_COLUMNS.split(",")]

_DECIMAL_COLUMN_NAMES = frozenset(
    ("gmv", "seller_shipping_cost", "buyer_shipping_fee", "estimated_shipping_fee", "reverse_shipping_fee")
)
_REQUIRED_NONNEGATIVE_INT_COLUMNS = (
    "orders", "units_sold", "canceled_orders", "returned_orders",
    "uf_known_orders", "uf_eligible_orders",
    "shipping_cost_covered_orders", "shipping_cost_eligible_orders",
)
_NULLABLE_DECIMAL_COLUMNS = (
    "seller_shipping_cost", "buyer_shipping_fee", "estimated_shipping_fee", "reverse_shipping_fee",
)

# Mesmo conjunto de 5 lojas Shopee de _BRAND_LOJA_VALUES (apice=1,
# barbours=2, kokeshi=3, lescent=4, rituaria=5) — hardcoded aqui (não
# reparseado de _BRAND_LOJA_VALUES) para o validador de registro de backup
# nunca depender de parsing de string em caminho de segurança.
_VALID_SHOPEE_LOJA_IDS = frozenset({1, 2, 3, 4, 5})

# 27 UFs oficiais + 'XX' — mesmo conjunto do CHECK chk_region_uf_valida em
# db/sql/gold/marketplace_region_daily_ddl.sql (fonte da verdade do schema;
# hardcoded aqui de propósito — um registro de backup é entrada NÃO
# CONFIÁVEL e não deve depender de uma query ao banco para ser validado).
_VALID_UF_CODES = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    "XX",
})

# Gate S3.1: limites explícitos contra um backup adversarial/corrompido —
# "o SHA comprova integridade dos bytes, mas não torna metadados
# semanticamente verdadeiros" (ver validate_window_backup_payload).
_ISO_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_WINDOW_BACKUP_FILE_BYTES = 64 * 1024 * 1024  # 64 MiB — verificado ANTES de read_text/json.loads
MAX_WINDOW_BACKUP_RECORDS = MAX_SHOPEE_WINDOW_DAYS * len(_VALID_SHOPEE_LOJA_IDS) * len(_VALID_UF_CODES)  # 180*5*28

_WINDOW_RECORD_KEYS = frozenset(_STAGING_INSERT_COLUMNS_LIST)

_WINDOW_BACKUP_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "created_at_utc", "marketplace_id", "date_from", "date_to",
    "grain_key", "business_columns", "before_count", "after_count",
    "before_aggregates", "after_aggregates", "before_records", "planned_after_records",
})


class BackupIntegrityError(RuntimeError):
    """Backup de janela Shopee gravado/lido não reconferiu com o esperado."""


def _rollback_best_effort(conn, result) -> None:
    """Tenta o rollback; se ele PRÓPRIO falhar, anexa um aviso sanitizado a
    `result` (que já foi decidido — bloqueio deliberado OU exceção capturada)
    em vez de deixar a falha do rollback mascarar o motivo original do
    abort. Usado em TODO ponto de rollback de `execute_shopee_window_refresh`/
    `execute_shopee_window_restore`, não só no `except` genérico."""
    try:
        conn.rollback()
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(
            f"falha ao executar rollback ({sanitize_error_message(exc)}) — resultado principal preservado"
        )


@dataclass
class ShopeeWindowRefreshResult:
    outcome: str  # "committed" | "no_op" | "blocked" | "failed"
    rows_deleted: int = 0
    rows_inserted: int = 0
    backup_path: Optional[str] = None
    backup_sha256: Optional[str] = None
    gold_gmv_before: Optional[Decimal] = None
    gold_gmv_after: Optional[Decimal] = None
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class ShopeeWindowRestoreResult:
    outcome: str  # "committed" | "blocked" | "failed"
    rows_deleted: int = 0
    rows_inserted: int = 0
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# audit_path — validação ANTECIPADA (refresh: destino NOVO, nunca deve
# existir) vs. tratamento como ENTRADA NÃO CONFIÁVEL (restore: arquivo
# EXISTENTE, ver validate_window_backup_payload). Duas funções distintas de
# propósito — as regras são opostas.
# ---------------------------------------------------------------------------

def _validate_new_window_audit_path(audit_path: Path, repo_root: Path) -> Optional[str]:
    """Checagem antecipada para o DESTINO de um novo backup (refresh) —
    NUNCA usada para o backup de ENTRADA do restore. A garantia final
    contra sobrescrita vem de `os.link` em `_write_window_backup_atomic`
    (imune à corrida TOCTOU entre esta checagem e a publicação).

    Gate S3.1: NENHUMA mensagem aqui pode conter o caminho absoluto,
    `repo_root`, ou qualquer estrutura de diretório — nunca expor
    usuário/máquina local em logs/saída de erro. Quando um nome de arquivo
    ajuda a identificar QUAL arquivo (já existe / companion já existe), usa
    só `.name` (basename), nunca o caminho completo.

    Gate S3.2 (Finding 4): NUNCA levanta exceção — `resolve()`/`is_dir()`/
    `exists()` podem levantar OSError/RuntimeError/ValueError em paths
    patológicos (loop de symlink, NUL embutido no Windows, ACL negando
    stat); qualquer uma vira um problema sanitizado, nunca um traceback."""
    try:
        if not audit_path.is_absolute():
            return "audit_path precisa ser um caminho absoluto"
        if audit_path.suffix != ".json":
            return "audit_path precisa terminar em .json"
        if not audit_path.parent.is_dir():
            return "diretório pai de audit_path não existe"

        resolved = audit_path.resolve()
        repo_resolved = repo_root.resolve()
        if resolved == repo_resolved or repo_resolved in resolved.parents:
            return "audit_path não pode estar dentro do repositório"

        if audit_path.exists():
            return f"audit_path já existe (recusado — nunca sobrescrever um backup anterior): {audit_path.name}"

        sha_path = Path(str(audit_path) + ".sha256")
        if sha_path.exists():
            return f"companion .sha256 já existe (recusado): {sha_path.name}"
    except (OSError, RuntimeError, ValueError) as exc:
        return f"falha ao validar audit_path (caminho inacessível ou inválido): {type(exc).__name__}"

    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Formato do registro de backup — 1 linha do grão regional
# (date, marketplace_id, loja_id, uf) + 13 campos de negócio, EXATAMENTE as
# 17 colunas de _STAGING_INSERT_COLUMNS. Nunca contém order_id/CPF/
# filename/file_id/URL/host/credencial — a Gold regional não tem essas
# colunas (grão é agregado, não linha de pedido). Decimal sempre como
# string; date sempre ISO-8601.
# ---------------------------------------------------------------------------

def _row_to_backup_record(row: tuple) -> dict:
    d = dict(zip(_STAGING_INSERT_COLUMNS_LIST, row))
    d["date"] = d["date"].isoformat()
    for col in _DECIMAL_COLUMN_NAMES:
        value = d[col]
        d[col] = None if value is None else str(value)
    return d


def _backup_record_to_row_values(record: dict) -> tuple:
    """Converte um registro de backup (JSON, JÁ validado por
    `_validate_window_record`) de volta para os tipos nativos do INSERT
    (Decimal/date/int) — nunca chamada sem validação prévia completa."""
    values = []
    for col in _STAGING_INSERT_COLUMNS_LIST:
        v = record[col]
        if col == "date":
            values.append(date.fromisoformat(v))
        elif col in _DECIMAL_COLUMN_NAMES:
            values.append(None if v is None else Decimal(v))
        else:
            values.append(v)
    return tuple(values)


def _records_by_key(records) -> dict:
    return {(r["date"], r["marketplace_id"], r["loja_id"], r["uf"]): r for r in records}


def _aggregate_backup_records(records: list[dict]) -> dict:
    gmv_sum = sum((Decimal(r["gmv"]) for r in records), Decimal("0"))
    orders_sum = sum(r["orders"] for r in records)
    return {"rows": len(records), "gmv": str(gmv_sum), "orders": orders_sum}


def _parse_decimal_field(value):
    """Retorna (Decimal, None) se válido, ou (None, problema) caso
    contrário. NUNCA levanta exceção. Só aceita string (formato de
    serialização esperado) — um int/float/bool aqui indica um arquivo que
    não passou pela serialização esperada (Decimal sempre como string)."""
    if not isinstance(value, str):
        return None, "esperado string numérica (Decimal serializado)"
    try:
        d = Decimal(value)
    except (InvalidOperation, ValueError):
        return None, "não é um número decimal válido"
    if not d.is_finite():
        return None, "não é finito (NaN/Infinity)"
    return d, None


def _valid_iso_date_in_window(value, date_from: date, date_to: date):
    if not isinstance(value, str):
        return None, "esperado string de data ISO (YYYY-MM-DD)"
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None, "formato ou calendário de data inválido"
    if not (date_from <= d <= date_to):
        return None, f"data fora da janela [{date_from.isoformat()}, {date_to.isoformat()}]"
    return d, None


def _validate_window_record(record, date_from: date, date_to: date) -> list[str]:
    """Type-safety PRIMEIRO (nunca usa um valor em set()/dict/comparação
    antes de saber o tipo), depois formato/domínio. NUNCA levanta exceção,
    seja qual for o tipo de `record`. Usada tanto por
    `validate_window_backup_payload` (backup como entrada não confiável no
    restore) quanto internamente por `_write_window_backup_atomic` (revalida
    o próprio backup relido do disco após publicar)."""
    if not isinstance(record, dict):
        return ["registro não é um objeto JSON"]

    extra = set(record.keys()) - _WINDOW_RECORD_KEYS
    missing = _WINDOW_RECORD_KEYS - set(record.keys())
    problems: list[str] = []
    if extra:
        problems.append(f"chave(s) inesperada(s): {sorted(extra)}")
    if missing:
        problems.append(f"chave(s) ausente(s): {sorted(missing)}")
    if problems:
        return problems

    _, date_problem = _valid_iso_date_in_window(record["date"], date_from, date_to)
    if date_problem:
        problems.append(f"date: {date_problem}")

    marketplace_id = record["marketplace_id"]
    if isinstance(marketplace_id, bool) or not isinstance(marketplace_id, int) or marketplace_id != SHOPEE_MARKETPLACE_ID:
        problems.append(f"marketplace_id inválido (esperado {SHOPEE_MARKETPLACE_ID}, Shopee)")

    loja_id = record["loja_id"]
    if isinstance(loja_id, bool) or not isinstance(loja_id, int) or loja_id not in _VALID_SHOPEE_LOJA_IDS:
        problems.append(f"loja_id inválido (esperado um de {sorted(_VALID_SHOPEE_LOJA_IDS)})")

    uf = record["uf"]
    if not isinstance(uf, str) or uf not in _VALID_UF_CODES:
        problems.append("uf inválida")

    gmv_value, gmv_problem = _parse_decimal_field(record["gmv"])
    if gmv_problem:
        problems.append(f"gmv: {gmv_problem}")
    elif gmv_value < 0:
        problems.append("gmv negativo")

    for col in _NULLABLE_DECIMAL_COLUMNS:
        value = record[col]
        if value is None:
            continue
        parsed, problem = _parse_decimal_field(value)
        if problem:
            problems.append(f"{col}: {problem}")
        elif parsed < 0:
            problems.append(f"{col} negativo")

    int_values: dict = {}
    for col in _REQUIRED_NONNEGATIVE_INT_COLUMNS:
        value = record[col]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            problems.append(f"{col} inválido (esperado inteiro >= 0)")
        else:
            int_values[col] = value

    if "uf_known_orders" in int_values and "uf_eligible_orders" in int_values:
        if int_values["uf_known_orders"] > int_values["uf_eligible_orders"]:
            problems.append("uf_known_orders > uf_eligible_orders")
    if "shipping_cost_covered_orders" in int_values and "shipping_cost_eligible_orders" in int_values:
        if int_values["shipping_cost_covered_orders"] > int_values["shipping_cost_eligible_orders"]:
            problems.append("shipping_cost_covered_orders > shipping_cost_eligible_orders")

    return problems


def _duplicate_keys_in_records(records: list[dict]) -> list[tuple]:
    """Só chamar DEPOIS que cada registro já passou por
    `_validate_window_record` sem problemas — usa (date, marketplace_id,
    loja_id, uf) como tupla de chave, que só é seguro depois da
    type-safety individual."""
    counts: dict[tuple, int] = {}
    for r in records:
        key = (r["date"], r["marketplace_id"], r["loja_id"], r["uf"])
        counts[key] = counts.get(key, 0) + 1
    return [k for k, n in counts.items() if n > 1]


def _validate_aggregates_field(payload_aggregates, records: list[dict], field_name: str) -> list[str]:
    """Valida `before_aggregates`/`after_aggregates`: dict com EXATAMENTE
    `rows`/`gmv`/`orders`, tipos válidos, `rows` igual à contagem real de
    `records`, e GMV/pedidos RECALCULADOS a partir de `records` idênticos
    ao declarado. Só chamar DEPOIS que `records` já passou por
    `_validate_window_record` sem problemas (usa `r["gmv"]`/`r["orders"]`
    como valores confiáveis). O SHA do arquivo comprova integridade de
    bytes, não que os agregados declarados batem com os registros —
    por isso esta reconciliação é obrigatória, não opcional."""
    if not isinstance(payload_aggregates, dict):
        return [f"{field_name} não é um objeto"]

    expected_keys = frozenset({"rows", "gmv", "orders"})
    extra = set(payload_aggregates.keys()) - expected_keys
    missing = expected_keys - set(payload_aggregates.keys())
    problems: list[str] = []
    if extra:
        problems.append(f"{field_name}: chave(s) inesperada(s): {sorted(extra)}")
    if missing:
        problems.append(f"{field_name}: chave(s) ausente(s): {sorted(missing)}")
    if problems:
        return problems

    rows = payload_aggregates["rows"]
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
        problems.append(f"{field_name}.rows inválido (esperado inteiro >= 0)")
    elif rows != len(records):
        problems.append(f"{field_name}.rows ({rows}) não bate com a contagem real de registros ({len(records)})")

    gmv_declared, gmv_problem = _parse_decimal_field(payload_aggregates.get("gmv"))
    if gmv_problem:
        problems.append(f"{field_name}.gmv: {gmv_problem}")
    else:
        recomputed_gmv = sum((Decimal(r["gmv"]) for r in records), Decimal("0"))
        if gmv_declared != recomputed_gmv:
            problems.append(f"{field_name}.gmv declarado não bate com a soma recalculada dos registros")

    orders_declared = payload_aggregates.get("orders")
    if isinstance(orders_declared, bool) or not isinstance(orders_declared, int) or orders_declared < 0:
        problems.append(f"{field_name}.orders inválido (esperado inteiro >= 0)")
    else:
        recomputed_orders = sum(r["orders"] for r in records)
        if orders_declared != recomputed_orders:
            problems.append(f"{field_name}.orders declarado não bate com a soma recalculada dos registros")

    return problems


def validate_window_backup_payload(payload) -> list[str]:
    """Trata o backup como ENTRADA NÃO CONFIÁVEL — NUNCA levanta exceção,
    seja qual for o JSON. O SHA-256 (conferido ANTES desta função, em
    `_validate_and_load_window_backup`) comprova só a integridade dos
    BYTES do arquivo — não torna os METADADOS declarados (contagens,
    agregados, colunas) semanticamente verdadeiros; por isso toda
    reconciliação abaixo continua obrigatória mesmo com o hash batendo.

    Ordem: forma do topo -> `schema_version` conhecido -> `created_at_utc`
    (timestamp UTC válido) / `marketplace_id` / `grain_key` (EXATAMENTE a
    chave oficial) / `business_columns` (EXATAMENTE as colunas oficiais, NA
    ORDEM oficial) -> janela válida (<=180 dias) -> `before_records`/
    `planned_after_records` são listas -> limite máximo de registros
    (180 dias × 5 lojas × 28 UFs) -> cada registro (campo a campo,
    `_validate_window_record`, before/planned validados SEPARADAMENTE) ->
    chave única dentro de cada lista -> `before_count`/`after_count` batem
    com o tamanho real das listas -> `before_aggregates`/`after_aggregates`
    recalculados a partir dos registros e idênticos ao declarado.

    Usada tanto por `_validate_and_load_window_backup` (arquivo fornecido
    pelo operador, tanto no fail-fast da CLI quanto na revalidação
    autoritativa de `execute_shopee_window_restore`) quanto por
    `_write_window_backup_atomic` (revalida o próprio backup relido do
    disco logo após publicar)."""
    if not isinstance(payload, dict):
        return ["backup não é um objeto JSON no nível superior"]

    extra = set(payload.keys()) - _WINDOW_BACKUP_TOP_LEVEL_KEYS
    missing = _WINDOW_BACKUP_TOP_LEVEL_KEYS - set(payload.keys())
    problems: list[str] = []
    if extra:
        problems.append(f"chave(s) de nível superior inesperada(s): {sorted(extra)}")
    if missing:
        problems.append(f"chave(s) de nível superior ausente(s): {sorted(missing)}")
    if problems:
        return problems

    schema_version = payload["schema_version"]
    if schema_version != WINDOW_BACKUP_SCHEMA_VERSION:
        return [f"schema_version desconhecido: {schema_version!r} (esperado {WINDOW_BACKUP_SCHEMA_VERSION})"]

    created_at_utc = payload["created_at_utc"]
    if not isinstance(created_at_utc, str) or not _ISO_UTC_TIMESTAMP_RE.match(created_at_utc):
        problems.append("created_at_utc inválido (esperado timestamp UTC 'YYYY-MM-DDTHH:MM:SSZ')")
    else:
        try:
            datetime.strptime(created_at_utc, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            problems.append("created_at_utc com calendário/hora inválidos")

    marketplace_id = payload["marketplace_id"]
    if isinstance(marketplace_id, bool) or not isinstance(marketplace_id, int) or marketplace_id != SHOPEE_MARKETPLACE_ID:
        problems.append("marketplace_id do backup não é Shopee")

    if payload["grain_key"] != list(_WINDOW_KEY_COLUMNS):
        problems.append("grain_key não bate com a chave oficial (date, marketplace_id, loja_id, uf)")

    if payload["business_columns"] != list(_WINDOW_BUSINESS_COLUMNS):
        problems.append("business_columns não bate com as colunas oficiais (conjunto e ordem)")

    date_from_raw = payload["date_from"]
    date_to_raw = payload["date_to"]
    if not isinstance(date_from_raw, str) or not isinstance(date_to_raw, str):
        problems.append("date_from/date_to devem ser strings ISO")
        return problems

    try:
        parsed_from = date.fromisoformat(date_from_raw)
        parsed_to = date.fromisoformat(date_to_raw)
    except ValueError:
        problems.append("date_from/date_to com formato ou calendário inválido")
        return problems

    try:
        _validate_shopee_window(parsed_from, parsed_to)
    except InvalidWindowError as exc:
        problems.append(f"janela do backup inválida: {exc}")

    if not isinstance(payload["before_records"], list):
        problems.append("before_records não é uma lista")
    if not isinstance(payload["planned_after_records"], list):
        problems.append("planned_after_records não é uma lista")
    if problems:
        return problems

    before_records = payload["before_records"]
    after_records = payload["planned_after_records"]

    if len(before_records) > MAX_WINDOW_BACKUP_RECORDS:
        problems.append(f"before_records excede o limite de {MAX_WINDOW_BACKUP_RECORDS} registros")
    if len(after_records) > MAX_WINDOW_BACKUP_RECORDS:
        problems.append(f"planned_after_records excede o limite de {MAX_WINDOW_BACKUP_RECORDS} registros")
    if problems:
        return problems

    for i, r in enumerate(before_records):
        problems.extend(f"before_records[{i}]: {p}" for p in _validate_window_record(r, parsed_from, parsed_to))
    for i, r in enumerate(after_records):
        problems.extend(f"planned_after_records[{i}]: {p}" for p in _validate_window_record(r, parsed_from, parsed_to))
    if problems:
        return problems

    before_dupes = _duplicate_keys_in_records(before_records)
    if before_dupes:
        problems.append(f"before_records com {len(before_dupes)} chave(s) duplicada(s)")
    after_dupes = _duplicate_keys_in_records(after_records)
    if after_dupes:
        problems.append(f"planned_after_records com {len(after_dupes)} chave(s) duplicada(s)")
    if problems:
        return problems

    before_count = payload["before_count"]
    if isinstance(before_count, bool) or not isinstance(before_count, int) or before_count < 0:
        problems.append("before_count inválido (esperado inteiro >= 0)")
    elif before_count != len(before_records):
        problems.append(f"before_count ({before_count}) não bate com o tamanho real de before_records ({len(before_records)})")

    after_count = payload["after_count"]
    if isinstance(after_count, bool) or not isinstance(after_count, int) or after_count < 0:
        problems.append("after_count inválido (esperado inteiro >= 0)")
    elif after_count != len(after_records):
        problems.append(f"after_count ({after_count}) não bate com o tamanho real de planned_after_records ({len(after_records)})")

    problems.extend(_validate_aggregates_field(payload["before_aggregates"], before_records, "before_aggregates"))
    problems.extend(_validate_aggregates_field(payload["after_aggregates"], after_records, "after_aggregates"))

    return problems


def _validate_existing_window_audit_path(audit_path: Path, repo_root: Path) -> Optional[str]:
    """Checagem do backup de ENTRADA do restore — o arquivo deve EXISTIR
    (oposto de `_validate_new_window_audit_path`, que é para o DESTINO de
    um novo backup no refresh, que nunca deve existir ainda). Mensagens
    nunca contêm caminho absoluto/repo_root — nunca expor estrutura de
    diretório local.

    Gate S3.2 (Finding 4): NUNCA levanta exceção — mesma proteção de
    `_validate_new_window_audit_path` (paths patológicos viram problema
    sanitizado, nunca traceback)."""
    try:
        if not audit_path.is_absolute():
            return "audit_path precisa ser um caminho absoluto"
        if audit_path.suffix != ".json":
            return "audit_path precisa terminar em .json"

        resolved = audit_path.resolve()
        repo_resolved = repo_root.resolve()
        if resolved == repo_resolved or repo_resolved in resolved.parents:
            return "audit_path não pode estar dentro do repositório"

        if not audit_path.exists():
            return "audit_path não existe"
        if not audit_path.is_file():
            return "audit_path não é um arquivo regular"
    except (OSError, RuntimeError, ValueError) as exc:
        return f"falha ao validar audit_path (caminho inacessível ou inválido): {type(exc).__name__}"

    return None


def _validate_and_load_window_backup(
    audit_path: Path, expected_backup_sha256: str, repo_root: Path,
) -> tuple[Optional[dict], list[str]]:
    """Valida e carrega um backup de janela Shopee como ENTRADA NÃO
    CONFIÁVEL — NUNCA levanta exceção. Retorna `(payload, [])` se tudo
    validar, ou `(None, problemas)` caso contrário.

    Usada tanto por `run_restore_shopee_window_cli` (fail-fast ANTES de ler
    o secret ou rodar o preflight) quanto por `execute_shopee_window_restore`
    (validação AUTORITATIVA — sempre revalida do zero; o arquivo pode ter
    mudado entre as duas chamadas, então a checagem antecipada da CLI NUNCA
    substitui esta).

    Gate S3.2 (Finding 3): o arquivo é aberto UMA ÚNICA VEZ, em modo
    binário, e TODAS as decisões (tamanho via `os.fstat` no descritor
    aberto, leitura limitada a `MAX_WINDOW_BACKUP_FILE_BYTES + 1` bytes,
    SHA-256, decodificação UTF-8, parse JSON) operam sobre o MESMO conjunto
    de bytes — fecha a janela TOCTOU do desenho anterior (stat, depois
    reabrir para hash, depois reabrir para read_text: o arquivo podia mudar
    entre cada passo, e o JSON parseado podia não ser o que o SHA validou).
    A leitura é sempre limitada: mesmo que `fstat` minta (arquivo crescendo
    concorrentemente), um byte além do teto aborta — nunca carrega um
    arquivo sem limite na memória.

    Ordem: caminho (absoluto, `.json`, fora do repo, existe) -> formato do
    SHA (64 hex) -> open binário único -> fstat (teto de tamanho) ->
    leitura limitada -> SHA-256 dos bytes lidos -> decodificação UTF-8 +
    parse JSON dos MESMOS bytes -> estrutura completa
    (`validate_window_backup_payload`). Mensagens nunca contêm caminho
    absoluto nem conteúdo do arquivo."""
    path_problem = _validate_existing_window_audit_path(audit_path, repo_root)
    if path_problem:
        return None, [path_problem]

    if not isinstance(expected_backup_sha256, str) or not _RE_SHA256_HEX.match(expected_backup_sha256):
        return None, ["expected_backup_sha256 inválido (esperado hexadecimal de 64 caracteres)"]

    try:
        with open(audit_path, "rb") as f:
            declared_size = os.fstat(f.fileno()).st_size
            if declared_size > MAX_WINDOW_BACKUP_FILE_BYTES:
                return None, [f"arquivo de backup excede o limite de tamanho ({MAX_WINDOW_BACKUP_FILE_BYTES} bytes)"]
            raw_bytes = f.read(MAX_WINDOW_BACKUP_FILE_BYTES + 1)
    except (OSError, ValueError) as exc:
        # ValueError cobre paths patológicos (ex.: NUL embutido no Windows).
        return None, [f"falha ao abrir/ler backup: {type(exc).__name__}"]

    if len(raw_bytes) > MAX_WINDOW_BACKUP_FILE_BYTES:
        # fstat disse que cabia, mas a leitura trouxe um byte além do teto
        # (arquivo mudou entre fstat e read) — nunca confiar só no fstat.
        return None, [f"arquivo de backup excede o limite de tamanho ({MAX_WINDOW_BACKUP_FILE_BYTES} bytes)"]

    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256 != expected_backup_sha256:
        return None, ["SHA-256 do backup não bate com o esperado -- arquivo pode ter sido alterado"]

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"falha ao decodificar/parsear backup: {type(exc).__name__}"]

    structure_problems = validate_window_backup_payload(payload)
    if structure_problems:
        return None, structure_problems

    return payload, []


def _write_window_backup_atomic(
    audit_path: Path,
    date_from: date,
    date_to: date,
    before_rows: list[tuple],
    after_rows: list[tuple],
) -> str:
    """Publica o backup em `audit_path` SEM POSSIBILIDADE DE SOBRESCRITA:

    1. Monta o payload versionado (schema_version/created_at_utc/
       marketplace_id/date_from/date_to/grain_key/business_columns/
       contagens/agregados/registros) — só agregados e linhas do grão
       regional; nunca order_id/CPF/filename/file_id/URL/host/credencial
       (a Gold regional não tem essas colunas).
    2. Cria um temporário com EXCLUSIVIDADE (`tempfile.mkstemp`) no MESMO
       diretório de `audit_path` (mesmo filesystem, necessário para o link).
    3. `flush` + `os.fsync` antes de fechar.
    4. Publica com `os.link(tmp, audit_path)` — cria uma segunda entrada de
       diretório apontando para o mesmo arquivo; falha com
       `FileExistsError` se `audit_path` já existir — NUNCA sobrescreve
       (diferente de `os.rename`/`os.replace`). Fecha a corrida TOCTOU
       entre `_validate_new_window_audit_path` e esta publicação.
    5. O temporário é removido em QUALQUER caminho (sucesso ou falha).
    6. Relê `audit_path` do disco, revalida a ESTRUTURA COMPLETA
       (`validate_window_backup_payload`), só então calcula o SHA-256 e
       publica o companion `.sha256` pelo MESMO mecanismo atômico.

    Mesmo padrão já auditado em
    pipelines/ingestion/shopee_raw/backfill_ads_metadata.py —
    reimplementado aqui (não importado), para não acoplar o pacote
    gold_regional ao pacote shopee_raw (domínios de negócio distintos)."""
    before_records = [_row_to_backup_record(r) for r in before_rows]
    after_records = [_row_to_backup_record(r) for r in after_rows]

    payload = {
        "schema_version": WINDOW_BACKUP_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "marketplace_id": SHOPEE_MARKETPLACE_ID,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "grain_key": list(_WINDOW_KEY_COLUMNS),
        "business_columns": list(_WINDOW_BUSINESS_COLUMNS),
        "before_count": len(before_records),
        "after_count": len(after_records),
        "before_aggregates": _aggregate_backup_records(before_records),
        "after_aggregates": _aggregate_backup_records(after_records),
        "before_records": before_records,
        "planned_after_records": after_records,
    }
    data = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")

    fd, tmp_name = tempfile.mkstemp(dir=str(audit_path.parent), prefix=audit_path.name + ".", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp_path, audit_path)
        except FileExistsError:
            raise BackupIntegrityError(
                f"audit_path passou a existir entre a validação e a publicação (corrida detectada, nada sobrescrito): {audit_path.name}"
            )
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    reread_text = audit_path.read_text(encoding="utf-8")
    reread_payload = json.loads(reread_text)
    reread_problems = validate_window_backup_payload(reread_payload)
    if reread_problems:
        raise BackupIntegrityError("backup relido do disco falhou na revalidação estrutural")
    if reread_payload["before_count"] != len(before_records) or reread_payload["after_count"] != len(after_records):
        raise BackupIntegrityError("backup relido do disco tem contagens diferentes das esperadas")

    backup_sha256 = _sha256_file(audit_path)

    sha_path = Path(str(audit_path) + ".sha256")
    sha_fd, sha_tmp_name = tempfile.mkstemp(dir=str(sha_path.parent), prefix=sha_path.name + ".", suffix=".tmp")
    sha_tmp_path = Path(sha_tmp_name)
    try:
        with os.fdopen(sha_fd, "wb") as f:
            f.write((backup_sha256 + "\n").encode("ascii"))
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(sha_tmp_path, sha_path)
        except FileExistsError:
            raise BackupIntegrityError(
                f"companion .sha256 passou a existir entre a validação e a publicação: {sha_path.name}"
            )
    finally:
        if sha_tmp_path.exists():
            sha_tmp_path.unlink()

    return backup_sha256


# ---------------------------------------------------------------------------
# SQL do refresh/restore — reaproveita ao máximo o que já existe
# (SQL_CREATE_STAGING, SQL_SHOPEE_WINDOW_RECALC_ROWS, SQL_VALIDATE_*,
# SQL_INSERT_FINAL, SQL_SHOPEE_WINDOW_GOLD_AGGREGATES, _WINDOW_GOLD_SELECT_COLUMNS,
# _WINDOW_CHANGED_PREDICATE — todos do Gate S2/S2.1, mesmo módulo). Só o que
# é genuinamente NOVO neste gate (fingerprint fora do escopo, checagem
# explícita de NaN/negativo, DELETE, leitura de linhas para o backup) ganha
# constante própria.
# ---------------------------------------------------------------------------

SQL_REFRESH_STAGING_AGGREGATES = "SELECT COUNT(*), COALESCE(SUM(gmv), 0), COALESCE(SUM(orders), 0) FROM stg_marketplace_region_daily"

SQL_REFRESH_OUT_OF_SCOPE_IN_STAGING = """
    SELECT COUNT(*) FROM stg_marketplace_region_daily
    WHERE marketplace_id <> %(shopee_marketplace_id)s
       OR date < %(date_from)s OR date > %(date_to)s
"""

# Postgres numeric: 'NaN'::numeric >= 0 é TRUE (achado documentado do
# projeto) — por isso o `<> 'NaN'` é SEMPRE explícito aqui, nunca só
# `>= 0`. Defesa em profundidade: a fonte (silver) já tem CHECK (<>'NaN')
# nas colunas de origem, então isto não deveria disparar nunca — mas o
# primeiro caminho de escrita com DELETE desta tabela garante de qualquer
# forma, sem confiar apenas na camada anterior.
SQL_REFRESH_NAN_NEGATIVE_CHECK = """
    SELECT COUNT(*) FROM stg_marketplace_region_daily
    WHERE gmv = 'NaN' OR gmv < 0
       OR (seller_shipping_cost IS NOT NULL AND (seller_shipping_cost = 'NaN' OR seller_shipping_cost < 0))
       OR (buyer_shipping_fee IS NOT NULL AND (buyer_shipping_fee = 'NaN' OR buyer_shipping_fee < 0))
       OR (estimated_shipping_fee IS NOT NULL AND (estimated_shipping_fee = 'NaN' OR estimated_shipping_fee < 0))
       OR (reverse_shipping_fee IS NOT NULL AND (reverse_shipping_fee = 'NaN' OR reverse_shipping_fee < 0))
"""

# Fingerprint agregado de TUDO fora do escopo do refresh/restore (ML,
# TikTok, e Shopee fora da janela) — capturado ANTES e DEPOIS de qualquer
# DELETE/INSERT, sob o mesmo table lock. Cobre as duas garantias exigidas
# (ML/TikTok inalterados E Shopee fora da janela inalterada) numa única
# query, porque `NOT (marketplace_id=SHOPEE AND date BETWEEN...)` é
# exatamente a união dos dois casos.
SQL_REFRESH_OUT_OF_SCOPE_FINGERPRINT = """
    SELECT COUNT(*), COALESCE(SUM(gmv), 0), COALESCE(SUM(orders), 0), COALESCE(SUM(units_sold), 0),
           COALESCE(SUM(canceled_orders), 0), COALESCE(SUM(returned_orders), 0),
           COALESCE(SUM(seller_shipping_cost), 0), COALESCE(SUM(buyer_shipping_fee), 0),
           COALESCE(SUM(estimated_shipping_fee), 0), COALESCE(SUM(reverse_shipping_fee), 0),
           COALESCE(SUM(uf_known_orders), 0), COALESCE(SUM(uf_eligible_orders), 0),
           COALESCE(SUM(shipping_cost_covered_orders), 0), COALESCE(SUM(shipping_cost_eligible_orders), 0)
    FROM gold.marketplace_region_daily
    WHERE NOT (marketplace_id = %(shopee_marketplace_id)s AND date BETWEEN %(date_from)s AND %(date_to)s)
"""

SQL_SELECT_GOLD_WINDOW_ROWS = f"""
    SELECT {_STAGING_INSERT_COLUMNS}
    FROM gold.marketplace_region_daily
    WHERE marketplace_id = %(shopee_marketplace_id)s
      AND date BETWEEN %(date_from)s AND %(date_to)s
    ORDER BY date, loja_id, uf
"""

SQL_SELECT_STAGING_ROWS = f"""
    SELECT {_STAGING_INSERT_COLUMNS}
    FROM stg_marketplace_region_daily
    ORDER BY date, loja_id, uf
"""

# Comparação exata por chave — MESMA lógica de SQL_SHOPEE_WINDOW_KEY_DIFF
# (Gate S2.1), mas contra a TEMP TABLE já materializada (o staging JÁ É o
# recálculo, feito uma única vez sob o lock) em vez de recalcular a fonte
# via CTE a cada chamada. Reexecutada 2x pelo refresh: antes do DELETE
# (decide no_op/blocked/prosseguir) e depois do INSERT (reconciliação
# pós-insert obrigatória) — mesma query, dois momentos diferentes da mesma
# transação.
SQL_REFRESH_KEY_DIFF = f"""
    WITH gold_window AS (
        SELECT {_WINDOW_GOLD_SELECT_COLUMNS}
        FROM gold.marketplace_region_daily
        WHERE marketplace_id = %(shopee_marketplace_id)s
          AND date BETWEEN %(date_from)s AND %(date_to)s
    )
    SELECT
        COUNT(*) FILTER (WHERE s.date IS NULL) AS gold_only_key_count,
        COUNT(*) FILTER (WHERE g.date IS NULL) AS source_only_key_count,
        COUNT(*) FILTER (
            WHERE g.date IS NOT NULL AND s.date IS NOT NULL
              AND ({_WINDOW_CHANGED_PREDICATE})
        ) AS changed_key_count
    FROM gold_window g
    FULL OUTER JOIN stg_marketplace_region_daily s
      ON g.date = s.date
     AND g.marketplace_id = s.marketplace_id
     AND g.loja_id = s.loja_id
     AND g.uf = s.uf
"""

# DELETE — o ÚNICO permitido em todo o módulo, e só neste formato lógico:
# marketplace_id = Shopee AND date BETWEEN date_from AND date_to. Datas e
# marketplace_id SEMPRE bind parameters (entrada de usuário/backup — nunca
# interpolados como literal de string).
SQL_REFRESH_DELETE = """
    DELETE FROM gold.marketplace_region_daily
    WHERE marketplace_id = %(shopee_marketplace_id)s
      AND date BETWEEN %(date_from)s AND %(date_to)s
"""

# INSERT de restauração — 1 linha por vez (nunca um bulk execute_values):
# um bulk INSERT paginado tornaria cur.rowcount não confiável entre páginas
# (gotcha conhecido do psycopg2), e este caminho já é raro/deliberado, não
# um hot path — o custo de N INSERTs individuais é aceitável em troca de um
# rowcount==1 por linha sempre confiável. Colunas explícitas, nunca `id`.
SQL_RESTORE_INSERT_ROW = (
    f"INSERT INTO gold.marketplace_region_daily ({_STAGING_INSERT_COLUMNS}) "
    f"VALUES ({', '.join(['%s'] * len(_STAGING_INSERT_COLUMNS_LIST))})"
)


def execute_shopee_window_refresh(
    write_url: str,
    date_from: date,
    date_to: date,
    audit_path: Path,
    *,
    confirm_empty_window: bool = False,
    repo_root: Path = REPO_ROOT,
) -> ShopeeWindowRefreshResult:
    """Gate S3 — refresh transacional da Gold regional Shopee por janela.
    Substitui SOMENTE `marketplace_id=SHOPEE AND date BETWEEN date_from AND
    date_to`.

    Ordem: validar janela/audit_path (ANTES de conectar) -> 1 conexão de
    escrita, autocommit=False -> advisory lock (MESMA chave de
    execute_first_load/execute_incremental_load — nunca rodam
    concorrentemente com este refresh) -> lock_timeout/statement_timeout ->
    LOCK TABLE ... SHARE ROW EXCLUSIVE MODE -> fingerprint fora do escopo
    (antes) -> staging TEMP materializado UMA vez -> validações do staging
    (duplicidade, nulos, numerador<=denominador, NaN/negativo, escopo
    marketplace/janela) -> structurally_safe recalculado SOB O LOCK (nunca
    reaproveita diagnose) -> comparação por chave Gold-atual vs. staging
    (decide no_op) -> backup atômico ANTES do DELETE -> DELETE escopado
    (rowcount == contagem anterior) -> INSERT do staging (rowcount ==
    contagem do staging) -> reconciliação pós-insert (chave a chave e campo
    a campo) -> fingerprint fora do escopo (depois, deve bater com antes)
    -> COMMIT só se tudo passar. ROLLBACK completo em qualquer falha, sem
    retry automático. Lock/close no finally — se o COMMIT já ocorreu e só
    a liberação do lock/close falhar, o resultado `committed` é preservado
    (com aviso sanitizado, sem sugestão de retry)."""
    try:
        _validate_shopee_window(date_from, date_to)
    except InvalidWindowError as exc:
        return ShopeeWindowRefreshResult(outcome="blocked", problems=[str(exc)])

    audit_problem = _validate_new_window_audit_path(audit_path, repo_root)
    if audit_problem:
        return ShopeeWindowRefreshResult(outcome="blocked", problems=[audit_problem])

    params = {"date_from": date_from, "date_to": date_to, "shopee_marketplace_id": SHOPEE_MARKETPLACE_ID}
    result: Optional[ShopeeWindowRefreshResult] = None
    lock_acquired = False
    backup_sha256: Optional[str] = None

    # Gate S3.1: connect() protegido — se falhar, retorna failed com erro
    # sanitizado (nunca deixa a exceção nativa do psycopg2/libpq escapar
    # sem passar por sanitize_error_message).
    try:
        conn = psycopg2.connect(write_url, connect_timeout=15)
    except Exception as exc:  # noqa: BLE001
        return ShopeeWindowRefreshResult(outcome="failed", problems=[sanitize_error_message(exc)])

    try:
        # autocommit=False DENTRO do try: se a própria atribuição falhar
        # (raro, mas possível), o finally ainda fecha a conexão.
        conn.autocommit = False

        lock_acquired = try_acquire_advisory_lock(conn)
        if not lock_acquired:
            result = ShopeeWindowRefreshResult(
                outcome="blocked",
                problems=[
                    f"advisory lock {ADVISORY_LOCK_KEY} já está em uso — outra execução de carga da Gold "
                    "regional pode estar em andamento. Abortando sem tentar novamente."
                ],
            )
            return result

        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            # Janela de até 180 dias recalculada do zero pode custar mais que
            # o incremental diário (600s, só "> MAX(date)") — teto próprio,
            # documentado aqui por ser um valor novo, não copiado sem revisão.
            cur.execute("SET LOCAL statement_timeout = '900s'")
            cur.execute(f"LOCK TABLE {_GOLD_TABLE_QUALIFIED} IN SHARE ROW EXCLUSIVE MODE")

            cur.execute(SQL_REFRESH_OUT_OF_SCOPE_FINGERPRINT, params)
            out_of_scope_before = cur.fetchone()

            cur.execute(SQL_CREATE_STAGING)
            cur.execute(
                f"INSERT INTO stg_marketplace_region_daily ({_STAGING_INSERT_COLUMNS}) {SQL_SHOPEE_WINDOW_RECALC_ROWS}",
                params,
            )

            cur.execute(SQL_REFRESH_STAGING_AGGREGATES)
            staging_rows, staging_gmv, staging_orders = cur.fetchone()
            staging_rows = int(staging_rows)

            cur.execute(SQL_VALIDATE_DUPLICATES)
            (dup_count,) = cur.fetchone()
            cur.execute(SQL_VALIDATE_NULLS)
            (null_count,) = cur.fetchone()
            cur.execute(SQL_VALIDATE_NUMERATOR_DENOMINATOR)
            (bad_count,) = cur.fetchone()
            cur.execute(SQL_REFRESH_NAN_NEGATIVE_CHECK)
            (nan_negative_count,) = cur.fetchone()
            cur.execute(SQL_REFRESH_OUT_OF_SCOPE_IN_STAGING, params)
            (out_of_scope_in_staging,) = cur.fetchone()

            cur.execute(SQL_SHOPEE_WINDOW_GOLD_AGGREGATES, params)
            gold_rows, gold_gmv, gold_orders = cur.fetchone()
            gold_rows = int(gold_rows)

            zero_source_risk = (staging_rows == 0 and gold_rows > 0)

            structural_problems: list[str] = []
            if dup_count:
                structural_problems.append(f"{dup_count} combinação(ões) de chave duplicada(s) no staging")
            if null_count:
                structural_problems.append(f"{null_count} linha(s) com coluna obrigatória nula no staging")
            if bad_count:
                structural_problems.append(f"{bad_count} linha(s) com numerador > denominador no staging")
            if nan_negative_count:
                structural_problems.append(f"{nan_negative_count} linha(s) com NaN/valor negativo incompatível com as constraints no staging")
            if out_of_scope_in_staging:
                structural_problems.append(f"{out_of_scope_in_staging} linha(s) no staging fora do escopo marketplace/janela")

            if zero_source_risk and not confirm_empty_window:
                result = ShopeeWindowRefreshResult(
                    outcome="blocked",
                    problems=[
                        "fonte recalculada tem ZERO linhas na janela e a Gold atual tem linhas -- "
                        "requer --confirm-empty-window"
                    ] + structural_problems,
                )
                _rollback_best_effort(conn, result)
                return result

            if structural_problems:
                result = ShopeeWindowRefreshResult(outcome="blocked", problems=structural_problems)
                _rollback_best_effort(conn, result)
                return result

            cur.execute(SQL_REFRESH_KEY_DIFF, params)
            gold_only, source_only, changed = cur.fetchone()
            would_change_data = bool(gold_only or source_only or changed)

            if not would_change_data:
                result = ShopeeWindowRefreshResult(outcome="no_op", rows_deleted=0, rows_inserted=0)
                _rollback_best_effort(conn, result)
                return result

            cur.execute(SQL_SELECT_GOLD_WINDOW_ROWS, params)
            before_rows = cur.fetchall()
            cur.execute(SQL_SELECT_STAGING_ROWS)
            after_rows = cur.fetchall()

            try:
                backup_sha256 = _write_window_backup_atomic(audit_path, date_from, date_to, before_rows, after_rows)
            except (OSError, BackupIntegrityError, json.JSONDecodeError) as exc:
                # Nenhum DELETE aconteceu ainda (o backup é publicado ANTES).
                # Se o JSON chegou a ser publicado mas o companion .sha256
                # falhou, um artefato parcial pode existir em disco — nunca
                # removido automaticamente, mas o resultado avisa disso de
                # forma sanitizada (sem caminho absoluto).
                result = ShopeeWindowRefreshResult(
                    outcome="failed",
                    problems=[
                        f"falha ao gravar/validar backup atômico ({type(exc).__name__}) — nenhum DELETE foi "
                        "executado; um artefato de backup parcial (JSON sem o companion .sha256, ou vice-versa) "
                        "pode existir em disco no destino informado e deve ser conferido manualmente antes de "
                        "tentar novamente"
                    ],
                )
                _rollback_best_effort(conn, result)
                return result

            cur.execute(SQL_REFRESH_DELETE, params)
            deleted = cur.rowcount
            if deleted != gold_rows:
                result = ShopeeWindowRefreshResult(
                    outcome="failed", backup_path=str(audit_path), backup_sha256=backup_sha256,
                    problems=[f"DELETE removeu {deleted} linha(s), esperado {gold_rows}"],
                )
                _rollback_best_effort(conn, result)
                return result

            cur.execute(SQL_INSERT_FINAL)
            inserted = cur.rowcount
            if inserted != staging_rows:
                result = ShopeeWindowRefreshResult(
                    outcome="failed", backup_path=str(audit_path), backup_sha256=backup_sha256,
                    problems=[f"INSERT inseriu {inserted} linha(s), esperado {staging_rows}"],
                )
                _rollback_best_effort(conn, result)
                return result

            cur.execute(SQL_REFRESH_KEY_DIFF, params)
            post_gold_only, post_source_only, post_changed = cur.fetchone()
            if post_gold_only or post_source_only or post_changed:
                result = ShopeeWindowRefreshResult(
                    outcome="failed", backup_path=str(audit_path), backup_sha256=backup_sha256,
                    problems=["reconciliação pós-insert divergente (Gold != staging chave a chave/campo a campo)"],
                )
                _rollback_best_effort(conn, result)
                return result

            cur.execute(SQL_REFRESH_OUT_OF_SCOPE_FINGERPRINT, params)
            out_of_scope_after = cur.fetchone()
            if out_of_scope_after != out_of_scope_before:
                result = ShopeeWindowRefreshResult(
                    outcome="failed", backup_path=str(audit_path), backup_sha256=backup_sha256,
                    problems=["linha(s) fora do escopo (ML/TikTok ou Shopee fora da janela) foram alteradas -- rollback"],
                )
                _rollback_best_effort(conn, result)
                return result

        # commit() fica FORA de qualquer atribuição prévia de `result` —
        # só é seguro considerar "committed" depois que commit() retornar
        # sem levantar. Se commit() falhar, cai no except abaixo (nunca
        # "committed").
        conn.commit()
        result = ShopeeWindowRefreshResult(
            outcome="committed",
            rows_deleted=deleted,
            rows_inserted=inserted,
            backup_path=str(audit_path),
            backup_sha256=backup_sha256,
            gold_gmv_before=gold_gmv,
            gold_gmv_after=staging_gmv,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        # Preserva backup_path/backup_sha256 se o backup já tinha sido
        # publicado com sucesso antes desta falha (ex.: DELETE/INSERT/commit
        # levantaram uma exceção genérica, não só um rowcount divergente
        # tratado explicitamente acima) — nunca perde essa informação de
        # auditoria só porque a falha não foi uma das antecipadas.
        result = ShopeeWindowRefreshResult(
            outcome="failed",
            problems=[sanitize_error_message(exc)],
            backup_path=str(audit_path) if backup_sha256 else None,
            backup_sha256=backup_sha256,
        )
        _rollback_best_effort(conn, result)
        return result
    finally:
        if lock_acquired:
            try:
                release_advisory_lock(conn)
            except Exception as exc:  # noqa: BLE001
                if result is not None:
                    result.warnings.append(
                        f"falha ao liberar advisory lock após a operação ({sanitize_error_message(exc)}) — "
                        "resultado principal preservado, sem retry sugerido"
                    )
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001
            if result is not None:
                result.warnings.append(
                    f"falha ao fechar a conexão ({sanitize_error_message(exc)}) — resultado principal preservado, sem retry sugerido"
                )


def execute_shopee_window_restore(
    write_url: str,
    audit_path: Path,
    expected_backup_sha256: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> ShopeeWindowRestoreResult:
    """Gate S3 — restore compare-and-swap a partir de um backup de
    `execute_shopee_window_refresh`. O arquivo é ENTRADA NÃO CONFIÁVEL:

    1. `_validate_and_load_window_backup` (AUTORITATIVA — sempre revalida
       do zero, nunca reaproveita uma checagem anterior da CLI, porque o
       arquivo pode ter mudado entre as duas chamadas): caminho, formato do
       SHA, tamanho do arquivo (ANTES de ler o conteúdo), SHA-256
       recalculado e comparado, JSON parseado, estrutura completa
       (`validate_window_backup_payload` — schema_version, created_at_utc,
       marketplace_id só Shopee, grain_key/business_columns oficiais,
       janela válida e <=180 dias, limite de registros, registros campo a
       campo, chave única, contagens e agregados reconciliados).
    2. Sob advisory lock + table lock, compara o estado ATUAL da Gold na
       janela do backup contra `planned_after_records` — se não for
       EXATAMENTE igual (chave a chave e campo a campo), aborta ANTES de
       qualquer DELETE (compare-and-swap: impede restaurar sobre uma carga
       posterior que já mudou a janela).
    3. DELETE restrito à mesma janela/Shopee -> insere `before_records`
       (1 INSERT por linha, nunca bulk — rowcount==1 sempre confiável) ->
       reconcilia EXATAMENTE contra `before_records` -> confirma fingerprint
       fora do escopo (ML/TikTok/Shopee fora da janela) inalterado -> COMMIT.
    4. ROLLBACK completo em qualquer falha, sem retry automático."""
    payload, problems = _validate_and_load_window_backup(audit_path, expected_backup_sha256, repo_root)
    if problems:
        return ShopeeWindowRestoreResult(outcome="blocked", problems=problems)

    date_from = date.fromisoformat(payload["date_from"])
    date_to = date.fromisoformat(payload["date_to"])
    before_records = payload["before_records"]
    planned_after_records = payload["planned_after_records"]

    params = {"date_from": date_from, "date_to": date_to, "shopee_marketplace_id": SHOPEE_MARKETPLACE_ID}
    result: Optional[ShopeeWindowRestoreResult] = None
    lock_acquired = False

    # Gate S3.1: connect() protegido — mesmo padrão do refresh.
    try:
        conn = psycopg2.connect(write_url, connect_timeout=15)
    except Exception as exc:  # noqa: BLE001
        return ShopeeWindowRestoreResult(outcome="failed", problems=[sanitize_error_message(exc)])

    try:
        conn.autocommit = False  # dentro do try -- se falhar, finally ainda fecha a conexão

        lock_acquired = try_acquire_advisory_lock(conn)
        if not lock_acquired:
            result = ShopeeWindowRestoreResult(
                outcome="blocked",
                problems=[f"advisory lock {ADVISORY_LOCK_KEY} já está em uso — abortando sem tentar novamente."],
            )
            return result

        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '900s'")
            cur.execute(f"LOCK TABLE {_GOLD_TABLE_QUALIFIED} IN SHARE ROW EXCLUSIVE MODE")

            cur.execute(SQL_REFRESH_OUT_OF_SCOPE_FINGERPRINT, params)
            out_of_scope_before = cur.fetchone()

            cur.execute(SQL_SELECT_GOLD_WINDOW_ROWS, params)
            current_rows = cur.fetchall()
            current_records = [_row_to_backup_record(r) for r in current_rows]

            current_by_key = _records_by_key(current_records)
            planned_by_key = _records_by_key(planned_after_records)
            cas_gold_only = sorted(set(current_by_key) - set(planned_by_key))
            cas_source_only = sorted(set(planned_by_key) - set(current_by_key))
            cas_changed = sorted(
                k for k in (set(current_by_key) & set(planned_by_key)) if current_by_key[k] != planned_by_key[k]
            )

            if cas_gold_only or cas_source_only or cas_changed:
                result = ShopeeWindowRestoreResult(
                    outcome="blocked",
                    problems=[
                        "estado atual da Gold diverge de planned_after_records -- restauração recusada "
                        "(compare-and-swap: uma carga posterior provavelmente alterou a janela)",
                        f"gold_only={len(cas_gold_only)} source_only={len(cas_source_only)} changed={len(cas_changed)}",
                    ],
                )
                _rollback_best_effort(conn, result)
                return result

            expected_current_count = len(planned_after_records)

            cur.execute(SQL_REFRESH_DELETE, params)
            deleted = cur.rowcount
            if deleted != expected_current_count:
                result = ShopeeWindowRestoreResult(
                    outcome="failed",
                    problems=[f"DELETE removeu {deleted} linha(s), esperado {expected_current_count}"],
                )
                _rollback_best_effort(conn, result)
                return result

            inserted = 0
            for record in before_records:
                values = _backup_record_to_row_values(record)
                cur.execute(SQL_RESTORE_INSERT_ROW, values)
                if cur.rowcount != 1:
                    result = ShopeeWindowRestoreResult(
                        outcome="failed",
                        problems=[f"INSERT de restauração afetou {cur.rowcount} linha(s) (esperado 1)"],
                    )
                    _rollback_best_effort(conn, result)
                    return result
                inserted += 1

            cur.execute(SQL_SELECT_GOLD_WINDOW_ROWS, params)
            final_rows = cur.fetchall()
            final_records = [_row_to_backup_record(r) for r in final_rows]
            final_by_key = _records_by_key(final_records)
            before_by_key = _records_by_key(before_records)

            recon_gold_only = sorted(set(final_by_key) - set(before_by_key))
            recon_source_only = sorted(set(before_by_key) - set(final_by_key))
            recon_changed = sorted(
                k for k in (set(final_by_key) & set(before_by_key)) if final_by_key[k] != before_by_key[k]
            )
            if recon_gold_only or recon_source_only or recon_changed:
                result = ShopeeWindowRestoreResult(
                    outcome="failed",
                    problems=["reconciliação pós-restauração divergente (Gold != before_records chave a chave/campo a campo)"],
                )
                _rollback_best_effort(conn, result)
                return result

            cur.execute(SQL_REFRESH_OUT_OF_SCOPE_FINGERPRINT, params)
            out_of_scope_after = cur.fetchone()
            if out_of_scope_after != out_of_scope_before:
                result = ShopeeWindowRestoreResult(
                    outcome="failed",
                    problems=["linha(s) fora do escopo (ML/TikTok ou Shopee fora da janela) foram alteradas durante a restauração -- rollback"],
                )
                _rollback_best_effort(conn, result)
                return result

        # commit() fica fora de qualquer atribuição prévia de `result` — só
        # é seguro considerar "committed" depois que commit() retornar sem
        # levantar.
        conn.commit()
        result = ShopeeWindowRestoreResult(outcome="committed", rows_deleted=deleted, rows_inserted=inserted)
        return result
    except Exception as exc:  # noqa: BLE001
        result = ShopeeWindowRestoreResult(outcome="failed", problems=[sanitize_error_message(exc)])
        _rollback_best_effort(conn, result)
        return result
    finally:
        if lock_acquired:
            try:
                release_advisory_lock(conn)
            except Exception as exc:  # noqa: BLE001
                if result is not None:
                    result.warnings.append(
                        f"falha ao liberar advisory lock após a operação ({sanitize_error_message(exc)}) — "
                        "resultado principal preservado, sem retry sugerido"
                    )
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001
            if result is not None:
                result.warnings.append(
                    f"falha ao fechar a conexão ({sanitize_error_message(exc)}) — resultado principal preservado, sem retry sugerido"
                )


# ---------------------------------------------------------------------------
# CLI — `python -m pipelines.ingestion.gold_regional.loader --diagnose` ou
# `--incremental`. `--diagnose` nunca lê o secret de escrita nem abre
# conexão de escrita. `--incremental` só lê `.env.gold-write.local` (nunca
# `os.environ`, nunca `.env` principal) e só chega a conectar depois dos
# guardrails estáticos (arquivo ignorado/não rastreado, exatamente as 2
# chaves esperadas, URL de escrita diferente da de leitura) e do preflight
# de escrita passarem. `--diagnose-shopee-window` (Gate S2) segue a mesma
# disciplina somente-leitura de `--diagnose`, nunca a de `--incremental`.
# `--refresh-shopee-window`/`--restore-shopee-window` (Gate S3) usam um
# secret e preflight PRÓPRIOS (`.env.gold-window-write.local`, via
# `window_write_conn.py`) — nunca `.env.gold-write.local`.
# ---------------------------------------------------------------------------

def _load_secret_or_none(secret_path: Path, repo_root: Path) -> tuple[Optional[dict], Optional[str]]:
    try:
        return write_conn.load_write_secret(secret_path, repo_root), None
    except write_conn.SecretLoadError as exc:
        return None, str(exc)


def _resolve_write_url(secret_path: Path, repo_root: Path) -> tuple[Optional[str], Optional[str]]:
    secret, err = _load_secret_or_none(secret_path, repo_root)
    if err:
        return None, err
    try:
        write_url = write_conn.validate_write_guardrails(secret, settings.datamart_url)
    except write_conn.SecretLoadError as exc:
        return None, str(exc)
    return write_url, None


def _print_write_preflight(report: write_conn.PreflightReport, label: str) -> None:
    print(f"\n=== Preflight de escrita ({label}) — nunca exibe host/IP/usuário/senha ===")
    for key, value in report.safe_summary.items():
        print(f"  {key}: {value}")
    for warning in report.warnings:
        print(f"  AVISO (não bloqueante): {warning}")
    if not report.ok:
        print("  BLOQUEADO:")
        for reason in report.blocking_reasons:
            print(f"    - {reason}")


def run_diagnose_cli() -> int:
    read_url = settings.datamart_url
    if not read_url:
        print("DATAMART_DATABASE_URL não configurado — diagnose abortado.", file=sys.stderr)
        return 2
    try:
        report = diagnose_incremental_load(read_url)
    except Exception as exc:  # noqa: BLE001
        print(f"diagnose falhou: {sanitize_error_message(exc)}", file=sys.stderr)
        return 3

    print("=== Diagnose Gold Regional (somente leitura) ===")
    for m in report.marketplaces:
        print(
            f"  {m.marketplace}: max_date_gold={m.max_date_gold} max_date_source={m.max_date_source} "
            f"estimated_new_rows={m.estimated_new_rows} will_update={m.will_update}"
        )
    print(f"\nPrecisa atualizar: {report.any_update_needed}")
    return 0


def run_diagnose_shopee_window_cli(date_from: date, date_to: date) -> int:
    """Somente leitura (Gate S2/S2.1) — nunca chama `_resolve_write_url`/
    `write_conn.load_write_secret`, nunca lê `.env.gold-write.local`.

    Exit codes:
      0  diagnose OK (independe de would_change_data — janela reconciliada
         ou não, desde que estruturalmente sã);
      2  configuração/janela inválida (sem DATAMART_DATABASE_URL, janela
         inválida);
      3  falha ao consultar a fonte;
      4  diagnose rodou, mas a fonte é ESTRUTURALMENTE INSEGURA para servir
         de base a um refresh (structurally_safe_for_refresh=False) — Gate
         S3 nunca deve prosseguir com esta janela sem investigação."""
    read_url = settings.datamart_url
    if not read_url:
        print("DATAMART_DATABASE_URL não configurado — diagnose-shopee-window abortado.", file=sys.stderr)
        return 2

    try:
        report = diagnose_shopee_window(read_url, date_from, date_to)
    except InvalidWindowError as exc:
        print(f"--diagnose-shopee-window rejeitado: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"diagnose-shopee-window falhou: {sanitize_error_message(exc)}", file=sys.stderr)
        return 3

    print(f"=== Diagnose Shopee — janela [{report.date_from} .. {report.date_to}] (somente leitura, snapshot consistente, sem refresh) ===")
    print(f"  Gold atual:        rows={report.gold_rows} gmv={report.gold_gmv} orders={report.gold_orders}")
    print(f"  Recalculado fonte: rows={report.recalculated_rows} gmv={report.recalculated_gmv} orders={report.recalculated_orders}")
    print(f"  Impacto de um futuro refresh (Gate S3, não implementado): rows_to_delete={report.rows_to_delete} rows_to_insert={report.rows_to_insert}")
    print(f"  Delta agregado: gmv={report.gmv_delta} orders={report.orders_delta}")
    print(f"  Sobrepõe dado já existente na Gold: {report.overlaps_existing_gold_data}")
    print("  --- Comparação exata por chave (date, marketplace_id, loja_id, uf) ---")
    print(f"  gold_only_key_count={report.gold_only_key_count} source_only_key_count={report.source_only_key_count} changed_key_count={report.changed_key_count}")
    print(f"  would_change_data={report.would_change_data}")
    print(f"  structurally_safe_for_refresh={report.structurally_safe_for_refresh}")
    if not report.would_change_data:
        print("  (janela já reconciliada — Gold e fonte batem chave a chave e campo a campo; não é erro.)")
    if report.zero_source_risk:
        print("  ALERTA: fonte recalculada tem ZERO linhas na janela, mas a Gold atual tem linhas — investigar antes de cogitar qualquer refresh.")
    if report.duplicate_key_count:
        print(f"  ALERTA: {report.duplicate_key_count} combinação(ões) de chave duplicada(s) no recálculo.")
    if report.null_required_count:
        print(f"  ALERTA: {report.null_required_count} linha(s) com coluna obrigatória nula no recálculo.")
    if report.numerator_over_denominator_count:
        print(f"  ALERTA: {report.numerator_over_denominator_count} linha(s) com numerador > denominador de cobertura no recálculo.")

    if not report.structurally_safe_for_refresh:
        print("  RESULTADO: fonte ESTRUTURALMENTE INSEGURA para refresh — Gate S3 não deve prosseguir com esta janela (exit 4).", file=sys.stderr)
        return 4
    return 0


def run_incremental_cli(secret_path: Path = DEFAULT_WRITE_SECRET_PATH, repo_root: Path = REPO_ROOT) -> int:
    write_url, err = _resolve_write_url(secret_path, repo_root)
    if err:
        print(f"--incremental bloqueado: {err}", file=sys.stderr)
        return 2

    report = write_conn.run_preflight(write_url, settings.datamart_url, expect_table_exists=True)
    _print_write_preflight(report, "antes da carga incremental — gold.marketplace_region_daily deve existir")
    if not report.ok:
        print("CARGA INCREMENTAL NÃO executada — preflight bloqueado.", file=sys.stderr)
        return 3

    try:
        result = execute_incremental_load(write_url)
    except Exception as exc:  # noqa: BLE001
        print(f"carga incremental falhou: {sanitize_error_message(exc)}", file=sys.stderr)
        return 4

    if result.no_op:
        print("NO_OP: nenhum marketplace tem data nova na fonte além do que já está em gold.marketplace_region_daily.")
        return 0

    print(f"Carga incremental OK: {result.rows_inserted} linha(s) inserida(s). Marketplaces atualizados: {result.marketplaces_updated}.")
    return 0


def _print_window_write_preflight(report: window_write_conn.WindowPreflightReport, label: str) -> None:
    print(f"\n=== Preflight de escrita ({label}) — nunca exibe host/IP/usuário/senha ===")
    for key, value in report.safe_summary.items():
        print(f"  {key}: {value}")
    for warning in report.warnings:
        print(f"  AVISO (não bloqueante): {warning}")
    if not report.ok:
        print("  BLOQUEADO:")
        for reason in report.blocking_reasons:
            print(f"    - {reason}")


def run_refresh_shopee_window_cli(
    date_from: date,
    date_to: date,
    audit_path: Path,
    confirm_empty_window: bool = False,
    secret_path: Path = DEFAULT_WINDOW_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
) -> int:
    """Gate S3.1 — CLI de `--refresh-shopee-window`. Secret/preflight
    DEDICADOS (`window_write_conn.py`/`.env.gold-window-write.local`) —
    nunca `.env.gold-write.local`.

    Ordem: `DATAMART_DATABASE_URL` configurado -> janela válida
    (`_validate_shopee_window`) -> `audit_path` válido
    (`_validate_new_window_audit_path`) -> SÓ ENTÃO secret -> preflight ->
    `execute_shopee_window_refresh` (que revalida janela/audit_path do zero
    — a checagem antecipada aqui é só fail-fast, nunca autoritativa).

    Exit codes: 0 committed/no_op; 2 config/janela/audit_path/secret/
    preflight inválido (nunca chegou a abrir a transação de escrita real);
    3 blocked (bloqueio estrutural encontrado sob o lock); 4 failed
    (rollback completo executado).

    Nunca imprime caminho absoluto: erros de audit_path usam categoria/
    basename; a confirmação de sucesso mostra só `audit_path.name` + SHA."""
    if not settings.datamart_url:
        print("DATAMART_DATABASE_URL não configurado — refresh-shopee-window abortado.", file=sys.stderr)
        return 2

    try:
        _validate_shopee_window(date_from, date_to)
    except InvalidWindowError as exc:
        print(f"--refresh-shopee-window rejeitado: {exc}", file=sys.stderr)
        return 2

    audit_problem = _validate_new_window_audit_path(audit_path, repo_root)
    if audit_problem:
        print(f"--refresh-shopee-window rejeitado: {audit_problem}", file=sys.stderr)
        return 2

    try:
        secret = window_write_conn.load_window_write_secret(secret_path, repo_root)
    except window_write_conn.WindowSecretLoadError as exc:
        print(f"--refresh-shopee-window bloqueado: {exc}", file=sys.stderr)
        return 2

    try:
        write_url = window_write_conn.validate_window_write_guardrails(secret, settings.datamart_url)
    except window_write_conn.WindowSecretLoadError as exc:
        print(f"--refresh-shopee-window bloqueado: {exc}", file=sys.stderr)
        return 2

    # Gate S3.2 (Finding 1): run_window_preflight é projetado para NUNCA
    # levantar (sempre retorna um report), mas esta barreira cobre uma
    # regressão futura — a CLI nunca pode virar traceback/mensagem nativa.
    try:
        report = window_write_conn.run_window_preflight(write_url, settings.datamart_url)
    except Exception as exc:  # noqa: BLE001
        print(f"--refresh-shopee-window bloqueado: falha inesperada no preflight: {sanitize_error_message(exc)}", file=sys.stderr)
        return 2
    _print_window_write_preflight(report, "refresh por janela Shopee")
    if not report.ok:
        print("REFRESH NÃO executado — preflight bloqueado.", file=sys.stderr)
        return 2

    try:
        result = execute_shopee_window_refresh(
            write_url, date_from, date_to, audit_path,
            confirm_empty_window=confirm_empty_window, repo_root=repo_root,
        )
    except Exception as exc:  # noqa: BLE001
        # Barreira final: nenhuma exceção não prevista pode escapar como
        # traceback com mensagem nativa do driver — execute_shopee_window_refresh
        # já captura e sanitiza tudo internamente, mas esta barreira cobre
        # qualquer bug futuro que reintroduza um caminho sem try/except.
        print(f"--refresh-shopee-window falhou de forma inesperada: {sanitize_error_message(exc)}", file=sys.stderr)
        return 4

    for w in result.warnings:
        print(f"AVISO: {w}", file=sys.stderr)

    if result.outcome == "committed":
        print(f"REFRESH COMMITTED: {result.rows_deleted} linha(s) deletada(s), {result.rows_inserted} linha(s) inserida(s).")
        backup_name = Path(result.backup_path).name if result.backup_path else None
        print(f"Backup: {backup_name} (sha256={result.backup_sha256})")
        return 0
    if result.outcome == "no_op":
        print("NO_OP: janela já reconciliada — Gold e fonte batem chave a chave e campo a campo. Nenhuma escrita realizada.")
        return 0
    if result.outcome == "blocked":
        print("BLOQUEADO (nenhuma escrita realizada):", file=sys.stderr)
        for p in result.problems:
            print(f"  - {p}", file=sys.stderr)
        return 3
    print("FALHOU (rollback completo executado):", file=sys.stderr)
    for p in result.problems:
        print(f"  - {p}", file=sys.stderr)
    if result.backup_path:
        print(f"  (backup preservado para auditoria: {Path(result.backup_path).name}, sha256={result.backup_sha256})", file=sys.stderr)
    return 4


def run_restore_shopee_window_cli(
    audit_path: Path,
    expected_backup_sha256: str,
    secret_path: Path = DEFAULT_WINDOW_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
) -> int:
    """Gate S3.1 — CLI de `--restore-shopee-window`. Mesmo secret/preflight
    dedicados do refresh.

    Ordem: `DATAMART_DATABASE_URL` configurado -> `audit_path`/SHA/tamanho/
    JSON/estrutura validados por completo via `_validate_and_load_window_backup`
    (fail-fast) -> SÓ ENTÃO secret -> preflight ->
    `execute_shopee_window_restore` (que REVALIDA tudo de novo, do zero —
    o arquivo pode ter mudado entre esta checagem e a execução; a checagem
    daqui nunca é autoritativa).

    Exit codes: 0 committed; 2 hash/JSON/estrutura/secret/preflight
    inválido; 3 blocked (compare-and-swap recusou — estado atual diverge de
    planned_after_records); 4 failed.

    Nunca imprime caminho absoluto nas mensagens de erro/sucesso."""
    if not settings.datamart_url:
        print("DATAMART_DATABASE_URL não configurado — restore-shopee-window abortado.", file=sys.stderr)
        return 2

    _, problems = _validate_and_load_window_backup(audit_path, expected_backup_sha256, repo_root)
    if problems:
        print("--restore-shopee-window rejeitado (validado antes de ler secret/preflight):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    try:
        secret = window_write_conn.load_window_write_secret(secret_path, repo_root)
    except window_write_conn.WindowSecretLoadError as exc:
        print(f"--restore-shopee-window bloqueado: {exc}", file=sys.stderr)
        return 2

    try:
        write_url = window_write_conn.validate_window_write_guardrails(secret, settings.datamart_url)
    except window_write_conn.WindowSecretLoadError as exc:
        print(f"--restore-shopee-window bloqueado: {exc}", file=sys.stderr)
        return 2

    # Gate S3.2 (Finding 1): mesma barreira defensiva do refresh — o
    # preflight nunca deveria levantar, mas se levantar, nunca vira traceback.
    try:
        report = window_write_conn.run_window_preflight(write_url, settings.datamart_url)
    except Exception as exc:  # noqa: BLE001
        print(f"--restore-shopee-window bloqueado: falha inesperada no preflight: {sanitize_error_message(exc)}", file=sys.stderr)
        return 2
    _print_window_write_preflight(report, "restore por janela Shopee")
    if not report.ok:
        print("RESTORE NÃO executado — preflight bloqueado.", file=sys.stderr)
        return 2

    try:
        result = execute_shopee_window_restore(write_url, audit_path, expected_backup_sha256, repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001
        print(f"--restore-shopee-window falhou de forma inesperada: {sanitize_error_message(exc)}", file=sys.stderr)
        return 4

    for w in result.warnings:
        print(f"AVISO: {w}", file=sys.stderr)

    if result.outcome == "committed":
        print(f"RESTORE COMMITTED: {result.rows_deleted} linha(s) deletada(s), {result.rows_inserted} linha(s) restaurada(s).")
        return 0
    if result.outcome == "blocked":
        print("BLOQUEADO (nenhuma escrita realizada):", file=sys.stderr)
        for p in result.problems:
            print(f"  - {p}", file=sys.stderr)
        return 3
    print("FALHOU (rollback completo executado):", file=sys.stderr)
    for p in result.problems:
        print(f"  - {p}", file=sys.stderr)
    return 4


def main(argv: Optional[list[str]] = None) -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(
        description="Gold regional (gold.marketplace_region_daily) — diagnose read-only e refresh incremental"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diagnose", action="store_true", help="Somente leitura: MAX(date) por marketplace (gold vs. fonte) e estimativa de linhas novas.")
    mode.add_argument("--incremental", action="store_true", help="Escreve em gold.marketplace_region_daily — só linhas novas. Requer .env.gold-write.local.")
    mode.add_argument(
        "--diagnose-shopee-window", action="store_true",
        help="Somente leitura (Gate S2): recalcula a janela Shopee [--date-from, --date-to] a partir da "
             "fonte (mesmo dedup arquivo-vencedor da carga) e compara com gold.marketplace_region_daily. "
             "Nunca escreve.",
    )
    mode.add_argument(
        "--refresh-shopee-window", action="store_true",
        help="Gate S3: substitui gold.marketplace_region_daily para Shopee na janela [--date-from, --date-to] "
             "(DELETE + INSERT restritos ao escopo, backup atômico obrigatório via --audit-path). "
             "Requer .env.gold-window-write.local.",
    )
    mode.add_argument(
        "--restore-shopee-window", action="store_true",
        help="Gate S3: restaura gold.marketplace_region_daily a partir de um backup de --refresh-shopee-window "
             "(compare-and-swap — --audit-path + --expected-backup-sha256). Requer .env.gold-window-write.local.",
    )
    parser.add_argument("--date-from", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD", help="Início da janela (inclusive) — obrigatório com --diagnose-shopee-window/--refresh-shopee-window.")
    parser.add_argument("--date-to", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD", help="Fim da janela (inclusive) — obrigatório com --diagnose-shopee-window/--refresh-shopee-window.")
    parser.add_argument(
        "--audit-path", type=Path, default=None,
        help="Caminho absoluto do .json de backup — destino (--refresh-shopee-window) ou origem "
             "(--restore-shopee-window). Obrigatório com os dois.",
    )
    parser.add_argument(
        "--confirm-empty-window", action="store_true",
        help="Só com --refresh-shopee-window: libera o caso staging vazio + Gold com linhas na janela.",
    )
    parser.add_argument(
        "--expected-backup-sha256", default=None, metavar="<64-hex>",
        help="Só com --restore-shopee-window: SHA-256 esperado do arquivo de backup — obrigatório.",
    )
    args = parser.parse_args(argv)

    if args.diagnose_shopee_window and (args.date_from is None or args.date_to is None):
        parser.error("--diagnose-shopee-window requer --date-from e --date-to")

    if args.refresh_shopee_window and (args.date_from is None or args.date_to is None or args.audit_path is None):
        parser.error("--refresh-shopee-window requer --date-from, --date-to e --audit-path")

    if args.restore_shopee_window and (args.audit_path is None or args.expected_backup_sha256 is None):
        parser.error("--restore-shopee-window requer --audit-path e --expected-backup-sha256")

    if (args.date_from is not None or args.date_to is not None) and not (
        args.diagnose_shopee_window or args.refresh_shopee_window
    ):
        parser.error("--date-from/--date-to só são válidos junto com --diagnose-shopee-window ou --refresh-shopee-window")

    if args.audit_path is not None and not (args.refresh_shopee_window or args.restore_shopee_window):
        parser.error("--audit-path só é válido junto com --refresh-shopee-window ou --restore-shopee-window")

    if args.confirm_empty_window and not args.refresh_shopee_window:
        parser.error("--confirm-empty-window só é válido junto com --refresh-shopee-window")

    if args.expected_backup_sha256 is not None and not args.restore_shopee_window:
        parser.error("--expected-backup-sha256 só é válido junto com --restore-shopee-window")

    # Barreira final: nenhuma exceção não prevista pode produzir traceback
    # ou mensagem nativa contendo infraestrutura na saída da CLI. Cada
    # run_*_cli já sanitiza tudo internamente — esta é a última rede de
    # segurança contra um bug futuro que reintroduza um caminho sem
    # try/except, nunca uma substituta para o tratamento interno.
    try:
        if args.diagnose:
            return run_diagnose_cli()
        if args.diagnose_shopee_window:
            return run_diagnose_shopee_window_cli(args.date_from, args.date_to)
        if args.refresh_shopee_window:
            return run_refresh_shopee_window_cli(args.date_from, args.date_to, args.audit_path, args.confirm_empty_window)
        if args.restore_shopee_window:
            return run_restore_shopee_window_cli(args.audit_path, args.expected_backup_sha256)
        return run_incremental_cli()
    except Exception as exc:  # noqa: BLE001
        print(f"falha inesperada e não tratada: {sanitize_error_message(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
