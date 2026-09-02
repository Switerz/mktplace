"""Gate UE8-I1 — cria marts.fact_tiktok_order_discounts_daily.

Materializa os DOIS descontos do pedido TikTok, medidos e arbitrados nos gates
UE7-D0 (semantica economica), UE7-D1 (maturacao) e UE7-D2 (arbitragem de fonte).

COMPETENCIA: COMERCIAL, DO PEDIDO
---------------------------------
`ref_date` e' `created_at::date` do pedido, em `raw.tiktok_shop_orders`. NAO e'
data de statement, NAO e' repasse, NAO e' caixa. A competencia financeira e' um
objeto DISTINTO e continua bloqueada: o UE7-D2 mediu que 72,23% dos statements
cruzam mais de um mes comercial (mediana de 2, maximo de 11), de modo que as
duas competencias nao sao intercambiaveis nem aproximaveis uma pela outra.

FONTE E' SNAPSHOT MUTAVEL, SEM HISTORICO DE VERSOES
---------------------------------------------------
`raw.tiktok_shop_orders` tem `uk_tiktok_orders UNIQUE (order_id)`: existe UMA
linha por pedido, atualizada EM LUGAR. Nao ha versionamento, e portanto nao e'
possivel reconstruir quando um valor mudou. O UE7-D2 mediu revisao retroativa
real: julho/2026 perdeu 8 pedidos e R$ 660,25 entre 28/08 e 01/09, ja fechado.

Consequencia direta para quem le esta tabela: ela NAO tem estado "maduro". O
estado terminal e' "fotografia atual da fonte". Quem precisar de numero imutavel
precisa de outra coisa, que nao existe hoje.

DOIS DESCONTOS COM FINANCIADORES DIFERENTES — SEM TOTAL, POR DECISAO
---------------------------------------------------------------------
    seller_discount_signed     <- -SUM(seller_discount)      financiado pela MARCA
    platform_subsidy_amount    <-  SUM(platform_discount)    financiado pelo TIKTOK

O UE7-D0 provou a assimetria por identidade contabil sobre 189.113 pedidos:
`seller_discount_amount` entra em `revenue_breakdown` com sinal NEGATIVO e reduz
a receita liquida; `platform_discount_amount` vive em `supplementary_component`
com sinal POSITIVO e NAO a reduz — a receita liquida do seller INCLUI o subsidio,
porque o TikTok ressarce.

NAO existe `total_discount` nesta tabela, e a ausencia e' intencional. Somar as
duas colunas funde dois financiadores em um numero que nao significa nada:
84% sai do caixa da marca e 16% do caixa da plataforma, e a proporcao varia mes
a mes (3,5% a 7,2% de subsidio sobre o valor cheio, em 15 meses medidos).

SINAL INVERTIDO EXATAMENTE UMA VEZ
-----------------------------------
Na Raw, `seller_discount` e' MAGNITUDE POSITIVA. A inversao para valor assinado
acontece UMA unica vez, no SQL do sync. Aqui os CHECKs travam o resultado:
`seller_discount_signed <= 0` e `platform_subsidy_amount >= 0`. API e UI nunca
podem inverter de novo — o teste de sinal existe para impedir exatamente isso.

Diferente de `012`, aqui EXISTE CHECK de sinal, e isso e' decisao apoiada em
medicao: a `012` guarda componentes financeiros, onde estorno chega com sinal
oposto e um CHECK reprovaria credito legitimo. Esta tabela guarda o desconto DO
PEDIDO, e o UE7-D2 mediu abril a julho de 2026, 5 marcas, ~833 mil pedidos
comerciais: ZERO negativos em `seller_discount` e ZERO em `platform_discount`.
Se aparecer negativo, e' mudanca de contrato da fonte e TEM de falhar alto.

CANCELADOS SEPARADOS, NUNCA MISTURADOS
---------------------------------------
`CANCELLED` tem colunas proprias, prefixadas. Nao entra em `commercial_*`, e a
disjuncao e' verificada no sync. Motivo: sao R$ 1,5-2,2 milhoes por mes de
desconto em venda desfeita — incluir infla o desconto de vendas efetivas, omitir
esconde promocao que nao virou receita. As duas leituras precisam existir.

`UNPAID` e `ON_HOLD` NAO tem coluna aqui: nao sao venda nem venda desfeita, e nao
ha consumidor. Sao contados no fechamento de populacao do sync e registrados em
`audit.data_quality_check` — a evidencia existe na execucao, sem coluna morta.

NULO NAO E' ZERO — E AQUI NADA MONETARIO E' ANULAVEL
-----------------------------------------------------
Todas as colunas monetarias sao `NOT NULL`, ao contrario da `012`. Justificativa
medida: o UE7-D2 provou ZERO nulos em `seller_discount` e `platform_discount` em
abril-julho/2026. O sync tem preflight que BLOQUEIA se um campo monetario
obrigatorio vier nulo. Se a fonte mudar, a carga falha — que e' o comportamento
desejado. `COALESCE(..., 0)` e' PROIBIDO em todo o caminho: converter ausencia em
zero inventaria medicao.

Ausencia de pedidos num dia produz AUSENCIA DE LINHA, nunca linha zerada. Zero em
`commercial_orders` so' e' publicado quando a marca teve pedido de ALGUM status
naquele dia — ai o zero e' medido, com evidencia de atividade.

O `CHECK (<> 'NaN')` e' explicito em cada numerica porque em Postgres
`'NaN'::numeric` sobrevive a qualquer comparacao de ordem: sem ele, um NaN
propagado por SUM entraria em silencio, e ate o CHECK de sinal o deixaria passar.

TRES RELOGIOS DISTINTOS DE PROCEDENCIA
---------------------------------------
O preflight do UE8-I1 mediu, sobre 2.692.671 pedidos deduplicados de todo o
historico desta tabela (jun/2025 a set/2026):

    updated_at_tiktok  menor valor 2025-06-06   0 nulos
    updated_at         menor valor 2026-06-12   0 nulos   >= updated_at_tiktok em 100%

`created_at` comeca em 2025-06-04. `updated_at_tiktok` acompanha o pedido desde a
origem; `updated_at` tem piso em junho/2026 -- sinal de que a coluna foi
reescrita em bloco pela nossa ingestao. Sao grandezas diferentes, e por isso
viram DUAS colunas, nao uma:

    source_max_updated_at = MAX(updated_at_tiktok)   quando o TIKTOK mexeu
    raw_max_updated_at    = MAX(updated_at)          quando a NOSSA Raw incorporou
    synced_at                                        quando o serving publicou

Confundir as tres esconde exatamente o que o diagnostico precisa distinguir:
revisao na origem, reprocessamento da nossa ingestao e republicacao do serving.

Ambas sao TECNICAS, naive e anulaveis. Nenhuma e' competencia, nenhuma prova
maturidade, nenhuma reconstroi versoes e nenhuma pode ser rotulada como BRT.
Anulaveis DEFENSIVAMENTE, nao por nulo observado -- hoje sao 0 nulos nas duas --
mas o piso de `updated_at` mostra que reescrita em bloco acontece, e `NOT NULL`
num campo que e' so' diagnostico transformaria isso em falha de carga.

As medicoes acima valem para `raw.tiktok_shop_orders` e NAO se estendem a outras
tabelas: `raw.tiktok_payments_by_order` tem historia propria de `updated_at`.

SEM TABELA DE SYNC_STATE
-------------------------
A `012` precisa de watermark porque seu incremental e' por `updated_at`. Aqui o
incremental e' por JANELA DE DATA (10 dias fechados), recarregada integralmente:
nao ha limite superior a persistir entre execucoes. Uma tabela de estado sem
leitor seria divida.

NAO APLICADA NESTA TASK: escrita no worktree isolado, nao executada contra banco
algum. Aplicacao e' escopo do UE8-I2.
"""
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sem IF NOT EXISTS (padrao de 006-012): colisao tem de falhar alto.
    op.execute("""
        CREATE TABLE marts.fact_tiktok_order_discounts_daily (
            -- Grao: (ref_date, brand). ref_date e' created_at::date do pedido --
            -- competencia COMERCIAL, coorte do PEDIDO, nunca do statement.
            ref_date                          DATE        NOT NULL,
            brand                             TEXT        NOT NULL,

            -- Populacao comercial: os cinco status de COMMERCIAL_ORDER_STATUSES
            -- (contrato DQ-TK1, pipelines/connectors/tiktok/connector.py).
            commercial_orders                 BIGINT      NOT NULL,
            official_gmv                      NUMERIC     NOT NULL,
            full_product_value                NUMERIC     NOT NULL,

            -- Financiado pela MARCA. Assinado negativo: reduz receita.
            seller_discount_signed            NUMERIC     NOT NULL,
            -- Financiado pelo TIKTOK. Positivo: nao reduz receita da marca,
            -- ja esta incorporado a receita economica. NUNCA somar com o de cima.
            platform_subsidy_amount           NUMERIC     NOT NULL,

            -- Populacao CANCELLED, disjunta da comercial. Venda desfeita.
            cancelled_orders                  BIGINT      NOT NULL,
            cancelled_seller_discount_signed  NUMERIC     NOT NULL,
            cancelled_platform_subsidy_amount NUMERIC     NOT NULL,

            -- Procedencia TECNICA, sem fuso, anulaveis. Dois relogios DISTINTOS:
            -- o do TikTok e o da nossa ingestao. Nenhum prova maturidade.
            source_max_updated_at             TIMESTAMP,
            raw_max_updated_at                TIMESTAMP,

            synced_at                         TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- NOT NULL: toda linha e' produto de uma publicacao concluida, e
            -- publicacao sem run_id nao e' rastreavel.
            source_run_id                     VARCHAR(64) NOT NULL,

            CONSTRAINT pk_fact_tiktok_order_discounts_daily
                PRIMARY KEY (ref_date, brand),

            -- ---------------------------------------------------------------
            -- 6 anti-NaN. 'NaN'::numeric passa por qualquer comparacao de ordem,
            -- inclusive pelos CHECKs de sinal abaixo: precisa de CHECK proprio.
            -- ---------------------------------------------------------------
            CONSTRAINT ck_ftodd_gmv_nao_nan
                CHECK (official_gmv <> 'NaN'),
            CONSTRAINT ck_ftodd_fpv_nao_nan
                CHECK (full_product_value <> 'NaN'),
            CONSTRAINT ck_ftodd_sd_nao_nan
                CHECK (seller_discount_signed <> 'NaN'),
            CONSTRAINT ck_ftodd_ps_nao_nan
                CHECK (platform_subsidy_amount <> 'NaN'),
            CONSTRAINT ck_ftodd_csd_nao_nan
                CHECK (cancelled_seller_discount_signed <> 'NaN'),
            CONSTRAINT ck_ftodd_cps_nao_nan
                CHECK (cancelled_platform_subsidy_amount <> 'NaN'),

            -- ---------------------------------------------------------------
            -- 4 de sinal. Ao contrario da 012, aqui HA CHECK: a fonte e' o
            -- pedido, nao o financeiro, e 4 meses x 5 marcas nao produziram um
            -- unico negativo. Negativo aqui e' mudanca de contrato.
            -- ---------------------------------------------------------------
            CONSTRAINT ck_ftodd_sd_nao_positivo
                CHECK (seller_discount_signed <= 0),
            CONSTRAINT ck_ftodd_csd_nao_positivo
                CHECK (cancelled_seller_discount_signed <= 0),
            CONSTRAINT ck_ftodd_ps_nao_negativo
                CHECK (platform_subsidy_amount >= 0),
            CONSTRAINT ck_ftodd_cps_nao_negativo
                CHECK (cancelled_platform_subsidy_amount >= 0),

            -- ---------------------------------------------------------------
            -- 4 de nao-negatividade: aritmetica de contagem e de valor bruto,
            -- nao semantica de sinal financeiro.
            -- ---------------------------------------------------------------
            CONSTRAINT ck_ftodd_gmv_nao_negativo
                CHECK (official_gmv >= 0),
            CONSTRAINT ck_ftodd_fpv_nao_negativo
                CHECK (full_product_value >= 0),
            CONSTRAINT ck_ftodd_commercial_orders_nao_negativo
                CHECK (commercial_orders >= 0),
            CONSTRAINT ck_ftodd_cancelled_orders_nao_negativo
                CHECK (cancelled_orders >= 0),

            -- 1 de integridade.
            CONSTRAINT ck_ftodd_brand_nao_vazia
                CHECK (LENGTH(BTRIM(brand)) > 0)
        )
    """)

    # Consumo previsto em Canais: uma marca ao longo dos dias. A PK
    # (ref_date, brand) nao serve esse acesso.
    op.execute("""
        CREATE INDEX idx_ftodd_brand_ref_date
            ON marts.fact_tiktok_order_discounts_daily (brand, ref_date)
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_tiktok_order_discounts_daily IS
        'Descontos do PEDIDO no TikTok Shop, grao (ref_date, brand). Competencia '
        'COMERCIAL = created_at::date do pedido, NUNCA data de statement. A fonte '
        '(raw.tiktok_shop_orders) e SNAPSHOT MUTAVEL sem historico de versoes: um '
        'dia fechado pode mudar retroativamente, e esta tabela NAO tem estado '
        'maduro. NAO e receita economica, NAO e settlement, NAO e caixa, NAO e '
        'margem. seller_discount_signed (marca) e platform_subsidy_amount (TikTok) '
        'tem financiadores DIFERENTES e NUNCA podem ser somados. Gate UE8-I1.'
    """)

    for coluna, texto in (
        ("ref_date",
         "Competencia comercial: created_at::date do pedido. Nunca statement."),
        ("brand",
         "Marca, restrita a BRANDS_IN_SCOPE do conector TikTok (DQ-TK1)."),
        ("commercial_orders",
         "COUNT(*) dos cinco status de COMMERCIAL_ORDER_STATUSES. Zero so' e "
         "publicado quando a marca teve pedido de algum status no dia."),
        ("official_gmv",
         "SUM(total_amount) da populacao comercial. GMV oficial vigente."),
        ("full_product_value",
         "SUM(sub_total + seller_discount + platform_discount) da populacao "
         "comercial. Valor cheio antes de qualquer desconto; e o valor que sai "
         "na nota fiscal (UE6-B2, residuo de 0,64%)."),
        ("seller_discount_signed",
         "-SUM(seller_discount) da populacao comercial. Financiado pela MARCA, "
         "reduz a receita. Assinado negativo; a inversao ocorre uma unica vez, "
         "no SQL do sync. NUNCA somar com platform_subsidy_amount."),
        ("platform_subsidy_amount",
         "SUM(platform_discount) da populacao comercial. Financiado pelo TIKTOK: "
         "o comprador paga menos e a marca recebe o valor cheio. Ja esta "
         "incorporado a receita economica e NAO deve ser somado a ela de novo."),
        ("cancelled_orders",
         "COUNT(*) de CANCELLED. Populacao disjunta da comercial."),
        ("cancelled_seller_discount_signed",
         "-SUM(seller_discount) de CANCELLED. Venda desfeita, jamais somada aos "
         "valores comerciais."),
        ("cancelled_platform_subsidy_amount",
         "SUM(platform_discount) de CANCELLED."),
        ("source_max_updated_at",
         "MAX(updated_at_tiktok): quando o TIKTOK declarou a ultima alteracao "
         "do pedido. TECNICO e NAIVE -- sem timezone, nunca rotular como BRT. "
         "Nao prova maturidade, nao reconstroi revisoes, nao e competencia."),
        ("raw_max_updated_at",
         "MAX(updated_at): quando a NOSSA Raw incorporou a versao. Relogio "
         "DIFERENTE de source_max_updated_at -- distingue revisao na origem de "
         "reprocessamento da ingestao. TECNICO, naive, nunca BRT."),
        ("synced_at",
         "Momento da publicacao no Neon: o terceiro relogio. Mede frescor da "
         "EXECUCAO, nao completude da competencia nem revisao da fonte."),
        ("source_run_id",
         "Execucao que publicou a linha, para rastreabilidade em "
         "audit.source_sync_run."),
    ):
        op.execute(
            "COMMENT ON COLUMN marts.fact_tiktok_order_discounts_daily.{} IS '{}'"
            .format(coluna, texto.replace("'", "''"))
        )


def downgrade() -> None:
    # Restrito aos objetos criados por esta migration (padrao de 010 e 012:
    # DROP INDEX explicito antes do DROP TABLE, ainda que redundante).
    op.execute("DROP INDEX IF EXISTS marts.idx_ftodd_brand_ref_date")
    op.execute("DROP TABLE IF EXISTS marts.fact_tiktok_order_discounts_daily")
