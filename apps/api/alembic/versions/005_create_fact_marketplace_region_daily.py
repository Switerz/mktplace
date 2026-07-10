"""create marts.fact_marketplace_region_daily (Gate 6B)

Revision ID: 005
Revises: 004
Create Date: 2026-07-09

Espelha gold.marketplace_region_daily (Data Mart, aplicada no Gate 6A — ver
db/sql/gold/marketplace_region_daily_ddl.sql e docs/regional_design_draft.md).
Grao: date x marketplace_id x loja_id x uf. TikTok nunca tem linhas aqui
(sem cobertura de UF em nenhuma fonte mapeada).

Diferenca deliberada frente ao Gate 6A: aqui usamos "IF NOT EXISTS", como
todas as demais migrations deste diretorio (001-004) — a convencao do Neon
e' upgrade idempotente, diferente da regra "falhar se ja existir" adotada
para a PRIMEIRA aplicacao no Data Mart (Gate 6A), que e' um sistema
diferente com um objetivo diferente (evitar mascarar uma aplicacao parcial
anterior). Nao ha contradicao: cada camada segue a convencao ja
estabelecida no seu proprio diretorio.
"""
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.fact_marketplace_region_daily (
            id                              BIGSERIAL PRIMARY KEY,
            date                            DATE NOT NULL REFERENCES marts.dim_calendario(date),
            marketplace_id                  INT  NOT NULL REFERENCES marts.dim_marketplace(marketplace_id),
            loja_id                         INT  NOT NULL REFERENCES marts.dim_loja(loja_id),
            uf                              CHAR(2) NOT NULL,

            gmv                             NUMERIC(14, 2) NOT NULL DEFAULT 0,
            orders                          INT NOT NULL DEFAULT 0,
            units_sold                      INT NOT NULL DEFAULT 0,
            canceled_orders                 INT NOT NULL DEFAULT 0,
            returned_orders                 INT NOT NULL DEFAULT 0,

            seller_shipping_cost           NUMERIC(14, 2),
            buyer_shipping_fee             NUMERIC(14, 2),
            estimated_shipping_fee         NUMERIC(14, 2),
            reverse_shipping_fee           NUMERIC(14, 2),

            uf_known_orders                 INT NOT NULL DEFAULT 0,
            uf_eligible_orders               INT NOT NULL DEFAULT 0,
            shipping_cost_covered_orders     INT NOT NULL DEFAULT 0,
            shipping_cost_eligible_orders    INT NOT NULL DEFAULT 0,

            source_updated_at               TIMESTAMPTZ,
            ingested_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT chk_fmrd_gmv_non_negative CHECK (gmv >= 0),
            CONSTRAINT chk_fmrd_shipping_non_negative CHECK (
                (seller_shipping_cost IS NULL OR (seller_shipping_cost >= 0 AND seller_shipping_cost <> 'NaN'))
                AND (buyer_shipping_fee IS NULL OR (buyer_shipping_fee >= 0 AND buyer_shipping_fee <> 'NaN'))
                AND (estimated_shipping_fee IS NULL OR (estimated_shipping_fee >= 0 AND estimated_shipping_fee <> 'NaN'))
                AND (reverse_shipping_fee IS NULL OR (reverse_shipping_fee >= 0 AND reverse_shipping_fee <> 'NaN'))
            ),
            CONSTRAINT chk_fmrd_uf_valida CHECK (uf IN (
                'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG',
                'PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO',
                'XX'
            )),

            UNIQUE (date, marketplace_id, loja_id, uf)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fmrd_date
            ON marts.fact_marketplace_region_daily (date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fmrd_uf
            ON marts.fact_marketplace_region_daily (uf)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_fmrd_loja_marketplace
            ON marts.fact_marketplace_region_daily (loja_id, marketplace_id)
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_marketplace_region_daily IS
            'Sync Data Mart -> Neon (Gate 6B) de gold.marketplace_region_daily. '
            'Grao: date x marketplace_id x loja_id x uf. TikTok nunca tem linhas '
            'aqui. Numeradores/denominadores (uf_*, shipping_cost_*) nunca devem '
            'ser convertidos em percentual antes de chegar aqui — calcular na API.'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS marts.fact_marketplace_region_daily CASCADE")
