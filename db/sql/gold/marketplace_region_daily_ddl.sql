-- OPERACIONAL — Gate 6A, primeira aplicacao de gold.marketplace_region_daily.
-- Executado por pipelines/ingestion/gold_regional/ddl.py::execute_ddl, em
-- transacao unica, sob advisory lock. Ver db/sql/gold/marketplace_region_daily_draft.sql
-- para o historico completo de decisoes/evidencias (schema identico; esse
-- arquivo permanece como registro, nao e mais referenciado pelo codigo).
--
-- Diferenca deliberada em relacao ao draft: aqui NAO se usa
-- "IF NOT EXISTS"/"IF NOT EXISTS" em nenhum CREATE. Para uma PRIMEIRA
-- aplicacao, silenciar "ja existe" mascara uma aplicacao parcial anterior
-- (ex: uma execucao passada que criou a tabela mas falhou num passo
-- seguinte, deixando um schema divergente do esperado) — preferimos que o
-- Postgres rejeite explicitamente com "relation already exists" e o
-- Python trate isso como falha (rollback), nunca um no-op silencioso. Essa
-- e uma camada adicional: o preflight de escrita (write_conn.run_preflight
-- com expect_table_exists=False) ja bloqueia antes de chegar aqui se a
-- tabela existir — este arquivo e defesa em profundidade caso o DDL seja
-- executado sem passar pelo preflight.
--
-- Decisoes de design ja resolvidas (ver docs/regional_design_draft.md):
--   (1) Dedup Shopee aprovada (secao 1.1a/2).
--   (2) Causa raiz da cobertura ML confirmada (secao 1.2a).
--   (3) Barbours nov/2025-mar/2026: Opcao A, sem coluna nova (secao 1.2b).
--   (4) Fonte ML = raw.ml_shipments/raw.ml_shipment_costs (secao 1.2c).
--   (5) Timezone BRT nativa, sem conversao (secao 1.2d).

CREATE TABLE gold.marketplace_region_daily (
    id                          BIGSERIAL PRIMARY KEY,
    date                        DATE NOT NULL,
    marketplace_id              INT NOT NULL,
    loja_id                     INT NOT NULL,
    uf                          CHAR(2) NOT NULL,  -- sigla oficial ou 'XX' = Nao identificada

    gmv                         NUMERIC(14, 2) NOT NULL DEFAULT 0,
    orders                      INT NOT NULL DEFAULT 0,
    units_sold                  INT NOT NULL DEFAULT 0,
    canceled_orders             INT NOT NULL DEFAULT 0,
    returned_orders             INT NOT NULL DEFAULT 0,

    seller_shipping_cost        NUMERIC(14, 2),
    buyer_shipping_fee          NUMERIC(14, 2),
    estimated_shipping_fee      NUMERIC(14, 2),
    reverse_shipping_fee        NUMERIC(14, 2),

    -- Numeradores/denominadores explicitos (NUNCA armazenar so o percentual
    -- pronto) — ver comentarios de coluna abaixo e
    -- docs/regional_design_draft.md secao 3/6.
    uf_known_orders              INT NOT NULL DEFAULT 0,
    uf_eligible_orders           INT NOT NULL DEFAULT 0,
    shipping_cost_covered_orders INT NOT NULL DEFAULT 0,
    shipping_cost_eligible_orders INT NOT NULL DEFAULT 0,

    source_updated_at           TIMESTAMPTZ,
    ingested_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_region_gmv_non_negative CHECK (gmv >= 0),

    CONSTRAINT chk_region_shipping_non_negative CHECK (
        (seller_shipping_cost IS NULL OR (seller_shipping_cost >= 0 AND seller_shipping_cost <> 'NaN'))
        AND (buyer_shipping_fee IS NULL OR (buyer_shipping_fee >= 0 AND buyer_shipping_fee <> 'NaN'))
        AND (estimated_shipping_fee IS NULL OR (estimated_shipping_fee >= 0 AND estimated_shipping_fee <> 'NaN'))
        AND (reverse_shipping_fee IS NULL OR (reverse_shipping_fee >= 0 AND reverse_shipping_fee <> 'NaN'))
    ),

    CONSTRAINT chk_region_uf_valida CHECK (uf IN (
        'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG',
        'PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO',
        'XX'
    )),

    CONSTRAINT uq_region_daily UNIQUE (date, marketplace_id, loja_id, uf)
);

