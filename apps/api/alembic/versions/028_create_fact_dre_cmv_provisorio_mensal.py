"""Gate DRE-CMV-CMP-1 — cria marts.fact_dre_cmv_provisorio_mensal.

POR QUE ESTA TABELA EXISTE
--------------------------
O CMV fiscal da DRE (`marts.fact_dre_insumo_mensal`, migration 025) so' chega
depois que o mes fecha. Para a competencia em andamento, o cenario ESTIMADO da
DRE pode usar um CMV PROVISORIO: unidades vendidas (fontes de API) x CMP
unitario por SKU (planilhas de custo, regra entidade x vigencia). Esta tabela
guarda esse provisorio AGREGADO por `competencia x canal x marca`.

NUNCA SUBSTITUI O FISCAL
------------------------
Tabela propria, de proposito. O CMV fiscal continua na 025 e e' sempre
preferido; o serving so' le esta quando o fiscal esta inutilizavel, e rotula a
linha como "CMV provisorio por CMP".

COBERTURA E AUSENCIA
--------------------
`cmv_provisorio` soma so' os SKUs com casamento valido. Sem casamento, o SKU
fica fora (nunca CMP zero) e aparece em `unidades_sem_cmp`/`skus_sem_cmp`.
Celula sem venda na fonte (ex.: Shopee Kokeshi, export manual) e' gravada como
`ausente_na_fonte`, com valores NULL — nunca zero.

KITS
----
`receita_kit_homogeneo` e `receita_kit_misto` medem a receita coberta pela BOM
explicita do Bling (DRE-CMV-CMP-2): kit so' recebe custo com TODOS os
componentes casados. Nunca custo parcial.

O QUE NAO ENTRA
---------------
Nenhum SKU, pedido ou dado pessoal: so' agregados da celula.

APLICACAO
---------
Manual e explicita (`alembic upgrade 027`). Reversivel por `alembic downgrade
026`, que derruba so' esta tabela. A migration nao insere dados.
"""
from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None

TABELA = "marts.fact_dre_cmv_provisorio_mensal"

UPGRADE_SQL = [
    f"""
    CREATE TABLE {TABELA} (
        competencia                 DATE          NOT NULL,
        canal                       TEXT          NOT NULL,
        marca                       TEXT          NOT NULL,
        data_corte                  DATE,
        status                      TEXT          NOT NULL,
        receita_fonte               NUMERIC(18, 2),
        unidades                    NUMERIC(18, 3),
        receita_coberta             NUMERIC(18, 2),
        cobertura                   NUMERIC(9, 6),
        cmv_provisorio              NUMERIC(18, 2),
        unidades_sem_cmp            NUMERIC(18, 3),
        skus_total                  INT           NOT NULL,
        skus_cobertos               INT           NOT NULL,
        skus_conflito               INT           NOT NULL,
        skus_sem_cmp                INT           NOT NULL,
        receita_gtin                NUMERIC(18, 2),
        receita_volume              NUMERIC(18, 2),
        receita_codigo_provisorio   NUMERIC(18, 2),
        receita_carry_forward       NUMERIC(18, 2),
        receita_kit_homogeneo       NUMERIC(18, 2),
        receita_kit_misto           NUMERIC(18, 2),
        confianca                   TEXT,
        regra_temporal              TEXT          NOT NULL,
        fonte_vendas                TEXT          NOT NULL,
        cmp_fontes_sha256           TEXT          NOT NULL,
        publicado_em                TIMESTAMPTZ   NOT NULL,
        run_id                      INT           NOT NULL
                                    REFERENCES audit.source_sync_run (sync_run_id),
        CONSTRAINT pk_fdcp PRIMARY KEY (competencia, canal, marca),
        CONSTRAINT ck_fdcp_competencia_dia_1
            CHECK (competencia = date_trunc('month', competencia)::date),
        CONSTRAINT ck_fdcp_canal CHECK (canal IN ('tiktok', 'ml', 'shopee')),
        CONSTRAINT ck_fdcp_marca CHECK (marca IN ('kokeshi', 'apice', 'barbours', 'rituaria', 'lescent')),
        CONSTRAINT ck_fdcp_status CHECK (status IN ('calculado', 'ausente_na_fonte')),
        CONSTRAINT ck_fdcp_ausencia_explicita CHECK (
            (status = 'ausente_na_fonte') = (receita_fonte IS NULL)
            AND (status <> 'ausente_na_fonte' OR (cmv_provisorio IS NULL AND cobertura IS NULL))),
        CONSTRAINT ck_fdcp_cmv_so_com_cobertura CHECK (
            (cmv_provisorio IS NULL) = (skus_cobertos = 0)),
        CONSTRAINT ck_fdcp_cobertura
            CHECK (cobertura IS NULL OR (cobertura <> 'NaN' AND cobertura >= 0 AND cobertura <= 1)),
        CONSTRAINT ck_fdcp_nao_negativos CHECK (
            (receita_fonte IS NULL OR (receita_fonte <> 'NaN' AND receita_fonte >= 0))
            AND (receita_coberta IS NULL OR (receita_coberta <> 'NaN' AND receita_coberta >= 0))
            AND (cmv_provisorio IS NULL OR (cmv_provisorio <> 'NaN' AND cmv_provisorio >= 0))
            AND skus_total >= 0 AND skus_cobertos >= 0 AND skus_conflito >= 0 AND skus_sem_cmp >= 0
            AND skus_cobertos + skus_sem_cmp = skus_total),
        CONSTRAINT ck_fdcp_coberta_le_fonte CHECK (receita_coberta IS NULL OR receita_coberta <= receita_fonte),
        CONSTRAINT ck_fdcp_confianca CHECK (confianca IS NULL OR confianca IN ('alta', 'media', 'baixa')),
        CONSTRAINT ck_fdcp_regra_temporal CHECK (regra_temporal = 'entidade_vigencia'),
        CONSTRAINT ck_fdcp_sha256 CHECK (cmp_fontes_sha256 ~ '^[0-9a-f]{{64}}$')
    )
    """,
    f"CREATE INDEX ix_fdcp_run ON {TABELA} (run_id)",
    f"""
    COMMENT ON TABLE {TABELA} IS
    'CMV PROVISORIO por CMP (unidades de API x CMP unitario, regra entidade x vigencia), agregado por '
    'competencia x canal x marca. NAO e o CMV fiscal (fact_dre_insumo_mensal) e nunca o substitui: o '
    'serving so o usa quando o fiscal esta inutilizavel. SKU sem casamento fica fora (nunca CMP zero). '
    'Escrita so pelo pipelines.dre.cmv_cmp_provisorio. Gate DRE-CMV-CMP-1.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.cobertura IS
    'receita_coberta / receita_fonte: parcela da receita da fonte com CMP casado. O restante nao tem custo atribuido.'
    """,
]

DOWNGRADE_SQL = [f"DROP TABLE IF EXISTS {TABELA}"]


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
