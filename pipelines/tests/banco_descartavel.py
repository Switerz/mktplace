"""PostgreSQL descartável para os testes do Gate SH-AUTO-1.

Compartilhado por `test_fato_diaria_lock_concorrencia.py` (exclusão mútua) e
`test_fato_diaria_upsert_parcial.py` (preservação dos upserts parciais).

Sem `FATO_DIARIA_LOCK_TEST_DSN`, os dois arquivos são SKIP inteiro: a suíte
normal continua offline.

    docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=postgres \\
        --name pg-lock-test postgres:16
    FATO_DIARIA_LOCK_TEST_DSN=postgresql://postgres:postgres@localhost:55432/postgres \\
        python -m pytest pipelines/tests/test_fato_diaria_lock_concorrencia.py \\
                         pipelines/tests/test_fato_diaria_upsert_parcial.py
"""
from __future__ import annotations

import os

DSN = os.environ.get("FATO_DIARIA_LOCK_TEST_DSN")

SKIP_REASON = (
    "FATO_DIARIA_LOCK_TEST_DSN ausente — esta prova exige PostgreSQL descartável"
)

#: Guarda contra apontar a suíte para um banco que importa. Barata e definitiva:
#: estes testes criam schema, derrubam tabela e inserem linhas.
_PROIBIDOS = ("neon.tech", "rds.amazonaws.com", "datamart")

#: Colunas da fato diária, separadas por tipo. A lista cobre o que os QUATRO
#: SQLs de upsert de `daily_performance` tocam — é o recorte que os testes
#: precisam, não uma cópia da migration.
COLUNAS_NUM = [
    "gmv", "avg_ticket", "repeat_buyer_rate_pct", "conversion_rate", "problem_rate",
    "cancel_rate_pct", "avg_delivery_hours", "avg_delivery_days", "ad_spend",
    "ad_revenue", "roas", "acos_pct", "ctr_pct", "cpc", "gmv_video", "gmv_live",
    "gmv_card", "total_settlement", "total_fees", "avg_fee_pct", "avg_settlement_pct",
    "seller_shipping_cost", "shipping_pct_of_gmv", "target_revenue",
    "target_attainment_pct", "projected_month_revenue", "data_quality_score",
]
COLUNAS_INT = [
    "orders", "units_sold", "unique_buyers", "new_buyers", "repeat_buyers",
    "visitors", "canceled_orders", "returned_orders", "refunded_orders",
    "delivered_orders", "ad_impressions", "ad_clicks",
]

DDL = f"""
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS audit;
DROP TABLE IF EXISTS marts.fact_marketplace_daily_performance;
CREATE TABLE marts.fact_marketplace_daily_performance (
    id serial PRIMARY KEY,
    date date NOT NULL,
    loja_id integer NOT NULL,
    marketplace_id integer NOT NULL,
    empresa_id integer NOT NULL,
    {', '.join(f'{c} numeric' for c in COLUNAS_NUM)},
    {', '.join(f'{c} bigint' for c in COLUNAS_INT)},
    source_updated_at timestamptz,
    ingested_at timestamptz NOT NULL DEFAULT NOW(),
    UNIQUE (date, loja_id, marketplace_id)
);
DROP TABLE IF EXISTS audit.source_sync_run;
CREATE TABLE audit.source_sync_run (
    sync_run_id serial PRIMARY KEY,
    source_name varchar NOT NULL,
    marketplace_id integer,
    loja_id integer,
    started_at timestamptz,
    finished_at timestamptz,
    status varchar,
    rows_extracted integer,
    rows_loaded integer,
    error_message text,
    source_min_date date,
    source_max_date date
);
DROP TABLE IF EXISTS audit.data_quality_check;
CREATE TABLE audit.data_quality_check (
    id serial PRIMARY KEY,
    check_name text, table_name text, marketplace_id integer,
    status text, severity text, failed_rows integer, details text
);
"""


def engine():
    from sqlalchemy import create_engine

    if not DSN:
        raise RuntimeError(SKIP_REASON)
    for proibido in _PROIBIDOS:
        if proibido in DSN:
            raise AssertionError(
                f"FATO_DIARIA_LOCK_TEST_DSN aponta para {proibido!r}. Estes testes "
                f"criam e apagam objetos; use um PostgreSQL descartável."
            )
    return create_engine(DSN)


def conectar():
    """Conexão em AUTOCOMMIT — a mesma forma que o lock usa em produção."""
    return engine().connect().execution_options(isolation_level="AUTOCOMMIT")


def criar_esquema():
    from sqlalchemy import text

    eng = engine()
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for stmt in filter(None, (s.strip() for s in DDL.split(";"))):
            conn.execute(text(stmt))
    return eng


def locks_ativos(chave: int) -> int:
    """Quantas sessões detêm a chave, na FORMA DE UM ARGUMENTO.

    🔑 `objsubid = 1` é a forma de um `bigint`; `objsubid = 2` é a de dois
    `int`. MEDIDO (22/09/2026): `pg_try_advisory_lock(918130003)` e
    `pg_try_advisory_lock(0, 918130003)` projetam para o MESMO
    `(classid << 32) | objid`, e sem o filtro a contagem devolve 2 para dois
    locks que não se excluem entre si. Num incidente isso apontaria a sessão
    errada para quem fosse investigar.
    """
    from sqlalchemy import text

    with conectar() as conn:
        return conn.execute(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                "AND ((classid::bigint << 32) | objid::bigint) = :chave "
                "AND objsubid = 1 AND granted"
            ),
            {"chave": chave},
        ).scalar_one()
