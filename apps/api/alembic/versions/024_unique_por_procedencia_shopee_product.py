"""Gate SHOPEE-API-CUTOVER-2 — a UNIQUE precisa incluir `source`.

O DEFEITO, ENCONTRADO PELO PRIMEIRO `--apply` REAL
----------------------------------------------------
A migration 023 desenhou a tabela para guardar as DUAS procedencias lado a
lado — e a UNIQUE herdada da migration 004 nao permite isso:

    UNIQUE (ref_month, brand, sku_ref_key, product_name)

Sem `source` na chave, a linha `api` colide com a linha `manual_export` do
MESMO produto, mes e marca. Medido em 28/09/2026, na primeira publicacao em
shadow:

    psycopg2.errors.UniqueViolation: duplicate key value violates unique
    constraint "fact_shopee_product_monthly_ref_month_brand_sku_ref_key_pro_key"
    DETAIL: Key (ref_month, brand, sku_ref_key, product_name)=
            (2026-07-01, apice, 01354, Bisnaga Gelatina Crespo Power 50g)
            already exists.

🔑 A publicacao inteira fez ROLLBACK e nada foi escrito — a transacao unica e o
advisory lock funcionaram como projetados. O defeito e' de MODELAGEM, nao de
escrita: a 023 declarou uma convivencia que a chave proibia.

Por que a validacao do publisher nao pegou: ela confere duplicidade DENTRO da
fonte (`validate_contract`), e ali nao havia nenhuma. A colisao e' contra as
linhas do export, que a fonte nao enxerga.

A CORRECAO
-----------
A UNIQUE passa a ser `(ref_month, brand, sku_ref_key, product_name, source)`.
E' o minimo que torna a convivencia possivel sem afrouxar nada: dentro de uma
procedencia, a chave continua exatamente tao estrita quanto antes.

⚠️ QUEM MAIS DEPENDE DESTA CHAVE
`apps/api/etl/load_shopee_products.py` faz
`ON CONFLICT (ref_month, brand, sku_ref_key, product_name)`. Depois desta
migration esse alvo NAO casa mais com nenhum indice unico, e o upsert falharia
com "there is no unique or exclusion constraint matching the ON CONFLICT
specification". O loader e' corrigido no MESMO PR — ele passa a gravar
`source = 'manual_export'` explicitamente e a usar o alvo de cinco colunas.

Isso tambem fecha um buraco que a 023 abriu: `source` ficou NOT NULL e SEM
default (de proposito — linha da API rotulada como export e' pior do que um
INSERT que falha), entao qualquer escritor que nao declare a procedencia
quebra. O loader manual era um desses.

NENHUM DADO MUDA
-----------------
As 3.631 linhas existentes sao todas `manual_export`. Trocar a UNIQUE nao
reescreve linha nenhuma e nao altera nenhum numero servido.
"""
from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None

#: Nome auto-gerado pela migration 004. Longo porque o Postgres trunca em 63
#: caracteres — por isso o `_pro_key` no fim, em vez de `_product_name_key`.
UNIQUE_ANTIGA = "fact_shopee_product_monthly_ref_month_brand_sku_ref_key_pro_key"

#: Nome EXPLICITO, para a proxima migration nao ter de adivinhar a truncagem.
UNIQUE_NOVA = "uq_shopee_prod_mes_marca_sku_nome_fonte"


def upgrade() -> None:
    # A ordem importa: derrubar antes de criar. Criar primeiro daria erro de
    # nome distinto mas indice redundante, e deixaria os dois vivos se algo
    # falhasse no meio.
    op.execute(f"""
        ALTER TABLE marts.fact_shopee_product_monthly
            DROP CONSTRAINT IF EXISTS {UNIQUE_ANTIGA}
    """)
    op.execute(f"""
        ALTER TABLE marts.fact_shopee_product_monthly
            DROP CONSTRAINT IF EXISTS {UNIQUE_NOVA}
    """)
    op.execute(f"""
        ALTER TABLE marts.fact_shopee_product_monthly
            ADD CONSTRAINT {UNIQUE_NOVA}
            UNIQUE (ref_month, brand, sku_ref_key, product_name, source)
    """)


def downgrade() -> None:
    # 🔴 O downgrade so' e' possivel se NAO houver linha `api` publicada: com as
    # duas procedencias na tabela, a chave de quatro colunas seria violada. O
    # `DELETE` das linhas `api` e deliberado e esta aqui porque a alternativa —
    # um downgrade que falha no meio — deixaria a tabela sem UNIQUE nenhuma.
    op.execute("""
        DELETE FROM marts.fact_shopee_product_monthly WHERE source = 'api'
    """)
    op.execute(f"""
        ALTER TABLE marts.fact_shopee_product_monthly
            DROP CONSTRAINT IF EXISTS {UNIQUE_NOVA}
    """)
    op.execute(f"""
        ALTER TABLE marts.fact_shopee_product_monthly
            ADD CONSTRAINT {UNIQUE_ANTIGA}
            UNIQUE (ref_month, brand, sku_ref_key, product_name)
    """)
