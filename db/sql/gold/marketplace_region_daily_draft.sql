-- DRAFT — NAO APLICADO.
-- Proposta de schema para gold.marketplace_region_daily (Data Mart).
-- Ver docs/regional_design_draft.md para o design completo, evidencias e
-- riscos.
--
-- Status da auditoria e decisoes de design (Sessoes 4-5, read-only,
-- credencial rotacionada e confirmada pelo usuario):
--   (1) Dedup Shopee — APROVADA. Comparacao campo a campo dos 1.411
--       pedidos com overlap (itens/SKUs, quantidade, subtotal/GMV, taxas,
--       frete, status, datas, geografia): 100% exatamente equivalentes, 0
--       divergencias. MAX(file_id) confirmado monotonico com
--       raw_ingested_at (0 excecoes). Ver docs/regional_design_draft.md
--       secao 1.1a e 2.
--   (2) Causa raiz da cobertura ML (72% total, 47% barbours) — CONFIRMADA:
--       gap de ingestao de shipments do ML restrito a barbours entre
--       novembro/2025 e marco/2026 (nao e limitacao estrutural permanente;
--       resolvido desde maio/2026). Ver docs/regional_design_draft.md
--       secao 1.2a.
--   (3) Decisao de produto para o historico de barbours — TOMADA: Opcao A
--       (manter no historico, expor coverage_warning/coverage_level no
--       contrato de API, nao bloquear a Gold inteira). Nenhuma coluna nova
--       necessaria aqui — os numeradores/denominadores abaixo ja bastam;
--       coverage_warning/coverage_level sao DERIVADOS NA API, nunca
--       armazenados nesta tabela (thresholds podem mudar sem migration).
--       Ver docs/regional_design_draft.md secao 1.2b e 6.
--   (4) Fonte ML para o transform — DECIDIDA: usar raw.ml_shipments/
--       raw.ml_shipment_costs (nao silver.stg_ml_*) — gap material e
--       concentrado nos ultimos 1-3 meses (atraso de sincronizacao), raw e
--       superconjunto de silver nesta auditoria. Ver secao 1.2c.
--   (5) Timezone dos timestamps naive de pedido (ML/Shopee) — CONFIRMADA:
--       ja em horario de Brasilia, sem necessidade de conversao/AT TIME
--       ZONE ao popular a coluna `date` abaixo. Ver secao 1.2d.
--   (6) Aprovacao explicita para aplicar esta DDL no Data Mart — AINDA
--       PENDENTE (Gate 6 separado, decisao de escopo/timing, nao decisao
--       tecnica — todas as decisoes tecnicas bloqueantes estao resolvidas).
--
-- Modulo de auditoria reutilizavel (read-only, testado com conexao falsa):
-- pipelines/reconciliation/audit_marketplace_region_sources.py

CREATE TABLE IF NOT EXISTS gold.marketplace_region_daily (
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
    -- pronto) — agregar dias/marcas/UFs por SOMA dos numeradores e
    -- denominadores, nunca por MEDIA dos percentuais (media de percentuais
    -- distorce e pode escondar um mes/marca ruim atras de uma media boa).
    -- uf_fill_pct = uf_known_orders / uf_eligible_orders (Shopee ~100%; ML
    -- depende de shipping_id/shipment, ver secao 1.2a do design doc).
    -- shipping_cost_coverage_pct = shipping_cost_covered_orders /
    -- shipping_cost_eligible_orders (47%-95% por marca no ML, TikTok N/A).
    --
    -- coverage_warning/coverage_level (contrato de API, secao 6 do design
    -- doc) sao derivados destes 4 campos EM TEMPO DE REQUISICAO — nao
    -- adicionar coluna coverage_warning/coverage_level aqui; thresholds de
    -- classificacao (ex: <50% = "baixa") podem mudar sem exigir migration
    -- se ficarem so na camada de API. E o mecanismo que implementa a
    -- decisao de manter barbours nov/2025-mar/2026 no historico (Opcao A,
    -- secao 1.2b) sem esconder a baixa cobertura desse periodo.
    uf_known_orders             INT NOT NULL DEFAULT 0,
    uf_eligible_orders          INT NOT NULL DEFAULT 0,
    shipping_cost_covered_orders INT NOT NULL DEFAULT 0,
    shipping_cost_eligible_orders INT NOT NULL DEFAULT 0,

    source_updated_at           TIMESTAMPTZ,
    ingested_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- GMV nunca negativo (mesma regra de fact_marketplace_daily_performance).
    CONSTRAINT chk_region_gmv_non_negative CHECK (gmv >= 0),

    -- Custos nao-negativos, com exclusao explicita de NaN — 'NaN'::numeric >= 0
    -- e TRUE em Postgres, entao o CHECK sozinho nao barra NaN (ver
    -- docs/filtros_globais_contrato.md / memoria do projeto:
    -- feedback_postgres_nan_check_gap).
    CONSTRAINT chk_region_shipping_non_negative CHECK (
        (seller_shipping_cost IS NULL OR (seller_shipping_cost >= 0 AND seller_shipping_cost <> 'NaN'))
        AND (buyer_shipping_fee IS NULL OR (buyer_shipping_fee >= 0 AND buyer_shipping_fee <> 'NaN'))
        AND (estimated_shipping_fee IS NULL OR (estimated_shipping_fee >= 0 AND estimated_shipping_fee <> 'NaN'))
        AND (reverse_shipping_fee IS NULL OR (reverse_shipping_fee >= 0 AND reverse_shipping_fee <> 'NaN'))
    ),

    -- UF valida: 27 siglas oficiais + XX (nao identificada).
    CONSTRAINT chk_region_uf_valida CHECK (uf IN (
        'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG',
        'PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO',
        'XX'
    )),

    -- Idempotencia: upsert por (date, marketplace_id, loja_id, uf).
    CONSTRAINT uq_region_daily UNIQUE (date, marketplace_id, loja_id, uf)
);

