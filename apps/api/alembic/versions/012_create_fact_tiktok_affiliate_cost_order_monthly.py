"""Gate UE2-B — cria marts.fact_tiktok_affiliate_cost_order_monthly e seu sync_state.

Materializa o contrato canonico de docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md 18.8.
Qualquer divergencia entre este arquivo e a 18.8 e' defeito deste arquivo: a
18.8 e' a unica especificacao executavel.

COMPETENCIA: COMERCIAL, NAO FINANCEIRA
--------------------------------------
`ref_month` e' o mes de `order_create_time` — coorte do PEDIDO. Nao e' o mes do
statement, nao e' repasse reconhecido e nao e' taxa financeira. A UE1-C mediu
24,6% das LINHAS da fonte com `statement_month` diferente do mes do pedido (a
fracao do VALOR que migra nao foi calculada), de modo que as duas competencias
nao sao intercambiaveis nem aproximaveis uma pela outra.

Se a competencia financeira for destravada algum dia, ela recebe um objeto
DISTINTO — por exemplo `marts.fact_tiktok_settlement_fees_monthly`, com grao
`(statement_month, brand)`. Nunca fundir as duas competencias nesta tabela nem
nestas colunas.

TRES COMPONENTES, DELIBERADAMENTE SEM TOTAL
-------------------------------------------
As colunas de negocio sao exatamente tres, extraidas de `fee_breakdown`:

    affiliate_creator_commission  <- fee_breakdown->>'affiliate_commission_amount_before_pit'
    affiliate_partner_commission  <- fee_breakdown->>'affiliate_partner_commission_amount'
    affiliate_ads_commission      <- fee_breakdown->>'affiliate_ads_commission_amount'

NAO existe `affiliate_cost_total` nesta tabela, e a ausencia e' intencional. Qual
subconjunto desses componentes constitui "custo de afiliado" continua sendo o
ponto aberto P2 da auditoria: materializar uma soma agora seria gravar uma
definicao de negocio que ninguem aprovou, com aparencia de fato medido. Consumidor
que precise de total soma explicitamente os componentes que decidir incluir, e
assume a decisao.

`affiliate_commission_amount` (sem `_before_pit`) NAO entra aqui e NUNCA pode ser
somado junto de `..._before_pit`: sao a mesma comissao antes e depois de PIT, e
somar as duas conta o mesmo custo duas vezes.

Tambem ficam fora, por nao serem custo de afiliado: platform commission, SFP
service fee, fee per item, GMV Max ad fee, e as contagens de transactions/orders.

SINAL PRESERVADO — SEM CHECK DE SINAL, DE PROPOSITO
---------------------------------------------------
Os tres componentes sao ASSINADOS e guardados como vem da fonte. Nao ha
`CHECK >= 0` nem `CHECK <= 0` neles, e isso e' decisao, nao esquecimento: a fonte
admite debito, zero e credito na mesma coluna (18.5.1), e reversao/estorno chega
com o sinal oposto ao da transacao original. Um CHECK de sinal reprovaria estorno
legitimo, e aplicar `abs()` faria um credito parecer custo. `abs()` e' PROIBIDO
em todo o caminho.

NULO NAO E' ZERO — E SO OS TRES COMPONENTES SAO ANULAVEIS
---------------------------------------------------------
Componente nulo significa chave ausente/indisponivel no `fee_breakdown`; zero
significa valor medido igual a zero. As tres colunas de componente sao anulaveis
por isso. Se todas as linhas de uma chave tiverem o componente ausente, o agregado
permanece NULO — nunca 0. Converter nulo em zero inventaria medicao.

Todo o resto e' `NOT NULL`. `source_row_count`, `source_max_updated_at` e
`source_run_id` existem para TODA linha do fato, porque toda linha do fato e'
produto de uma publicacao CONCLUIDA: a contagem foi feita, o `updated_at` de
origem foi validado pela fronteira A (que rejeita nulo na fonte) e a execucao tem
identificador. Deixa-las anulaveis permitiria linha "publicada pela metade",
indistinguivel de linha valida na leitura.

O `CHECK (<> 'NaN')` e' explicito em cada numerica porque em Postgres
`'NaN'::numeric` sobrevive a qualquer comparacao de ordem: sem este CHECK, um NaN
propagado por SUM entraria na tabela em silencio. Em coluna anulavel o CHECK
avalia NULL para linha nula, e NULL passa — que e' exatamente o desejado: nulo
permitido, NaN barrado.

WATERMARK E' TECNICO, NUNCA COMPETENCIA
---------------------------------------
`source_max_updated_at` existe para incremental e auditoria. `updated_at` da fonte
nao e' competencia comercial nem financeira e nao aparece em nenhuma agregacao de
negocio. A tabela de sync_state guarda o ultimo limite superior BEM-SUCEDIDO, um
por par (fonte, destino), e sobrevive a restart do pipeline.

AUSENCIA DE WATERMARK E' AUSENCIA DE LINHA, e significa BACKFILL INTEGRAL
OBRIGATORIO — nunca "desde o inicio de uma janela movel". A fonte reforca isso:
`updated_at` so existe a partir de 2026-03-12 enquanto `order_create_time` comeca
em 2025-06-04. Como `last_successful_upper_bound` e' `NOT NULL`, nao existe o
estado intermediario "linha presente com watermark nulo": a linha nasce de um
sucesso e e' REMOVIDA quando o `full` constata fonte vazia.

NAO APLICADA NESTA TASK: escrita no worktree isolado, nao executada contra banco
algum. Aplicacao e' escopo da Task 2/2.
"""
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sem IF NOT EXISTS (padrao de 006-011): colisao tem de falhar alto.
    op.execute("""
        CREATE TABLE marts.fact_tiktok_affiliate_cost_order_monthly (
            -- Grao: (ref_month, brand). ref_month e' o primeiro dia do mes de
            -- order_create_time -- competencia COMERCIAL, coorte do pedido.
            ref_month                     DATE        NOT NULL,
            brand                         TEXT        NOT NULL,

            -- Tres componentes ASSINADOS de afiliado. Anulaveis: nulo = chave
            -- ausente na fonte, zero = valor medido zero. Sem total, por decisao.
            affiliate_creator_commission  NUMERIC,
            affiliate_partner_commission  NUMERIC,
            affiliate_ads_commission      NUMERIC,

            -- Transacoes que passaram por competencia + marca + transaction_type
            -- dentro da fotografia. Nao e' contagem de pedidos.
            -- NOT NULL: toda chave publicada foi contada; nulo aqui nao teria
            -- leitura possivel.
            source_row_count              BIGINT      NOT NULL,

            -- Watermark TECNICO, sem timezone, coerente com a origem (18.8.1).
            -- NOT NULL: a fronteira A ja rejeita updated_at nulo na fonte, entao
            -- toda linha publicada tem watermark validado.
            source_max_updated_at         TIMESTAMP   NOT NULL,

            synced_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- NOT NULL: toda linha do fato e' produto de uma publicacao
            -- concluida, e publicacao sem run_id nao e' rastreavel.
            source_run_id                 VARCHAR(64) NOT NULL,

            CONSTRAINT pk_fact_tiktok_affiliate_cost_order_monthly
                PRIMARY KEY (ref_month, brand),

            -- SEM CHECK DE SINAL nos tres componentes: a fonte admite debito,
            -- zero e credito, e estorno chega com sinal invertido.
            CONSTRAINT ck_ftacom_creator_nao_nan
                CHECK (affiliate_creator_commission <> 'NaN'),
            CONSTRAINT ck_ftacom_partner_nao_nan
                CHECK (affiliate_partner_commission <> 'NaN'),
            CONSTRAINT ck_ftacom_ads_nao_nan
                CHECK (affiliate_ads_commission <> 'NaN'),

            -- Contagem de linhas lidas: nao-negatividade e' aritmetica, nao
            -- semantica de sinal financeiro.
            CONSTRAINT ck_ftacom_row_count_nao_negativo
                CHECK (source_row_count >= 0),

            CONSTRAINT ck_ftacom_brand_nao_vazia
                CHECK (LENGTH(BTRIM(brand)) > 0),

            -- ref_month e' mes, nao dia: o grao declarado tem de ser executavel.
            CONSTRAINT ck_ftacom_ref_month_e_primeiro_dia
                CHECK (ref_month = DATE_TRUNC('month', ref_month)::date)
        )
    """)

    # Consumo previsto em Canais: uma marca ao longo dos meses. A PK
    # (ref_month, brand) nao serve esse acesso.
    op.execute("""
        CREATE INDEX idx_ftacom_brand_ref_month
            ON marts.fact_tiktok_affiliate_cost_order_monthly (brand, ref_month)
    """)

    op.execute("""
        CREATE TABLE marts.fact_tiktok_affiliate_cost_order_monthly_sync_state (
            -- Um watermark por par (fonte, destino), conforme 18.8.7.
            source_table                TEXT        NOT NULL,
            target_table                TEXT        NOT NULL,

            -- Ultimo limite superior BEM-SUCEDIDO. NOT NULL de proposito: a
            -- LINHA so existe depois de uma publicacao bem-sucedida, e ausencia
            -- de watermark e' representada por AUSENCIA DE LINHA -- nunca por
            -- linha com coluna nula. Uma linha meio-preenchida seria um terceiro
            -- estado, e "sem watermark" e "watermark desconhecido" acabariam
            -- tratados como a mesma coisa: a proxima execucao poderia inferir
            -- janela onde nao ha nenhuma.
            last_successful_upper_bound TIMESTAMP   NOT NULL,

            -- Execucao que avancou o watermark, para rastreabilidade. NOT NULL
            -- pela mesma razao: toda linha vem de um sucesso identificavel.
            source_run_id               VARCHAR(64) NOT NULL,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT pk_ftacom_sync_state
                PRIMARY KEY (source_table, target_table),

            CONSTRAINT ck_ftacom_ss_source_nao_vazia
                CHECK (LENGTH(BTRIM(source_table)) > 0),
            CONSTRAINT ck_ftacom_ss_target_nao_vazio
                CHECK (LENGTH(BTRIM(target_table)) > 0)
        )
    """)

    op.execute("""
        COMMENT ON TABLE marts.fact_tiktok_affiliate_cost_order_monthly IS
        'Custo de afiliado do TikTok por coorte de PEDIDO. Grao (ref_month, brand); '
        'competencia COMERCIAL = mes de order_create_time, NAO mes de statement '
        '(24,6% das linhas da fonte cruzam a fronteira mensal). Tres componentes '
        'ASSINADOS de fee_breakdown, sem total: qual subconjunto e "custo de '
        'afiliado" e o ponto aberto P2. NAO e taxa financeira nem repasse '
        'reconhecido; se for usado como tal, o veredito de qualidade deixa de '
        'valer. Contrato canonico: docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md 18.8. '
        'Gate UE2-B.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_affiliate_cost_order_monthly.ref_month IS
        'Primeiro dia do mes de order_create_time (timestamp SEM timezone na fonte). '
        'Competencia comercial, coorte do pedido. Nunca mes de statement.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_affiliate_cost_order_monthly.affiliate_creator_commission IS
        'SUM de fee_breakdown->>affiliate_commission_amount_before_pit. ASSINADO. '
        'NUNCA somar com affiliate_commission_amount (sem _before_pit): sao a mesma '
        'comissao antes e depois de PIT, e somar conta o custo duas vezes. '
        'NULO = chave ausente na fonte; zero = valor medido zero.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_affiliate_cost_order_monthly.affiliate_partner_commission IS
        'SUM de fee_breakdown->>affiliate_partner_commission_amount. ASSINADO. '
        'NULO = chave ausente na fonte; zero = valor medido zero.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_affiliate_cost_order_monthly.affiliate_ads_commission IS
        'SUM de fee_breakdown->>affiliate_ads_commission_amount. ASSINADO. '
        'NULO = chave ausente na fonte; zero = valor medido zero.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_affiliate_cost_order_monthly.source_row_count IS
        'Transacoes que passaram por competencia, marca allowlisted e '
        'transaction_type allowlisted dentro da fotografia da execucao. NAO e '
        'contagem de pedidos nem de linhas brutas da fonte.'
    """)
    op.execute("""
        COMMENT ON COLUMN marts.fact_tiktok_affiliate_cost_order_monthly.source_max_updated_at IS
        'MAX(updated_at) da fonte para esta chave, dentro da fotografia. Watermark '
        'TECNICO, sem timezone. NAO e competencia e nao pertence a nenhuma '
        'agregacao de negocio.'
    """)
    op.execute("""
        COMMENT ON TABLE marts.fact_tiktok_affiliate_cost_order_monthly_sync_state IS
        'Ultimo limite superior de updated_at BEM-SUCEDIDO, um por par '
        '(source_table, target_table). Lido DEPOIS do advisory lock e escrito na '
        'MESMA transacao da publicacao, somente apos staging materializada, '
        'escrita concluida e reconciliacao aprovada. AUSENCIA DE LINHA (nao coluna '
        'nula) significa que nenhuma execucao teve sucesso ainda e exige backfill '
        'integral; a linha e removida quando o full constata fonte vazia. '
        'Gate UE2-B.'
    """)


def downgrade() -> None:
    # Restrito aos objetos criados por esta migration.
    op.execute("DROP TABLE IF EXISTS marts.fact_tiktok_affiliate_cost_order_monthly_sync_state")
    op.execute("DROP INDEX IF EXISTS marts.idx_ftacom_brand_ref_month")
    op.execute("DROP TABLE IF EXISTS marts.fact_tiktok_affiliate_cost_order_monthly")
