"""VENDAS-POR-HORA-1 — cria marts.fact_marketplace_hourly_sales e a tabela de fonte.

POR QUE EXISTE
--------------
O "Hoje" da Gerencial corta os tres canais na MESMA hora cheia ("ate' 13h") e
compara com ontem ate' a mesma hora. A fato diaria guarda um numero por dia e
nao serve para isso. Quem escreve e' `pipelines.sync_vendas_por_hora`, pela DAG
`marketplace_tower_dia_corrente` do Airflow; quem le' e' a API.

GRAO
----
`fact_marketplace_hourly_sales`: dia BRT x hora BRT x canal x loja, com GMV e
pedidos dos pedidos CRIADOS naquela hora. So' horas fechadas. Hora sem pedido
nao tem linha.

`fact_marketplace_hourly_sales_fonte`: uma linha por canal, `fonte_ate` = ate'
quando a fonte comprovadamente tem dado. E' o que prova que uma hora sem linha
e' zero, e nao atraso.

APLICACAO
---------
Manual no Neon (`alembic upgrade 039`), e SO' DEPOIS que o Airflow estiver com
a imagem cujo pin de `mktplace-pipelines` ja' contem esta migration: o PMA
recusa rodar quando a cabeca do Neon nao existe no grafo do pin. Reversivel por
`alembic downgrade 038`, que derruba so' estas duas tabelas. Nao insere dados.

PERMISSAO
---------
O perfil de escrita da Torre no Airflow (`tower_dst_rw`) precisa de
`SELECT, INSERT, DELETE` na tabela de horas e `SELECT, INSERT, UPDATE` na de
fonte. O GRANT fica fora da migration, como na 032 e na 034, porque o nome do
perfil nao e' versionado (ver `docs/vendas_por_hora.md`).
"""
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None

TABELA = "marts.fact_marketplace_hourly_sales"
FONTE = "marts.fact_marketplace_hourly_sales_fonte"

UPGRADE_SQL = [
    f"""
    CREATE TABLE {TABELA} (
        dia             DATE           NOT NULL,
        hora            SMALLINT       NOT NULL,
        marketplace_id  INTEGER        NOT NULL REFERENCES marts.dim_marketplace (marketplace_id),
        loja_id         INTEGER        NOT NULL REFERENCES marts.dim_loja (loja_id),
        gmv             NUMERIC(14, 2) NOT NULL,
        pedidos         INTEGER        NOT NULL,
        publicado_em    TIMESTAMPTZ    NOT NULL DEFAULT now(),
        CONSTRAINT pk_fmhs PRIMARY KEY (dia, hora, marketplace_id, loja_id),
        CONSTRAINT ck_fmhs_hora CHECK (hora BETWEEN 0 AND 23),
        CONSTRAINT ck_fmhs_gmv CHECK (gmv <> 'NaN' AND gmv >= 0),
        CONSTRAINT ck_fmhs_pedidos CHECK (pedidos >= 0)
    )
    """,
    f"""
    CREATE TABLE {FONTE} (
        marketplace_id  INTEGER     PRIMARY KEY REFERENCES marts.dim_marketplace (marketplace_id),
        fonte_ate       TIMESTAMPTZ NOT NULL,
        publicado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]

DOWNGRADE_SQL = [
    f"DROP TABLE IF EXISTS {FONTE}",
    f"DROP TABLE IF EXISTS {TABELA}",
]


def upgrade() -> None:
    for sql in UPGRADE_SQL:
        op.execute(sql)


def downgrade() -> None:
    for sql in DOWNGRADE_SQL:
        op.execute(sql)
