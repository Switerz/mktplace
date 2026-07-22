-- ============================================================================
-- DRAFT — NÃO EXECUTADO EM NENHUM BANCO (Fase Staging Shopee 2A — draft
-- não aplicado; contrato original da Fase Staging Shopee 1, revisado nas
-- rodadas de Gate 2B — source_metadata de ads e buyer_cpf).
-- Gerado por: python -m pipelines.staging.shopee.build_sql --write
-- Fonte da verdade do contrato: pipelines/staging/shopee/mapping.py
-- Fonte da verdade das validações: pipelines/staging/shopee/validations.py
-- NÃO EDITAR À MÃO — regenerar pelos comandos acima.
--
-- Alvo futuro: schema `silver` do Data Mart (convenção confirmada por
-- inspeção read-only em 2026-07-04: staging tipada de marketplaces usa
-- silver.stg_* — ex.: silver.stg_ml_orders, silver.stg_tiktok_orders).
-- Execução exigirá credencial de escrita dedicada e aprovação explícita.
-- FKs físicas de raw_id/file_id: funcionam de imediato se aplicadas com a
-- MESMA credencial de escrita da Raw (.env.shopee-write.local, role
-- "postgres", dona das tabelas raw.shopee_*, já tem REFERENCES sobre elas).
-- Só uma FUTURA role de automação dedicada e diferente precisaria de um
-- GRANT REFERENCES prévio do owner da Raw — ver docstring de build_sql.py.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

