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
("publicada antes da 034", nao "sem anuncio"), e `ADD COLUMN` sem default nao
reescreve a tabela. Os CHECKs aceitam NULL, entao validam as linhas existentes
sem tocar nelas.

ESTA MIGRATION NAO E' APLICADA PELO GATE QUE A CRIA
---------------------------------------------------
Ordem segura (humana):
  1. merge;
  2. `alembic upgrade 034` no Neon (hoje em 033);
  3. so' DEPOIS a imagem do publicador com as colunas novas: sem a 034 o
     INSERT dele falha dentro da transacao (revertida, nada gravado).
A API detecta as colunas a cada request: sem a 034 ela responde como antes,
com SKU/titulo nulos.

`downgrade` remove as tres colunas (os CHECKs de coluna saem junto).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "034"
down_revision = "033"
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
