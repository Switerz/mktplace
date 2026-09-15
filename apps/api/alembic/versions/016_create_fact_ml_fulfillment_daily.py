"""Gate FULL-1A — cria marts.fact_ml_fulfillment_daily e _listing_daily.

Materializa a superficie "Full Mercado Livre": quanto do negocio passa por
fulfillment (Full) contra as demais modalidades logisticas, medido no grao do
PEDIDO e reconciliado contra a planilha do stakeholder em agosto/2026.

ESTA MIGRATION NAO CRIA NADA DE ESTOQUE
----------------------------------------
Nao existe aqui coluna de estoque, cobertura em dias, ruptura ou disponibilidade.
O gate FULL-0R refutou empiricamente a tese de que `api.ml_item_stock_history.
available_quantity` seja estoque fisico do Full: em listings exclusivamente Full,
a correlacao entre a queda diaria do campo e as unidades vendidas e' -0,006, em
23,1% dos dias com venda o valor nao se move e em 14,6% ele SOBE. O campo e'
"proxy operacional do estoque disponivel/anunciado do listing", e nenhuma metrica
de nivel foi construida sobre ele. Qualquer coluna futura de estoque precisa de
gate proprio.

COMPETENCIA: COMERCIAL, DO PEDIDO
----------------------------------
`ref_date` e' `date_created::date` de `api.ml_orders`. Nao e' `date_closed`, nao
e' a data do envio, nao e' repasse. Frete NAO entra no GMV.

A DIRECAO DO JOIN E' UMA SO', E O MOTIVO E' MEDIDO
---------------------------------------------------
    api.ml_orders (brand, shipping_id) -> api.ml_shipments (brand, shipment_id)

Sempre PEDIDO -> ENVIO, nunca o contrario. Em agosto/2026 existem 4.821 packs
carregando de 2 a 8 pedidos no MESMO `shipping_id`. Projetar o envio sobre os
pedidos multiplica: medido, `shipping_items` lido a partir de pedidos pagos
produz 67.725 unidades contra as 60.916 reais -- 11,2% de inflacao. Por isso
unidade NUNCA vem de `shipping_items` nesta tabela.

`brand` faz parte da chave porque 17 `shipment_id` de agosto/2026 aparecem em
DUAS marcas distintas. Join so' por `shipment_id` duplicaria essas linhas.

TRES CLASSES, E `unknown` NUNCA CAI EM `non_full`
--------------------------------------------------
    full     <- logistic_type = 'fulfillment'
    non_full <- logistic_type presente e diferente de 'fulfillment'
    unknown  <- envio ausente OU logistic_type nulo

`unknown` existe como classe propria justamente para que pedido sem envio nao
seja contado como "nao-Full" por omissao. Em agosto/2026 sao 8 pedidos, todos
cancelados -- pequeno, mas o silencio seria uma mentira barata.

`logistic_type_original` PRESERVA O VALOR DA FONTE, INCLUSIVE OS EXTINTOS
--------------------------------------------------------------------------
A taxonomia do ML mudou no meio da serie. Medido em `api.ml_shipments`:

    fulfillment    336.088 envios   22/05/2025 -> hoje
    cross_docking  116.510          01/09/2025 -> hoje
    xd_drop_off     51.643          01/09/2025 -> 10/03/2026  (extinto)
    self_service     9.927          22/05/2025 -> 20/03/2026  (extinto)
    drop_off         3.389          25/05/2025 -> 30/09/2025  (extinto)

Em novembro/2025 `xd_drop_off` sozinho tinha 14.696 envios contra 794 de
`cross_docking`: uma serie que olhasse so' os dois rotulos vigentes perderia 62%
do mes. Por isso a coluna guarda o rotulo bruto e a classe binaria e' derivada.

NAO EXISTE CHECK FECHADO EM `logistic_type_original`, E A AUSENCIA E' DECISAO
-----------------------------------------------------------------------------
Um `CHECK (logistic_type_original IN (...))` quebraria a carga no dia em que o
Mercado Livre introduzir uma modalidade nova -- exatamente o evento em que a
Torre mais precisa continuar publicando. Valor desconhecido entra como
`non_full` (regra do contrato: tudo que nao e' `fulfillment` e nao e' ausencia)
e permanece visivel em `logistic_type_original` para auditoria. O CHECK existente
cobre apenas o sentinela de ausencia, que e' invariante nossa, nao da fonte.

MEDIAS NAO SAO ARMAZENADAS: SOMA E CONTAGEM DE AMOSTRA
-------------------------------------------------------
`handling_*` e `delivery_*` guardam `..._seconds_sum` e `..._sample_count`. A
media e' derivada como `sum / sample_count` no consumo, o que a torna agregavel
corretamente entre dias e marcas. Guardar a media pronta impediria somar duas
linhas sem reponderar, e e' o erro classico desse tipo de tabela.

UMA COORTE SO': O PEDIDO (corrigido no FULL-1A-R)
--------------------------------------------------
`ref_date` vale para TODAS as metricas da linha, inclusive `handling_*` e
`delivery_*`. A versao inicial media os tempos no grao do ENVIO com a data do
ENVIO, dando DUAS semanticas a uma coluna de chave: a mesma `ref_date` significava
"pedidos criados no dia" para GMV e "envios criados no dia" para tempo.

Agora a amostra e' o PEDIDO da coorte, medido do `date_created` dele ate' os
eventos do envio dele. Um pack com tres pedidos contribui tres amostras, e isso
e' correto: a pergunta e' "quanto tempo ESTE pedido levou para despachar", nao
"quantos envios existiram". `ck_fmfd_amostras_cabem_na_coorte` trava a invariante.

Medido em agosto/2026 na coorte do pedido: Full 28,67 h de handling e 2,65 d de
entrega, com 97,3% e 96,5% de cobertura; nao-Full 71,83 h e 4,89 d, com 97,1% e
96,0%. Zero negativos, zero outliers acima de 30 d de handling ou 60 d de entrega.
`sample_count` mede a CENSURA: os 2,7% sem handling sao pedidos que ainda nao
despacharam, nao pedidos com tempo zero.

Serie por data do evento logistico, se um dia for necessaria, e' OUTRA fato --
nunca uma segunda leitura desta `ref_date`.

GRAO DE LISTING E' SEGURO AQUI, E ISSO FOI MEDIDO
---------------------------------------------------
`marts.fact_ml_fulfillment_listing_daily` publica GMV por listing porque existe
alocacao DETERMINISTICA: `api.ml_order_line_items` fecha com
`api.ml_orders.total_amount` em 59.123 de 59.123 pedidos pagos de agosto/2026,
diferenca total de R$ 0,00. E o grao e' naturalmente 1:1 -- 257.681 pedidos entre
maio e agosto/2026 tem EXATAMENTE uma linha cada, porque o ML quebra compra
multi-item em pedidos distintos amarrados por `pack_id`. Nenhum rateio, nenhuma
replicacao de `total_amount`.

Gate FULL-1A. Migration NAO aplicada nesta rodada.
"""
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


