"""T-0015 - etapa do pedido na fila aberta da Expedicao.

Acrescenta duas colunas a `marts.expedicao_fila_atual` (criada pela 018):

    etapa        VARCHAR(30) NOT NULL DEFAULT 'nao_publicada'
                 CHECK de dominio FECHADO (ck_efa_dominio_etapa)
    etapa_fonte  VARCHAR(40) NULL   -- order_status (Shopee) ou substatus (ML),
                                    -- bruto, so' para auditoria. Nao e' PII.

O dominio e' o do contrato etapa v1 (`pipelines/expedicao/contract.py`,
`Etapa`), transcrito aqui como literal, como a 018 faz com os dela: a migration
nao importa codigo de pipeline. O teste compara os dois por igualdade.

`nao_publicada` e' o DEFAULT: as linhas ja' publicadas recebem esse valor ao
ganhar a coluna, e ele so' existe enquanto o publicador antigo estiver rodando.
Em PostgreSQL 11+ `ADD COLUMN ... DEFAULT <constante>` nao reescreve a tabela;
o `CHECK` valida as linhas existentes, que tem todas o default.

ESTA MIGRATION NAO E' APLICADA PELO GATE QUE A CRIA
---------------------------------------------------
Ordem segura em producao (humana, secao 4 do contrato):

  1. merge;
  2. pin/imagem do Airflow com o publicador novo. O publicador detecta a
     coluna `etapa` em `information_schema.columns` a cada execucao: sem a 033
     ele escreve exatamente como antes, entao o passo 2 pode vir antes do 3;
  3. `alembic upgrade` da 031 (Ads, PR #92), da 032 (Metas, PR #116) e DEPOIS
     desta 033 - a cadeia e' 033 -> 032 -> 031 e exige as duas na main antes
     (nasceu 032 e foi renumerada quando a 032 da Metas foi mesclada);
  4. a proxima publicacao preenche a etapa.

Nada disso e' aplicado pelo gate: so' o PG descartavel dos testes roda a cadeia.

`downgrade` remove o CHECK e as duas colunas. O publicador volta sozinho ao
comportamento antigo na execucao seguinte.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None

FILA_Q = "marts.expedicao_fila_atual"

#: Dominio fechado de `etapa` (contrato etapa v1, secao 3).
ETAPAS = (
    "a_preparar",
    "aguardando_coleta",
    "a_faturar",
    "a_imprimir",
    "em_separacao",
    "entregue_transportadora",
    "sinalizado_ml",
    "outro",
    "nao_publicada",
)
ETAPA_DEFAULT = "nao_publicada"
CK_ETAPA = "ck_efa_dominio_etapa"


def upgrade() -> None:
    itens = ", ".join(f"'{v}'" for v in ETAPAS)
    op.execute(
        f"ALTER TABLE {FILA_Q} "
        f"ADD COLUMN etapa VARCHAR(30) NOT NULL DEFAULT '{ETAPA_DEFAULT}'"
    )
    op.execute(f"ALTER TABLE {FILA_Q} ADD COLUMN etapa_fonte VARCHAR(40) NULL")
    op.execute(
        f"ALTER TABLE {FILA_Q} ADD CONSTRAINT {CK_ETAPA} CHECK (etapa IN ({itens}))"
    )


def downgrade() -> None:
    # Remove SOMENTE o que o upgrade criou, na ordem inversa.
    op.execute(f"ALTER TABLE {FILA_Q} DROP CONSTRAINT IF EXISTS {CK_ETAPA}")
    op.execute(f"ALTER TABLE {FILA_Q} DROP COLUMN IF EXISTS etapa_fonte")
    op.execute(f"ALTER TABLE {FILA_Q} DROP COLUMN IF EXISTS etapa")
