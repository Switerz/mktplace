"""Gate SHOPEE-API-PRODUTOS-2 — procedencia por linha em fact_shopee_product_monthly.

POR QUE ESTA MIGRATION EXISTE
------------------------------
A tabela vai passar a receber DUAS fontes ao mesmo tempo:

  · `api`           -> gold.shopee_product_daily (Data Mart), 4 contas
  · `manual_export` -> export XLSX do Seller Center, hoje a unica, e o unico
                       caminho da `kokeshi` (que nao tem aplicacao Shopee)

Sem uma coluna de procedencia, as duas ficariam indistinguiveis na mesma
tabela. E indistinguivel e' pior do que separado: quem lesse um numero nao
saberia se ele vem de uma fonte que MATURA sozinha (a API relê o estado a cada
6h) ou de uma fotografia congelada no dia da exportacao. Essa confusao ja'
custou caro nesta tabela -- ver `is_partial` abaixo.

AS QUATRO COLUNAS
------------------
`source`             procedencia da linha. NOT NULL, com CHECK de dominio
                     fechado. O default e' `manual_export` porque e' o que TODAS
                     as linhas existentes sao: o backfill nao e' suposicao, e'
                     a descricao do passado.

`source_run_id`      execucao que gravou a linha. NULL nas linhas historicas,
                     porque nenhuma execucao rastreada as gravou -- inventar um
                     id para elas seria fabricar procedencia.

`source_captured_at` instante em que a FONTE foi lida (nao em que a linha foi
                     escrita). Para a API e' o momento da leitura do gold; e' o
                     que responde "qual era o estado do mundo quando isto foi
                     medido". `ingested_at` continua sendo a hora da escrita, e
                     os dois sao diferentes de proposito.

`is_partial`         a competencia ainda esta ABERTA a maturacao.

🔴 POR QUE `is_partial` E' OBRIGATORIA, E NAO ENFEITE
------------------------------------------------------
Medido em 25/09/2026 sobre as 4 contas da API: o recorte `completed` vale 100%
do `is_sale` em julho e agosto, e apenas **70,6% a 79,1%** em setembro. Ou
seja: o mes corrente SEMPRE le' baixo, e nao por erro -- e o pedido que ainda
nao concluiu.

Sem esta coluna, a unica leitura possivel de um mes corrente baixo seria "as
vendas cairam", que e' falso. Foi exatamente essa confusao que deixou
agosto/2026 com GMV zero nas 5 marcas na tabela atual: o export foi tirado em
25/08, um dia depois do fim da janela, quando quase nada tinha amadurecido para
`Concluido` (agosto 01-24 no export: R$ 555,83 concluidos na barbours e
R$ 0,00 na apice). A tela mostrou zero e ninguem tinha como saber, olhando a
tabela, que aquilo era imaturidade e nao ausencia de venda.

NADA E' REESCRITO
------------------
`ALTER TABLE ... ADD COLUMN` com DEFAULT constante e' metadata-only no
PostgreSQL >= 11: nao reescreve a tabela e nao toma lock longo. Os dois CHECK
entram DEPOIS do backfill do default, entao nenhuma linha existente os viola.

O `DEFAULT` de `source` e' REMOVIDO ao final, de proposito: com ele, um INSERT
que esquecesse a coluna herdaria `manual_export` em silencio -- e uma linha da
API rotulada como export e' pior do que um INSERT que falha. Depois desta
migration, `source` e' obrigatorio e explicito em toda escrita nova.
"""
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

#: Dominio fechado de `source`. Valor novo exige migration -- e' o ponto.
SOURCES = ("api", "manual_export")


