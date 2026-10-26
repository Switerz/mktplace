"""TIKTOK-ADS-PRODUCTIZATION-1 — shadow diario de TikTok Ads (integrado).

NAO APLICADA. Aplicar e' gate proprio (`alembic upgrade 031` no Neon).
Reversivel por `alembic downgrade 030`. A migration nao insere dados.

POR QUE SHADOW
--------------
Mesmo motivo da 027: `marts.fact_marketplace_daily_performance.ad_spend` e' lido
por dezenas de consumidores (Canais, DRE, executivo). Tabelas proprias que
NENHUM consumidor le' tornam a dupla contagem impossivel por construcao. Ligar
o escopo `shop` ao `ad_spend` do TikTok e' gate proprio, depois de conferir
no Ads Manager.

DUAS TABELAS
------------
- `marts.fact_tiktok_ads_daily_shadow`: grao campanha x dia, chave
  (advertiser_id, campaign_id, stat_date, moeda). So' linhas que a fonte
  DEVOLVEU (`spend = 0` aqui e' zero MEDIDO).
- `marts.fact_tiktok_ads_coverage_shadow`: anunciante x dia CONSULTADO.
  `linhas = 0` e' "consultado sem linha" -- nao e' spend zero. Dia que nao
  esta aqui nao foi consultado: ausencia, nunca zero.
  A FK da fato para a cobertura impede, no banco, linha medida sem consulta.

MOEDA NA CHAVE (NULL NAO DEDUPLICA)
-----------------------------------
A moeda da fonte e' NULL hoje (/advertiser/info deu 40001). Numa UNIQUE,
NULL e' distinto de NULL, e a chave nao deduplicaria. Em vez de
`NULLS NOT DISTINCT` (exige PostgreSQL 15+), a moeda desconhecida e' gravada
como o marcador explicito 'DESCONHECIDA' (NOT NULL + CHECK). Nunca BRL por
suposicao. O fuso fica NULL quando desconhecido (nao entra na chave) e nunca
e' assumido America/Sao_Paulo.

O QUE ENTRA / NAO ENTRA
-----------------------
- So' o relatorio INTEGRADO (leilao), so' apice e barbours (capability do
  probe de 20/10/2026). Outras marcas: CHECK recusa.
- `escopo`: `shop` (PRODUCT_SALES) e' Ads do TikTok Shop; `nao_shop` e'
  midia de site/marca e `desconhecido` e' objetivo sem classificacao. So'
  `shop` pode, num gate futuro, virar ad_spend do TikTok Shop.
- GMV Max: sem fonte. Nunca somado aqui. `fonte` e' fixa em
  'integrado_leilao' por CHECK, para que uma fonte futura nao se misture.
- Comissao de afiliado e recarga do repasse: nao sao midia, nao entram.

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
TABELA_COBERTURA = "marts.fact_tiktok_ads_coverage_shadow"
MOEDA_DESCONHECIDA = "DESCONHECIDA"

UPGRADE_SQL = [
    """
    CREATE TABLE marts.fact_tiktok_ads_coverage_shadow (
        stat_date              DATE          NOT NULL,   -- dia no fuso DA CONTA (desconhecido)
        brand                  TEXT          NOT NULL CHECK (brand IN ('apice', 'barbours')),
        advertiser_id          TEXT          NOT NULL CHECK (advertiser_id <> ''),
        linhas                 INTEGER       NOT NULL CHECK (linhas >= 0),  -- 0 = consultado sem linha
        fonte                  TEXT          NOT NULL DEFAULT 'integrado_leilao'
                                             CHECK (fonte = 'integrado_leilao'),
        source_run_id          TEXT          NOT NULL,
        published_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
        CONSTRAINT pk_ftads_cob PRIMARY KEY (advertiser_id, stat_date)
    )
    """,
    """
    CREATE TABLE marts.fact_tiktok_ads_daily_shadow (
        stat_date              DATE          NOT NULL,   -- dia no fuso DA CONTA (desconhecido)
        brand                  TEXT          NOT NULL CHECK (brand IN ('apice', 'barbours')),
        advertiser_id          TEXT          NOT NULL CHECK (advertiser_id <> ''),
        campaign_id            TEXT          NOT NULL CHECK (campaign_id <> ''),
        escopo                 TEXT          NOT NULL CHECK (escopo IN ('shop', 'nao_shop', 'desconhecido')),
        moeda                  TEXT          NOT NULL
                                             CHECK (moeda = 'DESCONHECIDA' OR moeda ~ '^[A-Z]{3}$'),
        fuso                   TEXT          CHECK (fuso <> ''),   -- NULL = desconhecido
        spend                  NUMERIC(14,2) NOT NULL CHECK (spend >= 0 AND spend <> 'NaN'),
        impressions            BIGINT        NOT NULL CHECK (impressions >= 0),
        clicks                 BIGINT        NOT NULL CHECK (clicks >= 0),
        onsite_shopping        BIGINT        NOT NULL CHECK (onsite_shopping >= 0),
        onsite_shopping_value  NUMERIC(14,2) NOT NULL
                                             CHECK (onsite_shopping_value >= 0 AND onsite_shopping_value <> 'NaN'),
        fonte                  TEXT          NOT NULL DEFAULT 'integrado_leilao'
                                             CHECK (fonte = 'integrado_leilao'),
        source_run_id          TEXT          NOT NULL,
        published_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
        CONSTRAINT pk_ftads PRIMARY KEY (advertiser_id, campaign_id, stat_date, moeda),
        -- linha medida so' existe para anunciante x dia CONSULTADO.
        CONSTRAINT fk_ftads_cobertura FOREIGN KEY (advertiser_id, stat_date)
            REFERENCES marts.fact_tiktok_ads_coverage_shadow (advertiser_id, stat_date)
    )
    """,
    "CREATE INDEX ix_ftads_data ON marts.fact_tiktok_ads_daily_shadow (stat_date DESC, brand)",
    "CREATE INDEX ix_ftads_cobertura ON marts.fact_tiktok_ads_daily_shadow (advertiser_id, stat_date)",
]

DOWNGRADE_SQL = [
    "DROP TABLE IF EXISTS marts.fact_tiktok_ads_daily_shadow",
    "DROP TABLE IF EXISTS marts.fact_tiktok_ads_coverage_shadow",
]


def upgrade() -> None:
    for sql in UPGRADE_SQL:
        op.execute(sql)


def downgrade() -> None:
    for sql in DOWNGRADE_SQL:
        op.execute(sql)
