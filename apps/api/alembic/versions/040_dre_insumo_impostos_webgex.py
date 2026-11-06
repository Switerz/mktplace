"""T-0069 — aceita os impostos realizados (webgex) em marts.fact_dre_insumo_mensal.

POR QUE
-------
A DRE nao tem imposto sobre venda realizado: o que a API mostra hoje e' uma
premissa (11,6-14,6% da receita fiscal) que omite o DIFAL. O webgex do Data
Mart (`raw.webgex_impostos_gobeaute` + `raw.webgex_impostos_ap`, circuito `VX`,
debito `DB`) tem o imposto de cada NF de venda; o
`pipelines.dre.insumo_publisher` passa a publica-lo, agregado por mes x canal x
marca, em tres componentes: `imposto_icms`, `imposto_difal` e
`imposto_pis_cofins`. O CHECK `ck_fdim_componente` (025, estendido pela 037) e'
lista FECHADA e recusaria a linha.

O QUE MUDA
----------
SOMENTE o CHECK `ck_fdim_componente`: a lista da 037, literal e na mesma
ordem, com os tres componentes no fim. DROP e ADD no MESMO `ALTER TABLE`
(atomico). Nenhuma coluna, linha, indice ou outro CHECK e' tocado. Sem
`IF EXISTS`: sem a 025/037 aplicadas a revisao tem de falhar alto.

O publisher le' a definicao do CHECK no Neon e so' publica os componentes
quando os tres aparecem nela: antes desta revisao, nada muda na publicacao.

DOWNGRADE
---------
NAO apaga dado. Se existir linha de imposto, o downgrade RECUSA com mensagem
clara (quem desce decide, explicitamente, o que fazer com o imposto
publicado). Sem linha de imposto, restaura o CHECK da 037, literal.
Diferente da 037, que apaga as linhas dela no downgrade: aqui o dado e'
financeiro e apagar exige decisao humana.

APLICACAO
---------
Operacao manual e explicita no Neon (`alembic upgrade 040`). Nunca pelo
Airflow. Reversivel por `alembic downgrade 039` (ver DOWNGRADE).
"""
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None

TABELA = "marts.fact_dre_insumo_mensal"

#: Componentes publicaveis DEPOIS desta revisao: os da 037 + os tres impostos.
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
    "imposto_icms",
    "imposto_difal",
    "imposto_pis_cofins",
)

#: Os componentes que esta revisao acrescenta.
NOVOS = ("imposto_icms", "imposto_difal", "imposto_pis_cofins")

UPGRADE_SQL = [
    f"""
    ALTER TABLE {TABELA}
        DROP CONSTRAINT ck_fdim_componente,
        ADD CONSTRAINT ck_fdim_componente CHECK (componente IN (
            'cmv', 'cmv_receita_fiscal', 'tiktok_taxa_repasse', 'tiktok_frete_repasse',
            'tiktok_afiliado_repasse', 'tiktok_receita_repasse', 'tiktok_residuo_identidade',
            'tiktok_tx_afiliado_maior_taxa', 'tiktok_transacoes', 'shopee_taxa_escrow',
            'imposto_icms', 'imposto_difal', 'imposto_pis_cofins'))
    """,
]

DOWNGRADE_SQL = [
    # Guarda: recusa descer com imposto publicado. Nada e' apagado aqui.
    f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM {TABELA}
                   WHERE componente IN ('imposto_icms', 'imposto_difal', 'imposto_pis_cofins')) THEN
            -- Mensagem sem sinal de porcentagem: o texto passa pelo paramstyle do driver.
            RAISE EXCEPTION 'downgrade 040 recusado - {TABELA} tem linhas de imposto (imposto_icms, '
                            'imposto_difal, imposto_pis_cofins). Nada foi apagado. Decida o destino delas '
                            'e remova-as explicitamente antes de descer.';
        END IF;
    END
    $$
    """,
    f"""
    ALTER TABLE {TABELA}
        DROP CONSTRAINT ck_fdim_componente,
        ADD CONSTRAINT ck_fdim_componente CHECK (componente IN (
            'cmv', 'cmv_receita_fiscal', 'tiktok_taxa_repasse', 'tiktok_frete_repasse',
            'tiktok_afiliado_repasse', 'tiktok_receita_repasse', 'tiktok_residuo_identidade',
            'tiktok_tx_afiliado_maior_taxa', 'tiktok_transacoes', 'shopee_taxa_escrow'))
    """,
]


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
