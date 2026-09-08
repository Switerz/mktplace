"""Gate AVH-4A — cria as duas tabelas de snapshot manual da Avoe.

Transcricao funcional de `db/sql/marts/avoe_proxy_snapshot_ddl.sql`, que e' a
unica especificacao executavel. Qualquer divergencia entre este arquivo e aquele
e' defeito DESTE arquivo.

CADEIA LINEAR, SEM BRANCH
-------------------------
Head real de `origin/main` na abertura do gate: `014`
(`014_create_price_monitoring_serving.py`). Nenhuma outra frente reivindica
`015` — verificado em main, daily-d1-hotfix, oracle-mcp, pma-h1,
ui-v3-inteligencia-marca, unit-economics-audit, worktree-gate-dq-tk1 e
worktree-gate-pma-1a, e nao ha outra branch remota alem de origin/main.
Portanto `revision="015"`, `down_revision="014"`: arvore linear, zero branch.

DOIS OBJETOS, E SO DOIS
-----------------------
    marts.proxy_avoe_brand_monthly_target_snapshot
    marts.proxy_avoe_extra_channel_monthly_snapshot

Nenhum objeto oficial e' criado, alterado, renomeado ou indexado por esta
migration. `marts.fact_marketplace_daily_performance` e as dimensoes nao sao
tocadas.

APPEND-ONLY E IMUTAVEL
----------------------
As duas tabelas sao append-only. A PK inclui `captured_at`, de modo que um
snapshot novo coexiste com os anteriores em vez de sobrescreve-los. O
importador nao tem caminho de UPDATE nem de DELETE.

SEM COLUNA DE REALIZADO
-----------------------
Nao existe coluna para GMV, receita ou faturamento apurado pela Torre. O
realizado oficial fica em `marts.fact_marketplace_daily_performance` e e' lido
de la' no momento da consulta. A tabela de metas tambem NAO recebe o campo
`faturamento` da origem (`resumo_marca_mes.faturamento`) — esse e' o realizado
que a Avoe digita, e importa-lo criaria uma segunda verdade de realizado.

PROXY POR CHECK, NAO POR CONVENCAO
----------------------------------
Em `proxy_avoe_extra_channel_monthly_snapshot`, `is_proxy` e' obrigatoriamente
TRUE e `definition_status` obrigatoriamente 'unconfirmed'. A allowlist de
`channel` EXCLUI tiktok, mercado_livre e shopee: por construcao um canal oficial
nao entra nesta tabela, e nao ha soma acidental com o GMV oficial.

MOEDA ASSUMIDA
--------------
A origem nao declara moeda. `currency_code` e `currency_status` sao colunas
distintas, e `currency_status = 'assumed_unconfirmed'` obriga
`currency_warning` preenchido — por CHECK.

VIGENCIA
--------
Metas: `ref_month >= 2026-08-01`. Junho e julho rejeitados por escala
incompativel medida no AVH-3B. Canais adicionais: `ref_month >= 2026-06-01`,
porque o regime diario da fonte comeca em 2026-06-10 e antes disso o campo e'
uma janela movel de 28 dias, nao aditiva. Ambos os CHECKs sao estreitos de
proposito: afrouxar exige migration, nao flag de runtime.

ZERO PII
--------
Nenhuma coluna de pessoa, cliente, pedido, usuario, documento ou endereco. As
unicas entidades sao marca, canal e competencia.

Revision ID: 015
Revises: 014
Create Date: 2026-09-08
"""
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


