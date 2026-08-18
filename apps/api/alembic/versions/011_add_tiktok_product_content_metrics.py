"""Gate S3 — adiciona active_videos e video_views a marts.fact_tiktok_product_daily.

`/brand-detail` monta `top_produtos` a partir de `gold.tiktok_product_daily` e
consome doze colunas. Dez delas ja existem no mart; faltam exatamente duas:

- `active_videos` — exposta como `videos` no payload;
- `video_views`   — denominador de `gpm = gmv / video_views * 1000`.

Sem elas, `top_produtos` nao pode ser servido pelo Neon. Com elas, a rota inteira
passa a ler `marts.*`.

POR QUE ANULAVEIS, SEM DEFAULT
------------------------------
A tabela tem mais de 213 mil linhas ja carregadas. `ADD COLUMN ... NOT NULL` sem
default falharia sobre elas; `DEFAULT 0` seria pior: apagaria a distincao entre
"ainda nao retroalimentado" e "zero medido".

E essa distincao e' observavel. Medicao read-only na fonte, cinco marcas, 213.889
linhas: `active_videos` e `video_views` tem **zero nulo** e cerca de 104.800
**zeros** cada. Ou seja, na fonte o estado "sem video no dia" e' `0`, nunca
`NULL`. Logo, depois do backfill integral da Task 3/3, **qualquer NULL restante
significa linha nao retroalimentada** — e' esse o critério de aceite, e ele so'
existe se a coluna nascer anulavel e sem default.

Apertar para `NOT NULL` depois do backfill e' possivel, mas fica fora deste gate:
seria uma quarta migration, com sua propria decisao.

ESCOPO ESTREITO
---------------
Duas colunas, uma tabela. Nada de GMV, frete, filtro, agregacao, janela
incremental ou chave e' tocado. A PK de `fact_tiktok_product_daily` permanece
`(date, product_id)`.

NAO APLICADA NESTA TASK: escrita no worktree isolado, nao executada contra banco
algum. A aplicacao e o backfill `--full` sao escopo da Task 3/3.
"""
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Aditivo e anulavel: nao reescreve as 213 mil linhas existentes e preserva a
    # diferenca entre NULL (nao retroalimentado) e 0 (medido como zero).
    op.execute("""
        ALTER TABLE marts.fact_tiktok_product_daily
            ADD COLUMN active_videos BIGINT
    """)
    op.execute("""
        ALTER TABLE marts.fact_tiktok_product_daily
            ADD COLUMN video_views BIGINT
    """)

    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_product_daily.active_videos IS
        'Videos ativos do produto no dia. Exposta como "videos" em top_produtos de '
        '/brand-detail. Anulavel de proposito: NULL significa linha ainda nao '
        'retroalimentada pelo backfill, e 0 significa zero medido na fonte. '
        'Gate S3.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_product_daily.video_views IS
        'Views de video do produto no dia. Denominador de gpm em top_produtos de '
        '/brand-detail. Anulavel de proposito: NULL = nao retroalimentada, '
        '0 = zero medido. Gate S3.'
    """)


def downgrade() -> None:
    # Restrito as duas colunas criadas por esta migration.
    op.execute("ALTER TABLE marts.fact_tiktok_product_daily DROP COLUMN IF EXISTS video_views")
    op.execute("ALTER TABLE marts.fact_tiktok_product_daily DROP COLUMN IF EXISTS active_videos")
