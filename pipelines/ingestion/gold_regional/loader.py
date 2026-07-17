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
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

import psycopg2

from pipelines.common.config import settings
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


# ---------------------------------------------------------------------------
# CLI — `python -m pipelines.ingestion.gold_regional.loader --diagnose` ou
# `--incremental`. `--diagnose` nunca lê o secret de escrita nem abre
# conexão de escrita. `--incremental` só lê `.env.gold-write.local` (nunca
# `os.environ`, nunca `.env` principal) e só chega a conectar depois dos
# guardrails estáticos (arquivo ignorado/não rastreado, exatamente as 2
# chaves esperadas, URL de escrita diferente da de leitura) e do preflight
# de escrita passarem. `--diagnose-shopee-window` (Gate S2) segue a mesma
# disciplina somente-leitura de `--diagnose`, nunca a de `--incremental`.
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
             "Nunca escreve — o refresh real (Gate S3) ainda não existe.",
    )
    parser.add_argument("--date-from", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD", help="Início da janela (inclusive) — obrigatório com --diagnose-shopee-window.")
    parser.add_argument("--date-to", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD", help="Fim da janela (inclusive) — obrigatório com --diagnose-shopee-window.")
    args = parser.parse_args(argv)

    if args.diagnose_shopee_window and (args.date_from is None or args.date_to is None):
        parser.error("--diagnose-shopee-window requer --date-from e --date-to")

    if args.diagnose:
        return run_diagnose_cli()
    if args.diagnose_shopee_window:
        return run_diagnose_shopee_window_cli(args.date_from, args.date_to)
    return run_incremental_cli()


if __name__ == "__main__":
    sys.exit(main())
