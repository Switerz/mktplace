"""EXPEDICAO-KOKESHI-EXPORT — fila da Shopee a partir do export do Seller Centre.

POR QUE ESTAS TABELAS EXISTEM
-----------------------------
A Kokeshi nao tem app na Shopee Open Platform (`docs/shopee_api_resume_1_gate.md`),
entao nao existe em `raw.shopee_orders` e a Expedicao nunca a mostrou. O
operador da Kokeshi ja' baixa o export de pedidos do Seller Centre todo dia; a
partir dele, `pipelines.expedicao.shopee_export_cli` monta a fila da conta com a
MESMA classificacao das outras quatro (`transform.build_fila_shopee`).

POR QUE TABELAS PROPRIAS, E NAO AS DA 018
-----------------------------------------
O publicador agendado (`marketplace_tower_expedicao_refresh`, Airflow) faz
`DELETE ... WHERE channel = 'shopee'` em `marts.expedicao_fila_atual` a cada
2 h: uma linha da Kokeshi gravada ali sumiria na execucao seguinte. E cadastrar
a Kokeshi em `marts.dim_seller_account` faria o mesmo publicador esperar a conta
no Data Mart, onde ela nao existe — `ACCOUNT_MISSING` bloquearia a Shopee
inteira. Entao a conta vive num registry proprio e a fotografia, em tabelas
proprias; a API junta as duas na leitura.

  marts.expedicao_conta_export          contas alimentadas por export (registry)
  marts.expedicao_shopee_export_pedido  ultimo estado conhecido de cada pedido
                                        exportado (grao pedido, SEM comprador)
  marts.expedicao_fila_export           fila, MESMA forma da fila_atual
  marts.expedicao_refresh_run_export    resumo por conta, MESMA forma do run

As duas ultimas nascem com `LIKE ... INCLUDING ALL`: herdam colunas (inclusive
`etapa`/`etapa_fonte` da 033), defaults, CHECKs e indices. O `LIKE` nao copia
FOREIGN KEY, entao a FK de marca e' declarada aqui.

`marts.expedicao_shopee_export_pedido` guarda o que a LDR e a coorte vao precisar
(pagamento, prazo, coleta, status, FBS, motivo de cancelamento) — o bot nao tera'
de mudar de novo quando a LDR da Kokeshi for ligada. NAO guarda o numero de
rastreamento (so' se existe) nem nenhuma coluna do comprador.

APLICACAO
---------
`alembic upgrade 034` no Neon. Nenhuma tabela existente e' alterada: a API e o
Airflow seguem identicos ate' a primeira carga do export.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None

CONTA = "marts.expedicao_conta_export"
PEDIDO = "marts.expedicao_shopee_export_pedido"
FILA = "marts.expedicao_fila_export"
RUN = "marts.expedicao_refresh_run_export"

#: Conta da Kokeshi na Shopee. `shop_id` informado pelo operador (03/11/2026),
#: lido na pagina de perfil da loja no Seller Centre.
KOKESHI = ("shopee", "kokeshi", "395166866", "kokeshi", "Shopee Kokeshi")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {CONTA} (
            channel             VARCHAR(20)  NOT NULL,
            shop_account        VARCHAR(50)  NOT NULL,
            external_seller_id  VARCHAR(50)  NOT NULL,
            brand               VARCHAR(50)  NOT NULL
                REFERENCES marts.dim_loja (brand_key),
            account_name        VARCHAR(200) NOT NULL,
            ativo               BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT pk_expedicao_conta_export PRIMARY KEY (channel, shop_account),
            CONSTRAINT uk_expedicao_conta_export_seller
                UNIQUE (channel, external_seller_id),
            CONSTRAINT ck_ece_dominio_channel CHECK (channel IN ('shopee'))
        )
    """)
    # So' com a loja cadastrada: num banco recem-criado (testes, setup local)
    # `dim_loja` ainda esta' vazia e a FK recusaria a linha.
    op.execute(
        f"INSERT INTO {CONTA} (channel, shop_account, external_seller_id, brand,"
        " account_name) SELECT '" + "', '".join(KOKESHI) + "'"
        f" WHERE EXISTS (SELECT 1 FROM marts.dim_loja WHERE brand_key = '{KOKESHI[3]}')"
    )

    op.execute(f"""
        CREATE TABLE {PEDIDO} (
            shop_account        VARCHAR(50)  NOT NULL,
            order_sn            VARCHAR(64)  NOT NULL,
            shop_id             VARCHAR(50)  NOT NULL,
            order_status        VARCHAR(30)  NULL,
            status_export       VARCHAR(60)  NOT NULL,
            has_tracking        BOOLEAN      NOT NULL,
            create_time         TIMESTAMPTZ  NULL,
            pay_time            TIMESTAMPTZ  NULL,
            ship_by_date        TIMESTAMPTZ  NULL,
            pickup_done_time    TIMESTAMPTZ  NULL,
            shipping_carrier    VARCHAR(120) NULL,
            fulfillment_flag    VARCHAR(40)  NULL,
            cancel_reason       VARCHAR(300) NULL,
            ingested_at         TIMESTAMPTZ  NOT NULL,
            source_file         VARCHAR(300) NOT NULL,
            source_sha256       CHAR(64)     NOT NULL,
            loaded_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT pk_expedicao_shopee_export_pedido
                PRIMARY KEY (shop_account, order_sn)
        )
    """)
    op.execute(
        f"CREATE INDEX idx_esep_conta_criacao ON {PEDIDO} (shop_account, create_time)"
    )

    op.execute(f"CREATE TABLE {FILA} (LIKE marts.expedicao_fila_atual INCLUDING ALL)")
    op.execute(
        f"ALTER TABLE {FILA} ADD CONSTRAINT fk_efe_brand"
        " FOREIGN KEY (brand) REFERENCES marts.dim_loja (brand_key)"
    )
    op.execute(f"CREATE TABLE {RUN} (LIKE marts.expedicao_refresh_run INCLUDING ALL)")
    op.execute(
        f"ALTER TABLE {RUN} ADD CONSTRAINT fk_ere_brand"
        " FOREIGN KEY (brand) REFERENCES marts.dim_loja (brand_key)"
    )


def downgrade() -> None:
    # Remove SOMENTE o que o upgrade criou, na ordem inversa.
    op.execute(f"DROP TABLE IF EXISTS {RUN}")
    op.execute(f"DROP TABLE IF EXISTS {FILA}")
    op.execute(f"DROP TABLE IF EXISTS {PEDIDO}")
    op.execute(f"DROP TABLE IF EXISTS {CONTA}")
