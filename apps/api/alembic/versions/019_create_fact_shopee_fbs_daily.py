"""Gate FULL-SH-1A-R — cria marts.fact_shopee_fbs_daily.

Materializa o desempenho FBS da Shopee: quanto do negocio passa pelo
fulfillment da Shopee (FBS) contra o envio pelo proprio vendedor (seller),
medido no grao do PEDIDO e classificado pela modalidade OBSERVADA na venda.

A CLASSE VEM DO PEDIDO, NUNCA DO CATALOGO
------------------------------------------
    raw/silver Shopee Orders . fulfillment_flag     <- FONTE UNICA da classe
        fulfilled_by_shopee        -> fbs
        fulfilled_by_local_seller  -> seller

`silver.stg_shopee_products.is_fulfillment_by_shopee` E PROIBIDO aqui, e a
proibicao e' medida, nao estetica. Aquele campo descreve a CONFIGURACAO ATUAL
do anuncio; esta tabela descreve o que ACONTECEU em cada pedido. O gate
FULL-SH-0 mediu a distancia entre as duas coisas:

    333 de 587 itens vendidos (56,7%) aparecem em pedidos FBS *e* seller.

Projetar a flag de catalogo sobre o historico classificaria errado a maioria
dos itens que vendem. Alem disso a flag de catalogo e' elegibilidade, nao
estoque: 77 itens marcados FBS estao com estoque zero, e nenhum item nao-FBS
tem `reserved_stock` > 0.

A fonte da classe nao admite terceira classe: em 262.411 pedidos
`fulfillment_flag` tem exatamente dois valores e ZERO nulos. Por isso o CHECK
e' fechado -- ao contrario do ML, onde `unknown` existe porque la' o envio pode
faltar. Valor novo ou nulo deve FALHAR a carga, nao criar categoria silenciosa.

GMV: BRUTO DE PEDIDOS NAO CANCELADOS
-------------------------------------
    gross_gmv = SUM(silver.stg_shopee_order_items.item_total)
                dos itens de pedidos com order_status <> 'cancelled'

`total_amount` E PROIBIDO em GMV, share e qualquer KPI financeiro. A escolha
foi reconciliada contra as tres fontes, em agosto/2026:

    marca      fact_marketplace_daily   SUM(item_total)   total_amount
    apice           272.290,25            274.629,58       267.846,90
    barbours        943.379,47            942.411,66       854.911,95   (-9,38%)
    lescent         315.796,22            320.074,50       306.275,34
    rituaria        406.143,19            412.961,42       382.822,70   (-5,74%)

`item_total` fica a 0,1%-1,7% do canonico que a Torre ja' publica;
`total_amount` fica 1,6%-9,4% ABAIXO. A diferenca residual e' fotografia:
cobertura, cutoff e maturacao de status diferentes entre as esteiras.

No UNIVERSO COMUM (mesmos pedidos, mesmo cutoff, XLSX deduplicado pelo arquivo
mais recente) a identidade e' praticamente exata:

    apice     0,00    lescent   0,00    rituaria  0,00
    barbours 77,91 (0,011%) e 1 unidade

`item_total` e' portanto o equivalente literal do "Subtotal do produto" da
planilha e da regra canonica de `pipelines/connectors/shopee/_parser.py`, que
soma `subtotal` dos pedidos com status diferente de `Cancelado`.

`to_return` E `unpaid` FICAM NO GMV BRUTO
------------------------------------------
So' `cancelled` sai. `to_return` e `unpaid` PERMANECEM no denominador bruto,
para preservar comparabilidade com a Torre atual, e ganham colunas proprias
para que ninguem precise adivinhar o tamanho deles. Este numero e' GMV BRUTO
-- nao e' receita liquida nem realizada, e a documentacao nao pode chama-lo
assim.

CANCELAMENTO E DEVOLUCAO TEM DENOMINADORES PROPRIOS
----------------------------------------------------
`cancelled_orders` NAO entra no GMV mas CONTA na linha, porque o denominador
da taxa de cancelamento e' `created_orders` -- todos os pedidos criados no dia,
cancelados inclusive. Devolucao nunca e' subtraida do GMV em silencio.

HANDLING E' PAGAMENTO -> COLETA, NAO ENTREGA
---------------------------------------------
    handling = pickup_done_time - pay_time

A API da Shopee NAO tem data real de entrega. `delivered_date` so' existe no
export XLSX, que nao entra neste contrato. Nao existe coluna de entrega nesta
tabela, e a ausencia e' deliberada: uma coluna vazia convidaria alguem a
preenche-la com coleta e chamar de entrega.

Soma + amostra, nunca media: media de medias nao e' agregavel, e sem
`handling_sample_count` ninguem sabe quanto do dia a media cobre. Pedido sem
`pickup_done_time` fica FORA da amostra -- nunca entra como tempo zero.

COBERTURA: QUATRO CONTAS, E KOKESHI NAO EXISTE AQUI
----------------------------------------------------
A esteira API cobre apice, barbours, lescent e rituaria. Kokeshi NAO esta na
API (zero linhas) e NAO pode ser suprida pelo XLSX nesta tabela: sao contratos
distintos, e somar os dois produziria um denominador que nao significa nada.
A soma das quatro contas e' "Shopee -- cobertura API", nunca "Shopee total".

`shop_account` participa da chave junto de `brand` porque a cobertura e'
declarada POR CONTA: sem ela, uma conta que parasse de carregar desapareceria
dentro do total da marca sem deixar rastro.

ZERO PII
--------
Nenhuma coluna de comprador, documento, telefone ou endereco. A fonte tem
`recipient_address` preenchido em 100% dos pedidos e `buyer_username` em 99,9%;
nada disso e' lido, agregado ou publicado aqui.

COMPETENCIA: COMERCIAL, America/Sao_Paulo
------------------------------------------
`ref_date` = `silver.stg_shopee_orders.created_date_brt`, a data de CRIACAO do
pedido no fuso operacional. Nao e' pagamento, nao e' coleta, nao e' repasse.

Serie disponivel a partir de 2026-01-01.

Gate FULL-SH-1A-R. Migration NAO aplicada nesta rodada.
"""
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


