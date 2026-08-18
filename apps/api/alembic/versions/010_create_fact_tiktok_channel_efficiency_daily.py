"""Gate S3 — cria marts.fact_tiktok_channel_efficiency_daily (camada de serving).

Copia de `gold.v_channel_efficiency` (Data Mart, RDS) para o Neon, para que o
bloco `channel_funnel` de `/brand-detail` deixe de precisar alcancar o Data Mart.
Copia SEM transformacao: `ctr_pct` e `cvr_pct` continuam sendo calculados na
consulta da rota, a partir destas quatro metricas brutas — esta tabela nao guarda
razao derivada.

A FONTE E' UMA VIEW
-------------------
`gold.v_channel_efficiency` e' VIEW, nao tabela. Duas consequencias de desenho:
o custo de leitura e' o de recalcular a view, e nao ha' garantia de que uma
releitura devolva o mesmo resultado. Por isso o sync correspondente captura a
fonte **uma unica vez** por execucao e reconcilia o destino contra essa
fotografia, nunca contra uma releitura posterior.

POR QUE SNAPSHOT INTEGRAL, E NAO JANELA DE 90 DIAS
--------------------------------------------------
`/brand-detail` aceita **qualquer mes** desde outubro/2025. Medicao read-only de
17-18/08/2026, filtrada as cinco marcas: 4.728 linhas, 316 datas, 3 canais, e
**3.378 linhas (71,4%) fora da janela de 90 dias** — inalcancaveis por
incremental. Uma leitura integral custou 1,25 s e 2,45 s em duas medicoes, com
fingerprint identico. Substituir a tabela inteira e' portanto simultaneamente
mais simples e mais correto que manter watermark: absorve reafirmacao historica
em qualquer mes, que uma janela movel jamais alcancaria.

GRAO
----
`(date, brand, channel)`, tres canais na fonte: `VIDEO`, `LIVE`, `PRODUCT_CARD`.
Zero duplicidade medida nessa chave. O grao e' exatamente o de
`fact_tiktok_brand_content_daily` multiplicado pelos tres canais.

SOMENTE AS CINCO MARCAS OFICIAIS
--------------------------------
A view tem sete marcas; as cinco autorizadas sao as unicas com consumidor na
Torre. O sync filtra pela allowlist oficial
`pipelines.connectors.tiktok.connector.BRANDS_IN_SCOPE`, parametrizada, e esta
tabela recebe apenas as cinco.

SEM PII
-------
Nenhuma coluna identifica pessoa: `channel` e' categoria de canal e as quatro
metricas sao contagens agregadas por dia e marca. Diferente de
`fact_tiktok_creator_daily`, aqui nao ha' handle de criador.

LINHAGEM DE GMV
---------------
`gmv` aqui e' da linhagem de CONTEUDO do TikTok, por canal — **nao** e' o GMV
oficial do marketplace, calculado da Raw com `sub_total`. Esta migration nao toca
essa definicao, e a decisao de incluir frete no GMV segue frente separada.

CHECKS
------
Minimos medidos na fonte, nas cinco marcas: `impressions >= 447`,
`page_views >= 6`, `items_sold >= 0`, `gmv >= 0.00`, e zero nulo nas quatro. Logo
`CHECK >= 0` e' valido e `NOT NULL` seria defensavel — mas as colunas ficam
anulaveis para espelhar a view, cuja nulidade nao e' garantida por contrato. O
`<> 'NaN'` e' explicito nas numericas: `'NaN'::numeric >= 0` avalia TRUE em
Postgres, entao o CHECK de nao-negatividade sozinho nao barra NaN.

NAO APLICADA NESTA TASK: escrita no worktree isolado, nao executada contra banco
algum. A aplicacao e' escopo da Task 3/3.
"""
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sem IF NOT EXISTS (padrao de 006/007/008/009): colisao tem de falhar alto.
    op.execute("""
        CREATE TABLE marts.fact_tiktok_channel_efficiency_daily (
            date           DATE        NOT NULL,
            brand          TEXT        NOT NULL,
            channel        TEXT        NOT NULL,

            impressions    NUMERIC,
            page_views     BIGINT,
            items_sold     BIGINT,
            gmv            NUMERIC,

            synced_at      TIMESTAMPTZ NOT NULL,
            source_run_id  TEXT        NOT NULL,

            CONSTRAINT pk_fact_tiktok_channel_efficiency_daily
                PRIMARY KEY (date, brand, channel),

            CONSTRAINT ck_ftced_impressions CHECK (impressions >= 0 AND impressions <> 'NaN'),
            CONSTRAINT ck_ftced_gmv         CHECK (gmv         >= 0 AND gmv         <> 'NaN'),
            CONSTRAINT ck_ftced_page_views  CHECK (page_views  >= 0),
            CONSTRAINT ck_ftced_items_sold  CHECK (items_sold  >= 0),

            CONSTRAINT ck_ftced_brand_nao_vazia   CHECK (LENGTH(BTRIM(brand))   > 0),
            CONSTRAINT ck_ftced_channel_nao_vazio CHECK (LENGTH(BTRIM(channel)) > 0)
        )
    """)

    # `/brand-detail` filtra sempre por uma marca e um mes: (brand, date) e' a
    # ordem util, e a PK (date, brand, channel) nao serve esse acesso.
    op.execute("""
        CREATE INDEX idx_ftced_brand_date
            ON marts.fact_tiktok_channel_efficiency_daily (brand, date)
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_tiktok_channel_efficiency_daily IS
        'Serving do bloco channel_funnel de /brand-detail. Copia sem transformacao '
        'de gold.v_channel_efficiency (VIEW), grao (date, brand, channel), somente '
        'as cinco marcas oficiais. Substituida por inteiro a cada execucao do sync: '
        'a rota aceita qualquer mes e 71% do historico fica fora de uma janela de '
        '90 dias. ctr_pct/cvr_pct NAO sao guardados aqui — sao derivados na rota. '
        'gmv e da linhagem de CONTEUDO do TikTok, nao o GMV oficial do marketplace. '
        'Gate S3.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_channel_efficiency_daily.channel IS
        'Canal de conteudo: VIDEO, LIVE ou PRODUCT_CARD. Categoria, nao dado pessoal.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_channel_efficiency_daily.synced_at IS
        'Instante da execucao que publicou esta linha. Identico para todas as '
        'linhas do mesmo snapshot.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_channel_efficiency_daily.source_run_id IS
        'Identificador da execucao do sync que publicou esta linha.'
    """)


def downgrade() -> None:
    # Restrito aos objetos criados por esta migration.
    op.execute("DROP INDEX IF EXISTS marts.idx_ftced_brand_date")
    op.execute("DROP TABLE IF EXISTS marts.fact_tiktok_channel_efficiency_daily")
