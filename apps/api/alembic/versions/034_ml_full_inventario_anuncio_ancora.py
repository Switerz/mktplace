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
O head do Neon e' lido por OUTROS publicadores: a barreira do PMA
(`pipelines.channel_offer_sync` / `assert_apply_authorized`) recusa um head
que o grafo da imagem nao conhece. Aplicar esta DDL antes de os workers
conhecerem a 034 repete o apagao da passagem para a 033. Ordem segura
(humana; detalhe em `docs/ml_full_stock_runbook.md`, secao 7.2):
  1. merge em `main`. Nada roda: nenhuma DAG chama o publicador do ML Full
     e o pacote humano de publicacao tem `-Commit` fixo em `bad69cf`, que
     nao conhece a 034;
  2. pin da imagem do airflow (`MKTPLACE_PIPELINES_SHA`) e do runner Windows
     num commit com a 034 no grafo, com rollout e prova em runtime: a
     barreira do PMA aceita 017 -> 034 e todo publicador que valida o grafo
     continua passando. Gate humano do supervisor, coordenado com a frente
     FRESHNESS/PMA;
  3. so' entao `alembic upgrade 034` no Neon (hoje em 033), DDL humana, fora
     da janela das cadeias;
  4. so' depois o publicador novo do ML Full pode ser usado: o `-Commit` do
     pacote humano passa a um commit com a T-0018, em revisao separada.

Compatibilidade:
  - o pacote aprovado (`bad69cf`) funciona com ou sem a 034: as colunas sao
    NULL-aveis sem default e ele nao as escreve. Piloto RAW e primeira
    publicacao nao dependem da 034;
  - a API detecta as colunas a cada request: sem a 034 responde como antes,
    com SKU/titulo nulos;
  - GATE: o publicador novo NAO roda antes desta DDL. Sem ela o INSERT falha
    dentro da transacao (revertida, nada gravado), mas isso e' trava, nao
    risco aceito: o passo 4 so' vem depois do 3.

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