TARGET_SNAPSHOT_DDL = """
CREATE TABLE marts.proxy_avoe_brand_monthly_target_snapshot (
    source                  TEXT        NOT NULL,
    captured_at             TIMESTAMPTZ NOT NULL,
    ref_month               DATE        NOT NULL,
    brand                   TEXT        NOT NULL,
    brand_key               TEXT        NULL,
    target_amount           NUMERIC(18,2) NOT NULL,
    currency_code           TEXT        NOT NULL,
    currency_status         TEXT        NOT NULL,
    currency_warning        TEXT        NULL,
    source_recorded_at      TIMESTAMPTZ NULL,
    source_file             TEXT        NOT NULL,
    source_file_hash        TEXT        NOT NULL,
    snapshot_id             TEXT        NOT NULL,
    import_run_id           TEXT        NOT NULL,
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_proxy_avoe_target_snapshot
        PRIMARY KEY (source, captured_at, ref_month, brand),
    CONSTRAINT ck_pabmts_source
        CHECK (source = 'avoe_hub'),
    CONSTRAINT ck_pabmts_ref_month_truncado
        CHECK (ref_month = date_trunc('month', ref_month)::date),
    CONSTRAINT ck_pabmts_vigencia
        CHECK (ref_month >= DATE '2026-08-01'),
    CONSTRAINT ck_pabmts_target_amount
        CHECK (target_amount >= 0 AND target_amount <> 'NaN'),
    CONSTRAINT ck_pabmts_currency_code
        CHECK (currency_code = 'BRL'),
    CONSTRAINT ck_pabmts_currency_status
        CHECK (currency_status IN ('confirmed', 'assumed_unconfirmed')),
    CONSTRAINT ck_pabmts_currency_warning_obrigatorio
        CHECK (currency_status <> 'assumed_unconfirmed'
               OR (currency_warning IS NOT NULL AND btrim(currency_warning) <> '')),
    CONSTRAINT ck_pabmts_brand_nao_vazio
        CHECK (btrim(brand) <> ''),
    CONSTRAINT ck_pabmts_brand_key_nao_vazio
        CHECK (brand_key IS NULL OR btrim(brand_key) <> ''),
    CONSTRAINT ck_pabmts_source_file_nao_vazio
        CHECK (btrim(source_file) <> ''),
    CONSTRAINT ck_pabmts_snapshot_id_nao_vazio
        CHECK (btrim(snapshot_id) <> ''),
    CONSTRAINT ck_pabmts_import_run_id_nao_vazio
        CHECK (btrim(import_run_id) <> ''),
    CONSTRAINT ck_pabmts_source_file_hash
        CHECK (source_file_hash ~ '^[0-9a-f]{64}$')
)
"""

EXTRA_CHANNEL_SNAPSHOT_DDL = """
CREATE TABLE marts.proxy_avoe_extra_channel_monthly_snapshot (
    source                  TEXT        NOT NULL,
    captured_at             TIMESTAMPTZ NOT NULL,
    ref_month               DATE        NOT NULL,
    brand                   TEXT        NOT NULL,
    channel                 TEXT        NOT NULL,
    brand_key               TEXT        NULL,
    channel_source_label    TEXT        NOT NULL,
    reported_amount         NUMERIC(18,2) NULL,
    is_proxy                BOOLEAN     NOT NULL,
    definition_status       TEXT        NOT NULL,
    definition_warning      TEXT        NOT NULL,
    currency_code           TEXT        NOT NULL,
    currency_status         TEXT        NOT NULL,
    days_covered            INTEGER     NOT NULL,
    first_business_date     DATE        NOT NULL,
    last_business_date      DATE        NOT NULL,
    coverage_status         TEXT        NOT NULL,
    source_recorded_at      TIMESTAMPTZ NULL,
    source_file             TEXT        NOT NULL,
    source_file_hash        TEXT        NOT NULL,
    snapshot_id             TEXT        NOT NULL,
    import_run_id           TEXT        NOT NULL,
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_proxy_avoe_extra_channel_snapshot
        PRIMARY KEY (source, captured_at, ref_month, brand, channel),
    CONSTRAINT ck_paecms_source
        CHECK (source = 'avoe_hub'),
    CONSTRAINT ck_paecms_ref_month_truncado
        CHECK (ref_month = date_trunc('month', ref_month)::date),
    CONSTRAINT ck_paecms_vigencia
        CHECK (ref_month >= DATE '2026-06-01'),
    CONSTRAINT ck_paecms_reported_amount
        CHECK (reported_amount IS NULL
               OR (reported_amount >= 0 AND reported_amount <> 'NaN')),
    CONSTRAINT ck_paecms_is_proxy
        CHECK (is_proxy IS TRUE),
    CONSTRAINT ck_paecms_definition_status
        CHECK (definition_status = 'unconfirmed'),
    CONSTRAINT ck_paecms_definition_warning_obrigatorio
        CHECK (btrim(definition_warning) <> ''),
    CONSTRAINT ck_paecms_channel_allowlist
        CHECK (channel IN ('magalu', 'shein', 'kwai',
                           'beleza_na_web', 'rd_marketplace', 'amazon')),
    CONSTRAINT ck_paecms_currency_code
        CHECK (currency_code = 'BRL'),
    CONSTRAINT ck_paecms_currency_status
        CHECK (currency_status IN ('confirmed', 'assumed_unconfirmed')),
    CONSTRAINT ck_paecms_days_covered
        CHECK (days_covered >= 1),
    CONSTRAINT ck_paecms_datas_na_competencia
        CHECK (first_business_date <= last_business_date
               AND first_business_date >= ref_month
               AND last_business_date < (ref_month + INTERVAL '1 month')::date),
    CONSTRAINT ck_paecms_coverage_status
        CHECK (coverage_status IN ('partial_month', 'full_month')),
    CONSTRAINT ck_paecms_brand_nao_vazio
        CHECK (btrim(brand) <> ''),
    CONSTRAINT ck_paecms_brand_key_nao_vazio
        CHECK (brand_key IS NULL OR btrim(brand_key) <> ''),
    CONSTRAINT ck_paecms_channel_source_label_nao_vazio
        CHECK (btrim(channel_source_label) <> ''),
    CONSTRAINT ck_paecms_source_file_nao_vazio
        CHECK (btrim(source_file) <> ''),
    CONSTRAINT ck_paecms_snapshot_id_nao_vazio
        CHECK (btrim(snapshot_id) <> ''),
    CONSTRAINT ck_paecms_import_run_id_nao_vazio
        CHECK (btrim(import_run_id) <> ''),
    CONSTRAINT ck_paecms_source_file_hash
        CHECK (source_file_hash ~ '^[0-9a-f]{64}$')
)
"""

