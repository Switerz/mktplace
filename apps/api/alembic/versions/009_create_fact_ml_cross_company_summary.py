"""Gate S3 — cria marts.fact_ml_cross_company_summary (camada de serving).

Copia de `gold.ml_cross_company_summary` (Data Mart, RDS) para o Neon, para que o
bloco `ltv` de `/inteligencia` deixe de precisar alcancar o Data Mart. Copia SEM
transformacao: os valores sao exatamente os que a Gold serve hoje.

GRAO E VOLUME
-------------
Grao `(brand)`. Sao **quatro linhas** — uma por marca do Mercado Livre. Nao ha'
dimensao temporal: a fonte e' um snapshot recalculado, e o sync correspondente
substitui a tabela por inteiro dentro de uma transacao. Por isso nao existe
`date_column` aqui, e por isso esta tabela **nao** e' atendida pelo wrapper
`pipelines/ops/serving_refresh.py`, que exige janela `min(D-1, source_max)`.

SOMENTE AS NOVE COLUNAS CONSUMIDAS
----------------------------------
A fonte tem 30 colunas; `/inteligencia` le nove. Copiar as 30 ampliaria a
superficie sem servir a nenhuma tela. As nove sao as do bloco `ltv`:
`total_buyers`, `repeat_buyers`, `repeat_rate_pct`, `avg_customer_ltv`,
`vip_buyers`, `one_and_done_buyers`, `at_risk_or_churned`, `overall_roas` — mais
a chave `brand`.

NAO E' DADO PESSOAL
-------------------
As colunas com "buyer" no nome sao **contagens agregadas** de compradores por
marca. Nao ha' nome, e-mail, documento, telefone ou identificador de pessoa.
Nenhuma linha e' atribuivel a um individuo: a menor contagem medida na fonte e'
de centenas de compradores.

TIPOS
-----
`NUMERIC` sem precisao/escala declaradas, espelhando a fonte. Declarar escala
arredondaria valores e quebraria a igualdade de payload que o contrato congelado
de `/inteligencia` exige.

CHECKS
------
Todas as nove colunas de negocio sao anulaveis na fonte, e todas tem minimo
medido `>= 0` (menor valor observado: `repeat_rate_pct = 9.11`). Logo `CHECK >= 0`
e' semanticamente valido nas oito metricas. O `<> 'NaN'` e' explicito e separado
porque em Postgres `'NaN'::numeric >= 0` avalia como TRUE — um CHECK de
nao-negatividade sozinho **nao** barra NaN.

NAO APLICADA NESTA TASK: esta migration foi escrita no worktree isolado e nao foi
executada contra banco algum. A aplicacao e' escopo da Task 3/3.
"""
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sem IF NOT EXISTS de proposito (mesmo padrao de 006/007/008): se a tabela
    # ja existir, a migration precisa falhar alto em vez de seguir silenciosa
    # sobre um objeto que pode ter schema divergente.
    op.execute("""
        CREATE TABLE marts.fact_ml_cross_company_summary (
            brand                TEXT        NOT NULL,

            total_buyers         BIGINT,
            repeat_buyers        BIGINT,
            repeat_rate_pct      NUMERIC,
            avg_customer_ltv     NUMERIC,
            vip_buyers           BIGINT,
            one_and_done_buyers  BIGINT,
            at_risk_or_churned   BIGINT,
            overall_roas         NUMERIC,

            synced_at            TIMESTAMPTZ NOT NULL,
            source_run_id        TEXT        NOT NULL,

            CONSTRAINT pk_fact_ml_cross_company_summary PRIMARY KEY (brand),

            CONSTRAINT ck_fmccs_total_buyers        CHECK (total_buyers        >= 0),
            CONSTRAINT ck_fmccs_repeat_buyers       CHECK (repeat_buyers       >= 0),
            CONSTRAINT ck_fmccs_vip_buyers          CHECK (vip_buyers          >= 0),
            CONSTRAINT ck_fmccs_one_and_done        CHECK (one_and_done_buyers >= 0),
            CONSTRAINT ck_fmccs_at_risk_or_churned  CHECK (at_risk_or_churned  >= 0),

            CONSTRAINT ck_fmccs_repeat_rate_pct  CHECK (repeat_rate_pct  >= 0 AND repeat_rate_pct  <> 'NaN'),
            CONSTRAINT ck_fmccs_avg_customer_ltv CHECK (avg_customer_ltv >= 0 AND avg_customer_ltv <> 'NaN'),
            CONSTRAINT ck_fmccs_overall_roas     CHECK (overall_roas     >= 0 AND overall_roas     <> 'NaN'),

            CONSTRAINT ck_fmccs_brand_nao_vazia CHECK (LENGTH(BTRIM(brand)) > 0)
        )
    """)

    # Sem indice adicional: com quatro linhas, a PK basta e qualquer indice extra
    # seria custo de manutencao sem ganho de leitura.

    op.execute("""
        COMMENT ON TABLE marts.fact_ml_cross_company_summary IS
        'Serving do bloco ltv de /inteligencia. Copia sem transformacao de '
        'gold.ml_cross_company_summary, grao (brand), quatro linhas. Snapshot sem '
        'dimensao temporal: substituido por inteiro a cada execucao do sync, '
        'dentro de uma transacao. Contagens agregadas de compradores, nao dado '
        'pessoal. Gate S3.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_ml_cross_company_summary.synced_at IS
        'Instante da execucao que publicou esta linha. Identico para todas as '
        'linhas do mesmo snapshot.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_ml_cross_company_summary.source_run_id IS
        'Identificador da execucao do sync que publicou esta linha.'
    """)


def downgrade() -> None:
    # Restrito ao objeto criado por esta migration.
    op.execute("DROP TABLE IF EXISTS marts.fact_ml_cross_company_summary")