-- ----------------------------------------------------------------------------
-- silver.stg_shopee_order_item_snapshots — grão: 1 linha física de SKU de pedido, por arquivo/snapshot (igual à Raw)
-- ----------------------------------------------------------------------------
CREATE TABLE silver.stg_shopee_order_item_snapshots (
    raw_id                          bigint NOT NULL PRIMARY KEY,
    file_id                         bigint NOT NULL,
    brand                           varchar(50) NOT NULL,
    source_row_number               integer NOT NULL,
    row_sha256                      char(64) NOT NULL,
    raw_ingested_at                 timestamptz NOT NULL,
    order_id                        varchar(20) NOT NULL,
    buyer_cpf                       text,
    order_status                    text NOT NULL,
    return_refund_status            text,
    cancel_reason                   text,
    order_type                      text,
    is_hot_listing                  boolean,
    is_bmm_order                    boolean,
    is_fbs_order                    boolean,
    is_shopee_owned                 boolean,
    order_created_at                timestamp NOT NULL,
    paid_at                         timestamp,
    ship_by_at                      timestamp,
    shipped_at                      timestamp,
    order_completed_at              timestamp,
    delivered_date                  date,
    cancel_completed_date           date,
    tracking_number                 text,
    shipping_option                 text,
    shipping_method                 text,
    parent_sku_ref                  text,
    sku_ref                         text,
    product_name                    text NOT NULL,
    variation_name                  text,
    quantity                        integer NOT NULL CHECK (quantity >= 0),
    returned_quantity               integer CHECK (returned_quantity >= 0),
    order_products_count            integer CHECK (order_products_count >= 0),
    sku_total_weight_kg             numeric(10,3) CHECK (sku_total_weight_kg <> 'NaN' AND sku_total_weight_kg >= 0),
    order_total_weight_kg           numeric(10,3) CHECK (order_total_weight_kg <> 'NaN' AND order_total_weight_kg >= 0),
    original_price                  numeric(14,2) CHECK (original_price <> 'NaN'),
    deal_price                      numeric(14,2) CHECK (deal_price <> 'NaN'),
    product_subtotal                numeric(14,2) NOT NULL CHECK (product_subtotal <> 'NaN'),
    seller_discount                 numeric(14,2) CHECK (seller_discount <> 'NaN'),
    seller_discount_2               numeric(14,2) CHECK (seller_discount_2 <> 'NaN'),
    shopee_commercial_incentive     numeric(14,2) CHECK (shopee_commercial_incentive <> 'NaN'),
    commercial_action_adjustment    numeric(14,2) CHECK (commercial_action_adjustment <> 'NaN'),
    pix_payment_adjustment          numeric(14,2) CHECK (pix_payment_adjustment <> 'NaN'),
    bmm_shopee_discount             numeric(14,2) CHECK (bmm_shopee_discount <> 'NaN'),
    bmm_seller_discount             numeric(14,2) CHECK (bmm_seller_discount <> 'NaN'),
    coupon_code                     text,
    seller_voucher                  numeric(14,2) CHECK (seller_voucher <> 'NaN'),
    shopee_voucher                  numeric(14,2) CHECK (shopee_voucher <> 'NaN'),
    coin_cashback_voucher_seller    numeric(14,2) CHECK (coin_cashback_voucher_seller <> 'NaN'),
    coupon_incentive                numeric(14,2) CHECK (coupon_incentive <> 'NaN'),
    shopee_coins_offset             integer CHECK (shopee_coins_offset >= 0),
    credit_card_discount_total      numeric(14,2) CHECK (credit_card_discount_total <> 'NaN'),
    order_amount                    numeric(14,2) CHECK (order_amount <> 'NaN'),
    order_grand_total               numeric(14,2) CHECK (order_grand_total <> 'NaN'),
    buyer_paid_shipping_fee         numeric(14,2) CHECK (buyer_paid_shipping_fee <> 'NaN'),
    reverse_shipping_fee            numeric(14,2) CHECK (reverse_shipping_fee <> 'NaN'),
    transaction_fee                 numeric(14,2) CHECK (transaction_fee <> 'NaN'),
    commission_fee_gross            numeric(14,2) CHECK (commission_fee_gross <> 'NaN'),
    commission_fee_net              numeric(14,2) CHECK (commission_fee_net <> 'NaN'),
    service_fee_gross               numeric(14,2) CHECK (service_fee_gross <> 'NaN'),
    service_fee_net                 numeric(14,2) CHECK (service_fee_net <> 'NaN'),
    estimated_shipping_fee          numeric(14,2) CHECK (estimated_shipping_fee <> 'NaN'),
    approx_shipping_discount        numeric(14,2) CHECK (approx_shipping_discount <> 'NaN'),
    delivery_city                   text,
    delivery_state                  text,
    country_code                    varchar(2),
    staging_built_at                timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE silver.stg_shopee_order_item_snapshots IS
    'ATENCAO: NAO E UMA TABELA CANONICA. Staging tipada 1:1 da raw.shopee_order_item_export, grao = linha fisica de SKU por arquivo/snapshot. Exports sobrepostos NAO sao deduplicados aqui -- o mesmo pedido pode aparecer em multiplas linhas com file_id diferentes. NAO fazer SUM/COUNT/agregacao direta sobre esta tabela para metricas de negocio: o resultado pode contar o mesmo pedido mais de uma vez. A selecao do snapshot vigente por pedido (ex.: por raw_ingested_at mais recente, ou por file_id mais alto) e responsabilidade de uma camada Gold futura, ainda nao implementada. CONTEM PII DIRETA: buyer_cpf (CPF do comprador, so template apice) e mantido nesta tabela por decisao de negocio explicita (revisao de 2026-07-06) -- NAO vai para Gold/API/frontend automaticamente, nunca deve ser logado/exibido em preview/erros/testes. Nome, telefone, endereco, CEP, bairro, username e textos livres continuam SO na Raw (excluidos aqui).';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.raw_id IS 'PK; id da linha na tabela raw.shopee_*_export correspondente';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.file_id IS 'FK física para raw.shopee_ingestion_file(file_id) — ver build_sql.py';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.source_row_number IS 'linha física no arquivo original';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.row_sha256 IS 'hash da linha Raw — auditoria de correspondência';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.order_id IS '14 chars [0-9A-Z] em 100% da base; NAO e chave unica desta tabela (repete entre snapshots) — ver aviso de grao no comentario da tabela';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.buyer_cpf IS 'PII DIRETA -- CPF do comprador, preservado como texto puro (sem cast numerico, zeros a esquerda e mascara mantidos exatamente como vieram, sem normalizacao/validacao de digitos nesta fase); string vazia vira NULL. So o template apice tem essa chave (demais marcas -> NULL). Mantido na staging por decisao de negocio explicita (revisao de 2026-07-06) -- NAO propagar para Gold/API/frontend automaticamente. NUNCA logar, imprimir em preview/erro/teste, ou incluir em mensagem de excecao. Sem indice nesta coluna.';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.order_status IS 'valor bruto Shopee; inclui frases como ''O comprador pode pedir uma devolução até YYYY-MM-DD'' — mapeamento canônico é da Gold';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.order_type IS 'só template apice; 100% vazio na base atual';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.is_hot_listing IS 'conjunto documentado: Y/N — qualquer outro valor estoura';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.is_bmm_order IS 'Y/N; só N observado';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.is_fbs_order IS 'Yes/No; ausente no template apice → NULL';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.is_shopee_owned IS 'TRUE/FALSE; ausente no template apice → NULL';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.order_created_at IS '''YYYY-MM-DD HH:MM''; calendário validado (ver semantics.py)';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.paid_at IS 'placeholder ''-'' (48.359 linhas) → NULL';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.shipped_at IS 'semântica observada: data/hora do envio';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.delivered_date IS 'só template não-apice';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.cancel_completed_date IS 'só template não-apice';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.tracking_number IS 'código opaco da transportadora';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.sku_ref IS 'manter texto: 20 SKUs têm formato numérico BR (''9.401,45''-like)';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.quantity IS '1–20 na base; fração estoura no cast, negativo é rejeitado por CHECK';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.returned_quantity IS 'só template apice';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.order_products_count IS 'nível pedido, repetido em cada linha SKU';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.sku_total_weight_kg IS 'unidade inferida kg (0.02–24.0)';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.order_total_weight_kg IS 'nível pedido; unidade inferida kg';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.product_subtotal IS 'GMV bruto da linha; soma auditada R$ 24.859.859,62';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.seller_discount IS '1ª ocorrência do header duplicado';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.seller_discount_2 IS '2ª ocorrência do header duplicado (col22/col23 na apice, dependendo do layout do export — col22 aparece quando a coluna ''Tipo de pedido'' está ausente do template, deslocando as colunas seguintes em 1 posição; col26 nas demais marcas); semântica exata não confirmada — NÃO alimentar Gold enquanto não confirmado com a Shopee/Seller Center';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.shopee_coins_offset IS 'inteiro 0–10.000; unidade (moedas vs centavos) NÃO confirmada — não alimentar Gold enquanto não confirmado';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.order_amount IS 'nível pedido';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.order_grand_total IS 'nível pedido; NÃO é settlement (ver docs/data_contracts.md)';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.approx_shipping_discount IS 'só template apice';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.delivery_city IS 'header ''Cidade'' duplicado; 1ª ocorrência é 100% vazia — o valor real está em Cidade__col57/col58 (apice, dependendo do layout do export — col57 aparece quando a coluna ''Tipo de pedido'' está ausente do template, deslocando as colunas seguintes em 1 posição) / Cidade__col59 (demais)';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.delivery_state IS 'nome por extenso (ex: ''São Paulo'')';
COMMENT ON COLUMN silver.stg_shopee_order_item_snapshots.country_code IS '''BR'' em 100% da base';

-- FKs físicas de lineage — funcionam de imediato com a MESMA credencial
-- de escrita da Raw (.env.shopee-write.local, role "postgres", já é
-- dona de raw.shopee_* e tem REFERENCES sobre elas). Só uma FUTURA role
-- de automação dedicada e diferente precisaria de GRANT REFERENCES
-- prévio do owner da Raw — ver docstring de build_sql.py.
ALTER TABLE silver.stg_shopee_order_item_snapshots ADD CONSTRAINT fk_stg_shopee_order_item_snapshots_raw_id FOREIGN KEY (raw_id) REFERENCES raw.shopee_order_item_export (id);
ALTER TABLE silver.stg_shopee_order_item_snapshots ADD CONSTRAINT fk_stg_shopee_order_item_snapshots_file_id FOREIGN KEY (file_id) REFERENCES raw.shopee_ingestion_file (file_id);

CREATE UNIQUE INDEX uk_stg_shopee_order_item_snapshots_file_row ON silver.stg_shopee_order_item_snapshots (file_id, source_row_number);
CREATE INDEX idx_stg_shopee_order_item_snapshots_brand_created ON silver.stg_shopee_order_item_snapshots (brand, order_created_at);
CREATE INDEX idx_stg_shopee_order_item_snapshots_order_id ON silver.stg_shopee_order_item_snapshots (order_id);
CREATE INDEX idx_stg_shopee_order_item_snapshots_file_id ON silver.stg_shopee_order_item_snapshots (file_id);

-- REVOKE ALL FROM PUBLIC só remove o acesso implícito do pseudo-role
-- PUBLIC — NÃO revoga privilégios já concedidos a roles NOMEADAS via
-- ALTER DEFAULT PRIVILEGES de schema (mesmo achado documentado para as
-- tabelas raw.shopee_* em db/sql/raw/shopee_raw_ddl.sql). Nenhum
-- GRANT/REVOKE adicional é decidido por este DDL.
REVOKE ALL ON silver.stg_shopee_order_item_snapshots FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- silver.stg_shopee_shop_stats — grão: 1 linha física do relatório shop-stats: um dia OU o total do período
-- ----------------------------------------------------------------------------
CREATE TABLE silver.stg_shopee_shop_stats (
    raw_id                          bigint NOT NULL PRIMARY KEY,
    file_id                         bigint NOT NULL,
    brand                           varchar(50) NOT NULL,
    source_row_number               integer NOT NULL,
    row_sha256                      char(64) NOT NULL,
    raw_ingested_at                 timestamptz NOT NULL,
    row_type                        varchar(12) NOT NULL,
    stat_date                       date,
    period_start                    date,
    period_end                      date,
    sales_brl                       numeric(14,2) CHECK (sales_brl <> 'NaN'),
    sales_before_shopee_discounts   numeric(14,2) CHECK (sales_before_shopee_discounts <> 'NaN'),
    sales_per_order                 numeric(14,2) CHECK (sales_per_order <> 'NaN'),
    cancelled_sales                 numeric(14,2) CHECK (cancelled_sales <> 'NaN'),
    refunded_sales                  numeric(14,2) CHECK (refunded_sales <> 'NaN'),
    orders_count                    integer CHECK (orders_count >= 0),
    product_clicks                  integer CHECK (product_clicks >= 0),
    visitors                        integer CHECK (visitors >= 0),
    cancelled_orders                integer CHECK (cancelled_orders >= 0),
    refunded_orders                 integer CHECK (refunded_orders >= 0),
    buyers_count                    integer CHECK (buyers_count >= 0),
    new_buyers_count                integer CHECK (new_buyers_count >= 0),
    existing_buyers_count           integer CHECK (existing_buyers_count >= 0),
    potential_buyers_count          integer CHECK (potential_buyers_count >= 0),
    order_conversion_rate_pct       numeric(8,2) CHECK (order_conversion_rate_pct <> 'NaN' AND order_conversion_rate_pct >= 0),
    repeat_purchase_rate_pct        numeric(8,2) CHECK (repeat_purchase_rate_pct <> 'NaN' AND repeat_purchase_rate_pct >= 0),
    staging_built_at                timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE silver.stg_shopee_shop_stats IS
    'Staging tipada 1:1 da raw.shopee_shop_stats_export. row_type separa linha diaria (''daily'', coluna Data = DD/MM/YYYY) da linha de total do periodo (''period_total'', Data = range) — a Gold decide qual usar; esta camada preserva as duas. Valores monetarios no formato BR (''1.234,56'') e percentuais ''3,84%'' (unidade 0-100). Sem PII.';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.raw_id IS 'PK; id da linha na tabela raw.shopee_*_export correspondente';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.file_id IS 'FK física para raw.shopee_ingestion_file(file_id) — ver build_sql.py';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.source_row_number IS 'linha física no arquivo original';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.row_sha256 IS 'hash da linha Raw — auditoria de correspondência';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.row_type IS '''daily'' | ''period_total''';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.stat_date IS 'preenchida só quando row_type=''daily''';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.period_start IS 'preenchida só quando row_type=''period_total''';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.period_end IS 'preenchida só quando row_type=''period_total''';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.order_conversion_rate_pct IS 'unidade 0–100';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.repeat_purchase_rate_pct IS 'unidade 0–100';

-- FKs físicas de lineage — funcionam de imediato com a MESMA credencial
-- de escrita da Raw (.env.shopee-write.local, role "postgres", já é
-- dona de raw.shopee_* e tem REFERENCES sobre elas). Só uma FUTURA role
-- de automação dedicada e diferente precisaria de GRANT REFERENCES
-- prévio do owner da Raw — ver docstring de build_sql.py.
ALTER TABLE silver.stg_shopee_shop_stats ADD CONSTRAINT fk_stg_shopee_shop_stats_raw_id FOREIGN KEY (raw_id) REFERENCES raw.shopee_shop_stats_export (id);
ALTER TABLE silver.stg_shopee_shop_stats ADD CONSTRAINT fk_stg_shopee_shop_stats_file_id FOREIGN KEY (file_id) REFERENCES raw.shopee_ingestion_file (file_id);

ALTER TABLE silver.stg_shopee_shop_stats ADD CONSTRAINT ck_stg_shopee_shop_stats_row_type CHECK ((row_type = 'daily' AND stat_date IS NOT NULL AND period_start IS NULL AND period_end IS NULL) OR (row_type = 'period_total' AND stat_date IS NULL AND period_start IS NOT NULL AND period_end IS NOT NULL));
ALTER TABLE silver.stg_shopee_shop_stats ADD CONSTRAINT ck_stg_shopee_shop_stats_period_order CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end);
CREATE UNIQUE INDEX uk_stg_shopee_shop_stats_file_row ON silver.stg_shopee_shop_stats (file_id, source_row_number);
CREATE INDEX idx_stg_shopee_shop_stats_brand_date ON silver.stg_shopee_shop_stats (brand, stat_date);

-- REVOKE ALL FROM PUBLIC só remove o acesso implícito do pseudo-role
-- PUBLIC — NÃO revoga privilégios já concedidos a roles NOMEADAS via
-- ALTER DEFAULT PRIVILEGES de schema (mesmo achado documentado para as
-- tabelas raw.shopee_* em db/sql/raw/shopee_raw_ddl.sql). Nenhum
-- GRANT/REVOKE adicional é decidido por este DDL.
REVOKE ALL ON silver.stg_shopee_shop_stats FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- silver.stg_shopee_ads — grão: 1 anúncio agregado no período coberto pelo CSV (sem granularidade diária)
-- ----------------------------------------------------------------------------
CREATE TABLE silver.stg_shopee_ads (
    raw_id                          bigint NOT NULL PRIMARY KEY,
    file_id                         bigint NOT NULL,
    brand                           varchar(50) NOT NULL,
    source_row_number               integer NOT NULL,
    row_sha256                      char(64) NOT NULL,
    raw_ingested_at                 timestamptz NOT NULL,
    report_period_start             date NOT NULL,
    report_period_end               date NOT NULL,
    ad_seq                          integer NOT NULL CHECK (ad_seq >= 0),
    ad_name                         text NOT NULL,
    ad_status                       text NOT NULL,
    ad_type                         text,
    product_id                      text,
    audience_segmentation           text,
    creative                        text,
    bidding_method                  text,
    placement                       text,
    started_at                      timestamp NOT NULL,
    ended_at                        timestamp,
    impressions                     bigint NOT NULL CHECK (impressions >= 0),
    clicks                          bigint NOT NULL CHECK (clicks >= 0),
    ctr_pct                         numeric(8,2) CHECK (ctr_pct <> 'NaN' AND ctr_pct >= 0),
    add_to_cart                     integer CHECK (add_to_cart >= 0),
    add_to_cart_rate_pct            numeric(8,2) CHECK (add_to_cart_rate_pct <> 'NaN' AND add_to_cart_rate_pct >= 0),
    conversions                     integer CHECK (conversions >= 0),
    direct_conversions              integer CHECK (direct_conversions >= 0),
    conversion_rate_pct             numeric(8,2) CHECK (conversion_rate_pct <> 'NaN' AND conversion_rate_pct >= 0),
    direct_conversion_rate_pct      numeric(8,2) CHECK (direct_conversion_rate_pct <> 'NaN' AND direct_conversion_rate_pct >= 0),
    cost_per_conversion             numeric(14,2) CHECK (cost_per_conversion <> 'NaN'),
    cost_per_direct_conversion      numeric(14,2) CHECK (cost_per_direct_conversion <> 'NaN'),
    items_sold                      integer CHECK (items_sold >= 0),
    direct_items_sold               integer CHECK (direct_items_sold >= 0),
    gmv                             numeric(14,2) CHECK (gmv <> 'NaN'),
    direct_revenue                  numeric(14,2) CHECK (direct_revenue <> 'NaN'),
    expense                         numeric(14,2) CHECK (expense <> 'NaN'),
    roas                            numeric(10,4) CHECK (roas <> 'NaN'),
    direct_roas                     numeric(10,4) CHECK (direct_roas <> 'NaN'),
    acos_pct                        numeric(8,2) CHECK (acos_pct <> 'NaN' AND acos_pct >= 0),
    direct_acos_pct                 numeric(8,2) CHECK (direct_acos_pct <> 'NaN' AND direct_acos_pct >= 0),
    product_impressions             integer CHECK (product_impressions >= 0),
    product_clicks                  integer CHECK (product_clicks >= 0),
    product_ctr_pct                 numeric(8,2) CHECK (product_ctr_pct <> 'NaN' AND product_ctr_pct >= 0),
    voucher_amount                  numeric(14,2) CHECK (voucher_amount <> 'NaN'),
    vouchered_sales                 numeric(14,2) CHECK (vouchered_sales <> 'NaN'),
    staging_built_at                timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE silver.stg_shopee_ads IS
    'Staging tipada 1:1 da raw.shopee_ads_export. O periodo do relatorio vem de raw.shopee_ingestion_file.source_metadata (jsonb extraido do preambulo do CSV pelo parser ads_metadata.py) — NUNCA do nome do arquivo. Um manifesto ads sem source_metadata valido reprova a linha inteira na pre-validacao (sem fallback silencioso para NULL); por isso report_period_start/end sao NOT NULL nesta tabela. NAO distribuir valores por dia nesta camada. Sem PII.';
COMMENT ON COLUMN silver.stg_shopee_ads.raw_id IS 'PK; id da linha na tabela raw.shopee_*_export correspondente';
COMMENT ON COLUMN silver.stg_shopee_ads.file_id IS 'FK física para raw.shopee_ingestion_file(file_id) — ver build_sql.py';
COMMENT ON COLUMN silver.stg_shopee_ads.source_row_number IS 'linha física no arquivo original';
COMMENT ON COLUMN silver.stg_shopee_ads.row_sha256 IS 'hash da linha Raw — auditoria de correspondência';
COMMENT ON COLUMN silver.stg_shopee_ads.report_period_start IS 'extraído de raw.shopee_ingestion_file.source_metadata.period_start (preâmbulo do CSV); ausência/invalidez reprova a linha na pré-validação — nunca fallback do nome do arquivo';
COMMENT ON COLUMN silver.stg_shopee_ads.report_period_end IS 'extraído de raw.shopee_ingestion_file.source_metadata.period_end (preâmbulo do CSV); ausência/invalidez reprova a linha na pré-validação — nunca fallback do nome do arquivo';
COMMENT ON COLUMN silver.stg_shopee_ads.ad_seq IS 'posição da linha no relatório';
COMMENT ON COLUMN silver.stg_shopee_ads.ad_status IS 'Em Andamento | Pausado | Encerrado';
COMMENT ON COLUMN silver.stg_shopee_ads.ad_type IS 'vazio nos 5 anúncios shop-level (GMV Max Shop)';
COMMENT ON COLUMN silver.stg_shopee_ads.product_id IS '''-'' nos anúncios shop-level → NULL';
COMMENT ON COLUMN silver.stg_shopee_ads.audience_segmentation IS 'só template kokeshi; sempre ''-'' na base atual';
COMMENT ON COLUMN silver.stg_shopee_ads.creative IS 'sempre ''-'' na base atual';
COMMENT ON COLUMN silver.stg_shopee_ads.started_at IS '''DD/MM/YYYY HH:MM:SS''';
COMMENT ON COLUMN silver.stg_shopee_ads.ended_at IS '''Ilimitado'' → NULL (803 de 804)';
COMMENT ON COLUMN silver.stg_shopee_ads.ctr_pct IS '0–100';
COMMENT ON COLUMN silver.stg_shopee_ads.add_to_cart_rate_pct IS '0–100; ''-'' → NULL';
COMMENT ON COLUMN silver.stg_shopee_ads.conversion_rate_pct IS '0–100';
COMMENT ON COLUMN silver.stg_shopee_ads.direct_conversion_rate_pct IS '0–100';
COMMENT ON COLUMN silver.stg_shopee_ads.gmv IS 'soma auditada R$ 16.887.993,55';
COMMENT ON COLUMN silver.stg_shopee_ads.acos_pct IS '0–100 tipicamente, mas pode ultrapassar 100% quando o custo excede a receita — sem CHECK de teto';
COMMENT ON COLUMN silver.stg_shopee_ads.direct_acos_pct IS 'idem — sem CHECK de teto';
COMMENT ON COLUMN silver.stg_shopee_ads.product_impressions IS 'sempre ''-'' na base atual';
COMMENT ON COLUMN silver.stg_shopee_ads.product_clicks IS 'sempre ''-'' na base atual';
COMMENT ON COLUMN silver.stg_shopee_ads.product_ctr_pct IS 'sempre ''-'' na base atual → NULL';

-- FKs físicas de lineage — funcionam de imediato com a MESMA credencial
-- de escrita da Raw (.env.shopee-write.local, role "postgres", já é
-- dona de raw.shopee_* e tem REFERENCES sobre elas). Só uma FUTURA role
-- de automação dedicada e diferente precisaria de GRANT REFERENCES
-- prévio do owner da Raw — ver docstring de build_sql.py.
ALTER TABLE silver.stg_shopee_ads ADD CONSTRAINT fk_stg_shopee_ads_raw_id FOREIGN KEY (raw_id) REFERENCES raw.shopee_ads_export (id);
ALTER TABLE silver.stg_shopee_ads ADD CONSTRAINT fk_stg_shopee_ads_file_id FOREIGN KEY (file_id) REFERENCES raw.shopee_ingestion_file (file_id);

CREATE UNIQUE INDEX uk_stg_shopee_ads_file_row ON silver.stg_shopee_ads (file_id, source_row_number);
CREATE INDEX idx_stg_shopee_ads_brand ON silver.stg_shopee_ads (brand);
CREATE INDEX idx_stg_shopee_ads_file_id ON silver.stg_shopee_ads (file_id);
ALTER TABLE silver.stg_shopee_ads ADD CONSTRAINT ck_stg_shopee_ads_report_period CHECK (report_period_start <= report_period_end);
ALTER TABLE silver.stg_shopee_ads ADD CONSTRAINT ck_stg_shopee_ads_ended_after_started CHECK (ended_at IS NULL OR ended_at >= started_at);

-- REVOKE ALL FROM PUBLIC só remove o acesso implícito do pseudo-role
-- PUBLIC — NÃO revoga privilégios já concedidos a roles NOMEADAS via
-- ALTER DEFAULT PRIVILEGES de schema (mesmo achado documentado para as
-- tabelas raw.shopee_* em db/sql/raw/shopee_raw_ddl.sql). Nenhum
-- GRANT/REVOKE adicional é decidido por este DDL.
REVOKE ALL ON silver.stg_shopee_ads FROM PUBLIC;

COMMIT;