INDEXES = [
    """CREATE INDEX idx_pabmts_ref_month_brand
           ON marts.proxy_avoe_brand_monthly_target_snapshot (ref_month, brand)""",
    """CREATE INDEX idx_pabmts_captured_at
           ON marts.proxy_avoe_brand_monthly_target_snapshot (captured_at DESC)""",
    """CREATE INDEX idx_pabmts_brand_ref_month
           ON marts.proxy_avoe_brand_monthly_target_snapshot (brand, ref_month)""",
    """CREATE INDEX idx_paecms_ref_month_channel_brand
           ON marts.proxy_avoe_extra_channel_monthly_snapshot (ref_month, channel, brand)""",
    """CREATE INDEX idx_paecms_captured_at
           ON marts.proxy_avoe_extra_channel_monthly_snapshot (captured_at DESC)""",
    """CREATE INDEX idx_paecms_brand_ref_month
           ON marts.proxy_avoe_extra_channel_monthly_snapshot (brand, ref_month)""",
]

COMMENTS = [
    """COMMENT ON TABLE marts.proxy_avoe_brand_monthly_target_snapshot IS
       'Gate AVH-4A. Meta mensal por marca informada pela Avoe, snapshot imutavel append-only. NAO contem realizado: o realizado oficial vive em marts.fact_marketplace_daily_performance e e'' consultado na leitura. Vigencia a partir de 2026-08-01; junho/julho rejeitados por escala incompativel. Moeda BRL assumida, nao confirmada pela fonte.'""",
    """COMMENT ON COLUMN marts.proxy_avoe_brand_monthly_target_snapshot.captured_at IS
       'Momento da captura do snapshot na origem. E'' o criterio de versao — nunca imported_at.'""",
    """COMMENT ON COLUMN marts.proxy_avoe_brand_monthly_target_snapshot.brand_key IS
       'Chave da Torre, por mapa explicito. NULL quando nao ha regra declarada.'""",
    """COMMENT ON TABLE marts.proxy_avoe_extra_channel_monthly_snapshot IS
       'Gate AVH-4A. "Faturamento informado pela Avoe" para canais que a Torre nao cobre, snapshot imutavel append-only. NAO e'' GMV oficial: is_proxy sempre TRUE e definition_status sempre unconfirmed, por CHECK. A allowlist de channel exclui tiktok/mercado_livre/shopee. reported_amount NULL e'' indisponivel, nunca 0.'""",
    """COMMENT ON COLUMN marts.proxy_avoe_extra_channel_monthly_snapshot.reported_amount IS
       'Faturamento informado pela Avoe. NULL = indisponivel; 0 = zero informado. Nunca somar aos fatos oficiais de marketplace.'""",
    """COMMENT ON COLUMN marts.proxy_avoe_extra_channel_monthly_snapshot.days_covered IS
       'Dias com linha na origem dentro da competencia. Torna auditavel a agregacao diaria->mensal.'""",
]


def upgrade() -> None:
    op.execute(TARGET_SNAPSHOT_DDL)
    op.execute(EXTRA_CHANNEL_SNAPSHOT_DDL)
    for stmt in INDEXES:
        op.execute(stmt)
    for stmt in COMMENTS:
        op.execute(stmt)


def downgrade() -> None:
    # Somente os dois objetos criados aqui. Nenhum objeto oficial e' tocado.
    op.execute("DROP TABLE marts.proxy_avoe_extra_channel_monthly_snapshot")
    op.execute("DROP TABLE marts.proxy_avoe_brand_monthly_target_snapshot")
