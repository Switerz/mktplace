"""METAS-1 — cria marts.meta_gmv_mensal e marts.meta_gmv_mensal_historico.

POR QUE ESTA TABELA EXISTE
--------------------------
A Torre nao tinha meta comercial no dominio (ver
`docs/INTELIGENCIA_BRAND_V3_PLAN.md`): o unico registro era um arquivo
estatico no frontend (`apps/web/src/lib/goals-data.ts`), copiado de uma
planilha e sem uso em nenhuma tela. Esta tabela guarda a meta de GMV que o
proprio time digita na aba "Metas" da Torre, mes a mes.

GRAO
----
`mes x brand x channel`. Uma linha e' a meta de GMV daquela marca naquele canal
naquele mes. Os totais (por marca, por canal, global) NAO sao gravados: sao
soma, calculada na leitura.

AUSENCIA E' EXPLICITA
---------------------
Celula sem meta nao tem linha. Zero so' existe quando alguem digitou zero —
"sem meta" e "meta zero" nao se confundem, e o atingimento de uma celula sem
meta e' indefinido, nunca 0%.

HISTORICO
---------
A aba nao tem login (decisao do proprietario: qualquer pessoa com o link
edita). Cada alteracao grava valor anterior e valor novo em
`meta_gmv_mensal_historico`, para que um erro de digitacao possa ser
desfeito. Nao se grava quem alterou: nao ha identidade, e IP nao e'
identidade.

NaN
---
`'NaN'::numeric >= 0` e' TRUE no PostgreSQL: a faixa tem `<> 'NaN'` explicito.

ENCADEAMENTO
------------
Nasceu 031 e foi renumerada para 032 ao mesclar a `main`, que ganhou a 031 do
shadow de TikTok Ads: duas 031 abririam duas cabecas no Alembic.

APLICACAO
---------
Operacao manual e explicita no Neon (`alembic upgrade 032`). Reversivel por
`alembic downgrade 031`, que derruba so' estas duas tabelas. A migration nao
insere dados.

PERMISSAO
---------
Estas sao as primeiras tabelas da Torre ESCRITAS pela API. O papel da
`DATABASE_URL` do Render precisa de `SELECT, INSERT, UPDATE, DELETE` em
`meta_gmv_mensal` e `SELECT, INSERT` em `meta_gmv_mensal_historico` (mais
`USAGE` na sequence do historico). O GRANT e' do proprietario, fora da
migration, porque o nome do papel nao e' versionado.
"""
from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None

TABELA = "marts.meta_gmv_mensal"
HISTORICO = "marts.meta_gmv_mensal_historico"

#: Marcas e canais aceitos. Listas FECHADAS por CHECK, escritas LITERALMENTE no
#: DDL; `tests/test_metas.py` confere que o servico usa as mesmas.
MARCAS = ("apice", "barbours", "kokeshi", "lescent", "rituaria")
CANAIS = ("tiktok", "ml", "shopee")

UPGRADE_SQL = [
    f"""
    CREATE TABLE {TABELA} (
        mes         DATE           NOT NULL,
        brand       TEXT           NOT NULL,
        channel     TEXT           NOT NULL,
        meta_gmv    NUMERIC(14, 2) NOT NULL,
        updated_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
        CONSTRAINT pk_mgm PRIMARY KEY (mes, brand, channel),
        CONSTRAINT ck_mgm_mes_dia_1 CHECK (mes = date_trunc('month', mes)::date),
        CONSTRAINT ck_mgm_brand CHECK (brand IN ('apice', 'barbours', 'kokeshi', 'lescent', 'rituaria')),
        CONSTRAINT ck_mgm_channel CHECK (channel IN ('tiktok', 'ml', 'shopee')),
        CONSTRAINT ck_mgm_meta_faixa
            CHECK (meta_gmv <> 'NaN' AND meta_gmv >= 0 AND meta_gmv < 10000000000)
    )
    """,
    f"""
    CREATE TABLE {HISTORICO} (
        id             BIGSERIAL      PRIMARY KEY,
        mes            DATE           NOT NULL,
        brand          TEXT           NOT NULL,
        channel        TEXT           NOT NULL,
        meta_anterior  NUMERIC(14, 2),
        meta_nova      NUMERIC(14, 2),
        alterado_em    TIMESTAMPTZ    NOT NULL DEFAULT now(),
        CONSTRAINT ck_mgmh_houve_valor
            CHECK (meta_anterior IS NOT NULL OR meta_nova IS NOT NULL),
        CONSTRAINT ck_mgmh_mudou
            CHECK (meta_anterior IS DISTINCT FROM meta_nova)
    )
    """,
    f"CREATE INDEX ix_mgmh_mes ON {HISTORICO} (mes, alterado_em DESC)",
]

DOWNGRADE_SQL = [
    f"DROP TABLE IF EXISTS {HISTORICO}",
    f"DROP TABLE IF EXISTS {TABELA}",
]


def upgrade() -> None:
    for sql in UPGRADE_SQL:
        op.execute(sql)


def downgrade() -> None:
    for sql in DOWNGRADE_SQL:
        op.execute(sql)
