"""create dimension tables

Revision ID: 002
Revises: 001
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # dim_empresa
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.dim_empresa (
            empresa_id   SERIAL PRIMARY KEY,
            nome_empresa      VARCHAR(100) NOT NULL,
            nome_normalizado  VARCHAR(100) NOT NULL,
            ativo        BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # dim_loja
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.dim_loja (
            loja_id          SERIAL PRIMARY KEY,
            empresa_id       INT NOT NULL REFERENCES marts.dim_empresa(empresa_id),
            brand_key        VARCHAR(50) NOT NULL UNIQUE,
            nome_loja        VARCHAR(100) NOT NULL,
            nome_normalizado VARCHAR(100) NOT NULL,
            ativo            BOOLEAN NOT NULL DEFAULT TRUE,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # dim_marketplace
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.dim_marketplace (
            marketplace_id   SERIAL PRIMARY KEY,
            nome_marketplace VARCHAR(50) NOT NULL,
            slug             VARCHAR(20) NOT NULL UNIQUE,
            ativo            BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)

    # dim_seller_account
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.dim_seller_account (
            seller_account_id  SERIAL PRIMARY KEY,
            marketplace_id     INT NOT NULL REFERENCES marts.dim_marketplace(marketplace_id),
            loja_id            INT NOT NULL REFERENCES marts.dim_loja(loja_id),
            external_seller_id VARCHAR(100) NOT NULL,
            account_name       VARCHAR(200),
            ativo              BOOLEAN NOT NULL DEFAULT TRUE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (marketplace_id, external_seller_id)
        )
    """)

    # dim_status_pedido
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.dim_status_pedido (
            status_id       SERIAL PRIMARY KEY,
            marketplace_id  INT REFERENCES marts.dim_marketplace(marketplace_id),
            raw_status      VARCHAR(50) NOT NULL,
            status_canonico VARCHAR(30) NOT NULL,
            descricao       TEXT,
            UNIQUE (marketplace_id, raw_status)
        )
    """)

    # dim_calendario — gerada via SQL para cobrir 2024-2027
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.dim_calendario (
            date            DATE PRIMARY KEY,
            ano             INT NOT NULL,
            mes             INT NOT NULL,
            mes_nome        VARCHAR(20) NOT NULL,
            mes_abrev       VARCHAR(3) NOT NULL,
            semana_iso      INT NOT NULL,
            trimestre       INT NOT NULL,
            dia_semana      INT NOT NULL,
            dia_semana_nome VARCHAR(15) NOT NULL,
            inicio_semana   DATE NOT NULL,
            inicio_mes      DATE NOT NULL,
            fim_mes         DATE NOT NULL,
            dias_no_mes     INT NOT NULL,
            is_weekend      BOOLEAN NOT NULL
        )
    """)

    op.execute("""
        INSERT INTO marts.dim_calendario
        SELECT
            d::date AS date,
            EXTRACT(YEAR  FROM d)::int AS ano,
            EXTRACT(MONTH FROM d)::int AS mes,
            TO_CHAR(d, 'TMMonth')      AS mes_nome,
            TO_CHAR(d, 'TMMon')        AS mes_abrev,
            EXTRACT(WEEK  FROM d)::int AS semana_iso,
            EXTRACT(QUARTER FROM d)::int AS trimestre,
            EXTRACT(ISODOW FROM d)::int  AS dia_semana,
            TO_CHAR(d, 'TMDay')        AS dia_semana_nome,
            (d - ((EXTRACT(ISODOW FROM d)::int - 1) || ' days')::interval)::date AS inicio_semana,
            DATE_TRUNC('month', d)::date AS inicio_mes,
            (DATE_TRUNC('month', d) + INTERVAL '1 month - 1 day')::date AS fim_mes,
            EXTRACT(DAY FROM (DATE_TRUNC('month', d) + INTERVAL '1 month - 1 day'))::int AS dias_no_mes,
            EXTRACT(ISODOW FROM d) IN (6, 7) AS is_weekend
        FROM GENERATE_SERIES('2024-01-01'::date, '2027-12-31'::date, '1 day') AS d
        ON CONFLICT (date) DO NOTHING
    """)

    # Índices nas dimensões
    op.execute("CREATE INDEX IF NOT EXISTS idx_dim_loja_brand_key ON marts.dim_loja(brand_key)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_dim_loja_empresa_id ON marts.dim_loja(empresa_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS marts.dim_calendario CASCADE")
    op.execute("DROP TABLE IF EXISTS marts.dim_status_pedido CASCADE")
    op.execute("DROP TABLE IF EXISTS marts.dim_seller_account CASCADE")
    op.execute("DROP TABLE IF EXISTS marts.dim_marketplace CASCADE")
    op.execute("DROP TABLE IF EXISTS marts.dim_loja CASCADE")
    op.execute("DROP TABLE IF EXISTS marts.dim_empresa CASCADE")
