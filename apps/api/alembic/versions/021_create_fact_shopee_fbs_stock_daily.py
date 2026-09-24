"""Gate FULL-SOURCE-1 Fase 2 — cria as duas fatos de ESTOQUE FBS da Shopee.

O QUE ESTA FATO MEDE, E O QUE NAO MEDE
---------------------------------------
Mede o ESTOQUE FISICO VENDAVEL que esta dentro do centro de distribuicao da
Shopee (FBS), por dia. Nao mede desempenho de venda: isso e'
`marts.fact_shopee_fbs_daily`, que classifica o PEDIDO pela modalidade
observada e NAO e' tocada por esta migration.

A FONTE E' UMA SO': stock_info.shopee_stock
--------------------------------------------
    raw.shopee_products.stock_info (JSONB)
        shopee_stock[]  -> {stock, location_id, if_saleable}   <-- ESTA
        seller_stock[]  -> estoque no deposito do VENDEDOR      (contexto)
        summary_info    -> agregado da propria Shopee           (contexto)
        advance_stock   -> objeto, hoje zerado                  (nao usado)

`SUM(shopee_stock[].stock)` por item foi conciliado contra o "Total Vendavel"
do Seller Center em 3 produtos normais, com diferenca de 0,7% a 1,5% explicada
pela diferenca de horario entre as duas fotografias (validacao manual,
2026-09-24).

POR QUE NAO USAR OS OUTROS CAMPOS COMO ESTOQUE FULL
----------------------------------------------------
- `summary_info.total_available_stock` NAO reconcilia: nenhuma composicao dos
  campos fecha com ele em 7 dos 301 produtos FBS, com desvio de ate' 174.906
  unidades num unico item. Ele entra aqui apenas como COLUNA DE CONTEXTO, para
  que a divergencia continue visivel -- nunca como estoque Full.
- `seller_stock` e' o deposito do vendedor, nao o CD da Shopee.
- `reserved_stock` e' bloqueio por pedido em andamento, nao estoque disponivel.
- `advance_stock` e' um OBJETO com `sellable_advance_stock` e
  `in_transit_advance_stock` -- o programa de reposicao antecipada, que inclui
  unidade AINDA A CAMINHO do CD. O que conciliamos com a tela foi o "Total
  Vendavel", nao um total logistico: somar `in_transit` contaria estoque que nao
  da' para vender hoje e inflaria a cobertura. Medido em 2026-09-24: os dois
  campos valem 0 nos 301 produtos FBS, entao a exclusao nao muda numero nenhum
  hoje -- ela existe pela semantica, para o dia em que deixarem de ser zero.

DEMANDA: POPULACAO EXPLICITA, RECONCILIADA COM A FATO VIGENTE
--------------------------------------------------------------
Allowlist explicita de status com PAGAMENTO COMPROVADO. Medido em 180 dias: os
seis status da demanda operacional tem `pay_time` em 100% dos pedidos
(completed 164.406/164.406, shipped 2.318/2.318, to_confirm_receive 2.246/2.246,
processed 336/336, to_return 206/206, ready_to_ship 9/9), contra `unpaid` com
0 de 159. O contraste e' binario.

`unpaid` fica FORA: nunca foi pago e nunca consumiu estoque; soma-lo faria a
velocidade parecer maior e a cobertura menor, disparando alerta de ruptura em
produto que nao vendeu. Isso DIVERGE de `marts.fact_shopee_fbs_daily`, que o
mantem no GMV bruto -- e a divergencia e' deliberada: comparabilidade com uma
fato de VALOR nao justifica herdar a distorcao numa fonte OPERACIONAL de
reposicao. O criterio antigo continua publicado em
`units_sold_28d_legado_com_unpaid`, como contexto, sem classificar nada.

`to_return` PERMANECE: 206/206 passaram por pagamento, a unidade foi vendida e
deixou o estoque. Status desconhecido ou nulo BLOQUEIA a carga.

A janela sao N dias COMPLETOS, em [ref_date - N, ref_date): o dia corrente fica
fora porque esta' pela metade.

KITS FICAM DE FORA
-------------------
O unico kit levado a' validacao nao foi localizado no Seller Center, entao a
semantica de estoque de kit segue NAO CONCILIADA. Kits sao publicados com
`is_kit = TRUE` e `classificacao_torre = 'KIT_NAO_CONCILIADO'`, e ficam fora de
qualquer contagem de ruptura/excesso -- sem impedir a entrega dos produtos
normais.

POR QUE DUAS TABELAS
---------------------
A Raw e' SOBRESCRITA a cada ingestao: nao ha historico. A fotografia diaria e'
criada aqui. O grao fino preserva `location_id` (CD) e `model_id` (variacao)
para que nada se perca; o agregado por produto e' o que a Torre consome.

`model_id = 0` e' a sentinela de "produto sem variacao". Hoje TODO produto FBS
e' sem variacao (`is_fulfillment_by_shopee` <=> NOT `has_model`), mas isso e'
ESTADO da operacao, nao regra da API: a coluna existe para que um FBS com
variacao futuro entre pela porta da frente em vez de ser somado por engano.
"""
from alembic import op

