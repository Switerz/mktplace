"""T-0059 — aceita o componente `shopee_taxa_escrow` em marts.fact_dre_insumo_mensal.

POR QUE
-------
Desde o `daily_shopee` (22/10/2026) a fato diaria grava `total_fees` NULL nas
lojas Shopee com API, e a DRE ficou sem taxa Shopee em set/out. A taxa existe
no escrow (Data Mart, `silver.fct_gobeaute_despesas_shopee`); o
`pipelines.dre.insumo_publisher` passa a publica-la, agregada por mes x marca,
como o componente `shopee_taxa_escrow`. O CHECK `ck_fdim_componente` da 025 e'
lista FECHADA e recusaria a linha.

O QUE MUDA
----------
SOMENTE o CHECK `ck_fdim_componente`: a lista da 025, literal e na mesma
ordem, com `shopee_taxa_escrow` no fim. DROP e ADD no MESMO `ALTER TABLE`
(atomico). Nenhuma coluna, linha, indice ou outro CHECK e' tocado. Sem
`IF EXISTS`: sem a 025 aplicada a revisao tem de falhar alto.

O publisher le' a definicao do CHECK no Neon e so' publica o componente quando
ele aparece nela: antes desta revisao, nada muda na publicacao diaria.

DOWNGRADE
---------
Apaga SO' as linhas `componente = 'shopee_taxa_escrow'` (o CHECK antigo as
recusaria) e restaura o CHECK da 025, literal.

APLICACAO
---------
Operacao manual e explicita no Neon (`alembic upgrade 037`). Nunca pelo
Airflow.
"""
from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None

TABELA = "marts.fact_dre_insumo_mensal"

#: Componentes publicaveis DEPOIS desta revisao: os da 025 + `shopee_taxa_escrow`.
#: O CHECK abaixo e' escrito LITERALMENTE (nenhuma chamada no topo do modulo);
#: um teste confere que as listas sao iguais.
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
    "shopee_taxa_escrow",
)

UPGRADE_SQL = [
    f"""
    ALTER TABLE {TABELA}
        DROP CONSTRAINT ck_fdim_componente,
        ADD CONSTRAINT ck_fdim_componente CHECK (componente IN (
            'cmv', 'cmv_receita_fiscal', 'tiktok_taxa_repasse', 'tiktok_frete_repasse',
            'tiktok_afiliado_repasse', 'tiktok_receita_repasse', 'tiktok_residuo_identidade',
            'tiktok_tx_afiliado_maior_taxa', 'tiktok_transacoes', 'shopee_taxa_escrow'))
    """,
]

DOWNGRADE_SQL = [
    f"DELETE FROM {TABELA} WHERE componente = 'shopee_taxa_escrow'",
    f"""
    ALTER TABLE {TABELA}
        DROP CONSTRAINT ck_fdim_componente,
        ADD CONSTRAINT ck_fdim_componente CHECK (componente IN (
            'cmv', 'cmv_receita_fiscal', 'tiktok_taxa_repasse', 'tiktok_frete_repasse',
            'tiktok_afiliado_repasse', 'tiktok_receita_repasse', 'tiktok_residuo_identidade',
            'tiktok_tx_afiliado_maior_taxa', 'tiktok_transacoes'))
    """,
]


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