CREATE INDEX IF NOT EXISTS ix_region_daily_date ON gold.marketplace_region_daily (date);
CREATE INDEX IF NOT EXISTS ix_region_daily_uf ON gold.marketplace_region_daily (uf);
CREATE INDEX IF NOT EXISTS ix_region_daily_loja ON gold.marketplace_region_daily (loja_id);

COMMENT ON TABLE gold.marketplace_region_daily IS
    'DRAFT — nao aplicado (Gate 6 separado, aguardando autorizacao explicita '
    'para aplicar; todas as decisoes tecnicas bloqueantes ja resolvidas, ver '
    'docs/regional_design_draft.md secao 9). Grao: date x marketplace_id x '
    'loja_id(=brand) x uf. TikTok nao tem cobertura de UF em nenhuma fonte '
    'mapeada — nao inserir linhas TikTok aqui; a API deve marcar TikTok '
    'como sem cobertura, nunca como GMV=0 por UF. Dedup Shopee (MAX(file_id) '
    'por pedido) APROVADA apos comparacao campo a campo (secao 1.1a/2). '
    'Transform ML deve ler de raw.ml_shipments/raw.ml_shipment_costs, nao '
    'silver.stg_ml_* (gap material e concentrado nos ultimos meses, secao '
    '1.2c). Cobertura ML por pedido varia 47%-95% por marca — causa raiz '
    'confirmada (gap de ingestao de shipments restrito a barbours, '
    'nov/2025-mar/2026, secao 1.2a); decisao de produto TOMADA: manter no '
    'historico com coverage_warning/coverage_level no contrato de API '
    '(Opcao A, secao 1.2b) — nunca esconder atras de uma media nacional.';

COMMENT ON COLUMN gold.marketplace_region_daily.uf IS
    'Sigla oficial (27 UFs) ou XX para pedidos sem UF identificavel. '
    'Nunca NULL — normalizar na transformacao, nunca descartar a linha.';

COMMENT ON COLUMN gold.marketplace_region_daily.uf_known_orders IS
    'Numerador de uf_fill_pct: pedidos do grao com UF identificada (!= XX). '
    'Agregar por SOMA entre dias/marcas/UFs, nunca por media do percentual.';

COMMENT ON COLUMN gold.marketplace_region_daily.uf_eligible_orders IS
    'Denominador de uf_fill_pct: total de pedidos elegiveis do grao '
    '(inclui os que cairam em UF=XX). uf_known_orders/uf_eligible_orders = '
    'uf_fill_pct, calculado na API, nunca armazenado pronto.';

COMMENT ON COLUMN gold.marketplace_region_daily.shipping_cost_covered_orders IS
    'Numerador de shipping_cost_coverage_pct: pedidos do grao com custo de '
    'frete associado (join shipment+cost resolvido, ver secao 1.2a). '
    'Varia 47%-95% por marca no ML — nunca expor so a media nacional.';

COMMENT ON COLUMN gold.marketplace_region_daily.shipping_cost_eligible_orders IS
    'Denominador de shipping_cost_coverage_pct: total de pedidos pagos do '
    'grao (candidatos a ter custo de frete). Calcular o percentual na API '
    'a partir da soma destes dois campos, nunca armazenar o percentual pronto.';