CREATE INDEX ix_region_daily_date ON gold.marketplace_region_daily (date);
CREATE INDEX ix_region_daily_uf ON gold.marketplace_region_daily (uf);
CREATE INDEX ix_region_daily_loja ON gold.marketplace_region_daily (loja_id);

COMMENT ON TABLE gold.marketplace_region_daily IS
    'Aplicado no Gate 6A. Grao: date x marketplace_id x loja_id(=brand) x uf. '
    'TikTok nao tem cobertura de UF em nenhuma fonte mapeada — nunca inserir '
    'linhas TikTok aqui; a API marca TikTok como sem cobertura, nunca GMV=0 '
    'por UF. Dedup Shopee (file_id vencedor por pedido, todas as linhas '
    'desse arquivo preservadas) e fonte ML (raw.ml_shipments/'
    'raw.ml_shipment_costs) documentadas em '
    'pipelines/ingestion/gold_regional/loader.py e '
    'docs/regional_design_draft.md.';

COMMENT ON COLUMN gold.marketplace_region_daily.uf IS
    'Sigla oficial (27 UFs) ou XX para pedidos sem UF identificavel. '
    'Nunca NULL — normalizar na carga, nunca descartar a linha.';

COMMENT ON COLUMN gold.marketplace_region_daily.uf_known_orders IS
    'Numerador de uf_fill_pct: pedidos do grao com UF identificada (!= XX). '
    'Agregar por SOMA entre dias/marcas/UFs, nunca por media do percentual.';

COMMENT ON COLUMN gold.marketplace_region_daily.uf_eligible_orders IS
    'Denominador de uf_fill_pct: total de pedidos elegiveis do grao '
    '(inclui os que cairam em UF=XX). uf_known_orders/uf_eligible_orders = '
    'uf_fill_pct, calculado na API, nunca armazenado pronto.';

COMMENT ON COLUMN gold.marketplace_region_daily.shipping_cost_covered_orders IS
    'Numerador de shipping_cost_coverage_pct: pedidos do grao com custo de '
    'frete associado. Shopee sempre 0 (conceito nao existe na fonte) — API '
    'trata denominador 0 como N/A, nunca "0% cobertura".';

COMMENT ON COLUMN gold.marketplace_region_daily.shipping_cost_eligible_orders IS
    'Denominador de shipping_cost_coverage_pct: total de pedidos pagos do '
    'grao (candidatos a ter custo de frete). Calcular o percentual na API '
    'a partir da soma destes dois campos, nunca armazenar o percentual pronto.';

COMMENT ON COLUMN gold.marketplace_region_daily.seller_shipping_cost IS
    'Custo de frete pago pelo seller (= sender_cost no ML, confirmado por '
    'lineage). NULL para Shopee — sem campo equivalente na fonte, nunca '
    'inventar um substituto.';

COMMENT ON COLUMN gold.marketplace_region_daily.units_sold IS
    'Preenchido para Shopee (SUM(quantity) das linhas de SKU do pedido). '
    'Sempre 0 para ML nesta primeira carga — raw.ml_orders nao expoe '
    'quantidade por pedido sem juntar raw.ml_order_line_items, fora do '
    'escopo desta carga; limitacao conhecida, nao bloqueante.';

COMMENT ON COLUMN gold.marketplace_region_daily.returned_orders IS
    'Shopee: COUNT(return_refund_status IS NOT NULL). ML: sempre 0 nesta '
    'primeira carga — sem sinal limpo de devolucao identificado na '
    'auditoria; limitacao conhecida, nao bloqueante.';
