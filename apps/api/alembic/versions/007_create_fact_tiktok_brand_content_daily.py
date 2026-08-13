"""Gate S2 — cria marts.fact_tiktok_brand_content_daily (camada de serving).

Copia de `gold.tiktok_brand_daily` (Data Mart, RDS) para o Neon, para que
`/operacoes` e `/brand-detail` deixem de precisar alcancar o Data Mart. Copia
SEM transformacao: os valores sao exatamente os que a Gold serve hoje.

POR QUE O NOME TEM `content`
----------------------------
As colunas de valor desta tabela pertencem a linhagem de CONTEUDO do TikTok, que
NAO e' o GMV oficial do marketplace:

- o GMV canonico e' calculado da Raw (`raw.tiktok_shop_orders`) com `sub_total` e
  allowlist de status, e vive em `marts.fact_marketplace_daily_performance`;
- `gold.tiktok_brand_daily` calcula sobre o valor antigo (~`total_amount`) e fica
  **+2,43%** acima na janela medida (docs/tiktok_gmv_com_frete_decisao.md §2);
- a quebra `gmv_video`/`gmv_live`/`gmv_card` **nao decompoe** o GMV dos pedidos
  (docs/tiktok_marts_grain_extension_handoff.md §7).

O sufixo `content` existe para que ninguem some `gmv` desta tabela como GMV
oficial do canal. A decisao de incluir frete no GMV e' frente SEPARADA e nao toca
esta migration: producao segue em `sub_total`.

ESCOPO: SOMENTE AS CINCO MARCAS OFICIAIS
----------------------------------------
A Gold tem marcas alem das cinco autorizadas, e nenhuma delas tem consumidor na
Torre. O sync (`pipelines/sync_tiktok_serving.py`) filtra pela allowlist oficial
`pipelines.connectors.tiktok.connector.BRANDS_IN_SCOPE`, entao esta tabela recebe
apenas as cinco. `brand VARCHAR(64)` nao restringe valores por DDL de proposito:
uma marca entrar ou sair da allowlist e' mudanca de configuracao, nao de schema.

FATOS DA AUDITORIA READ-ONLY DA FONTE (12/08/2026, JA COM A ALLOWLIST)
----------------------------------------------------------------------
Janela fechada `date <= 2026-08-10`, cinco marcas: **1.546 linhas, 310 datas**.

- `(date, brand)` e' UNICO (1.546 linhas = 1.546 chaves, 0 duplicados) -> PK.
  A Gold NAO tem PK nem UNIQUE fisico: o grao e' convencao, e aqui passa a ser
  restricao;
- zero nulo em coluna obrigatoria e zero NaN nas 35 colunas numericas;
- nulabilidade copiada da evidencia: NOT NULL somente onde a fonte tem ZERO nulos
  em todo o historico. 14 colunas de demografia tem **100% de nulos** (a fonte nao
  as calcula hoje) e `visitors`/`customers` tem 68,6%/48,2% -> NULLABLE;
- `total_fees` tem **1.529 valores negativos de 1.546** (min -266.342,00): e'
  TAXA, e negativo e' o esperado. **Proibido CHECK >= 0**;
- `total_live_minutes` tem **2 valores negativos**, um deles -29.545.461, em
  03/04/2026 e 06/05/2026, ambos em marcas do escopo. E' defeito de dado na
  origem, fora da janela de 30 dias que `/operacoes` le hoje. **Proibido CHECK
  >= 0**: copiar exatamente e' o contrato desta task, e corrigir a origem
  pertence ao pipeline de ingestao TikTok, nao a camada de serving;
- zero NaN em toda a fonte, mas todo CHECK numerico traz `<> 'NaN'` explicito
  porque `'NaN'::numeric >= 0` e' TRUE no Postgres e passaria sozinho;
- `NUMERIC` sem precisao declarada, igual a fonte: fixar `NUMERIC(18,2)`
  arredondaria e quebraria a igualdade de payload que o Gate S2 precisa provar.

Esta migration NAO faz backfill, NAO consulta o Data Mart e NAO altera nenhuma
tabela existente.

Revision ID: 007
Revises: 006
"""
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sem `IF NOT EXISTS`, de proposito (mesma regra do Gate S1).
    #
    # Se a tabela ja existir, o upgrade tem de FALHAR: com `IF NOT EXISTS` a
    # migration adotaria silenciosamente uma tabela de origem e contrato
    # desconhecidos, marcaria a revision como aplicada e passaria a publicar
    # dado dentro dela. Tabela preexistente e' decisao humana, nao efeito
    # colateral de migration.
    op.execute("""
        CREATE TABLE marts.fact_tiktok_brand_content_daily (
            -- chave: grao real da fonte, provado unico na auditoria
            date                        DATE          NOT NULL,
            brand                       VARCHAR(64)   NOT NULL,

            -- valor e volume (linhagem de CONTEUDO, ver docstring)
            gmv                         NUMERIC       NOT NULL,
            orders                      BIGINT        NOT NULL,
            gmv_video                   NUMERIC       NOT NULL,
            gmv_live                    NUMERIC       NOT NULL,
            gmv_card                    NUMERIC       NOT NULL,
            gmv_fresh                   NUMERIC       NOT NULL,
            gmv_evergreen               NUMERIC       NOT NULL,

            -- TAXA: negativa por natureza, sem CHECK de nao-negatividade
            total_fees                  NUMERIC       NOT NULL,

            -- funil: a fonte deixa nulo na maior parte do historico
            visitors                    INTEGER,
            customers                   BIGINT,

            -- producao de conteudo
            active_videos               BIGINT        NOT NULL,
            new_videos_posted           BIGINT        NOT NULL,
            active_video_creators       BIGINT        NOT NULL,
            total_views                 NUMERIC       NOT NULL,
            fresh_videos                BIGINT        NOT NULL,
            evergreen_videos            BIGINT        NOT NULL,

            -- lives. `total_live_minutes` SEM CHECK: a fonte tem 2 negativos
            total_lives                 BIGINT        NOT NULL,
            total_live_minutes          BIGINT        NOT NULL,
            live_creators               BIGINT        NOT NULL,

            -- demografia ponderada: 100% nula na fonte hoje
            viewers_views_weighted      NUMERIC       NOT NULL,
            viewers_pct_female          NUMERIC,
            viewers_pct_male            NUMERIC,
            viewers_pct_age_18_24       NUMERIC,
            viewers_pct_age_25_34       NUMERIC,
            viewers_pct_age_35_44       NUMERIC,
            viewers_pct_age_45_54       NUMERIC,
            viewers_pct_age_55_plus     NUMERIC,
            followers_views_weighted    NUMERIC       NOT NULL,
            followers_pct_female        NUMERIC,
            followers_pct_male          NUMERIC,
            followers_pct_age_18_24     NUMERIC,
            followers_pct_age_25_34     NUMERIC,
            followers_pct_age_35_44     NUMERIC,
            followers_pct_age_45_54     NUMERIC,
            followers_pct_age_55_plus   NUMERIC,

            -- auditoria tecnica (nao vem da fonte)
            synced_at                   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            source_run_id               VARCHAR(64),

            CONSTRAINT pk_fact_tiktok_brand_content_daily PRIMARY KEY (date, brand),

            CONSTRAINT ck_ftbcd_gmv           CHECK (gmv           >= 0 AND gmv           <> 'NaN'),
            CONSTRAINT ck_ftbcd_gmv_video     CHECK (gmv_video     >= 0 AND gmv_video     <> 'NaN'),
            CONSTRAINT ck_ftbcd_gmv_live      CHECK (gmv_live      >= 0 AND gmv_live      <> 'NaN'),
            CONSTRAINT ck_ftbcd_gmv_card      CHECK (gmv_card      >= 0 AND gmv_card      <> 'NaN'),
            CONSTRAINT ck_ftbcd_gmv_fresh     CHECK (gmv_fresh     >= 0 AND gmv_fresh     <> 'NaN'),
            CONSTRAINT ck_ftbcd_gmv_evergreen CHECK (gmv_evergreen >= 0 AND gmv_evergreen <> 'NaN'),
            CONSTRAINT ck_ftbcd_total_views   CHECK (total_views   >= 0 AND total_views   <> 'NaN'),

            -- TAXA: apenas sanidade de NaN, jamais >= 0
            CONSTRAINT ck_ftbcd_total_fees    CHECK (total_fees <> 'NaN'),

            CONSTRAINT ck_ftbcd_contagens CHECK (
                orders                >= 0 AND
                active_videos         >= 0 AND
                new_videos_posted     >= 0 AND
                active_video_creators >= 0 AND
                fresh_videos          >= 0 AND
                evergreen_videos      >= 0 AND
                total_lives           >= 0 AND
                live_creators         >= 0
            ),

            CONSTRAINT ck_ftbcd_funil CHECK (
                (visitors  IS NULL OR visitors  >= 0) AND
                (customers IS NULL OR customers >= 0)
            ),

            CONSTRAINT ck_ftbcd_views_weighted CHECK (
                viewers_views_weighted   >= 0 AND viewers_views_weighted   <> 'NaN' AND
                followers_views_weighted >= 0 AND followers_views_weighted <> 'NaN'
            ),

            -- percentuais: NULL-tolerantes e sem faixa 0..100, porque a fonte os
            -- entrega 100% nulos e o dominio real nao pode ser verificado hoje.
            -- NaN, esse sim, nunca e' percentual valido.
            CONSTRAINT ck_ftbcd_viewers_pct CHECK (
                (viewers_pct_female      IS NULL OR viewers_pct_female      <> 'NaN') AND
                (viewers_pct_male        IS NULL OR viewers_pct_male        <> 'NaN') AND
                (viewers_pct_age_18_24   IS NULL OR viewers_pct_age_18_24   <> 'NaN') AND
                (viewers_pct_age_25_34   IS NULL OR viewers_pct_age_25_34   <> 'NaN') AND
                (viewers_pct_age_35_44   IS NULL OR viewers_pct_age_35_44   <> 'NaN') AND
                (viewers_pct_age_45_54   IS NULL OR viewers_pct_age_45_54   <> 'NaN') AND
                (viewers_pct_age_55_plus IS NULL OR viewers_pct_age_55_plus <> 'NaN')
            ),
            CONSTRAINT ck_ftbcd_followers_pct CHECK (
                (followers_pct_female      IS NULL OR followers_pct_female      <> 'NaN') AND
                (followers_pct_male        IS NULL OR followers_pct_male        <> 'NaN') AND
                (followers_pct_age_18_24   IS NULL OR followers_pct_age_18_24   <> 'NaN') AND
                (followers_pct_age_25_34   IS NULL OR followers_pct_age_25_34   <> 'NaN') AND
                (followers_pct_age_35_44   IS NULL OR followers_pct_age_35_44   <> 'NaN') AND
                (followers_pct_age_45_54   IS NULL OR followers_pct_age_45_54   <> 'NaN') AND
                (followers_pct_age_55_plus IS NULL OR followers_pct_age_55_plus <> 'NaN')
            )
        )
    """)

    # Nao redundante com a PK (date, brand): a coluna lider e' outra, e os dois
    # consumidores filtram por marca com janela de data —
    # `brand IN (...) AND date >= X` em /operacoes,
    # `brand = X AND date BETWEEN` em /brand-detail.
    # Tambem sem `IF NOT EXISTS`: indice preexistente com este nome e' estado
    # inesperado, e o upgrade deve parar em vez de segui-lo em silencio.
    op.execute("""
        CREATE INDEX idx_ftbcd_brand_date
            ON marts.fact_tiktok_brand_content_daily (brand, date)
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_tiktok_brand_content_daily IS
        'Serving de gold.tiktok_brand_daily (Data Mart). Copia sem transformacao, '
        'grao (date, brand). Preenchida por pipelines/sync_tiktok_serving.py. '
        'ATENCAO: gmv aqui e da linhagem de CONTEUDO do TikTok, NAO o GMV oficial '
        'do marketplace (esse vive em fact_marketplace_daily_performance, calculado '
        'da Raw com sub_total). gmv_video/live/card nao decompoem o GMV de pedidos.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_brand_content_daily.total_fees IS
        'Taxa: NEGATIVA por natureza (1.529 de 1.546 linhas na auditoria). Sem CHECK >= 0.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_brand_content_daily.total_live_minutes IS
        'Copiada como servida. A origem tem 2 valores negativos (03/04 e 06/05/2026), '
        'defeito de dado da ingestao TikTok. Sem CHECK >= 0 de proposito.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_brand_content_daily.synced_at IS
        'Gerado no destino, no momento da publicacao da janela.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_brand_content_daily.source_run_id IS
        'Identificador sanitizado da execucao de sync que publicou a linha.'
    """)


def downgrade() -> None:
    # Limitado EXCLUSIVAMENTE ao que ESTA revision criou. A 008 e' independente.
    op.execute("DROP INDEX IF EXISTS marts.idx_ftbcd_brand_date")
    op.execute("DROP TABLE IF EXISTS marts.fact_tiktok_brand_content_daily")
