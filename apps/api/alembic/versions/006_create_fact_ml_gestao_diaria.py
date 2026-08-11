"""Gate S1 — cria marts.fact_ml_gestao_diaria (camada de serving).

Destino da primeira fato da camada de serving desenhada em
docs/SERVING_AIRFLOW_PLAN.md: copia de gold.ml_gestao_diaria (Data Mart, RDS)
para o Neon, para que o backend no Render deixe de precisar alcancar o Data Mart.

Contrato de colunas EXPLICITO (§4.2 do blueprint): somente a chave, os campos
que `/operacoes` consome e auditoria tecnica. Nada de espelho da fonte — a view
de origem tem 37 colunas e aqui entram 7.

Fatos da auditoria read-only da fonte (11/08/2026) que este DDL reflete:

- `(ref_date, brand)` e' UNICO na origem (0 duplicados em 1.625 linhas) -> PK;
- `gmv`, `ad_spend`, `ad_revenue`, `paid_orders`: 0 nulos e 0 negativos em 472
  datas -> NOT NULL + CHECK de nao-negatividade;
- `roas` tem 906 nulos de 1.625 linhas e e' RAZAO, nao metrica aditiva ->
  NULLABLE, CHECK tolerante a NULL, e nunca somada em reconciliacao;
- todo CHECK numerico traz `<> 'NaN'` explicito porque `'NaN'::numeric >= 0` e'
  TRUE no Postgres e passaria por um CHECK de nao-negatividade sozinho.

Esta migration NAO faz backfill, NAO consulta o Data Mart e NAO altera nenhuma
tabela existente.

Revision ID: 006
Revises: 005
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sem `IF NOT EXISTS`, de proposito (Gate S1, correcao terminal).
    #
    # Esta migration CRIA uma tabela nova. Se ela ja existir, o upgrade tem de
    # FALHAR e o Alembic nao pode avancar — com `IF NOT EXISTS` a migration
    # adotaria silenciosamente uma tabela de origem e contrato desconhecidos,
    # marcaria a revision como aplicada e passaria a publicar dado dentro dela.
    # Nao existe adocao, `ALTER` nem compatibilizacao automatica: tabela
    # preexistente e' decisao humana, nao efeito colateral de migration.
    op.execute("""
        CREATE TABLE marts.fact_ml_gestao_diaria (
            -- chave: grao real da fonte, provado unico na auditoria
            ref_date        DATE          NOT NULL,
            brand           VARCHAR(64)   NOT NULL,

            -- metricas ADITIVAS consumidas por /operacoes
            gmv             NUMERIC(18,2) NOT NULL,
            ad_spend        NUMERIC(18,2) NOT NULL,
            ad_revenue      NUMERIC(18,2) NOT NULL,
            paid_orders     BIGINT        NOT NULL,

            -- RAZAO servida pela origem: nullable e nunca somada
            roas            NUMERIC(12,4),

            -- auditoria tecnica (nao vem da fonte)
            synced_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            source_run_id   VARCHAR(64),

            CONSTRAINT pk_fact_ml_gestao_diaria PRIMARY KEY (ref_date, brand),

            CONSTRAINT ck_fmgd_gmv         CHECK (gmv         >= 0 AND gmv         <> 'NaN'),
            CONSTRAINT ck_fmgd_ad_spend    CHECK (ad_spend    >= 0 AND ad_spend    <> 'NaN'),
            CONSTRAINT ck_fmgd_ad_revenue  CHECK (ad_revenue  >= 0 AND ad_revenue  <> 'NaN'),
            CONSTRAINT ck_fmgd_paid_orders CHECK (paid_orders >= 0),
            CONSTRAINT ck_fmgd_roas        CHECK (roas IS NULL OR (roas >= 0 AND roas <> 'NaN'))
        )
    """)

    # Nao redundante com a PK (ref_date, brand): a coluna lider e' outra, e
    # /operacoes filtra por `brand IN (...)` com janela de ref_date.
    # Tambem sem `IF NOT EXISTS`: indice preexistente com este nome e' estado
    # inesperado, e o upgrade deve parar em vez de segui-lo em silencio.
    op.execute("""
        CREATE INDEX idx_fmgd_brand_ref_date
            ON marts.fact_ml_gestao_diaria (brand, ref_date)
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_ml_gestao_diaria IS
        'Serving de gold.ml_gestao_diaria (Data Mart). Copia sem transformacao, '
        'grao (ref_date, brand). Preenchida por pipelines/sync_ml_gestao_diaria.py. '
        'roas e RAZAO servida pela origem: nunca somar.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_ml_gestao_diaria.roas IS
        'Razao ad_revenue/ad_spend servida pela origem; NULL quando a origem nao '
        'a calcula. Nunca somar: recalcular a partir dos totais quando preciso.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_ml_gestao_diaria.synced_at IS
        'Gerado no destino, no momento da publicacao da janela.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_ml_gestao_diaria.source_run_id IS
        'Identificador sanitizado da execucao de sync que publicou a linha.'
    """)


def downgrade() -> None:
    # Limitado EXCLUSIVAMENTE ao que o upgrade criou. Nenhuma outra tabela e'
    # tocada, e nenhum schema e' removido.
    op.execute("DROP INDEX IF EXISTS marts.idx_fmgd_brand_ref_date")
    op.execute("DROP TABLE IF EXISTS marts.fact_ml_gestao_diaria")
