"""
Loader operacional da primeira carga de `gold.marketplace_region_daily` —
Gate 6A.3.

A carga inteira roda em UMA transação: staging temporário (`TEMP TABLE ...
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

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import psycopg2

from pipelines.ingestion.gold_regional.write_conn import (
    ADVISORY_LOCK_KEY,
    WritePreflightBlocked,
    release_advisory_lock,
    sanitize_error_message,
    try_acquire_advisory_lock,
)

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


@dataclass
class LoadResult:
    rows_inserted: int = 0
    shopee_gmv_staging: Optional[Decimal] = None
    shopee_gmv_source: Optional[Decimal] = None
    ml_gmv_staging: Optional[Decimal] = None
    ml_gmv_source: Optional[Decimal] = None
    tiktok_rows: int = 0


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
