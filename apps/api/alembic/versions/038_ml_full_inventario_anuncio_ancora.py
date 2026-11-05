"""T-0018 - anuncio-ancora por inventario no estoque Full do ML.

Acrescenta tres colunas a `marts.fact_ml_full_stock_inventory_daily` (029):

    anuncio_ancora_item_id       TEXT     NULL  -- item do anuncio-ancora
    anuncio_ancora_variation_id  TEXT     NULL  -- variacao do ancora; NULL = sem variacao
    anuncios_extras              INTEGER  NULL  -- anuncios distintos do inventario - 1

Servem SO' para IDENTIFICAR o inventario na tela (SKU e titulo vem da 014,
`marts.fact_marketplace_listing_price_daily`, por item, na API). O grao
continua (ref_date, brand, seller_id, inventory_id): nenhuma linha nasce,
nenhuma some, e o estoque nunca e' somado por anuncio.

Regra do ancora (publicador, `pipelines/transforms/ml_full_stock.py::ancora`):
Full e `active`, depois Full, depois qualquer; desempate pelo menor `item_id`
e depois pelo menor `variation_id`, como texto.

NULAS E SEM DEFAULT, de proposito: as linhas ja' publicadas ficam NULL
("publicada antes da 038", nao "sem anuncio"), e `ADD COLUMN` sem default nao
reescreve a tabela. Os CHECKs aceitam NULL, entao validam as linhas existentes
sem tocar nelas.

NUMERACAO
---------
Nasceu 034 (branch `feat/T-0018-ml-full-sku-titulo`), foi renumerada para 037
sobre a 036 e depois para 038 sobre a 037: a `main` ja' tinha 034
(expedicao_shopee_export), 035 (expedicao_atraso_7d), 036 (cx_kokeshi_export)
e 037 (dre_insumo_taxa_shopee_escrow, T-0059, que so' troca o CHECK de
componentes de `marts.fact_dre_insumo_mensal` e nao toca esta tabela).

ESTA MIGRATION NAO E' APLICADA PELO GATE QUE A CRIA
---------------------------------------------------
O head do Neon e' lido por OUTROS publicadores: a barreira do PMA
(`pipelines.channel_offer_sync` / `assert_apply_authorized`) recusa um head
que o grafo da imagem nao conhece. Aplicar esta DDL antes de os workers
conhecerem a 038 repete o apagao da passagem para a 033. A 037 (T-0059) e a
038 sobem numa janela UNICA. Ordem segura (humana; detalhe em
`docs/ml_full_stock_runbook.md`, secao 7.2):
  a. merge em `main` (inerte);
  b. bump do pin `MKTPLACE_PIPELINES_SHA` do airflow para a `main` que contem
     a 037 e a 038 (o grafo da imagem conhece as duas), com rollout. NAO
     disparar o `publicar` da DAG `mercadolivre_full_stock_neon_publish`
     entre b e c;
  c. so' depois do rollout, `alembic upgrade 038` no Neon (em 036: aplica a
     037 e a 038);
  c2. a identidade do destino inclui `alembic_version_num`: rodar a DAG em
     modo `identificar`, conferir que so' o alembic mudou e atualizar a
     Variable `ML_FULL_NEON_DESTINO_ESPERADO` (edicao humana);
  d. deploy da API;
  e. republicar as datas que devem ganhar a ancora.

Compatibilidade:
  - publicador anterior a' T-0018 funciona com ou sem a 038: as colunas sao
    NULL-aveis sem default e ele nao as escreve;
  - a API detecta as colunas a cada request: sem a 038 responde como antes,
    com SKU/titulo nulos;
  - GATE: o publicador novo NAO grava antes desta DDL. Sem ela o INSERT falha
    dentro da transacao (revertida, nada gravado).

`downgrade` remove as tres colunas (os CHECKs de coluna saem junto).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None

INVENTARIO = "marts.fact_ml_full_stock_inventory_daily"

#: Ordem do upgrade; o downgrade remove na ordem inversa.
COLUNAS = ("anuncio_ancora_item_id", "anuncio_ancora_variation_id", "anuncios_extras")


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {INVENTARIO} ADD COLUMN anuncio_ancora_item_id TEXT NULL "
        "CONSTRAINT ck_fmlfsi_ancora_item_nao_vazio CHECK (btrim(anuncio_ancora_item_id) <> '')"
    )
    op.execute(
        f"ALTER TABLE {INVENTARIO} ADD COLUMN anuncio_ancora_variation_id TEXT NULL "
        "CONSTRAINT ck_fmlfsi_ancora_variacao_nao_vazia CHECK (btrim(anuncio_ancora_variation_id) <> '')"
    )
    op.execute(
        f"ALTER TABLE {INVENTARIO} ADD COLUMN anuncios_extras INTEGER NULL "
        "CONSTRAINT ck_fmlfsi_anuncios_extras CHECK (anuncios_extras >= 0)"
    )


def downgrade() -> None:
    # Remove SOMENTE o que o upgrade criou, na ordem inversa.
    op.execute(f"ALTER TABLE {INVENTARIO} DROP COLUMN IF EXISTS anuncios_extras")
    op.execute(f"ALTER TABLE {INVENTARIO} DROP COLUMN IF EXISTS anuncio_ancora_variation_id")
    op.execute(f"ALTER TABLE {INVENTARIO} DROP COLUMN IF EXISTS anuncio_ancora_item_id")
