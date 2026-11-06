"""CX-KOKESHI-BOT — Saude da Conta, chat e devolucoes da Kokeshi pelo bot.

POR QUE ESTAS TABELAS EXISTEM
-----------------------------
A Kokeshi nao tem app na Shopee Open Platform: as abas CX e Performance leem
a Saude da Conta, o chat e as devolucoes das outras quatro lojas no Data Mart
(`raw.shopee_account_health_*`, `raw.shopee_chat_snapshot`,
`raw.shopee_returns`, gravadas pelo Airflow). A Kokeshi entra pelo bot do
Seller Centre, que ja' grava a Expedicao direto no Neon (034). Aqui ficam as
MESMAS formas, no Neon, para a API ler a Kokeshi com a mesma regra:

  marts.shopee_saude_conta_export    = raw.shopee_account_health_snapshot
  marts.shopee_saude_metrica_export  = raw.shopee_account_health_metric
  marts.shopee_chat_export           = raw.shopee_chat_snapshot (+ painel de chat)
  marts.shopee_returns_export        = raw.shopee_returns (sem PII)

Quem grava e' `pipelines.cx.kokeshi_cli` (maquina do bot), a partir de um JSON
que o bot monta lendo o Seller Centre (`docs/cx_kokeshi_bot_runbook.md`).
Cancelamentos e pedidos NAO precisam de tabela nova: ja' vem do export de
pedidos (`marts.expedicao_shopee_export_pedido`, 034).

GRANT
-----
O perfil de escrita do bot (`tower_dst_rw`, o mesmo da 034) recebe acesso SE
existir no banco; num banco de teste sem o perfil, nada acontece.

APLICACAO
---------
`alembic upgrade 036` no Neon. Nenhuma tabela existente e' alterada.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None

SAUDE = "marts.shopee_saude_conta_export"
METRICA = "marts.shopee_saude_metrica_export"
CHAT = "marts.shopee_chat_export"
RETURNS = "marts.shopee_returns_export"
TABELAS = (SAUDE, METRICA, CHAT, RETURNS)
PERFIL_BOT = "tower_dst_rw"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {SAUDE} (
            brand                  VARCHAR(50)  NOT NULL REFERENCES marts.dim_loja (brand_key),
            captured_date          DATE         NOT NULL,
            captured_at            TIMESTAMPTZ  NOT NULL,
            overall_rating         SMALLINT     NULL,
            fulfillment_failed     INTEGER      NULL,
            listing_failed         INTEGER      NULL,
            custom_service_failed  INTEGER      NULL,
            penalty_points         INTEGER      NULL,
            ongoing_punishments    INTEGER      NULL,
            source_sha256          CHAR(64)     NOT NULL,
            loaded_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT pk_shopee_saude_conta_export PRIMARY KEY (brand, captured_date),
            CONSTRAINT ck_ssce_nota CHECK (overall_rating IS NULL OR overall_rating BETWEEN 1 AND 4)
        )
    """)
    op.execute(f"""
        CREATE TABLE {METRICA} (
            brand              VARCHAR(50)    NOT NULL REFERENCES marts.dim_loja (brand_key),
            captured_date      DATE           NOT NULL,
            metric_id          INTEGER        NOT NULL,
            metric_name        VARCHAR(80)    NOT NULL,
            metric_type        SMALLINT       NOT NULL,
            parent_metric_id   INTEGER        NOT NULL DEFAULT 0,
            unit               SMALLINT       NOT NULL,
            current_period     NUMERIC(14, 4) NULL,
            last_period        NUMERIC(14, 4) NULL,
            target_value       NUMERIC(14, 4) NULL,
            target_comparator  VARCHAR(2)     NULL,
            loaded_at          TIMESTAMPTZ    NOT NULL DEFAULT now(),
            CONSTRAINT pk_shopee_saude_metrica_export PRIMARY KEY (brand, captured_date, metric_id)
        )
    """)
    op.execute(f"""
        CREATE TABLE {CHAT} (
            brand                   VARCHAR(50)    NOT NULL REFERENCES marts.dim_loja (brand_key),
            captured_at             TIMESTAMPTZ    NOT NULL,
            unread_count            INTEGER        NULL,
            conversas_7d            INTEGER        NULL,
            conversas_30d           INTEGER        NULL,
            aguardando_resposta     INTEGER        NULL,
            conversas_truncado      BOOLEAN        NULL,
            periodo_dias            SMALLINT       NULL,
            visitantes_chat         INTEGER        NULL,
            taxa_resposta_pct       NUMERIC(6, 2)  NULL,
            tempo_resposta_medio_s  INTEGER        NULL,
            csat_pct                NUMERIC(6, 2)  NULL,
            conversao_chat_pct      NUMERIC(6, 2)  NULL,
            source_sha256           CHAR(64)       NOT NULL,
            loaded_at               TIMESTAMPTZ    NOT NULL DEFAULT now(),
            CONSTRAINT pk_shopee_chat_export PRIMARY KEY (brand, captured_at)
        )
    """)
    op.execute(f"""
        CREATE TABLE {RETURNS} (
            brand          VARCHAR(50)    NOT NULL REFERENCES marts.dim_loja (brand_key),
            return_sn      VARCHAR(64)    NOT NULL,
            order_sn       VARCHAR(64)    NULL,
            create_time    TIMESTAMPTZ    NULL,
            update_time    TIMESTAMPTZ    NULL,
            status         VARCHAR(30)    NULL,
            status_export  VARCHAR(80)    NOT NULL,
            reason         VARCHAR(60)    NULL,
            reason_export  VARCHAR(200)   NULL,
            refund_amount  NUMERIC(14, 2) NULL,
            currency       VARCHAR(3)     NULL,
            captured_at    TIMESTAMPTZ    NOT NULL,
            loaded_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),
            CONSTRAINT pk_shopee_returns_export PRIMARY KEY (brand, return_sn)
        )
    """)
    op.execute(f"CREATE INDEX idx_sre_brand_create ON {RETURNS} (brand, create_time)")
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{PERFIL_BOT}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TABELAS)} TO {PERFIL_BOT};
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    # Remove SOMENTE o que o upgrade criou, na ordem inversa.
    for tabela in reversed(TABELAS):
        op.execute(f"DROP TABLE IF EXISTS {tabela}")
