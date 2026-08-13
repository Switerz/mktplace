"""Gate S2 — cria marts.fact_tiktok_creator_daily (camada de serving).

Copia de `gold.tiktok_creator_daily` (Data Mart, RDS) para o Neon, para que
`/operacoes` e `/brand-detail` deixem de precisar alcancar o Data Mart. Copia SEM
transformacao: os valores sao exatamente os que a Gold serve hoje.

Vale aqui a mesma ressalva de linhagem da migration 007: `gmv_total`,
`gmv_video` e `gmv_live` sao da linhagem de CONTEUDO do TikTok e **nao** sao o
GMV oficial do marketplace, que e' calculado da Raw com `sub_total`. A decisao de
incluir frete no GMV e' frente SEPARADA e nao toca esta migration.

ESCOPO: SOMENTE AS CINCO MARCAS OFICIAIS
----------------------------------------
A Gold tem marcas alem das cinco autorizadas, e nenhuma delas tem consumidor na
Torre. O sync (`pipelines/sync_tiktok_serving.py`) filtra pela allowlist oficial
`pipelines.connectors.tiktok.connector.BRANDS_IN_SCOPE`, entao esta tabela recebe
apenas as cinco. Isso e' tambem minimizacao de dado pessoal: `creator` e' handle
publico potencialmente identificavel, e copiar marca sem consumidor ampliaria a
superficie sem servir a nenhuma tela.

FATOS DA AUDITORIA READ-ONLY DA FONTE (12/08/2026, JA COM A ALLOWLIST)
----------------------------------------------------------------------
Janela fechada `date <= 2026-08-10`, cinco marcas: **184.252 linhas, 308 datas**.

- `(date, brand, creator)` e' UNICO (184.252 linhas = 184.252 chaves, 0
  duplicados) -> PK. A Gold NAO tem PK nem UNIQUE fisico: o grao e' convencao, e
  aqui passa a ser restricao;
- **zero nulo** nas 9 colunas consumidas, em todo o historico -> todas NOT NULL.
  Diferente da 007, aqui nao existe coluna opcional;
- **zero valor negativo** e **zero NaN** nas 6 metricas -> CHECK `>= 0` valido em
  todas, sempre com `<> 'NaN'` explicito nas NUMERIC porque `'NaN'::numeric >= 0`
  e' TRUE no Postgres e passaria sozinho;
- 22.074 criadores distintos; maior `creator` tem 24 caracteres e maior `brand`
  tem 8. `VARCHAR(128)`/`VARCHAR(64)` dao folga de mais de 5x e falham alto em
  caso de estouro, em vez de truncar em silencio;
- `NUMERIC` sem precisao declarada, igual a fonte: fixar escala arredondaria e
  quebraria a igualdade de payload que o Gate S2 precisa provar.

Esta e' a tabela com PII potencial do S2: `creator` e' handle publico de criador.
Nenhum handle individual foi impresso em auditoria, log ou relatorio; o modulo de
sync tambem nao imprime linha individual.

Esta migration NAO faz backfill, NAO consulta o Data Mart e NAO altera nenhuma
tabela existente.

Revision ID: 008
Revises: 007
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sem `IF NOT EXISTS`, de proposito: tabela preexistente e' decisao humana,
    # nao efeito colateral de migration. Ver comentario equivalente na 007.
    op.execute("""
        CREATE TABLE marts.fact_tiktok_creator_daily (
            -- chave: grao real da fonte, provado unico na auditoria
            date            DATE           NOT NULL,
            brand           VARCHAR(64)    NOT NULL,
            creator         VARCHAR(128)   NOT NULL,

            -- valor por criador (linhagem de CONTEUDO, ver docstring)
            gmv_total       NUMERIC        NOT NULL,
            gmv_video       NUMERIC        NOT NULL,
            gmv_live        NUMERIC        NOT NULL,

            -- volume
            views_video     NUMERIC        NOT NULL,
            videos_count    BIGINT         NOT NULL,
            lives_count     BIGINT         NOT NULL,

            -- auditoria tecnica (nao vem da fonte)
            synced_at       TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
            source_run_id   VARCHAR(64),

            CONSTRAINT pk_fact_tiktok_creator_daily PRIMARY KEY (date, brand, creator),

            CONSTRAINT ck_ftcd_gmv_total   CHECK (gmv_total   >= 0 AND gmv_total   <> 'NaN'),
            CONSTRAINT ck_ftcd_gmv_video   CHECK (gmv_video   >= 0 AND gmv_video   <> 'NaN'),
            CONSTRAINT ck_ftcd_gmv_live    CHECK (gmv_live    >= 0 AND gmv_live    <> 'NaN'),
            CONSTRAINT ck_ftcd_views_video CHECK (views_video >= 0 AND views_video <> 'NaN'),
            CONSTRAINT ck_ftcd_contagens   CHECK (videos_count >= 0 AND lives_count >= 0)
        )
    """)

    # Nao redundante com a PK (date, brand, creator): a coluna lider e' outra, e
    # os dois consumidores filtram por marca com janela de data e agrupam por
    # criador — `brand IN (...) AND date >= X GROUP BY brand, creator` em
    # /operacoes e `brand = X AND date BETWEEN ... GROUP BY creator` em
    # /brand-detail.
    op.execute("""
        CREATE INDEX idx_ftcd_brand_date
            ON marts.fact_tiktok_creator_daily (brand, date)
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_tiktok_creator_daily IS
        'Serving de gold.tiktok_creator_daily (Data Mart). Copia sem transformacao, '
        'grao (date, brand, creator). Preenchida por pipelines/sync_tiktok_serving.py. '
        'ATENCAO: gmv_total/video/live sao da linhagem de CONTEUDO do TikTok, NAO o '
        'GMV oficial do marketplace. creator e handle publico de criador: nunca '
        'imprimir linha individual em log ou relatorio.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_creator_daily.creator IS
        'Handle publico do criador. 22.074 distintos na auditoria; maior com 24 chars.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_creator_daily.synced_at IS
        'Gerado no destino, no momento da publicacao da janela.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_creator_daily.source_run_id IS
        'Identificador sanitizado da execucao de sync que publicou a linha.'
    """)


def downgrade() -> None:
    # Limitado EXCLUSIVAMENTE ao que ESTA revision criou. A 007 nao e' tocada.
    op.execute("DROP INDEX IF EXISTS marts.idx_ftcd_brand_date")
    op.execute("DROP TABLE IF EXISTS marts.fact_tiktok_creator_daily")