#: Sentinela para `logistic_type` ausente. A coluna e' NOT NULL e participa da
#: PK; NULL em coluna de chave tornaria a linha inalcancavel por igualdade e
#: silenciaria a ausencia num UPSERT. O valor e' impossivel na fonte (o ML nunca
#: devolve parenteses num enum), entao nao colide com rotulo real nem futuro.
SENTINELA_LT = "(sem envio)"


def upgrade() -> None:
    # Sem IF NOT EXISTS (padrao 006-015): colisao tem de falhar alto.
    op.execute(f"""
        CREATE TABLE marts.fact_ml_fulfillment_daily (
            -- Grao: (ref_date, brand, fulfillment_class, logistic_type_original).
            -- ref_date = api.ml_orders.date_created::date -- competencia
            -- COMERCIAL do pedido, valida para TODAS as colunas desta linha.
            -- Nao ha excecao: handling e delivery tambem pertencem a esta
            -- coorte (corrigido no FULL-1A-R).
            ref_date                 DATE        NOT NULL,
            brand                    TEXT        NOT NULL,

            -- Classe binaria derivada + ausencia explicita.
            fulfillment_class        TEXT        NOT NULL,
            -- Rotulo BRUTO da fonte, preservado inclusive quando extinto ou
            -- ainda desconhecido. Sentinela '{SENTINELA_LT}' quando nao ha envio.
            logistic_type_original   TEXT        NOT NULL,

            -- ---------------------------------------------------------------
            -- Populacao e valor. Grao do PEDIDO.
            -- ---------------------------------------------------------------
            -- Denominador da taxa de cancelamento: TODOS os status criados no dia.
            eligible_orders          BIGINT      NOT NULL,
            -- Subconjunto status='paid'. Populacao do GMV e das unidades.
            paid_orders              BIGINT      NOT NULL,
            cancelled_orders         BIGINT      NOT NULL,
            -- Todo status que nao e paid nem cancelled. Hoje so
            -- 'partially_refunded'. Existe como COLUNA e nao como resto
            -- aritmetico: sem ela, um status novo do ML sumiria da linha sem
            -- que nenhum CHECK reclamasse.
            other_orders             BIGINT      NOT NULL,
            -- SUM(total_amount) dos pagos. SEM frete, SEM cancelado,
            -- SEM partially_refunded.
            paid_gmv                 NUMERIC     NOT NULL,
            -- SUM(quantity) de api.ml_order_line_items dos pagos. NUNCA de
            -- shipping_items: aquele grao e' do envio e infla 11,2% via packs.
            paid_units               BIGINT      NOT NULL,

            -- ---------------------------------------------------------------
            -- Tempos. Grao do ENVIO, deduplicado por (brand, shipment_id).
            -- Soma + amostra para que a media seja agregavel.
            -- ---------------------------------------------------------------
            handling_seconds_sum     BIGINT      NOT NULL,
            handling_sample_count    BIGINT      NOT NULL,
            delivery_seconds_sum     BIGINT      NOT NULL,
            delivery_sample_count    BIGINT      NOT NULL,

            -- ---------------------------------------------------------------
            -- Qualidade do join, publicada com o dado e nao a parte.
            -- ---------------------------------------------------------------
            -- Pedidos sem envio correspondente. Por construcao so' e' > 0 na
            -- classe `unknown`.
            unmatched_orders         BIGINT      NOT NULL,
            -- Envios da classe cujo `shipping_items` veio nulo ou vazio. Nao
            -- afeta paid_units (que vem de line_items), mas denuncia lacuna na
            -- fonte antes que alguem construa algo em cima dela.
            missing_shipping_items   BIGINT      NOT NULL,

            -- Procedencia TECNICA, sem fuso. Relogio da FONTE, nao da nossa
            -- ingestao e nao prova de maturidade.
            source_updated_at        TIMESTAMP,
            -- Relogio da PUBLICACAO no Neon. Terceiro relogio, mede frescor da
            -- execucao.
            ingested_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_run_id            VARCHAR(64) NOT NULL,

            CONSTRAINT pk_fact_ml_fulfillment_daily
                PRIMARY KEY (ref_date, brand, fulfillment_class,
                             logistic_type_original),

            -- --- Dominio fechado APENAS onde a invariante e' nossa -----------
            CONSTRAINT ck_fmfd_classe_conhecida
                CHECK (fulfillment_class IN ('full', 'non_full', 'unknown')),
            -- Coerencia entre classe e rotulo: `unknown` <-> sentinela. Impede
            -- que ausencia seja publicada com rotulo real, ou vice-versa.
            CONSTRAINT ck_fmfd_unknown_coerente
                CHECK (
                    (fulfillment_class =  'unknown'
                     AND logistic_type_original =  '{SENTINELA_LT}')
                 OR (fulfillment_class <> 'unknown'
                     AND logistic_type_original <> '{SENTINELA_LT}')
                ),
            -- `full` e' exatamente 'fulfillment'. Nao ha CHECK simetrico em
            -- `non_full`: e' justamente a classe aberta a rotulo novo.
            CONSTRAINT ck_fmfd_full_e_fulfillment
                CHECK (fulfillment_class <> 'full'
                       OR logistic_type_original = 'fulfillment'),

            -- --- Anti-NaN: 'NaN'::numeric passa por qualquer comparacao de
            -- --- ordem, inclusive por `>= 0`. Precisa de CHECK proprio.
            CONSTRAINT ck_fmfd_gmv_nao_nan
                CHECK (paid_gmv <> 'NaN'),
            -- Infinito passa por `>= 0` tao silenciosamente quanto NaN, e
            -- numeric aceita os dois literais. Precisa de CHECK proprio.
            CONSTRAINT ck_fmfd_gmv_finito
                CHECK (paid_gmv > '-Infinity' AND paid_gmv < 'Infinity'),

            -- --- Aritmetica de contagem -------------------------------------
            CONSTRAINT ck_fmfd_gmv_nao_negativo
                CHECK (paid_gmv >= 0),
            CONSTRAINT ck_fmfd_contagens_nao_negativas
                CHECK (eligible_orders >= 0 AND paid_orders >= 0
                   AND cancelled_orders >= 0 AND other_orders >= 0
                   AND paid_units >= 0
                   AND unmatched_orders >= 0 AND missing_shipping_items >= 0),
            CONSTRAINT ck_fmfd_tempos_nao_negativos
                CHECK (handling_seconds_sum  >= 0 AND handling_sample_count  >= 0
                   AND delivery_seconds_sum  >= 0 AND delivery_sample_count  >= 0),
            -- Amostra zero obriga soma zero: sem isso, `sum/count` viraria
            -- divisao por zero com numerador nao nulo -- um tempo medio infinito
            -- publicado sem que nenhum CHECK reclamasse.
            CONSTRAINT ck_fmfd_handling_soma_exige_amostra
                CHECK (handling_sample_count > 0 OR handling_seconds_sum = 0),
            CONSTRAINT ck_fmfd_delivery_soma_exige_amostra
                CHECK (delivery_sample_count > 0 OR delivery_seconds_sum = 0),
            -- A amostra de tempo e um SUBCONJUNTO da coorte de pedidos. Se
            -- exceder, o grao escorregou de volta para o envio -- exatamente a
            -- confusao que o FULL-1A-R corrigiu.
            CONSTRAINT ck_fmfd_amostras_cabem_na_coorte
                CHECK (handling_sample_count <= eligible_orders
                   AND delivery_sample_count <= eligible_orders),
            -- Subpopulacoes nao podem exceder o total.
            -- Fechamento EXATO, nao <=: a soma das tres populacoes TEM de
            -- reproduzir o elegivel. Com <=, um status novo do ML cairia num
            -- limbo silencioso -- foi assim que 21 pedidos Full e 16 nao-Full
            -- ficaram sem casa ate o FULL-1A-R.
            CONSTRAINT ck_fmfd_populacoes_fecham
                CHECK (paid_orders + cancelled_orders + other_orders
                       = eligible_orders),
            -- Unidade so' existe se houver pedido pago; e pedido pago sem
            -- unidade e' line_item faltando, nao venda de zero item.
            CONSTRAINT ck_fmfd_unidade_exige_pedido_pago
                CHECK ((paid_orders = 0) = (paid_units = 0)),

            CONSTRAINT ck_fmfd_brand_nao_vazia
                CHECK (LENGTH(BTRIM(brand)) > 0)
        )
    """)

    # Consumo previsto: uma marca ao longo dos dias, e o recorte por classe.
    # A PK (ref_date, ...) nao serve nenhum dos dois.
    op.execute("""
        CREATE INDEX idx_fmfd_brand_ref_date
            ON marts.fact_ml_fulfillment_daily (brand, ref_date)
    """)
    op.execute("""
        CREATE INDEX idx_fmfd_classe_ref_date
            ON marts.fact_ml_fulfillment_daily (fulfillment_class, ref_date)
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_ml_fulfillment_daily IS
        'Modalidade logistica do Mercado Livre por (ref_date, brand, '
        'fulfillment_class, logistic_type_original). Competencia COMERCIAL = '
        'date_created do PEDIDO. GMV sem frete, apenas status paid. Join sempre '
        'pedido->envio por (brand, shipping_id), e handling/delivery pertencem a '
        'MESMA coorte do pedido (sem segunda semantica de ref_date). A direcao '
        'inversa do join multiplica '
        'via packs (medido: +11,2% em unidades). unknown e classe PROPRIA -- '
        'pedido sem envio nunca vira non_full por omissao. NAO CONTEM ESTOQUE: '
        'nenhuma coluna de disponibilidade, cobertura ou ruptura, por decisao do '
        'gate FULL-0R. Gate FULL-1A.'
    """)

    for coluna, texto in (
        ("ref_date",
         "Competencia comercial: date_created::date do PEDIDO. Nunca date_closed, "
         "nunca data do envio. UMA coorte para TODA a linha, inclusive handling e "
         "delivery -- sem excecao."),
        ("brand",
         "Marca. Faz parte da chave do join porque 17 shipment_id de agosto/2026 "
         "aparecem em duas marcas distintas."),
        ("fulfillment_class",
         "full = logistic_type 'fulfillment'; non_full = qualquer outro rotulo "
         "presente, INCLUSIVE rotulo futuro desconhecido; unknown = envio ausente "
         "ou logistic_type nulo. unknown jamais e agregado a non_full."),
        ("logistic_type_original",
         "Rotulo bruto da fonte, preservado para serie longa: cross_docking, "
         "xd_drop_off, self_service, drop_off e qualquer valor futuro. Sentinela "
         f"'{SENTINELA_LT}' quando nao ha envio. Sem CHECK de dominio fechado, "
         "de proposito: modalidade nova nao pode quebrar a carga."),
        ("eligible_orders",
         "COUNT(*) de TODOS os status criados no dia. Denominador da taxa de "
         "cancelamento."),
        ("paid_orders",
         "COUNT(*) de status='paid'. Populacao do GMV e das unidades."),
        ("other_orders",
         "COUNT(*) de todo status que nao e paid nem cancelled -- hoje apenas "
         "'partially_refunded'. Fora do GMV por contrato canonico da Torre "
         "(db/seeds/03_status_canonico.sql mapeia para 'returned' e o "
         "MARKETPLACE_DATA_QUALITY_CHECKPOINT registra que o pedido INTEIRO sai, "
         "nao so a parcela reembolsada). Coluna propria para que nenhum status "
         "vire resto aritmetico invisivel."),
        ("cancelled_orders",
         "COUNT(*) de status='cancelled'. Numerador da taxa de cancelamento. "
         "Maturacao medida em jun-ago/2026: p50 0,05 d, p90 4,85 d, p99 14,62 d, "
         "maximo 44,12 d -- um dia fechado ainda recebe cancelamento."),
        ("paid_gmv",
         "SUM(total_amount) dos pedidos pagos. SEM frete (shipping_cost fora), "
         "sem cancelado e sem partially_refunded. Reconciliado contra a planilha "
         "do stakeholder em agosto/2026: 8 de 8 valores por marca exatos."),
        ("paid_units",
         "SUM(quantity) de api.ml_order_line_items dos pedidos pagos. NUNCA de "
         "shipping_items: aquele grao e do ENVIO e infla 11,2% quando projetado "
         "sobre pedidos, por causa dos packs."),
        ("handling_seconds_sum",
         "SUM(date_shipped do envio - date_created do PEDIDO) em segundos, sobre "
         "os pedidos da coorte. Mesma populacao do GMV. Eventos anteriores a "
         "criacao do pedido sao descartados como amostra invalida, nunca somados "
         "como negativo."),
        ("handling_sample_count",
         "PEDIDOS da coorte cujo envio ja tem date_shipped. Divisor da media e "
         "medida da CENSURA: pedido ainda nao despachado fica fora da amostra em "
         "vez de entrar como zero. Guardar a media pronta impediria reagregar."),
        ("delivery_seconds_sum",
         "SUM(date_delivered do envio - date_created do PEDIDO) em segundos, "
         "sobre os pedidos da coorte. Mesmo criterio de handling_seconds_sum."),
        ("delivery_sample_count",
         "PEDIDOS da coorte cujo envio ja foi entregue. Amostra menor que a de "
         "handling: pedido despachado e ainda em transito nao entra."),
        ("unmatched_orders",
         "Pedidos sem envio correspondente. Por construcao so e > 0 na classe "
         "unknown. Publicado com o dado para que a cobertura do join seja "
         "visivel sem consulta extra."),
        ("missing_shipping_items",
         "Envios da classe com shipping_items nulo ou vazio. Nao afeta "
         "paid_units; denuncia lacuna da fonte."),
        ("source_updated_at",
         "MAX(updated_at) das fontes lidas. Relogio da FONTE, naive, sem fuso. "
         "Nao prova maturidade nem completude."),
        ("ingested_at",
         "Momento da publicacao no Neon. Mede frescor da EXECUCAO, nao da "
         "competencia."),
        ("source_run_id",
         "Execucao que publicou a linha, rastreavel em audit.source_sync_run."),
    ):
        op.execute(
            "COMMENT ON COLUMN marts.fact_ml_fulfillment_daily.{} IS '{}'"
            .format(coluna, texto.replace("'", "''"))
        )

    # -----------------------------------------------------------------------
    # Grao de listing. Tabela SEPARADA, e nao colunas extras na de cima, porque
    # a cardinalidade e' outra (centenas de listings por marca/dia) e misturar
    # os dois graos numa tabela so' obrigaria todo consumidor agregado a filtrar
    # para nao contar duas vezes.
    # -----------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE marts.fact_ml_fulfillment_listing_daily (
            ref_date                 DATE        NOT NULL,
            brand                    TEXT        NOT NULL,
            -- item_id do ML (MLB...). Um SKU pode ter varios listings; a
            -- agregacao por SKU pertence a quem consome, com o de-para proprio.
            item_id                  TEXT        NOT NULL,
            fulfillment_class        TEXT        NOT NULL,
            logistic_type_original   TEXT        NOT NULL,

            paid_orders              BIGINT      NOT NULL,
            -- Seguro: line_items fecha com total_amount em 100% dos pedidos
            -- pagos de agosto/2026 (59.123/59.123, diferenca R$ 0,00) e o grao
            -- e 1:1 -- 257.681 pedidos de mai-ago/2026 tem uma linha cada.
            -- Nenhum rateio de total_amount por listing.
            paid_gmv                 NUMERIC     NOT NULL,
            paid_units               BIGINT      NOT NULL,

            ingested_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_run_id            VARCHAR(64) NOT NULL,

            CONSTRAINT pk_fact_ml_fulfillment_listing_daily
                PRIMARY KEY (ref_date, brand, item_id, fulfillment_class,
                             logistic_type_original),

            CONSTRAINT ck_fmfld_classe_conhecida
                CHECK (fulfillment_class IN ('full', 'non_full', 'unknown')),
            CONSTRAINT ck_fmfld_unknown_coerente
                CHECK (
                    (fulfillment_class =  'unknown'
                     AND logistic_type_original =  '{SENTINELA_LT}')
                 OR (fulfillment_class <> 'unknown'
                     AND logistic_type_original <> '{SENTINELA_LT}')
                ),
            CONSTRAINT ck_fmfld_full_e_fulfillment
                CHECK (fulfillment_class <> 'full'
                       OR logistic_type_original = 'fulfillment'),
            CONSTRAINT ck_fmfld_gmv_nao_nan
                CHECK (paid_gmv <> 'NaN'),
            CONSTRAINT ck_fmfld_nao_negativos
                CHECK (paid_gmv >= 0 AND paid_orders >= 0 AND paid_units >= 0),
            -- Linha de listing so' existe para venda paga: a tabela nao carrega
            -- populacao elegivel nem cancelado, entao zero pedido pago aqui
            -- seria linha sem fato.
            CONSTRAINT ck_fmfld_linha_tem_venda
                CHECK (paid_orders > 0 AND paid_units > 0),
            CONSTRAINT ck_fmfld_item_nao_vazio
                CHECK (LENGTH(BTRIM(item_id)) > 0),
            CONSTRAINT ck_fmfld_brand_nao_vazia
                CHECK (LENGTH(BTRIM(brand)) > 0)
        )
    """)

    # Classificacao de listing (so-Full / misto / so-nao-Full) e' um GROUP BY
    # por (brand, item_id) numa janela: este indice serve exatamente esse acesso.
    op.execute("""
        CREATE INDEX idx_fmfld_brand_item_ref_date
            ON marts.fact_ml_fulfillment_listing_daily (brand, item_id, ref_date)
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_ml_fulfillment_listing_daily IS
        'GMV, pedidos e unidades por LISTING e modalidade, grao (ref_date, brand, '
        'item_id, fulfillment_class, logistic_type_original). Alocacao '
        'DETERMINISTICA via api.ml_order_line_items -- nunca rateio de '
        'total_amount. Base da classificacao so-Full / misto / so-nao-Full e da '
        'oportunidade de migracao. Somente pedidos pagos. Gate FULL-1A.'
    """)

    for coluna, texto in (
        ("item_id",
         "Listing do ML (MLB...). Varios listings podem apontar ao mesmo SKU; "
         "agregar por SKU e responsabilidade do consumidor."),
        ("paid_gmv",
         "SUM(quantity * unit_price) de api.ml_order_line_items dos pedidos "
         "pagos. Alocacao determonistica: fecha com total_amount do pedido em "
         "59.123/59.123 casos de agosto/2026, diferenca R$ 0,00."),
        ("paid_units",
         "SUM(quantity) de line_items dos pedidos pagos."),
        ("paid_orders",
         "COUNT(DISTINCT order_id) pagos que tocaram o listing no dia."),
    ):
        op.execute(
            "COMMENT ON COLUMN marts.fact_ml_fulfillment_listing_daily.{} IS '{}'"
            .format(coluna, texto.replace("'", "''"))
        )


def downgrade() -> None:
    # Simetrico e restrito aos objetos desta migration (padrao 010/012/013:
    # DROP INDEX explicito antes do DROP TABLE, ainda que redundante).
    op.execute("DROP INDEX IF EXISTS marts.idx_fmfld_brand_item_ref_date")
    op.execute("DROP TABLE IF EXISTS marts.fact_ml_fulfillment_listing_daily")
    op.execute("DROP INDEX IF EXISTS marts.idx_fmfd_classe_ref_date")
    op.execute("DROP INDEX IF EXISTS marts.idx_fmfd_brand_ref_date")
    op.execute("DROP TABLE IF EXISTS marts.fact_ml_fulfillment_daily")