#: Encadeia APOS a migration da Expedicao (020), que virou a cabeca do
#: Alembic no merge do PR #43. Cadeia: 019 -> 020 -> 021.
revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


#: Dominio FECHADO da classificacao da Torre. Valor fora daqui e' contrato
#: quebrado e tem de falhar a carga.
CLASSIFICACOES = (
    "RUPTURA_CANDIDATA",
    "BAIXO_CANDIDATO",
    "EXCESSO_CANDIDATO",
    "SEM_GIRO_CANDIDATO",
    "SUFICIENTE",
    "SEM_DEMANDA_MEDIDA",
    "KIT_NAO_CONCILIADO",
)

#: Qualidade do vinculo entre o produto e a demanda medida.
#: Dominio de DOIS valores. `FORA_DO_CATALOGO_DE_VENDAS` foi removido por ser
#: inalcancavel: toda linha desta fato nasce de `stg_shopee_products`, entao nao
#: existe produto aqui que esteja fora do catalogo.
VINCULOS = ("COM_VENDA", "SEM_VENDA_NA_JANELA")


def upgrade() -> None:
    _lista = ", ".join(f"'{c}'" for c in CLASSIFICACOES)
    _vinc = ", ".join(f"'{v}'" for v in VINCULOS)

    # ------------------------------------------------------------------ #
    # 1. GRAO FINO: uma linha por CD                                      #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE marts.fact_shopee_fbs_stock_location_daily (
            -- Grao: (ref_date, shop_account, item_id, model_id, location_id).
            -- ref_date = dia da FOTOGRAFIA, nao competencia de venda.
            ref_date          DATE        NOT NULL,
            brand             TEXT        NOT NULL,
            shop_account      TEXT        NOT NULL,
            item_id           TEXT        NOT NULL,
            -- '0' = produto sem variacao. Ver docstring.
            model_id          TEXT        NOT NULL,
            -- Centro de distribuicao da Shopee.
            location_id       TEXT        NOT NULL,

            -- Unidades neste CD. Vem de shopee_stock[].stock, sem transformacao.
            full_stock        BIGINT      NOT NULL,
            -- shopee_stock[].if_saleable. NULL quando a API omite a chave --
            -- distinto de FALSE, que e' "medido como nao vendavel".
            is_saleable       BOOLEAN,

            is_kit            BOOLEAN     NOT NULL,
            item_status       TEXT,

            source_run_id     TEXT        NOT NULL,
            source_captured_at TIMESTAMPTZ NOT NULL,
            ingested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT pk_fsfsl
                PRIMARY KEY (ref_date, shop_account, item_id, model_id, location_id),
            CONSTRAINT ck_fsfsl_estoque_nao_negativo
                CHECK (full_stock >= 0)
        )
    """)
    op.execute("""CREATE INDEX ix_fsfsl_data ON
                  marts.fact_shopee_fbs_stock_location_daily (ref_date DESC)""")
    op.execute("""CREATE INDEX ix_fsfsl_item ON
                  marts.fact_shopee_fbs_stock_location_daily (shop_account, item_id)""")

    # ------------------------------------------------------------------ #
    # 2. AGREGADO POR PRODUTO: o que a Torre consome                      #
    # ------------------------------------------------------------------ #
    op.execute(f"""
        CREATE TABLE marts.fact_shopee_fbs_stock_daily (
            ref_date          DATE        NOT NULL,
            brand             TEXT        NOT NULL,
            shop_account      TEXT        NOT NULL,
            item_id           TEXT        NOT NULL,
            model_id          TEXT        NOT NULL,

            item_name         TEXT,
            item_sku          TEXT,
            item_status       TEXT,
            is_kit            BOOLEAN     NOT NULL,

            -- ============================================================ #
            -- ESTOQUE FULL. A unica medida de estoque Full desta fato.     #
            -- ============================================================ #
            -- SUM(shopee_stock[].stock) WHERE if_saleable IS TRUE.
            -- E' o numero conciliado com o "Total Vendavel" do Seller Center.
            full_stock_saleable   BIGINT  NOT NULL,
            -- SUM de TODOS os CDs, inclusive if_saleable false/ausente. A
            -- diferenca para o de cima e' o retido -- hoje zero nos FBS, mas a
            -- coluna existe para que o dia em que deixar de ser zero apareca.
            full_stock_total      BIGINT  NOT NULL,
            location_count        INTEGER NOT NULL,

            -- ============================================================ #
            -- CONTEXTO. NAO e' estoque Full. Nao usar em cobertura.        #
            -- ============================================================ #
            -- Deposito do vendedor.
            seller_stock_total        BIGINT,
            -- Bloqueio por pedido em andamento.
            reserved_stock            BIGINT,
            -- Agregado da propria Shopee. NAO reconcilia com a soma dos campos
            -- em 7 de 301 produtos: fica aqui so' para a divergencia
            -- permanecer visivel.
            summary_available_stock   BIGINT,

            -- ============================================================ #
            -- DEMANDA. Janela de 28 dias.                                  #
            -- ============================================================ #
            -- Demanda OPERACIONAL: apenas status com pagamento comprovado
            -- (100% de pay_time, medido em 180 dias). E' a UNICA que alimenta
            -- media diaria, cobertura e classificacao.
            units_sold_28d        BIGINT  NOT NULL,
            days_with_sales_28d   INTEGER NOT NULL,
            -- CONTEXTO: demanda no criterio da migration 019, que mantem
            -- `unpaid`. Existe para comparabilidade com a fato de desempenho e
            -- NAO classifica nada. `unpaid` tem 0 de 159 pedidos com pay_time:
            -- nunca foi pago e nunca consumiu estoque.
            units_sold_28d_legado_com_unpaid  BIGINT,
            avg_daily_units_28d   NUMERIC NOT NULL,

            -- ============================================================ #
            -- COBERTURA DA TORRE                                           #
            -- ============================================================ #
            -- full_stock_saleable / (units_sold_28d / 28).
            -- NULL quando nao houve venda na janela: cobertura infinita nao e'
            -- um numero grande, e' ausencia de resposta.
            -- 🔑 E' calculo NOSSO. NAO reproduz nenhuma formula da Shopee e nao
            -- deve ser apresentado como tal.
            cobertura_torre_dias  NUMERIC,

            classificacao_torre   TEXT    NOT NULL,
            vinculo_vendas        TEXT    NOT NULL,

            source_run_id     TEXT        NOT NULL,
            source_captured_at TIMESTAMPTZ NOT NULL,
            ingested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT pk_fsfs
                PRIMARY KEY (ref_date, shop_account, item_id, model_id),
            CONSTRAINT ck_fsfs_estoques_nao_negativos
                CHECK (full_stock_saleable >= 0 AND full_stock_total >= 0
                       AND location_count >= 0 AND units_sold_28d >= 0),
            -- O vendavel e' um SUBCONJUNTO do total: nunca pode exceder.
            CONSTRAINT ck_fsfs_vendavel_cabe_no_total
                CHECK (full_stock_saleable <= full_stock_total),
            CONSTRAINT ck_fsfs_classificacao_conhecida
                CHECK (classificacao_torre IN ({_lista})),
            CONSTRAINT ck_fsfs_vinculo_conhecido
                CHECK (vinculo_vendas IN ({_vinc})),
            -- Kit NUNCA recebe classificacao operacional: a semantica de
            -- estoque de kit nao foi conciliada.
            CONSTRAINT ck_fsfs_kit_fica_fora
                CHECK (NOT is_kit OR classificacao_torre = 'KIT_NAO_CONCILIADO'),
            -- Cobertura exige demanda: sem venda na janela, tem de ser NULL.
            CONSTRAINT ck_fsfs_cobertura_exige_demanda
                CHECK ((cobertura_torre_dias IS NULL) = (units_sold_28d = 0)),
            -- Media diaria e' derivada da janela: as duas tem de concordar
            -- sobre haver ou nao demanda.
            CONSTRAINT ck_fsfs_media_coerente_com_janela
                CHECK ((avg_daily_units_28d = 0) = (units_sold_28d = 0)),
            CONSTRAINT ck_fsfs_cobertura_nao_nan
                CHECK (cobertura_torre_dias IS NULL
                       OR cobertura_torre_dias <> 'NaN'::numeric)
        )
    """)
    op.execute("""CREATE INDEX ix_fsfs_data ON
                  marts.fact_shopee_fbs_stock_daily (ref_date DESC)""")
    op.execute("""CREATE INDEX ix_fsfs_classificacao ON
                  marts.fact_shopee_fbs_stock_daily (ref_date DESC, classificacao_torre)""")
    op.execute("""CREATE INDEX ix_fsfs_marca ON
                  marts.fact_shopee_fbs_stock_daily (brand, ref_date DESC)""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS marts.fact_shopee_fbs_stock_daily")
    op.execute("DROP TABLE IF EXISTS marts.fact_shopee_fbs_stock_location_daily")