def upgrade() -> None:
    # 1. Colunas. `source` nasce com DEFAULT para o backfill das linhas
    #    existentes ser feito pelo proprio ALTER, sem UPDATE em massa.
    op.execute("""
        ALTER TABLE marts.fact_shopee_product_monthly
            ADD COLUMN IF NOT EXISTS source             VARCHAR(32)
                NOT NULL DEFAULT 'manual_export',
            ADD COLUMN IF NOT EXISTS source_run_id      VARCHAR(64),
            ADD COLUMN IF NOT EXISTS source_captured_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS is_partial         BOOLEAN
                NOT NULL DEFAULT FALSE
    """)

    # 2. Dominio fechado. Entra depois do backfill: nenhuma linha existente o
    #    viola, porque todas receberam o default.
    op.execute("""
        ALTER TABLE marts.fact_shopee_product_monthly
            DROP CONSTRAINT IF EXISTS ck_shopee_prod_source
    """)
    op.execute(f"""
        ALTER TABLE marts.fact_shopee_product_monthly
            ADD CONSTRAINT ck_shopee_prod_source
            CHECK (source IN ({', '.join(f"'{s}'" for s in SOURCES)}))
    """)

    # 3. Coerencia da procedencia: linha de `api` TEM de dizer quando a fonte
    #    foi lida. Sem isto, uma linha da API sem `source_captured_at` seria
    #    indistinguivel de uma linha historica, e a coluna perderia o sentido.
    #    A recíproca nao vale: linha `manual_export` pode nao ter instante de
    #    captura, porque as historicas nao tem.
    op.execute("""
        ALTER TABLE marts.fact_shopee_product_monthly
            DROP CONSTRAINT IF EXISTS ck_shopee_prod_api_tem_captura
    """)
    op.execute("""
        ALTER TABLE marts.fact_shopee_product_monthly
            ADD CONSTRAINT ck_shopee_prod_api_tem_captura
            CHECK (source <> 'api' OR source_captured_at IS NOT NULL)
    """)

    # 4. O publisher apaga e reescreve por (brand, ref_month, source). O indice
    #    existente e' (brand, ref_month) e serve de prefixo, mas incluir `source`
    #    e' o que mantem o DELETE do publisher restrito a SUA fonte sem varrer
    #    as linhas da outra.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shopee_prod_brand_month_source
            ON marts.fact_shopee_product_monthly (brand, ref_month, source)
    """)

    # 5. Tira o DEFAULT. A partir daqui, escrever sem dizer a procedencia falha.
    op.execute("""
        ALTER TABLE marts.fact_shopee_product_monthly
            ALTER COLUMN source DROP DEFAULT
    """)

    # 6. O INTERRUPTOR POR MARCA.
    #
    #    🔴 E' isto que torna a migracao gradual e o rollback INSTANTANEOS. As
    #    duas procedencias convivem na fato; quem decide qual delas a tela le'
    #    e' esta tabela, marca a marca. Ligar a `apice` e' um UPDATE de uma
    #    linha; desligar tambem. Nao ha carga, nao ha DELETE, nao ha deploy.
    #
    #    E' tambem o que garante que API e arquivo NUNCA se somam: a leitura
    #    casa `fact.source = mode.source` por marca, entao para cada
    #    (marca, competencia) exatamente UMA procedencia entra no resultado.
    #    Sem este join, um `SUM(gmv)` sobre a fato contaria as duas.
    #
    #    A marca AUSENTE desta tabela nao e' "sem modo": a leitura trata
    #    ausencia como `manual_export`, que e' o comportamento de hoje. Uma
    #    marca nova nunca nasce lendo uma fonte que ninguem ligou.
    op.execute("""
        CREATE TABLE IF NOT EXISTS marts.shopee_product_source_mode (
            brand       VARCHAR(64)  PRIMARY KEY,
            source      VARCHAR(32)  NOT NULL,
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            note        VARCHAR(512)
        )
    """)
    op.execute(f"""
        ALTER TABLE marts.shopee_product_source_mode
            ADD CONSTRAINT ck_shopee_mode_source
            CHECK (source IN ({', '.join(f"'{s}'" for s in SOURCES)}))
    """)

    # Todas as cinco marcas nascem em `manual_export` — inclusive as quatro que
    # a API cobre. A migration NAO liga nada: ligar e' decisao operacional,
    # feita depois da reconciliacao, uma marca por vez.
    op.execute("""
        INSERT INTO marts.shopee_product_source_mode (brand, source, note)
        VALUES
            ('apice',    'manual_export', 'nasce manual; API cobre esta conta'),
            ('barbours', 'manual_export', 'nasce manual; API cobre esta conta'),
            ('lescent',  'manual_export', 'nasce manual; API cobre esta conta'),
            ('rituaria', 'manual_export', 'nasce manual; API cobre esta conta'),
            ('kokeshi',  'manual_export', 'SEM aplicacao Shopee: manual permanente')
        ON CONFLICT (brand) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS marts.shopee_product_source_mode")
    op.execute("""
        ALTER TABLE marts.fact_shopee_product_monthly
            DROP CONSTRAINT IF EXISTS ck_shopee_prod_api_tem_captura
    """)
    op.execute("""
        ALTER TABLE marts.fact_shopee_product_monthly
            DROP CONSTRAINT IF EXISTS ck_shopee_prod_source
    """)
    op.execute("DROP INDEX IF EXISTS marts.idx_shopee_prod_brand_month_source")
    op.execute("""
        ALTER TABLE marts.fact_shopee_product_monthly
            DROP COLUMN IF EXISTS is_partial,
            DROP COLUMN IF EXISTS source_captured_at,
            DROP COLUMN IF EXISTS source_run_id,
            DROP COLUMN IF EXISTS source
    """)
