"""TIKTOK-ADS-PRODUCTIZATION-1 — shadow diario de TikTok Ads (integrado).

NAO APLICADA. Aplicar e' gate proprio (`alembic upgrade 031` no Neon).
Reversivel por `alembic downgrade 030`. A migration nao insere dados.

POR QUE SHADOW
--------------
Mesmo motivo da 027: `marts.fact_marketplace_daily_performance.ad_spend` e' lido
por dezenas de consumidores (Canais, DRE, executivo). Uma tabela propria que
NENHUM consumidor le' torna a dupla contagem impossivel por construcao. Ligar
o escopo `shop` ao `ad_spend` do TikTok e' gate proprio, depois de conferir
no Ads Manager.

O QUE ENTRA / NAO ENTRA
-----------------------
- So' o relatorio INTEGRADO (leilao), so' apice e barbours (capability do
  probe de 20/10/2026). Outras marcas: CHECK recusa.
- `escopo`: `shop` (PRODUCT_SALES) e' Ads do TikTok Shop; `nao_shop` e' midia
  de site/marca e NAO pode ser lido como TikTok Shop.
- GMV Max: sem fonte. Nunca somado aqui. `fonte` e' fixa em
  'integrado_leilao' por CHECK, para que uma fonte futura nao se misture.
- Comissao de afiliado e recarga do repasse: nao sao midia, nao entram.
- `consultado_sem_linha`: metricas NULAS (CHECK) -- nao e' spend zero.
- Moeda e fuso NULL ate' haver fonte (advertiser/info negado).

CADEIA
------
`down_revision = "030"`: a 030 e' do LDR Shopee (EXPEDICAO-SHOPEE-LDR-1),
mesclada em main antes deste PR, que nasceu como 030 e foi renumerado para
031. O #78 (028, DRE) ainda parte da 027 e, ao ser mesclado, reencadeia
sobre a head vigente e ajusta os pinos de head.

NaN: `'NaN'::numeric >= 0` e' TRUE no PostgreSQL; as faixas tem `<> 'NaN'`.
"""
from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None

TABELA = "marts.fact_tiktok_ads_daily_shadow"

UPGRADE_SQL = [
    """
    CREATE TABLE marts.fact_tiktok_ads_daily_shadow (
        stat_date              DATE          NOT NULL,   -- dia no fuso DA CONTA (desconhecido)
        brand                  TEXT          NOT NULL CHECK (brand IN ('apice', 'barbours')),
        escopo                 TEXT          NOT NULL CHECK (escopo IN ('shop', 'nao_shop', 'desconhecido')),
        estado                 TEXT          NOT NULL CHECK (estado IN ('medido', 'consultado_sem_linha')),
        spend                  NUMERIC(14,2) CHECK (spend >= 0 AND spend <> 'NaN'),
        impressions            BIGINT        CHECK (impressions >= 0),
        clicks                 BIGINT        CHECK (clicks >= 0),
        onsite_shopping        BIGINT        CHECK (onsite_shopping >= 0),
        onsite_shopping_value  NUMERIC(14,2) CHECK (onsite_shopping_value >= 0 AND onsite_shopping_value <> 'NaN'),
        campanhas              INTEGER       CHECK (campanhas >= 1),
        anunciantes            INTEGER       NOT NULL CHECK (anunciantes >= 1),
        moeda                  TEXT,
        fuso                   TEXT,
        fonte                  TEXT          NOT NULL DEFAULT 'integrado_leilao'
                                             CHECK (fonte = 'integrado_leilao'),
        source_run_id          TEXT          NOT NULL,
        published_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
        CONSTRAINT pk_ftads PRIMARY KEY (stat_date, brand, escopo),
        -- medido exige todas as metricas; consultado_sem_linha proibe todas.
        CONSTRAINT ck_ftads_estado_metricas CHECK (
            (estado = 'medido' AND spend IS NOT NULL AND impressions IS NOT NULL AND clicks IS NOT NULL
             AND onsite_shopping IS NOT NULL AND onsite_shopping_value IS NOT NULL AND campanhas IS NOT NULL)
            OR (estado = 'consultado_sem_linha' AND spend IS NULL AND impressions IS NULL AND clicks IS NULL
                AND onsite_shopping IS NULL AND onsite_shopping_value IS NULL AND campanhas IS NULL))
    )
    """,
    "CREATE INDEX ix_ftads_data ON marts.fact_tiktok_ads_daily_shadow (stat_date DESC, brand)",
]

DOWNGRADE_SQL = ["DROP TABLE IF EXISTS marts.fact_tiktok_ads_daily_shadow"]


def upgrade() -> None:
    for sql in UPGRADE_SQL:
        op.execute(sql)


def downgrade() -> None:
    for sql in DOWNGRADE_SQL:
        op.execute(sql)
