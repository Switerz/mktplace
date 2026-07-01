"""create product tables for Shopee, ML and TikTok

Revision ID: 004
Revises: 003
Create Date: 2026-06-26

Fontes:
  - marts.fact_shopee_product_monthly  <- local PG (marts.fact_shopee_product_monthly)
  - marts.fact_ml_produto_ranking      <- gold.ml_produto_ranking (RDS, snapshot)
  - marts.fact_tiktok_product_daily    <- gold.tiktok_product_daily (RDS, serie diaria)

Chaves de upsert:
  - Shopee:  (ref_month, brand, sku_ref_key, product_name)
  - ML:      (brand, item_id)  — deduplicado por gross_revenue DESC na origem
  - TikTok:  (date, product_id)
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Shopee — dados mensais por produto/variacao/SKU
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.fact_shopee_product_monthly (
            id                  SERIAL PRIMARY KEY,
            ref_month           DATE        NOT NULL,
            brand               VARCHAR(64) NOT NULL,
            sku_ref             VARCHAR(128),
            sku_ref_key         VARCHAR(256) NOT NULL,
            product_name        VARCHAR(512) NOT NULL,
            variation_name      VARCHAR(256),

            gmv                 NUMERIC(18,2),
            units_sold          BIGINT,
            completed_orders    BIGINT,
            canceled_orders     BIGINT,
            cancel_rate_pct     NUMERIC(8,4),
            unique_buyers       BIGINT,
            avg_price           NUMERIC(14,2),

            ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            UNIQUE (ref_month, brand, sku_ref_key, product_name)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shopee_prod_brand_month
            ON marts.fact_shopee_product_monthly (brand, ref_month)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shopee_prod_month
            ON marts.fact_shopee_product_monthly (ref_month)
    """)

    # ------------------------------------------------------------------
    # 2. ML — ranking snapshot de produtos (sem dimensao temporal)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.fact_ml_produto_ranking (
            id                      SERIAL PRIMARY KEY,
            brand                   VARCHAR(64)  NOT NULL,
            item_id                 VARCHAR(64)  NOT NULL,
            seller_sku              VARCHAR(128),
            title                   TEXT,

            gross_revenue           NUMERIC(18,2),
            units_sold              BIGINT,
            unique_buyers           BIGINT,
            units_per_buyer         NUMERIC(10,4),
            cancel_rate_pct         NUMERIC(8,4),

            ad_spend                NUMERIC(14,2),
            ad_roas                 NUMERIC(10,4),
            ad_acos_pct             NUMERIC(8,4),
            days_advertised         BIGINT,

            revenue_share_pct       NUMERIC(8,4),
            cumulative_revenue_pct  NUMERIC(8,4),
            estimated_margin        NUMERIC(18,2),
            price_spread_pct        NUMERIC(8,4),

            pareto_bucket           TEXT,
            revenue_velocity        TEXT,
            ad_efficiency           TEXT,
            action_signal           TEXT,
            product_status          TEXT,

            first_sale              DATE,
            last_sale               DATE,

            ingested_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            refreshed_at            TIMESTAMPTZ,

            UNIQUE (brand, item_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ml_prod_brand
            ON marts.fact_ml_produto_ranking (brand)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ml_prod_pareto
            ON marts.fact_ml_produto_ranking (pareto_bucket)
    """)

    # ------------------------------------------------------------------
    # 3. TikTok — serie diaria por produto
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.fact_tiktok_product_daily (
            id                          SERIAL PRIMARY KEY,
            date                        DATE        NOT NULL,
            brand                       VARCHAR(64) NOT NULL,
            product_id                  VARCHAR(128) NOT NULL,
            product_name                VARCHAR(512),

            gmv                         NUMERIC(18,2),
            orders                      INTEGER,
            items_sold                  INTEGER,

            gmv_video                   NUMERIC(18,2),
            gmv_live                    NUMERIC(18,2),
            gmv_product_card            NUMERIC(18,2),
            items_sold_video            INTEGER,
            items_sold_live             INTEGER,
            items_sold_product_card     INTEGER,
            pct_gmv_video               NUMERIC(8,4),
            pct_gmv_live                NUMERIC(8,4),
            pct_gmv_card                NUMERIC(8,4),

            canceled                    INTEGER,
            refunded                    INTEGER,
            returned                    INTEGER,
            problem_rate                NUMERIC(8,4),

            rating_avg                  NUMERIC(6,3),
            total_ratings               INTEGER,

            ingested_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            UNIQUE (date, product_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tk_prod_brand_date
            ON marts.fact_tiktok_product_daily (brand, date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tk_prod_date
            ON marts.fact_tiktok_product_daily (date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tk_prod_product_id
            ON marts.fact_tiktok_product_daily (product_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS marts.fact_tiktok_product_daily")
    op.execute("DROP TABLE IF EXISTS marts.fact_ml_produto_ranking")
    op.execute("DROP TABLE IF EXISTS marts.fact_shopee_product_monthly")
