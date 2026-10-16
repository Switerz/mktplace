"""Gate DRE-PREMISSAS-1 — cria marts.fact_dre_premissa_mensal.

POR QUE ESTA TABELA EXISTE
--------------------------
A DRE realizada (`/api/v1/dre`) nao tem fonte para impostos, devolucoes,
bonificacao, embalagem, publicidade e outras linhas de despesa. A matriz de
premissas por marca (orcamento) traz esses itens como PERCENTUAL da receita
bruta. Esta tabela guarda SO' esses percentuais, para que a API monte um
cenario complementar chamado explicitamente de "estimado com premissas" — nunca
misturado com a margem de contribuicao conhecida.

O QUE NAO ENTRA
---------------
Nenhum valor monetario, margem, receita, caminho local ou credencial. O grao e'
`competencia x brand x component`, e cada linha e' uma TAXA entre 0 e 1.

AUSENCIA E' EXPLICITA
---------------------
Celula vazia na matriz = ausencia, nunca zero: `rate IS NULL` <=>
`status = 'ausente_na_origem'`, por CHECK. Zero so' entra quando a matriz traz
zero numerico.

SINAL NAO RATIFICADO
--------------------
"Outras Receitas Operacionais" tem rotulo de receita, mas a formula da matriz a
trata como deducao. Ate ratificacao, ela e' armazenada com `somavel = false` e
`status = 'sinal_nao_ratificado'` — por CHECK, nenhum publisher consegue
grava-la como somavel.

SOBREPOSICAO NAO RATIFICADA
---------------------------
"Comissoes" pode se sobrepor as taxas reais de marketplace, que a DRE ja usa.
Ate ratificacao, a taxa fica armazenada e visivel, mas com `somavel = false` e
`status = 'sobreposicao_nao_ratificada'`, tambem travado por CHECK.

PRIVACIDADE DA PROCEDENCIA
--------------------------
`source_note` e' so' um rotulo no formato `Premissas de <mes>/<aaaa>`; o nome do
arquivo de orcamento nunca e' gravado (fica no manifesto privado do publisher).
A data da fonte vai em `source_sent_on`.

COMPETENCIA
-----------
A premissa vale SO' para a competencia gravada. O serving nao a aplica
retroativamente nem a carrega para meses seguintes.

NaN
---
`'NaN'::numeric >= 0` e' TRUE no PostgreSQL: a faixa de `rate` tem `<> 'NaN'`
explicito.

APLICACAO
---------
Operacao manual e explicita no Neon (`alembic upgrade 026`). Reversivel por
`alembic downgrade 025`, que derruba so' esta tabela. A migration nao insere
dados.
"""
from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None

TABELA = "marts.fact_dre_premissa_mensal"

#: Marcas da Torre com premissa. Lista FECHADA por CHECK, escrita
#: LITERALMENTE no DDL (regra de `test_migration_nao_tem_efeito_no_import`);
#: um teste confere que a lista do publisher e' a mesma.
MARCAS = ("kokeshi", "apice", "barbours", "rituaria", "lescent")

#: Componentes percentuais da matriz, na ordem da DRE estimada.
COMPONENTES = (
    "devolucoes_reembolsos",
    "icms",
    "difal",
    "pis",
    "cofins",
    "iss_ipi_simples",
    "outras_receitas_operacionais",
    "frete_primario",
    "bonificacoes",
    "embalagens",
    "provisao_perda_estoque",
    "publicidade_propaganda",
    "comissoes",
    "agencia_growth",
    "despesas_gateway",
    "brindes",
    "logistica_fretes",
    "logistica_operador",
)

STATUS = ("ok", "ausente_na_origem", "sinal_nao_ratificado", "sobreposicao_nao_ratificada")

#: Componentes armazenados mas FORA da soma, com o status que explica por que.
NAO_SOMAVEIS = {
    "outras_receitas_operacionais": "sinal_nao_ratificado",
    "comissoes": "sobreposicao_nao_ratificada",
}