#: Dominio FECHADO da classe. Diferente do ML, aqui nao ha terceira classe:
#: `fulfillment_flag` tem dois valores e zero nulos em 262.411 pedidos, entao
#: qualquer outro valor e' contrato quebrado e tem de falhar a carga.
CLASSES = ("fbs", "seller")


def upgrade() -> None:
    # Sem IF NOT EXISTS (padrao 006-018): colisao tem de falhar alto.
    op.execute("""
        CREATE TABLE marts.fact_shopee_fbs_daily (
            -- Grao: (ref_date, brand, shop_account, fbs_class).
            -- ref_date = created_date_brt -- competencia COMERCIAL do pedido,
            -- valida para TODAS as colunas desta linha, handling inclusive.
            ref_date                 DATE        NOT NULL,
            brand                    TEXT        NOT NULL,
            -- Conta da esteira API. Na chave porque a cobertura e' declarada
            -- por conta: sem ela, conta parada some dentro do total da marca.
            shop_account             TEXT        NOT NULL,
            -- Derivada de fulfillment_flag OBSERVADO no pedido. Jamais do
            -- catalogo.
            fbs_class                TEXT        NOT NULL,

            -- ---------------------------------------------------------------
            -- Populacao. Grao do PEDIDO.
            -- ---------------------------------------------------------------
            -- TODOS os pedidos criados no dia. Denominador da taxa de
            -- cancelamento.
            created_orders           BIGINT      NOT NULL,
            -- order_status <> 'cancelled'. Populacao do GMV e das unidades.
            -- INCLUI to_return e unpaid, por decisao de comparabilidade.
            eligible_orders          BIGINT      NOT NULL,
            cancelled_orders         BIGINT      NOT NULL,
            -- Recortes de qualidade/maturacao DENTRO de eligible_orders.
            -- Existem como coluna para que o peso deles seja visivel sem
            -- consulta nova -- nao para serem subtraidos do GMV.
            to_return_orders         BIGINT      NOT NULL,
            unpaid_orders            BIGINT      NOT NULL,

            -- ---------------------------------------------------------------
            -- Valor. GMV BRUTO -- nao e' receita liquida nem realizada.
            -- ---------------------------------------------------------------
            -- SUM(item_total) dos itens dos pedidos elegiveis. NUNCA
            -- total_amount: mede 1,6%-9,4% abaixo do canonico da Torre.
            gross_gmv                NUMERIC     NOT NULL,
            gross_units              BIGINT      NOT NULL,
            -- Parcelas dos recortes acima, para que possam ser destacadas na
            -- leitura sem refazer o calculo.
            to_return_gmv            NUMERIC     NOT NULL,
            unpaid_gmv               NUMERIC     NOT NULL,

            -- ---------------------------------------------------------------
            -- Tempo. Pagamento -> COLETA. Nao existe entrega nesta fonte.
            -- Soma + amostra para que a media seja agregavel e censurada.
            -- ---------------------------------------------------------------
            handling_seconds_sum     BIGINT      NOT NULL,
            handling_sample_count    BIGINT      NOT NULL,

            -- ---------------------------------------------------------------
            -- Procedencia. Tres relogios distintos, como no 016.
            -- ---------------------------------------------------------------
            -- Relogio da FONTE (ingestao no Data Mart). Nao e' prova de
            -- maturidade do pedido.
            source_max_ingested_at   TIMESTAMPTZ,
            -- Relogio da PUBLICACAO no Neon.
            ingested_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_run_id            VARCHAR(64) NOT NULL,

            CONSTRAINT pk_fact_shopee_fbs_daily
                PRIMARY KEY (ref_date, brand, shop_account, fbs_class),

            -- --- Dominio FECHADO: a fonte nao admite terceira classe --------
            CONSTRAINT ck_fsfd_classe_conhecida
                CHECK (fbs_class IN ('fbs', 'seller')),

            -- --- Populacoes fecham -----------------------------------------
            CONSTRAINT ck_fsfd_populacoes_fecham
                CHECK (eligible_orders + cancelled_orders = created_orders),
            CONSTRAINT ck_fsfd_recortes_cabem
                CHECK (to_return_orders + unpaid_orders <= eligible_orders),
            CONSTRAINT ck_fsfd_contagens_nao_negativas
                CHECK (created_orders >= 0 AND eligible_orders >= 0
                   AND cancelled_orders >= 0 AND to_return_orders >= 0
                   AND unpaid_orders >= 0 AND gross_units >= 0),

            -- --- Dinheiro: NUMERIC aceita NaN e Infinity; barrar explicito ---
            -- 'NaN'::numeric >= 0 e' TRUE no Postgres, entao um CHECK de sinal
            -- NAO pega NaN. Precisa ser desigualdade direta.
            CONSTRAINT ck_fsfd_gmv_nao_nan
                CHECK (gross_gmv <> 'NaN' AND to_return_gmv <> 'NaN'
                   AND unpaid_gmv <> 'NaN'),
            CONSTRAINT ck_fsfd_gmv_finito
                CHECK (gross_gmv > '-Infinity' AND gross_gmv < 'Infinity'
                   AND to_return_gmv > '-Infinity' AND to_return_gmv < 'Infinity'
                   AND unpaid_gmv > '-Infinity' AND unpaid_gmv < 'Infinity'),
            CONSTRAINT ck_fsfd_gmv_nao_negativo
                CHECK (gross_gmv >= 0 AND to_return_gmv >= 0
                   AND unpaid_gmv >= 0),
            -- Os recortes sao PARCELA do bruto, nunca maiores que ele.
            CONSTRAINT ck_fsfd_recorte_gmv_cabe
                CHECK (to_return_gmv + unpaid_gmv <= gross_gmv),

            -- --- Coerencia entre populacao e valor --------------------------
            -- Sem pedido elegivel nao pode haver GMV nem unidade. Impede que
            -- valor orfao entre por join torto.
            CONSTRAINT ck_fsfd_valor_exige_pedido
                CHECK ((eligible_orders > 0)
                       OR (gross_gmv = 0 AND gross_units = 0)),

            -- --- Amostra de handling ----------------------------------------
            -- A amostra e' subconjunto da coorte: nao pode haver mais medicoes
            -- de handling do que pedidos elegiveis no dia.
            CONSTRAINT ck_fsfd_amostra_cabe_na_coorte
                CHECK (handling_sample_count <= eligible_orders),
            CONSTRAINT ck_fsfd_amostra_nao_negativa
                CHECK (handling_sample_count >= 0
                   AND handling_seconds_sum >= 0),
            -- Soma e amostra andam juntas: amostra zero com soma positiva
            -- seria media infinita; amostra positiva com soma zero seria
            -- coleta instantanea. Os dois sao impossiveis.
            CONSTRAINT ck_fsfd_soma_exige_amostra
                CHECK ((handling_sample_count = 0)
                       = (handling_seconds_sum = 0))
        )
    """)

    # Leitura da tela: recorte por marca dentro de uma janela de datas.
    op.execute("""
        CREATE INDEX ix_fsfd_brand_ref_date
            ON marts.fact_shopee_fbs_daily (brand, ref_date)
    """)
    # Varredura por periodo sem filtro de marca (cobertura consolidada).
    op.execute("""
        CREATE INDEX ix_fsfd_ref_date
            ON marts.fact_shopee_fbs_daily (ref_date)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS marts.fact_shopee_fbs_daily")
