"""Atraso de 7 dias por conta no resumo da Expedicao (regra oficial da Shopee).

Acrescenta quatro colunas NULLABLE a `marts.expedicao_refresh_run` (criada
pela 018):

    due_7d_count          INTEGER  -- base: pedidos (Shopee) / envios (ML) com
                                   -- vencimento num dia (Brasilia) da janela
    late_7d_count         INTEGER  -- quantos da base atrasaram
    late_7d_window_start  DATE     -- janela EFETIVAMENTE usada (dias de
    late_7d_window_end    DATE     -- vencimento, Brasilia)

A janela nominal e' `[hoje - 7, hoje - 1]` em Brasilia, `hoje` = dia BRT do
`effective_at` da publicacao. O calculo vive em `pipelines/expedicao/atraso_7d.py`
e a regra esta' em `docs/expedicao_contrato.md` ("Atraso de 7 dias").

NULL nas quatro = nao calculado (canal ou conta sem dado, linha anterior a
esta migration ou publicador antigo). Nunca zero por ausencia. Sem DEFAULT: as
linhas existentes ficam NULL, que e' exatamente o que elas sao. Em
PostgreSQL 11+ `ADD COLUMN` sem default nao reescreve a tabela.

`marts.expedicao_refresh_run_export` (034, export da Kokeshi) NAO recebe as
colunas: o export nao calcula o atraso de 7 dias, e o publicador grava as
colunas so' onde as quatro existem.

`IF NOT EXISTS` / `IF EXISTS`: reaplicar o upgrade (ou o downgrade) nao falha.

ESTA MIGRATION NAO E' APLICADA PELO GATE QUE A CRIA
---------------------------------------------------
Ordem segura em producao:

  1. merge;
  2. imagem do Airflow com o publicador novo. Ele detecta as quatro colunas em
     `information_schema.columns` a cada execucao: sem a 035 o INSERT do
     resumo e' o de antes, entao o passo 2 pode vir antes do 3;
  3. `alembic upgrade 035` no Neon;
  4. a proxima publicacao horaria de cada canal preenche as colunas.

`downgrade` remove as quatro colunas. O publicador volta sozinho ao
comportamento antigo na execucao seguinte.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None

RESUMO_Q = "marts.expedicao_refresh_run"

#: Colunas, na ordem do contrato (`contract.ATRASO_7D_COLUMNS`).
COLUNAS = (
    ("due_7d_count", "INTEGER"),
    ("late_7d_count", "INTEGER"),
    ("late_7d_window_start", "DATE"),
    ("late_7d_window_end", "DATE"),
)


def upgrade() -> None:
    for nome, tipo in COLUNAS:
        op.execute(
            f"ALTER TABLE {RESUMO_Q} ADD COLUMN IF NOT EXISTS {nome} {tipo} NULL"
        )


def downgrade() -> None:
    # Remove SOMENTE o que o upgrade criou, na ordem inversa.
    for nome, _tipo in reversed(COLUNAS):
        op.execute(f"ALTER TABLE {RESUMO_Q} DROP COLUMN IF EXISTS {nome}")
