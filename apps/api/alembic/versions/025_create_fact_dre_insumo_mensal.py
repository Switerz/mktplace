"""Gate DRE-DATA-BRIDGE-1 — cria marts.fact_dre_insumo_mensal.

POR QUE ESTA TABELA EXISTE
--------------------------
A DRE (`/api/v1/dre`, PR #66) precisa de dois insumos que so' existem no Data
Mart: o CMV fiscal (`gold.nf_vendas_plataforma_sku_mensal`) e o repasse TikTok
por coorte de pedido (`silver.stg_tiktok_payments_by_order`). O Render nao
alcanca o Data Mart (Gate G4). Esta fato recebe, de um publisher que roda onde
o Data Mart e' alcancavel, SOMENTE os agregados mensais que a DRE consome — e a
API passa a ler so' o Neon.

O QUE NAO ENTRA
---------------
Nenhum pedido, nota, settlement, SKU ou dado pessoal. O grao e'
`competencia x canal x marca x componente`, e cada componente e' uma soma.

FORMATO LONGO, NAO LARGO
------------------------
Uma linha por componente permite que CADA valor carregue a propria cobertura,
condicao provisoria, status de reconciliacao e data da origem. Numa tabela
larga, o CMV provisorio e o repasse reconciliado dividiriam a mesma linha e um
unico `status` mentiria para um dos dois.

AUSENCIA E' EXPLICITA
---------------------
`valor IS NULL` <=> `status_reconciliacao = 'ausente_na_origem'`, por CHECK. O
publisher grava a ausencia como linha NULL em vez de omitir a chave, para que
uma republicacao APAGUE o valor antigo em vez de deixa-lo parecer atual. Zero
so' entra quando a fonte mediu zero.

SINAL DA ORIGEM, NAO MAGNITUDE
------------------------------
`valor` e' gravado com o sinal da fonte (repasse TikTok negativo, CMV positivo)
e `sinal_origem` diz qual e' a convencao de custo daquela fonte. A
normalizacao acontece num unico ponto, no serving — o mesmo `normalizar_custo`
que recusa sinal inesperado em vez de aplicar `abs()`.

LOTE
----
`lote_id` aponta para `audit.source_sync_run`. As linhas de um lote sao escritas
na MESMA transacao que marca o lote `success`; o serving confere esse status e
a idade de `publicado_em` antes de usar qualquer linha.

NaN
---
`'NaN'::numeric >= 0` e' TRUE no PostgreSQL. Toda coluna numerica com faixa tem
um `<> 'NaN'` explicito.

APLICACAO
---------
Operacao manual e explicita no Neon (`alembic upgrade 025`). Nunca pelo
Airflow. Reversivel por `alembic downgrade 024`, que derruba so' esta tabela.
"""
from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None

TABELA = "marts.fact_dre_insumo_mensal"

#: Componentes publicaveis. Lista FECHADA por CHECK: um publisher com bug nao
#: consegue gravar um componente que o serving nao conhece. O CHECK abaixo e'
#: escrito LITERALMENTE (nenhuma chamada no topo do modulo, regra de
#: `test_migration_nao_tem_efeito_no_import`); um teste confere que as duas
#: listas sao iguais.
COMPONENTES = (
    "cmv",
    "cmv_receita_fiscal",
    "tiktok_taxa_repasse",
    "tiktok_frete_repasse",
    "tiktok_afiliado_repasse",
    "tiktok_receita_repasse",
    "tiktok_residuo_identidade",
    "tiktok_tx_afiliado_maior_taxa",
    "tiktok_transacoes",
)

STATUS = ("reconciliado", "nao_reconciliado", "ausente_na_origem", "nao_avaliado")

UPGRADE_SQL = [
    f"""
    CREATE TABLE {TABELA} (
        competencia           DATE        NOT NULL,
        canal                 TEXT        NOT NULL,
        marca                 TEXT        NOT NULL,
        componente            TEXT        NOT NULL,
        valor                 NUMERIC(18, 2),
        sinal_origem          SMALLINT,
        cobertura             NUMERIC(9, 6),
        provisorio_share      NUMERIC(9, 6),
        status_reconciliacao  TEXT        NOT NULL,
        motivo                TEXT,
        data_max_origem       TIMESTAMPTZ,
        publicado_em          TIMESTAMPTZ NOT NULL,
        lote_id               INT         NOT NULL
                              REFERENCES audit.source_sync_run (sync_run_id),
        procedencia           TEXT        NOT NULL,
        CONSTRAINT pk_fdim PRIMARY KEY (competencia, canal, marca, componente),
        CONSTRAINT ck_fdim_competencia_dia_1
            CHECK (competencia = date_trunc('month', competencia)::date),
        CONSTRAINT ck_fdim_canal CHECK (canal IN ('tiktok', 'ml', 'shopee')),
        CONSTRAINT ck_fdim_marca_nao_vazia CHECK (length(btrim(marca)) > 0),
        CONSTRAINT ck_fdim_componente CHECK (componente IN (
            'cmv', 'cmv_receita_fiscal', 'tiktok_taxa_repasse', 'tiktok_frete_repasse',
            'tiktok_afiliado_repasse', 'tiktok_receita_repasse', 'tiktok_residuo_identidade',
            'tiktok_tx_afiliado_maior_taxa', 'tiktok_transacoes')),
        CONSTRAINT ck_fdim_status CHECK (status_reconciliacao IN (
            'reconciliado', 'nao_reconciliado', 'ausente_na_origem', 'nao_avaliado')),
        CONSTRAINT ck_fdim_ausencia_explicita
            CHECK ((valor IS NULL) = (status_reconciliacao = 'ausente_na_origem')),
        CONSTRAINT ck_fdim_valor_nao_nan CHECK (valor IS NULL OR valor <> 'NaN'),
        CONSTRAINT ck_fdim_sinal CHECK (sinal_origem IS NULL OR sinal_origem IN (-1, 1)),
        CONSTRAINT ck_fdim_cobertura
            CHECK (cobertura IS NULL OR (cobertura <> 'NaN' AND cobertura >= 0 AND cobertura <= 1)),
        CONSTRAINT ck_fdim_provisorio
            CHECK (provisorio_share IS NULL
                   OR (provisorio_share <> 'NaN' AND provisorio_share >= 0 AND provisorio_share <= 1)),
        CONSTRAINT ck_fdim_procedencia_nao_vazia CHECK (length(btrim(procedencia)) > 0)
    )
    """,
    f"CREATE INDEX ix_fdim_lote ON {TABELA} (lote_id)",
    f"""
    COMMENT ON TABLE {TABELA} IS
    'Insumos MENSAIS AGREGADOS da DRE que so existem no Data Mart (CMV fiscal e repasse TikTok '
    'por coorte de pedido). Grao competencia x canal x marca x componente. Sem pedido, nota, '
    'settlement ou dado pessoal. valor NULL = ausente na origem (nunca zero). Escrita so pelo '
    'publisher pipelines.dre.insumo_publisher, em transacao unica com o lote em audit.source_sync_run. '
    'Gate DRE-DATA-BRIDGE-1.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.valor IS
    'Soma com o SINAL DA ORIGEM (ver sinal_origem). NULL = ausente na origem; zero = medido zero.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.sinal_origem IS
    'Convencao de custo da fonte: 1 = custo positivo (CMV), -1 = custo negativo (repasse TikTok). '
    'NULL para contagens e receita.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.publicado_em IS
    'Instante da publicacao. O serving recusa lote mais velho que o limite de frescor.'
    """,
]

DOWNGRADE_SQL = [f"DROP TABLE IF EXISTS {TABELA}"]


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