UPGRADE_SQL = [
    f"""
    CREATE TABLE {TABELA} (
        competencia     DATE         NOT NULL,
        brand           TEXT         NOT NULL,
        component       TEXT         NOT NULL,
        rate            NUMERIC(9, 6),
        somavel         BOOLEAN      NOT NULL,
        status          TEXT         NOT NULL,
        source_period   TEXT         NOT NULL,
        source_sha256   TEXT         NOT NULL,
        source_note     TEXT         NOT NULL,
        source_sent_on  DATE         NOT NULL,
        loaded_at       TIMESTAMPTZ  NOT NULL,
        run_id          INT          NOT NULL
                        REFERENCES audit.source_sync_run (sync_run_id),
        CONSTRAINT pk_fdpm PRIMARY KEY (competencia, brand, component),
        CONSTRAINT ck_fdpm_competencia_dia_1
            CHECK (competencia = date_trunc('month', competencia)::date),
        CONSTRAINT ck_fdpm_brand CHECK (brand IN ('kokeshi', 'apice', 'barbours', 'rituaria', 'lescent')),
        CONSTRAINT ck_fdpm_component CHECK (component IN (
            'devolucoes_reembolsos', 'icms', 'difal', 'pis', 'cofins', 'iss_ipi_simples',
            'outras_receitas_operacionais', 'frete_primario', 'bonificacoes', 'embalagens',
            'provisao_perda_estoque', 'publicidade_propaganda', 'comissoes', 'agencia_growth',
            'despesas_gateway', 'brindes', 'logistica_fretes', 'logistica_operador')),
        CONSTRAINT ck_fdpm_status CHECK (status IN (
            'ok', 'ausente_na_origem', 'sinal_nao_ratificado', 'sobreposicao_nao_ratificada')),
        CONSTRAINT ck_fdpm_rate_faixa
            CHECK (rate IS NULL OR (rate <> 'NaN' AND rate >= 0 AND rate <= 1)),
        CONSTRAINT ck_fdpm_ausencia_explicita
            CHECK ((rate IS NULL) = (status = 'ausente_na_origem')),
        CONSTRAINT ck_fdpm_nao_somavel CHECK (
            (component IN ('outras_receitas_operacionais', 'comissoes')) = (NOT somavel)
            AND (component <> 'outras_receitas_operacionais'
                 OR status IN ('sinal_nao_ratificado', 'ausente_na_origem'))
            AND (component <> 'comissoes'
                 OR status IN ('sobreposicao_nao_ratificada', 'ausente_na_origem'))
            AND (status <> 'sinal_nao_ratificado' OR component = 'outras_receitas_operacionais')
            AND (status <> 'sobreposicao_nao_ratificada' OR component = 'comissoes')),
        CONSTRAINT ck_fdpm_sha256 CHECK (source_sha256 ~ '^[0-9a-f]{{64}}$'),
        CONSTRAINT ck_fdpm_periodo_nao_vazio CHECK (length(btrim(source_period)) > 0),
        CONSTRAINT ck_fdpm_nota_rotulo CHECK (source_note ~ (
            '^Premissas de (janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|'
            'novembro|dezembro)/[0-9]{{4}}$'))
    )
    """,
    f"CREATE INDEX ix_fdpm_run ON {TABELA} (run_id)",
    f"""
    COMMENT ON TABLE {TABELA} IS
    'PREMISSAS ESTIMADAS da DRE (orcamento), NAO realizado. Grao competencia x brand x component; '
    'cada linha e uma taxa sobre a receita bruta da propria marca. Sem valor monetario. rate NULL = '
    'ausente na matriz (nunca zero). Vale so para a competencia gravada: sem retroacao e sem '
    'carregar para meses seguintes. Escrita so pelo pipelines.dre.premissa_publisher, em transacao '
    'unica com o lote em audit.source_sync_run. Gate DRE-PREMISSAS-1.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.rate IS
    'Taxa estimada entre 0 e 1 sobre a receita bruta da celula. NULL = ausente; zero = premissa explicita de 0%.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.somavel IS
    'false para outras_receitas_operacionais (sinal nao ratificado) e comissoes (sobreposicao com as '
    'taxas de marketplace nao ratificada): armazenadas e visiveis, fora da margem.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.source_note IS
    'Rotulo publico "Premissas de <mes>/<aaaa>". Nunca o nome do arquivo de orcamento nem um caminho.'
    """,
    f"""
    COMMENT ON COLUMN {TABELA}.source_sent_on IS
    'Data de envio da fonte declarada na matriz.'
    """,
]

DOWNGRADE_SQL = [f"DROP TABLE IF EXISTS {TABELA}"]


def upgrade() -> None:
    for stmt in UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_SQL:
        op.execute(stmt)
