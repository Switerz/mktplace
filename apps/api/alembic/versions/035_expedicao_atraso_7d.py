"""Atraso de 7 dias por conta no resumo da Expedicao (regra oficial da Shopee).

Acrescenta seis colunas NULLABLE a `marts.expedicao_refresh_run` (criada
pela 018):

    due_7d_count          INTEGER  -- base: pedidos (Shopee) / envios (ML) com
                                   -- vencimento num dia (Brasilia) da janela
    late_7d_count         INTEGER  -- quantos da base atrasaram
    late_7d_window_start  DATE     -- janela EFETIVAMENTE usada (dias de
    late_7d_window_end    DATE     -- vencimento, Brasilia)
    over_48h_7d_count     INTEGER  -- quantos da base passaram de 48 h
                                   -- (coleta, ou agora se aberto, - marco)
    stalled_7d_count      INTEGER  -- quantos da base passaram de 2 x p50 da
                                   -- conta; NULL sem baseline (ML sempre)

A janela nominal e' `[hoje - 7, hoje - 1]` em Brasilia, `hoje` = dia BRT do
`effective_at` da publicacao. O calculo vive em `pipelines/expedicao/atraso_7d.py`
e a regra esta' em `docs/expedicao_contrato.md` ("Atraso de 7 dias").

NULL em todas = nao calculado (canal ou conta sem dado, linha anterior a
esta migration ou publicador antigo). Nunca zero por ausencia. Sem DEFAULT: as
linhas existentes ficam NULL, que e' exatamente o que elas sao. Em
PostgreSQL 11+ `ADD COLUMN` sem default nao reescreve a tabela.

`marts.expedicao_refresh_run_export` (034, export da Kokeshi) recebe as MESMAS
colunas (03/11/2026): a carga do export calcula o atraso de 7 dias com a regra
da Shopee sobre o historico do export ("todos os casos devem ter %").

POR QUE NA 035 E NAO NUMA 036: o PMA de ofertas, na cadeia diaria do Airflow,
recusa um head do Neon que a imagem nao conheca, e o pin da imagem e' 4617c15
(conhece ate' a 035). Uma 036 exigiria outro PR de pin. A 035 ja' estava
aplicada no Neon quando o export ganhou as colunas; o mesmo `ADD COLUMN IF NOT
EXISTS` foi aplicado a' mao no resumo do export, e banco novo recebe tudo pela
035. O head continua 035.

AS DUAS ULTIMAS COLUNAS ENTRARAM DEPOIS, NA MESMA 035 (03/11/2026, aba "Ultimos
7 dias" da Expedicao): `over_48h_7d_count` e `stalled_7d_count` foram criadas
A' MAO no Neon, nas DUAS tabelas de resumo, com o mesmo `ADD COLUMN IF NOT
EXISTS ... INTEGER NULL`, e entraram aqui em `COLUNAS` pelo mesmo motivo de
cima: uma 036 faria o PMA de ofertas recusar o Neon. Banco novo recebe as seis
pela 035; no Neon a 035 ja' aplicada nao roda de novo, e nem precisa. O
publicador so' grava as colunas de 7 dias se as SEIS existirem no destino.

`IF NOT EXISTS` / `IF EXISTS`: reaplicar o upgrade (ou o downgrade) nao falha.

ESTA MIGRATION NAO E' APLICADA PELO GATE QUE A CRIA
---------------------------------------------------
Ordem segura em producao:

  1. merge;
  2. imagem do Airflow com o publicador novo. Ele detecta as seis colunas em
     `information_schema.columns` a cada execucao: sem a 035 o INSERT do
     resumo e' o de antes, entao o passo 2 pode vir antes do 3;
  3. `alembic upgrade 035` no Neon;
  4. a proxima publicacao horaria de cada canal preenche as colunas.

`downgrade` remove as seis colunas. O publicador volta sozinho ao
comportamento antigo na execucao seguinte.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None

RESUMO_Q = "marts.expedicao_refresh_run"
#: Resumo do export da Kokeshi (034), mesmas colunas.
RESUMO_EXPORT_Q = "marts.expedicao_refresh_run_export"

#: Colunas, na ordem do contrato (`contract.ATRASO_7D_COLUMNS`).
COLUNAS = (
    ("due_7d_count", "INTEGER"),
    ("late_7d_count", "INTEGER"),
    ("late_7d_window_start", "DATE"),
    ("late_7d_window_end", "DATE"),
    ("over_48h_7d_count", "INTEGER"),
    ("stalled_7d_count", "INTEGER"),
)


def upgrade() -> None:
    for tabela in (RESUMO_Q, RESUMO_EXPORT_Q):
        for nome, tipo in COLUNAS:
            op.execute(
                f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {nome} {tipo} NULL"
            )


def downgrade() -> None:
    # Remove SOMENTE o que o upgrade criou, na ordem inversa.
    for tabela in (RESUMO_EXPORT_Q, RESUMO_Q):
        for nome, _tipo in reversed(COLUNAS):
            op.execute(f"ALTER TABLE {tabela} DROP COLUMN IF EXISTS {nome}")
