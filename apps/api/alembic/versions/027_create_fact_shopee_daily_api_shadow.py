"""Gate SHOPEE-MANUAL-RETIREMENT-1 — cria marts.fact_shopee_daily_api_shadow.

POR QUE ESTA TABELA EXISTE
--------------------------
`marts.fact_marketplace_daily_performance` serve a Shopee 100% a partir dos
exports do Seller Center (`shopee_manual_refresh`). Para as quatro contas com
aplicacao Shopee (apice, barbours, lescent, rituaria), a FAMILIA DE PEDIDOS
daquela fato (pedidos, unidades, cancelados, concluidos, frete estimado) tem
fonte na API — `silver.stg_shopee_orders` / `_order_items` no Data Mart,
ingeridos a cada 6h pelo `shopee_orders_etl`. Medido no gate: de janeiro a
junho/2026 a API reproduz o publicado com divergencia 0,00% nessas metricas.

Esta tabela guarda essa versao da API em SHADOW: ao lado, nunca dentro.

POR QUE NAO UMA COLUNA `source` NA FATO DIARIA
----------------------------------------------
A fato diaria nao tem procedencia e e' lida por ~15 consumidores
(`performance_service`, DRE, executive summary). Acrescentar linhas `api` nela
exigiria um interruptor em TODOS eles antes da primeira escrita — esquecer um
dobraria o numero naquela leitura (o defeito ja' visto no cutover de produtos,
ver docs/shopee_produtos_api_cutover.md). Uma tabela separada que NENHUM
consumidor le' torna a dupla contagem impossivel por construcao.

O QUE NAO ENTRA
---------------
- `gmv`: o GMV da tela e' o do shop-stats (vendas − canceladas − devolvidas),
  que a API de pedidos nao reproduz (+4,21% medido). O subtotal de produto vai
  em `product_subtotal`, com nome proprio, para nunca ser confundido com ele.
- `unique_buyers`, `visitors`, funil, novos/recorrentes: vem do shop-stats.
- `total_fees`, `total_settlement`: vivem no escrow (`payment/get_escrow_*`),
  ainda nao ingerido.
- `returned_orders`: o export conta qualquer status de devolucao/reembolso; a
  API de pedidos so' expoe `to_return` (Returns API nao ingerida).
- Ads.
- kokeshi: sem aplicacao Shopee. Fora por CHECK.

NaN
---
`'NaN'::numeric >= 0` e' TRUE no PostgreSQL: as faixas tem `<> 'NaN'` explicito.

APLICACAO
---------
Operacao manual e explicita no Neon (`alembic upgrade 027`). Reversivel por
`alembic downgrade 026`, que derruba so' esta tabela. A migration nao insere
dados.
"""
from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None

TABELA = "marts.fact_shopee_daily_api_shadow"

#: Contas com aplicacao Shopee. Lista FECHADA por CHECK, escrita LITERALMENTE
#: no DDL; um teste confere que a lista do publisher e' a mesma.
MARCAS = ("apice", "barbours", "lescent", "rituaria")

UPGRADE_SQL = [
    f"""
    CREATE TABLE {TABELA} (
        date                  DATE           NOT NULL,
        brand                 TEXT           NOT NULL,
        orders                INT            NOT NULL,
        units_sold            INT            NOT NULL,
        canceled_orders       INT            NOT NULL,
        delivered_orders      INT            NOT NULL,
        to_return_orders      INT            NOT NULL,
        unpaid_orders         INT            NOT NULL,
        seller_shipping_cost  NUMERIC(14, 2) NOT NULL,
        product_subtotal      NUMERIC(14, 2) NOT NULL,
        source                TEXT           NOT NULL,
        source_run_id         TEXT           NOT NULL,
        source_captured_at    TIMESTAMPTZ    NOT NULL,
        source_max_ingested_at TIMESTAMPTZ   NOT NULL,
        is_partial            BOOLEAN        NOT NULL,
        CONSTRAINT pk_fsdas PRIMARY KEY (date, brand),
        CONSTRAINT ck_fsdas_brand CHECK (brand IN ('apice', 'barbours', 'lescent', 'rituaria')),
        CONSTRAINT ck_fsdas_source CHECK (source = 'api'),
        CONSTRAINT ck_fsdas_contagens CHECK (
            orders >= 0 AND units_sold >= 0 AND canceled_orders >= 0 AND delivered_orders >= 0
            AND to_return_orders >= 0 AND unpaid_orders >= 0),
        CONSTRAINT ck_fsdas_subconjuntos CHECK (
            delivered_orders <= orders AND to_return_orders <= orders AND unpaid_orders <= orders),
        CONSTRAINT ck_fsdas_valores CHECK (
            seller_shipping_cost <> 'NaN' AND seller_shipping_cost >= 0
            AND product_subtotal <> 'NaN' AND product_subtotal >= 0),
        CONSTRAINT ck_fsdas_run_id CHECK (source_run_id ~ '^[0-9a-f]{{32}}$')
    )
    """,
    f"CREATE INDEX ix_fsdas_run ON {TABELA} (source_run_id)",
    f"""
    COMMENT ON TABLE {TABELA} IS
    'SHADOW da familia de pedidos da Shopee via API oficial (Data Mart silver.stg_shopee_orders/_order_items). '
    'NENHUM consumidor le esta tabela: a tela segue servida por marts.fact_marketplace_daily_performance '
    '(export manual). Grao dia BRT de criacao do pedido x marca. Sem gmv (o GMV da tela e o do shop-stats). '
    'Escrita so pelo pipelines.sync_shopee_daily_api_shadow. Gate SHOPEE-MANUAL-RETIREMENT-1.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.orders IS
    'Pedidos criados no dia com status diferente de cancelled (mesma regra do export: status <> Cancelado).'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.product_subtotal IS
    'Soma de item_total dos pedidos nao cancelados. NAO e o GMV da tela (que vem do shop-stats).'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.is_partial IS
    'true enquanto o dia esta na janela de maturacao: status ainda mudam (cancelamento, conclusao).'
    """,
]

DOWNGRADE_SQL = [f"DROP TABLE IF EXISTS {TABELA}"]


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
