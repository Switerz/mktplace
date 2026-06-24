"""create fact tables and audit schema

Revision ID: 003
Revises: 002
Create Date: 2026-06-16
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # fact_marketplace_daily_performance — tabela central do MVP
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.fact_marketplace_daily_performance (
            id                      SERIAL PRIMARY KEY,
            date                    DATE NOT NULL REFERENCES marts.dim_calendario(date),
            loja_id                 INT  NOT NULL REFERENCES marts.dim_loja(loja_id),
            marketplace_id          INT  NOT NULL REFERENCES marts.dim_marketplace(marketplace_id),
            empresa_id              INT  NOT NULL REFERENCES marts.dim_empresa(empresa_id),

            -- Comercial
            gmv                     NUMERIC(18,2),
            orders                  BIGINT,
            units_sold              BIGINT,
            avg_ticket              NUMERIC(14,2),
            unique_buyers           BIGINT,
            new_buyers              BIGINT,
            repeat_buyers           BIGINT,
            repeat_buyer_rate_pct   NUMERIC(8,4),

            -- Funil
            visitors                BIGINT,
            conversion_rate         NUMERIC(8,4),

            -- Operacional
            canceled_orders         BIGINT,
            returned_orders         BIGINT,
            refunded_orders         BIGINT,
            problem_rate            NUMERIC(8,4),
            cancel_rate_pct         NUMERIC(8,4),
            delivered_orders        BIGINT,
            avg_delivery_hours      NUMERIC(10,2),
            avg_delivery_days       NUMERIC(10,2),

            -- Mídia
            ad_spend                NUMERIC(14,2),
            ad_revenue              NUMERIC(14,2),
            ad_impressions          BIGINT,
            ad_clicks               BIGINT,
            roas                    NUMERIC(10,4),
            acos_pct                NUMERIC(8,4),
            ctr_pct                 NUMERIC(8,4),
            cpc                     NUMERIC(10,4),

            -- TikTok-específico: conteúdo
            gmv_video               NUMERIC(18,2),
            gmv_live                NUMERIC(18,2),
            gmv_card                NUMERIC(18,2),

            -- Financeiro
            total_settlement        NUMERIC(18,2),
            total_fees              NUMERIC(14,2),
            avg_fee_pct             NUMERIC(8,4),
            avg_settlement_pct      NUMERIC(8,4),
            seller_shipping_cost    NUMERIC(14,2),
            shipping_pct_of_gmv     NUMERIC(8,4),

            -- Metas e projeções (calculados)
            target_revenue          NUMERIC(18,2),
            target_attainment_pct   NUMERIC(8,4),
            projected_month_revenue NUMERIC(18,2),

            -- Qualidade e rastreabilidade
            data_quality_score      NUMERIC(5,2),
            source_updated_at       TIMESTAMPTZ,
            ingested_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            UNIQUE (date, loja_id, marketplace_id)
        )
    """)

    # fact_goal_monthly — metas mensais (carga futura do XLSX)
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.fact_goal_monthly (
            goal_id        SERIAL PRIMARY KEY,
            ref_month      DATE NOT NULL,
            loja_id        INT  NOT NULL REFERENCES marts.dim_loja(loja_id),
            marketplace_id INT  REFERENCES marts.dim_marketplace(marketplace_id),
            empresa_id     INT  NOT NULL REFERENCES marts.dim_empresa(empresa_id),
            metric_name    VARCHAR(50) NOT NULL,
            target_value   NUMERIC(18,2) NOT NULL,
            source         VARCHAR(50) NOT NULL DEFAULT 'manual',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (ref_month, loja_id, marketplace_id, metric_name)
        )
    """)

    # audit.source_sync_run
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit.source_sync_run (
            sync_run_id      SERIAL PRIMARY KEY,
            source_name      VARCHAR(100) NOT NULL,
            marketplace_id   INT REFERENCES marts.dim_marketplace(marketplace_id),
            loja_id          INT REFERENCES marts.dim_loja(loja_id),
            started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at      TIMESTAMPTZ,
            status           VARCHAR(20) NOT NULL DEFAULT 'running'
                             CHECK (status IN ('running','success','failed')),
            rows_extracted   INT,
            rows_loaded      INT,
            error_message    TEXT,
            source_min_date  DATE,
            source_max_date  DATE
        )
    """)

    # audit.data_quality_check
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit.data_quality_check (
            check_id        SERIAL PRIMARY KEY,
            check_name      VARCHAR(100) NOT NULL,
            table_name      VARCHAR(100) NOT NULL,
            marketplace_id  INT REFERENCES marts.dim_marketplace(marketplace_id),
            loja_id         INT REFERENCES marts.dim_loja(loja_id),
            check_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status          VARCHAR(10) NOT NULL CHECK (status IN ('pass','fail','warn')),
            severity        VARCHAR(10) NOT NULL CHECK (severity IN ('critical','high','medium','low')),
            failed_rows     INT,
            details         TEXT
        )
    """)

    # Índices para performance
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fact_daily_date
            ON marts.fact_marketplace_daily_performance(date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fact_daily_loja
            ON marts.fact_marketplace_daily_performance(loja_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fact_daily_marketplace
            ON marts.fact_marketplace_daily_performance(marketplace_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fact_daily_empresa
            ON marts.fact_marketplace_daily_performance(empresa_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fact_daily_date_loja_mkt
            ON marts.fact_marketplace_daily_performance(date, loja_id, marketplace_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fact_goal_month_loja
            ON marts.fact_goal_monthly(ref_month, loja_id, marketplace_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sync_run_source
            ON audit.source_sync_run(source_name, started_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit.data_quality_check CASCADE")
    op.execute("DROP TABLE IF EXISTS audit.source_sync_run CASCADE")
    op.execute("DROP TABLE IF EXISTS marts.fact_goal_monthly CASCADE")
    op.execute("DROP TABLE IF EXISTS marts.fact_marketplace_daily_performance CASCADE")
