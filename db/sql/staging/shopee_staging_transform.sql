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

-- Transformação incremental e idempotente Raw → silver.stg_shopee_*.
-- Ver arquitetura transacional completa no docstring de build_sql.py.
-- Fail-fast: qualquer valor fora do formato/domínio comprovado é
-- contado e ABORTA a transação ANTES de qualquer INSERT (passo 4) —
-- nunca carga parcial silenciosa. As mesmas condições de validação são
-- usadas pelo preview read-only (pipelines/staging/shopee/preview.py).

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '600s';

-- Passo 2: advisory lock de transação — impede duas execuções
-- concorrentes deste script (idempotência concorrente). Namespace
-- fixo e arbitrário desta fase; '1' cobre as 7 tabelas processadas
-- juntas por este script (não há um lock por tabela).
SELECT pg_advisory_xact_lock(84772001, 1);

-- Passo 3: LOCK TABLE em modo compatível com leitura concorrente (SHARE
-- permite SELECT de outras sessões, inclusive um preview rodando ao
-- mesmo tempo), mas bloqueia INSERT/UPDATE/DELETE de OUTRAS sessões nas
-- 4 tabelas Raw/manifesto E nas 3 tabelas staging até o fim desta
-- transação — nem a Raw muda sob os pés da validação, nem a staging
-- pode ser alterada externamente entre o pós-check e o COMMIT. Uma
-- única instrução com todas as 7 tabelas em ordem alfabética fixa
-- (evita deadlock entre execuções concorrentes). O INSERT desta mesma
-- transação no passo 5 não é bloqueado por este próprio SHARE lock —
-- uma transação nunca bloqueia a si mesma.
LOCK TABLE raw.shopee_ads_export, raw.shopee_ingestion_file, raw.shopee_order_item_export, raw.shopee_shop_stats_export, silver.stg_shopee_ads, silver.stg_shopee_order_item_snapshots, silver.stg_shopee_shop_stats IN SHARE MODE;

-- Passo 4 (PRÉ-VALIDAÇÃO): TODAS as fontes, ANTES de qualquer INSERT.
-- Mesma fonte de regras do preview (validations.py) — nunca duas
-- listas divergentes. ~3 scans de pré-validação por fonte (1 agregado
-- + 2 estruturais) = 9 no total das 3 fontes — não mais um scan por
-- condição, e não confundir com o total da transformação completa
-- (passos 5 e 6 somam mais scans à parte). Mensagens contêm só motivo
-- e contagem, nunca payload. Escopo: só linhas AINDA NÃO presentes na
-- staging (anti-join por raw_id) — não revalida formato de linhas já
-- carregadas; na 1ª carga, elegível = 100% da Raw.
DO $$
DECLARE
    c RECORD;
    v_count bigint;
BEGIN
    -- orders: 1 scan agregado cobre 59 condições de linha
    SELECT
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'ID do pedido'), '') IS NULL)) AS c0,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Status do pedido'), '') IS NULL)) AS c1,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Hot Listing'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Hot Listing'), '') NOT IN ('Y', 'N'))) AS c2,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Indicador da Leve Mais por Menos'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Indicador da Leve Mais por Menos'), '') NOT IN ('Y', 'N'))) AS c3,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Pedido FBS'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Pedido FBS'), '') NOT IN ('Yes', 'No'))) AS c4,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Shopee Owned'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Shopee Owned'), '') NOT IN ('TRUE', 'FALSE'))) AS c5,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), '') IS NULL)) AS c6,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), '') IS NOT NULL AND (NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), '') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$' OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer BETWEEN 0 AND 23 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer BETWEEN 0 AND 59)))) AS c7,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-') IS NOT NULL AND (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$' OR NOT ((regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer BETWEEN 0 AND 23 AND (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer BETWEEN 0 AND 59)))) AS c8,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), '') IS NOT NULL AND (NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), '') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$' OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer BETWEEN 0 AND 23 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer BETWEEN 0 AND 59)))) AS c9,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), '') IS NOT NULL AND (NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), '') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$' OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer BETWEEN 0 AND 23 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer BETWEEN 0 AND 59)))) AS c10,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), '') IS NOT NULL AND (NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), '') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$' OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer BETWEEN 0 AND 23 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer BETWEEN 0 AND 59)))) AS c11,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), '') IS NOT NULL AND (NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), '') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2})$' OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END))))) AS c12,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), '') IS NOT NULL AND (NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), '') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2})$' OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END))))) AS c13,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Nome do Produto'), '') IS NULL)) AS c14,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Quantidade'), '') IS NULL)) AS c15,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Quantidade'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Quantidade'), '') !~ '^-?[0-9]+$')) AS c16,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Quantidade') ~ '^-[0-9]')) AS c17,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Returned quantity'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Returned quantity'), '') !~ '^-?[0-9]+$')) AS c18,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Returned quantity') ~ '^-[0-9]')) AS c19,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Número de produtos pedidos'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Número de produtos pedidos'), '') !~ '^-?[0-9]+$')) AS c20,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Número de produtos pedidos') ~ '^-[0-9]')) AS c21,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Peso total SKU'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Peso total SKU'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c22,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Peso total SKU') ~ '^-[0-9]')) AS c23,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Peso total do pedido'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Peso total do pedido'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c24,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Peso total do pedido') ~ '^-[0-9]')) AS c25,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Preço original'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Preço original'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c26,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Preço acordado'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Preço acordado'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c27,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Subtotal do produto'), '') IS NULL)) AS c28,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Subtotal do produto'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Subtotal do produto'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c29,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c30,
        count(*) FILTER (WHERE ((NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col22'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col22'), '') !~ '^-?[0-9]+(\.[0-9]+)?$') OR (NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col23'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col23'), '') !~ '^-?[0-9]+(\.[0-9]+)?$') OR (NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col26'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col26'), '') !~ '^-?[0-9]+(\.[0-9]+)?$'))) AS c31,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Incentivo Shopee para ação comercial'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Incentivo Shopee para ação comercial'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c32,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Ajuste por participação em ação comercial'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Ajuste por participação em ação comercial'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c33,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Ajuste por pagamento via PIX'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Ajuste por pagamento via PIX'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c34,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Desconto Shopee da Leve Mais por Menos'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Desconto Shopee da Leve Mais por Menos'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c35,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Desconto da Leve Mais por Menos do vendedor'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Desconto da Leve Mais por Menos do vendedor'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c36,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Cupom do vendedor'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Cupom do vendedor'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c37,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Cupom'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Cupom'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c38,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Coin Cashback Voucher Amount Sponsored by Seller'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Coin Cashback Voucher Amount Sponsored by Seller'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c39,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Incentivo de cupom'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Incentivo de cupom'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c40,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Compensar Moedas Shopee'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Compensar Moedas Shopee'), '') !~ '^-?[0-9]+$')) AS c41,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Compensar Moedas Shopee') ~ '^-[0-9]')) AS c42,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Total descontado Cartão de Crédito'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Total descontado Cartão de Crédito'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c43,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Valor Total'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Valor Total'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c44,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Total global'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Total global'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c45,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Taxa de envio pagas pelo comprador'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Taxa de envio pagas pelo comprador'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c46,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Taxa de Envio Reversa'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Taxa de Envio Reversa'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c47,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Taxa de transação'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Taxa de transação'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c48,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão bruta'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão bruta'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c49,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão líquida'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão líquida'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c50,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço bruta'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço bruta'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c51,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço líquida'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço líquida'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c52,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Valor estimado do frete'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Valor estimado do frete'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c53,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Desconto de Frete Aproximado'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Desconto de Frete Aproximado'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c54,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'ID do pedido'), '') IS NOT NULL AND btrim(r.raw_payload ->> 'ID do pedido') !~ '^[0-9A-Z]{14}$')) AS c55,
        count(*) FILTER (WHERE (f.file_id IS NULL)) AS c56,
        count(*) FILTER (WHERE (f.file_id IS NOT NULL AND f.source_type <> 'orders')) AS c57,
        count(*) FILTER (WHERE (f.file_id IS NOT NULL AND r.brand <> f.brand)) AS c58
    INTO c
    FROM raw.shopee_order_item_export r LEFT JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id
    WHERE NOT EXISTS (SELECT 1 FROM silver.stg_shopee_order_item_snapshots s WHERE s.raw_id = r.id);
    IF c.c0 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_id: campo obrigatório vazio', c.c0; END IF;
    IF c.c1 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_status: campo obrigatório vazio', c.c1; END IF;
    IF c.c2 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: is_hot_listing: valor fora do formato/domínio esperado', c.c2; END IF;
    IF c.c3 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: is_bmm_order: valor fora do formato/domínio esperado', c.c3; END IF;
    IF c.c4 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: is_fbs_order: valor fora do formato/domínio esperado', c.c4; END IF;
    IF c.c5 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: is_shopee_owned: valor fora do formato/domínio esperado', c.c5; END IF;
    IF c.c6 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_created_at: campo obrigatório vazio', c.c6; END IF;
    IF c.c7 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_created_at: valor fora do formato/domínio esperado', c.c7; END IF;
    IF c.c8 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: paid_at: valor fora do formato/domínio esperado', c.c8; END IF;
    IF c.c9 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: ship_by_at: valor fora do formato/domínio esperado', c.c9; END IF;
    IF c.c10 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: shipped_at: valor fora do formato/domínio esperado', c.c10; END IF;
    IF c.c11 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_completed_at: valor fora do formato/domínio esperado', c.c11; END IF;
    IF c.c12 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: delivered_date: valor fora do formato/domínio esperado', c.c12; END IF;
    IF c.c13 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: cancel_completed_date: valor fora do formato/domínio esperado', c.c13; END IF;
    IF c.c14 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: product_name: campo obrigatório vazio', c.c14; END IF;
    IF c.c15 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: quantity: campo obrigatório vazio', c.c15; END IF;
    IF c.c16 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: quantity: valor fora do formato/domínio esperado', c.c16; END IF;
    IF c.c17 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: quantity: valor negativo', c.c17; END IF;
    IF c.c18 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: returned_quantity: valor fora do formato/domínio esperado', c.c18; END IF;
    IF c.c19 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: returned_quantity: valor negativo', c.c19; END IF;
    IF c.c20 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_products_count: valor fora do formato/domínio esperado', c.c20; END IF;
    IF c.c21 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_products_count: valor negativo', c.c21; END IF;
    IF c.c22 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: sku_total_weight_kg: valor fora do formato/domínio esperado', c.c22; END IF;
    IF c.c23 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: sku_total_weight_kg: valor negativo', c.c23; END IF;
    IF c.c24 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_total_weight_kg: valor fora do formato/domínio esperado', c.c24; END IF;
    IF c.c25 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_total_weight_kg: valor negativo', c.c25; END IF;
    IF c.c26 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: original_price: valor fora do formato/domínio esperado', c.c26; END IF;
    IF c.c27 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: deal_price: valor fora do formato/domínio esperado', c.c27; END IF;
    IF c.c28 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: product_subtotal: campo obrigatório vazio', c.c28; END IF;
    IF c.c29 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: product_subtotal: valor fora do formato/domínio esperado', c.c29; END IF;
    IF c.c30 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: seller_discount: valor fora do formato/domínio esperado', c.c30; END IF;
    IF c.c31 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: seller_discount_2: valor fora do formato/domínio esperado', c.c31; END IF;
    IF c.c32 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: shopee_commercial_incentive: valor fora do formato/domínio esperado', c.c32; END IF;
    IF c.c33 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: commercial_action_adjustment: valor fora do formato/domínio esperado', c.c33; END IF;
    IF c.c34 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: pix_payment_adjustment: valor fora do formato/domínio esperado', c.c34; END IF;
    IF c.c35 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: bmm_shopee_discount: valor fora do formato/domínio esperado', c.c35; END IF;
    IF c.c36 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: bmm_seller_discount: valor fora do formato/domínio esperado', c.c36; END IF;
    IF c.c37 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: seller_voucher: valor fora do formato/domínio esperado', c.c37; END IF;
    IF c.c38 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: shopee_voucher: valor fora do formato/domínio esperado', c.c38; END IF;
    IF c.c39 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: coin_cashback_voucher_seller: valor fora do formato/domínio esperado', c.c39; END IF;
    IF c.c40 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: coupon_incentive: valor fora do formato/domínio esperado', c.c40; END IF;
    IF c.c41 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: shopee_coins_offset: valor fora do formato/domínio esperado', c.c41; END IF;
    IF c.c42 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: shopee_coins_offset: valor negativo', c.c42; END IF;
    IF c.c43 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: credit_card_discount_total: valor fora do formato/domínio esperado', c.c43; END IF;
    IF c.c44 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_amount: valor fora do formato/domínio esperado', c.c44; END IF;
    IF c.c45 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_grand_total: valor fora do formato/domínio esperado', c.c45; END IF;
    IF c.c46 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: buyer_paid_shipping_fee: valor fora do formato/domínio esperado', c.c46; END IF;
    IF c.c47 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: reverse_shipping_fee: valor fora do formato/domínio esperado', c.c47; END IF;
    IF c.c48 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: transaction_fee: valor fora do formato/domínio esperado', c.c48; END IF;
    IF c.c49 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: commission_fee_gross: valor fora do formato/domínio esperado', c.c49; END IF;
    IF c.c50 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: commission_fee_net: valor fora do formato/domínio esperado', c.c50; END IF;
    IF c.c51 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: service_fee_gross: valor fora do formato/domínio esperado', c.c51; END IF;
    IF c.c52 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: service_fee_net: valor fora do formato/domínio esperado', c.c52; END IF;
    IF c.c53 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: estimated_shipping_fee: valor fora do formato/domínio esperado', c.c53; END IF;
    IF c.c54 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: approx_shipping_discount: valor fora do formato/domínio esperado', c.c54; END IF;
    IF c.c55 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: order_id fora do padrão de 14 alfanuméricos maiúsculos', c.c55; END IF;
    IF c.c56 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: linha órfã sem manifesto correspondente', c.c56; END IF;
    IF c.c57 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: source_type do manifesto incompatível com a tabela-filha', c.c57; END IF;
    IF c.c58 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: brand diferente entre linha Raw e manifesto', c.c58; END IF;
    SELECT count(*) INTO v_count FROM (SELECT file_id, source_row_number FROM raw.shopee_order_item_export GROUP BY 1, 2 HAVING count(*) > 1) d;
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: duplicidade de (file_id, source_row_number)', v_count; END IF;
    SELECT count(*) INTO v_count FROM (
    SELECT DISTINCT CASE WHEN row_number() OVER (PARTITION BY f.file_id, h.value ORDER BY h.ordinality) = 1 THEN h.value ELSE h.value || '__col' || (h.ordinality - 1)::text END AS key
    FROM raw.shopee_ingestion_file f,
         LATERAL jsonb_array_elements_text(f.headers_json) WITH ORDINALITY AS h(value, ordinality)
    WHERE f.source_type = 'orders'
) k
WHERE k.key <> ALL(ARRAY['Ajuste por pagamento via PIX', 'Ajuste por participação em ação comercial', 'Bairro', 'CEP', 'CPF do Comprador', 'Cancelar Motivo', 'Cidade', 'Cidade__col57', 'Cidade__col58', 'Cidade__col59', 'Coin Cashback Voucher Amount Sponsored by Seller', 'Compensar Moedas Shopee', 'Cupom', 'Cupom do vendedor', 'Código do Cupom', 'Data da Finalização do Cancelamento', 'Data de criação do pedido', 'Data prevista de envio', 'Desconto Shopee da Leve Mais por Menos', 'Desconto da Leve Mais por Menos do vendedor', 'Desconto de Frete Aproximado', 'Desconto do vendedor', 'Desconto do vendedor__col22', 'Desconto do vendedor__col23', 'Desconto do vendedor__col26', 'Domestic Delivered Date', 'Endereço de entrega', 'Hora completa do pedido', 'Hora do pagamento do pedido', 'Hot Listing', 'ID do pedido', 'Incentivo Shopee para ação comercial', 'Incentivo de cupom', 'Indicador da Leve Mais por Menos', 'Método de envio', 'Nome da variação', 'Nome de usuário (comprador)', 'Nome do Produto', 'Nome do destinatário', 'Nota', 'Nº de referência do SKU principal', 'Número de produtos pedidos', 'Número de rastreamento', 'Número de referência SKU', 'Observação do comprador', 'Opção de envio', 'País', 'Pedido FBS', 'Peso total SKU', 'Peso total do pedido', 'Preço acordado', 'Preço original', 'Quantidade', 'Returned quantity', 'Shopee Owned', 'Status da Devolução / Reembolso', 'Status do pedido', 'Subtotal do produto', 'Taxa de Envio Reversa', 'Taxa de comissão bruta', 'Taxa de comissão líquida', 'Taxa de envio pagas pelo comprador', 'Taxa de serviço bruta', 'Taxa de serviço líquida', 'Taxa de transação', 'Telefone', 'Tempo de Envio', 'Tipo de pedido', 'Total descontado Cartão de Crédito', 'Total global', 'UF', 'Valor Total', 'Valor estimado do frete']::text[]);
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'orders: chave do JSONB fora do contrato (schema drift não mapeado)', v_count; END IF;
    -- shop_stats: 1 scan agregado cobre 35 condições de linha
    SELECT
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Data'), '') IS NULL)) AS c0,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Data') !~ '^([0-9]{2})/([0-9]{2})/([0-9]{4})$' AND btrim(r.raw_payload ->> 'Data') !~ '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$')) AS c1,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Data') ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$' AND NOT ((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[1]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 4 = 0 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 100 <> 0) OR (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)))) AS c2,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Data') ~ '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$' AND (NOT ((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[1]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 4 = 0 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 100 <> 0) OR (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer BETWEEN 1 AND 12 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[4]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer = 2 THEN (CASE WHEN (((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer % 4 = 0 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer % 100 <> 0) OR (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT (((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer * 10000 + (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer * 100 + (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[1]::integer) <= ((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer * 10000 + (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer * 100 + (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[4]::integer))))) AS c3,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Data') ~ '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$' AND (NOT ((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[1]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 4 = 0 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 100 <> 0) OR (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer BETWEEN 1 AND 12 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[4]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer = 2 THEN (CASE WHEN (((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer % 4 = 0 AND (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer % 100 <> 0) OR (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT (((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer * 10000 + (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer * 100 + (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[1]::integer) <= ((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer * 10000 + (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer * 100 + (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[4]::integer))))) AS c4,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') !~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') !~ '^-?[0-9]+,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c5,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') !~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') !~ '^-?[0-9]+,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c6,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') !~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') !~ '^-?[0-9]+,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c7,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') !~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') !~ '^-?[0-9]+,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c8,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') !~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') !~ '^-?[0-9]+,[0-9]+$' AND NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c9,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Pedidos'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Pedidos'), '') !~ '^-?[0-9]+$')) AS c10,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Pedidos') ~ '^-[0-9]')) AS c11,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Cliques Por Produto'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Cliques Por Produto'), '') !~ '^-?[0-9]+$')) AS c12,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Cliques Por Produto') ~ '^-[0-9]')) AS c13,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Visitantes'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Visitantes'), '') !~ '^-?[0-9]+$')) AS c14,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Visitantes') ~ '^-[0-9]')) AS c15,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Pedidos Cancelados'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Pedidos Cancelados'), '') !~ '^-?[0-9]+$')) AS c16,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Pedidos Cancelados') ~ '^-[0-9]')) AS c17,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Pedidos Devolvidos / Reembolsados'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Pedidos Devolvidos / Reembolsados'), '') !~ '^-?[0-9]+$')) AS c18,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Pedidos Devolvidos / Reembolsados') ~ '^-[0-9]')) AS c19,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> '# de compradores'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> '# de compradores'), '') !~ '^-?[0-9]+$')) AS c20,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> '# de compradores') ~ '^-[0-9]')) AS c21,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> '# de novos compradores'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> '# de novos compradores'), '') !~ '^-?[0-9]+$')) AS c22,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> '# de novos compradores') ~ '^-[0-9]')) AS c23,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> '# de compradores existentes'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> '# de compradores existentes'), '') !~ '^-?[0-9]+$')) AS c24,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> '# de compradores existentes') ~ '^-[0-9]')) AS c25,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> '# de compradores em potencial'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> '# de compradores em potencial'), '') !~ '^-?[0-9]+$')) AS c26,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> '# de compradores em potencial') ~ '^-[0-9]')) AS c27,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão de Pedidos'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão de Pedidos'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c28,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Taxa de Conversão de Pedidos') ~ '^-[0-9]')) AS c29,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Repetir Índice de Compras'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'Repetir Índice de Compras'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c30,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Repetir Índice de Compras') ~ '^-[0-9]')) AS c31,
        count(*) FILTER (WHERE (f.file_id IS NULL)) AS c32,
        count(*) FILTER (WHERE (f.file_id IS NOT NULL AND f.source_type <> 'shop_stats')) AS c33,
        count(*) FILTER (WHERE (f.file_id IS NOT NULL AND r.brand <> f.brand)) AS c34
    INTO c
    FROM raw.shopee_shop_stats_export r LEFT JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id
    WHERE NOT EXISTS (SELECT 1 FROM silver.stg_shopee_shop_stats s WHERE s.raw_id = r.id);
    IF c.c0 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: row_type: campo obrigatório vazio', c.c0; END IF;
    IF c.c1 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: row_type: valor fora do formato/domínio esperado', c.c1; END IF;
    IF c.c2 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: stat_date: valor fora do formato/domínio esperado', c.c2; END IF;
    IF c.c3 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: period_start: valor fora do formato/domínio esperado', c.c3; END IF;
    IF c.c4 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: period_end: valor fora do formato/domínio esperado', c.c4; END IF;
    IF c.c5 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: sales_brl: valor fora do formato/domínio esperado', c.c5; END IF;
    IF c.c6 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: sales_before_shopee_discounts: valor fora do formato/domínio esperado', c.c6; END IF;
    IF c.c7 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: sales_per_order: valor fora do formato/domínio esperado', c.c7; END IF;
    IF c.c8 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: cancelled_sales: valor fora do formato/domínio esperado', c.c8; END IF;
    IF c.c9 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: refunded_sales: valor fora do formato/domínio esperado', c.c9; END IF;
    IF c.c10 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: orders_count: valor fora do formato/domínio esperado', c.c10; END IF;
    IF c.c11 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: orders_count: valor negativo', c.c11; END IF;
    IF c.c12 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: product_clicks: valor fora do formato/domínio esperado', c.c12; END IF;
    IF c.c13 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: product_clicks: valor negativo', c.c13; END IF;
    IF c.c14 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: visitors: valor fora do formato/domínio esperado', c.c14; END IF;
    IF c.c15 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: visitors: valor negativo', c.c15; END IF;
    IF c.c16 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: cancelled_orders: valor fora do formato/domínio esperado', c.c16; END IF;
    IF c.c17 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: cancelled_orders: valor negativo', c.c17; END IF;
    IF c.c18 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: refunded_orders: valor fora do formato/domínio esperado', c.c18; END IF;
    IF c.c19 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: refunded_orders: valor negativo', c.c19; END IF;
    IF c.c20 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: buyers_count: valor fora do formato/domínio esperado', c.c20; END IF;
    IF c.c21 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: buyers_count: valor negativo', c.c21; END IF;
    IF c.c22 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: new_buyers_count: valor fora do formato/domínio esperado', c.c22; END IF;
    IF c.c23 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: new_buyers_count: valor negativo', c.c23; END IF;
    IF c.c24 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: existing_buyers_count: valor fora do formato/domínio esperado', c.c24; END IF;
    IF c.c25 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: existing_buyers_count: valor negativo', c.c25; END IF;
    IF c.c26 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: potential_buyers_count: valor fora do formato/domínio esperado', c.c26; END IF;
    IF c.c27 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: potential_buyers_count: valor negativo', c.c27; END IF;
    IF c.c28 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: order_conversion_rate_pct: valor fora do formato/domínio esperado', c.c28; END IF;
    IF c.c29 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: order_conversion_rate_pct: valor negativo', c.c29; END IF;
    IF c.c30 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: repeat_purchase_rate_pct: valor fora do formato/domínio esperado', c.c30; END IF;
    IF c.c31 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: repeat_purchase_rate_pct: valor negativo', c.c31; END IF;
    IF c.c32 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: linha órfã sem manifesto correspondente', c.c32; END IF;
    IF c.c33 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: source_type do manifesto incompatível com a tabela-filha', c.c33; END IF;
    IF c.c34 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: brand diferente entre linha Raw e manifesto', c.c34; END IF;
    SELECT count(*) INTO v_count FROM (SELECT file_id, source_row_number FROM raw.shopee_shop_stats_export GROUP BY 1, 2 HAVING count(*) > 1) d;
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: duplicidade de (file_id, source_row_number)', v_count; END IF;
    SELECT count(*) INTO v_count FROM (
    SELECT DISTINCT CASE WHEN row_number() OVER (PARTITION BY f.file_id, h.value ORDER BY h.ordinality) = 1 THEN h.value ELSE h.value || '__col' || (h.ordinality - 1)::text END AS key
    FROM raw.shopee_ingestion_file f,
         LATERAL jsonb_array_elements_text(f.headers_json) WITH ORDINALITY AS h(value, ordinality)
    WHERE f.source_type = 'shop_stats'
) k
WHERE k.key <> ALL(ARRAY['# de compradores', '# de compradores em potencial', '# de compradores existentes', '# de novos compradores', 'Cliques Por Produto', 'Data', 'Pedidos', 'Pedidos Cancelados', 'Pedidos Devolvidos / Reembolsados', 'Repetir Índice de Compras', 'Taxa de Conversão de Pedidos', 'Vendas (BRL)', 'Vendas Canceladas', 'Vendas Devolvidas / Reembolsadas', 'Vendas Sem os Descontos da Shopee', 'Vendas por Pedido', 'Visitantes']::text[]);
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'shop_stats: chave do JSONB fora do contrato (schema drift não mapeado)', v_count; END IF;
    -- ads: 1 scan agregado cobre 55 condições de linha
    SELECT
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> '#'), '') IS NULL)) AS c0,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> '#'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> '#'), '') !~ '^-?[0-9]+$')) AS c1,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> '#') ~ '^-[0-9]')) AS c2,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Nome do Anúncio'), '') IS NULL)) AS c3,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Status'), '') IS NULL)) AS c4,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Data de Início'), '') IS NULL)) AS c5,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Data de Início'), '') IS NOT NULL AND (NULLIF(btrim(r.raw_payload ->> 'Data de Início'), '') !~ '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$' OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[1]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[3]::integer % 4 = 0 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[3]::integer % 100 <> 0) OR (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[3]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[4]::integer BETWEEN 0 AND 23 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[5]::integer BETWEEN 0 AND 59 AND (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[6]::integer BETWEEN 0 AND 59)))) AS c6,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado') IS NOT NULL AND (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado') !~ '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$' OR NOT ((regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[1]::integer BETWEEN 1 AND (CASE WHEN (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[3]::integer % 4 = 0 AND (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[3]::integer % 100 <> 0) OR (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[3]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[4]::integer BETWEEN 0 AND 23 AND (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[5]::integer BETWEEN 0 AND 59 AND (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[6]::integer BETWEEN 0 AND 59)))) AS c7,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Impressões'), '') IS NULL)) AS c8,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Impressões'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Impressões'), '') !~ '^-?[0-9]+$')) AS c9,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Impressões') ~ '^-[0-9]')) AS c10,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Cliques'), '') IS NULL)) AS c11,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Cliques'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Cliques'), '') !~ '^-?[0-9]+$')) AS c12,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Cliques') ~ '^-[0-9]')) AS c13,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c14,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'CTR') ~ '^-[0-9]')) AS c15,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart'), ''), '-') !~ '^-?[0-9]+$')) AS c16,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Add to Cart') ~ '^-[0-9]')) AS c17,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart Rate'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart Rate'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c18,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Add to Cart Rate') ~ '^-[0-9]')) AS c19,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Conversões'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Conversões'), '') !~ '^-?[0-9]+$')) AS c20,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Conversões') ~ '^-[0-9]')) AS c21,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Conversões Diretas'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Conversões Diretas'), '') !~ '^-?[0-9]+$')) AS c22,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Conversões Diretas') ~ '^-[0-9]')) AS c23,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c24,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Taxa de Conversão') ~ '^-[0-9]')) AS c25,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão Direta'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão Direta'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c26,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Taxa de Conversão Direta') ~ '^-[0-9]')) AS c27,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c28,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão Direta'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão Direta'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c29,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos'), '') !~ '^-?[0-9]+$')) AS c30,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Itens Vendidos') ~ '^-[0-9]')) AS c31,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos Diretos'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos Diretos'), '') !~ '^-?[0-9]+$')) AS c32,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Itens Vendidos Diretos') ~ '^-[0-9]')) AS c33,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'GMV'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'GMV'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c34,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Receita direta'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Receita direta'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c35,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Despesas'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Despesas'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c36,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'ROAS'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'ROAS'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c37,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'ROAS Direto'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'ROAS Direto'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c38,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c39,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'ACOS') ~ '^-[0-9]')) AS c40,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS Direto'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS Direto'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c41,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'ACOS Direto') ~ '^-[0-9]')) AS c42,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Impressões do Produto'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'Impressões do Produto'), ''), '-') !~ '^-?[0-9]+$')) AS c43,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Impressões do Produto') ~ '^-[0-9]')) AS c44,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Cliques de Produtos'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'Cliques de Produtos'), ''), '-') !~ '^-?[0-9]+$')) AS c45,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'Cliques de Produtos') ~ '^-[0-9]')) AS c46,
        count(*) FILTER (WHERE (NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR do Produto'), ''), '-') IS NOT NULL AND NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR do Produto'), ''), '-') !~ '^-?[0-9]+([.,][0-9]+)?%?$')) AS c47,
        count(*) FILTER (WHERE (btrim(r.raw_payload ->> 'CTR do Produto') ~ '^-[0-9]')) AS c48,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Voucher Amount'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Voucher Amount'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c49,
        count(*) FILTER (WHERE (NULLIF(btrim(r.raw_payload ->> 'Vouchered Sales'), '') IS NOT NULL AND NULLIF(btrim(r.raw_payload ->> 'Vouchered Sales'), '') !~ '^-?[0-9]+(\.[0-9]+)?$')) AS c50,
        count(*) FILTER (WHERE (f.source_metadata IS NULL OR jsonb_typeof(f.source_metadata) <> 'object' OR (f.source_metadata ->> 'period_start') IS NULL OR (f.source_metadata ->> 'period_end') IS NULL OR (f.source_metadata ->> 'period_start') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2})$' OR (f.source_metadata ->> 'period_end') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2})$' OR NOT ((regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT ((regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer BETWEEN 1 AND 12 AND (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer BETWEEN 1 AND (CASE WHEN (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer = 2 THEN (CASE WHEN (((regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 4 = 0 AND (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 100 <> 0) OR (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer % 400 = 0) THEN 29 ELSE 28 END) WHEN (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer IN (4, 6, 9, 11) THEN 30 ELSE 31 END)) OR NOT (((regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer * 10000 + (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer * 100 + (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer) <= ((regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer * 10000 + (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer * 100 + (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer)))) AS c51,
        count(*) FILTER (WHERE (f.file_id IS NULL)) AS c52,
        count(*) FILTER (WHERE (f.file_id IS NOT NULL AND f.source_type <> 'ads')) AS c53,
        count(*) FILTER (WHERE (f.file_id IS NOT NULL AND r.brand <> f.brand)) AS c54
    INTO c
    FROM raw.shopee_ads_export r LEFT JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id
    WHERE NOT EXISTS (SELECT 1 FROM silver.stg_shopee_ads s WHERE s.raw_id = r.id);
    IF c.c0 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: ad_seq: campo obrigatório vazio', c.c0; END IF;
    IF c.c1 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: ad_seq: valor fora do formato/domínio esperado', c.c1; END IF;
    IF c.c2 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: ad_seq: valor negativo', c.c2; END IF;
    IF c.c3 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: ad_name: campo obrigatório vazio', c.c3; END IF;
    IF c.c4 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: ad_status: campo obrigatório vazio', c.c4; END IF;
    IF c.c5 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: started_at: campo obrigatório vazio', c.c5; END IF;
    IF c.c6 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: started_at: valor fora do formato/domínio esperado', c.c6; END IF;
    IF c.c7 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: ended_at: valor fora do formato/domínio esperado', c.c7; END IF;
    IF c.c8 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: impressions: campo obrigatório vazio', c.c8; END IF;
    IF c.c9 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: impressions: valor fora do formato/domínio esperado', c.c9; END IF;
    IF c.c10 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: impressions: valor negativo', c.c10; END IF;
    IF c.c11 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: clicks: campo obrigatório vazio', c.c11; END IF;
    IF c.c12 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: clicks: valor fora do formato/domínio esperado', c.c12; END IF;
    IF c.c13 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: clicks: valor negativo', c.c13; END IF;
    IF c.c14 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: ctr_pct: valor fora do formato/domínio esperado', c.c14; END IF;
    IF c.c15 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: ctr_pct: valor negativo', c.c15; END IF;
    IF c.c16 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: add_to_cart: valor fora do formato/domínio esperado', c.c16; END IF;
    IF c.c17 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: add_to_cart: valor negativo', c.c17; END IF;
    IF c.c18 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: add_to_cart_rate_pct: valor fora do formato/domínio esperado', c.c18; END IF;
    IF c.c19 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: add_to_cart_rate_pct: valor negativo', c.c19; END IF;
    IF c.c20 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: conversions: valor fora do formato/domínio esperado', c.c20; END IF;
    IF c.c21 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: conversions: valor negativo', c.c21; END IF;
    IF c.c22 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_conversions: valor fora do formato/domínio esperado', c.c22; END IF;
    IF c.c23 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_conversions: valor negativo', c.c23; END IF;
    IF c.c24 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: conversion_rate_pct: valor fora do formato/domínio esperado', c.c24; END IF;
    IF c.c25 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: conversion_rate_pct: valor negativo', c.c25; END IF;
    IF c.c26 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_conversion_rate_pct: valor fora do formato/domínio esperado', c.c26; END IF;
    IF c.c27 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_conversion_rate_pct: valor negativo', c.c27; END IF;
    IF c.c28 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: cost_per_conversion: valor fora do formato/domínio esperado', c.c28; END IF;
    IF c.c29 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: cost_per_direct_conversion: valor fora do formato/domínio esperado', c.c29; END IF;
    IF c.c30 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: items_sold: valor fora do formato/domínio esperado', c.c30; END IF;
    IF c.c31 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: items_sold: valor negativo', c.c31; END IF;
    IF c.c32 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_items_sold: valor fora do formato/domínio esperado', c.c32; END IF;
    IF c.c33 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_items_sold: valor negativo', c.c33; END IF;
    IF c.c34 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: gmv: valor fora do formato/domínio esperado', c.c34; END IF;
    IF c.c35 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_revenue: valor fora do formato/domínio esperado', c.c35; END IF;
    IF c.c36 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: expense: valor fora do formato/domínio esperado', c.c36; END IF;
    IF c.c37 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: roas: valor fora do formato/domínio esperado', c.c37; END IF;
    IF c.c38 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_roas: valor fora do formato/domínio esperado', c.c38; END IF;
    IF c.c39 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: acos_pct: valor fora do formato/domínio esperado', c.c39; END IF;
    IF c.c40 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: acos_pct: valor negativo', c.c40; END IF;
    IF c.c41 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_acos_pct: valor fora do formato/domínio esperado', c.c41; END IF;
    IF c.c42 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: direct_acos_pct: valor negativo', c.c42; END IF;
    IF c.c43 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: product_impressions: valor fora do formato/domínio esperado', c.c43; END IF;
    IF c.c44 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: product_impressions: valor negativo', c.c44; END IF;
    IF c.c45 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: product_clicks: valor fora do formato/domínio esperado', c.c45; END IF;
    IF c.c46 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: product_clicks: valor negativo', c.c46; END IF;
    IF c.c47 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: product_ctr_pct: valor fora do formato/domínio esperado', c.c47; END IF;
    IF c.c48 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: product_ctr_pct: valor negativo', c.c48; END IF;
    IF c.c49 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: voucher_amount: valor fora do formato/domínio esperado', c.c49; END IF;
    IF c.c50 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: vouchered_sales: valor fora do formato/domínio esperado', c.c50; END IF;
    IF c.c51 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: source_metadata do manifesto ausente ou período do relatório inválido/incompleto (sem fallback do nome do arquivo)', c.c51; END IF;
    IF c.c52 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: linha órfã sem manifesto correspondente', c.c52; END IF;
    IF c.c53 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: source_type do manifesto incompatível com a tabela-filha', c.c53; END IF;
    IF c.c54 > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: brand diferente entre linha Raw e manifesto', c.c54; END IF;
    SELECT count(*) INTO v_count FROM (SELECT file_id, source_row_number FROM raw.shopee_ads_export GROUP BY 1, 2 HAVING count(*) > 1) d;
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: duplicidade de (file_id, source_row_number)', v_count; END IF;
    SELECT count(*) INTO v_count FROM (
    SELECT DISTINCT CASE WHEN row_number() OVER (PARTITION BY f.file_id, h.value ORDER BY h.ordinality) = 1 THEN h.value ELSE h.value || '__col' || (h.ordinality - 1)::text END AS key
    FROM raw.shopee_ingestion_file f,
         LATERAL jsonb_array_elements_text(f.headers_json) WITH ORDINALITY AS h(value, ordinality)
    WHERE f.source_type = 'ads'
) k
WHERE k.key <> ALL(ARRAY['#', 'ACOS', 'ACOS Direto', 'Add to Cart', 'Add to Cart Rate', 'CTR', 'CTR do Produto', 'Cliques', 'Cliques de Produtos', 'Conversões', 'Conversões Diretas', 'Criativo', 'Custo por Conversão', 'Custo por Conversão Direta', 'Data de Encerramento', 'Data de Início', 'Despesas', 'GMV', 'ID do produto', 'Impressões', 'Impressões do Produto', 'Itens Vendidos', 'Itens Vendidos Diretos', 'Método de Lance', 'Nome do Anúncio', 'Posicionamento', 'ROAS', 'ROAS Direto', 'Receita direta', 'Segmentação de Público', 'Status', 'Taxa de Conversão', 'Taxa de Conversão Direta', 'Tipos de Anúncios', 'Voucher Amount', 'Vouchered Sales']::text[]);
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pre-insert falhou -- %: % linha(s)', 'ads: chave do JSONB fora do contrato (schema drift não mapeado)', v_count; END IF;
END $$;

-- Passo 5 (1 leitura+INSERT por fonte, 3 no total): só executam se
-- TODAS as validações acima passaram.

-- ----------------------------------------------------------------------------
-- orders: raw.shopee_order_item_export → silver.stg_shopee_order_item_snapshots
-- ----------------------------------------------------------------------------
INSERT INTO silver.stg_shopee_order_item_snapshots (
    raw_id,
    file_id,
    brand,
    source_row_number,
    row_sha256,
    raw_ingested_at,
    order_id,
    buyer_cpf,
    order_status,
    return_refund_status,
    cancel_reason,
    order_type,
    is_hot_listing,
    is_bmm_order,
    is_fbs_order,
    is_shopee_owned,
    order_created_at,
    paid_at,
    ship_by_at,
    shipped_at,
    order_completed_at,
    delivered_date,
    cancel_completed_date,
    tracking_number,
    shipping_option,
    shipping_method,
    parent_sku_ref,
    sku_ref,
    product_name,
    variation_name,
    quantity,
    returned_quantity,
    order_products_count,
    sku_total_weight_kg,
    order_total_weight_kg,
    original_price,
    deal_price,
    product_subtotal,
    seller_discount,
    seller_discount_2,
    shopee_commercial_incentive,
    commercial_action_adjustment,
    pix_payment_adjustment,
    bmm_shopee_discount,
    bmm_seller_discount,
    coupon_code,
    seller_voucher,
    shopee_voucher,
    coin_cashback_voucher_seller,
    coupon_incentive,
    shopee_coins_offset,
    credit_card_discount_total,
    order_amount,
    order_grand_total,
    buyer_paid_shipping_fee,
    reverse_shipping_fee,
    transaction_fee,
    commission_fee_gross,
    commission_fee_net,
    service_fee_gross,
    service_fee_net,
    estimated_shipping_fee,
    approx_shipping_discount,
    delivery_city,
    delivery_state,
    country_code
)
SELECT
    r.id                                     AS raw_id,
    r.file_id                                AS file_id,
    r.brand                                  AS brand,
    r.source_row_number                      AS source_row_number,
    r.row_sha256                             AS row_sha256,
    r.ingested_at                            AS raw_ingested_at,
    NULLIF(btrim(r.raw_payload ->> 'ID do pedido'), '') AS order_id,
    NULLIF(btrim(r.raw_payload ->> 'CPF do Comprador'), '') AS buyer_cpf,
    NULLIF(btrim(r.raw_payload ->> 'Status do pedido'), '') AS order_status,
    NULLIF(btrim(r.raw_payload ->> 'Status da Devolução / Reembolso'), '') AS return_refund_status,
    NULLIF(btrim(r.raw_payload ->> 'Cancelar Motivo'), '') AS cancel_reason,
    NULLIF(btrim(r.raw_payload ->> 'Tipo de pedido'), '') AS order_type,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Hot Listing'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Hot Listing'), '') = 'Y' THEN true WHEN NULLIF(btrim(r.raw_payload ->> 'Hot Listing'), '') = 'N' THEN false ELSE ((NULLIF(btrim(r.raw_payload ->> 'Hot Listing'), '') || 'CONTRATO_INVALIDO')::boolean) END) AS is_hot_listing,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Indicador da Leve Mais por Menos'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Indicador da Leve Mais por Menos'), '') = 'Y' THEN true WHEN NULLIF(btrim(r.raw_payload ->> 'Indicador da Leve Mais por Menos'), '') = 'N' THEN false ELSE ((NULLIF(btrim(r.raw_payload ->> 'Indicador da Leve Mais por Menos'), '') || 'CONTRATO_INVALIDO')::boolean) END) AS is_bmm_order,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Pedido FBS'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Pedido FBS'), '') = 'Yes' THEN true WHEN NULLIF(btrim(r.raw_payload ->> 'Pedido FBS'), '') = 'No' THEN false ELSE ((NULLIF(btrim(r.raw_payload ->> 'Pedido FBS'), '') || 'CONTRATO_INVALIDO')::boolean) END) AS is_fbs_order,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Shopee Owned'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Shopee Owned'), '') = 'TRUE' THEN true WHEN NULLIF(btrim(r.raw_payload ->> 'Shopee Owned'), '') = 'FALSE' THEN false ELSE ((NULLIF(btrim(r.raw_payload ->> 'Shopee Owned'), '') || 'CONTRATO_INVALIDO')::boolean) END) AS is_shopee_owned,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), '') IS NULL THEN NULL ELSE make_timestamp((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de criação do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer, 0) END) AS order_created_at,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-') IS NULL THEN NULL ELSE make_timestamp((regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Hora do pagamento do pedido'), ''), '-'), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer, 0) END) AS paid_at,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), '') IS NULL THEN NULL ELSE make_timestamp((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data prevista de envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer, 0) END) AS ship_by_at,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), '') IS NULL THEN NULL ELSE make_timestamp((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Tempo de Envio'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer, 0) END) AS shipped_at,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), '') IS NULL THEN NULL ELSE make_timestamp((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[3]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[4]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Hora completa do pedido'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2})$'))[5]::integer, 0) END) AS order_completed_at,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), '') IS NULL THEN NULL ELSE make_date((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Domestic Delivered Date'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer) END) AS delivered_date,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), '') IS NULL THEN NULL ELSE make_date((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data da Finalização do Cancelamento'), ''), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer) END) AS cancel_completed_date,
    NULLIF(btrim(r.raw_payload ->> 'Número de rastreamento'), '') AS tracking_number,
    NULLIF(btrim(r.raw_payload ->> 'Opção de envio'), '') AS shipping_option,
    NULLIF(btrim(r.raw_payload ->> 'Método de envio'), '') AS shipping_method,
    NULLIF(btrim(r.raw_payload ->> 'Nº de referência do SKU principal'), '') AS parent_sku_ref,
    NULLIF(btrim(r.raw_payload ->> 'Número de referência SKU'), '') AS sku_ref,
    NULLIF(btrim(r.raw_payload ->> 'Nome do Produto'), '') AS product_name,
    NULLIF(btrim(r.raw_payload ->> 'Nome da variação'), '') AS variation_name,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Quantidade'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Quantidade'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Quantidade'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Quantidade'), '') || 'CONTRATO_INVALIDO')::integer) END) AS quantity,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Returned quantity'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Returned quantity'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Returned quantity'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Returned quantity'), '') || 'CONTRATO_INVALIDO')::integer) END) AS returned_quantity,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Número de produtos pedidos'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Número de produtos pedidos'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Número de produtos pedidos'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Número de produtos pedidos'), '') || 'CONTRATO_INVALIDO')::integer) END) AS order_products_count,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Peso total SKU'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Peso total SKU'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Peso total SKU'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Peso total SKU'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS sku_total_weight_kg,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Peso total do pedido'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Peso total do pedido'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Peso total do pedido'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Peso total do pedido'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS order_total_weight_kg,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Preço original'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Preço original'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Preço original'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Preço original'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS original_price,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Preço acordado'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Preço acordado'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Preço acordado'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Preço acordado'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS deal_price,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Subtotal do produto'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Subtotal do produto'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Subtotal do produto'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Subtotal do produto'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS product_subtotal,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS seller_discount,
    COALESCE((CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col22'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col22'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col22'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col22'), '') || 'CONTRATO_INVALIDO')::numeric) END), (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col23'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col23'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col23'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col23'), '') || 'CONTRATO_INVALIDO')::numeric) END), (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col26'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col26'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col26'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Desconto do vendedor__col26'), '') || 'CONTRATO_INVALIDO')::numeric) END)) AS seller_discount_2,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Incentivo Shopee para ação comercial'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Incentivo Shopee para ação comercial'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Incentivo Shopee para ação comercial'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Incentivo Shopee para ação comercial'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS shopee_commercial_incentive,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Ajuste por participação em ação comercial'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Ajuste por participação em ação comercial'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Ajuste por participação em ação comercial'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Ajuste por participação em ação comercial'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS commercial_action_adjustment,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Ajuste por pagamento via PIX'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Ajuste por pagamento via PIX'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Ajuste por pagamento via PIX'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Ajuste por pagamento via PIX'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS pix_payment_adjustment,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto Shopee da Leve Mais por Menos'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto Shopee da Leve Mais por Menos'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Desconto Shopee da Leve Mais por Menos'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Desconto Shopee da Leve Mais por Menos'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS bmm_shopee_discount,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto da Leve Mais por Menos do vendedor'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto da Leve Mais por Menos do vendedor'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Desconto da Leve Mais por Menos do vendedor'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Desconto da Leve Mais por Menos do vendedor'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS bmm_seller_discount,
    NULLIF(btrim(r.raw_payload ->> 'Código do Cupom'), '') AS coupon_code,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Cupom do vendedor'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Cupom do vendedor'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Cupom do vendedor'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Cupom do vendedor'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS seller_voucher,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Cupom'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Cupom'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Cupom'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Cupom'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS shopee_voucher,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Coin Cashback Voucher Amount Sponsored by Seller'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Coin Cashback Voucher Amount Sponsored by Seller'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Coin Cashback Voucher Amount Sponsored by Seller'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Coin Cashback Voucher Amount Sponsored by Seller'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS coin_cashback_voucher_seller,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Incentivo de cupom'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Incentivo de cupom'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Incentivo de cupom'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Incentivo de cupom'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS coupon_incentive,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Compensar Moedas Shopee'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Compensar Moedas Shopee'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Compensar Moedas Shopee'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Compensar Moedas Shopee'), '') || 'CONTRATO_INVALIDO')::integer) END) AS shopee_coins_offset,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Total descontado Cartão de Crédito'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Total descontado Cartão de Crédito'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Total descontado Cartão de Crédito'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Total descontado Cartão de Crédito'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS credit_card_discount_total,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Valor Total'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Valor Total'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Valor Total'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Valor Total'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS order_amount,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Total global'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Total global'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Total global'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Total global'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS order_grand_total,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de envio pagas pelo comprador'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de envio pagas pelo comprador'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Taxa de envio pagas pelo comprador'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Taxa de envio pagas pelo comprador'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS buyer_paid_shipping_fee,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de Envio Reversa'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de Envio Reversa'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Taxa de Envio Reversa'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Taxa de Envio Reversa'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS reverse_shipping_fee,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de transação'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de transação'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Taxa de transação'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Taxa de transação'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS transaction_fee,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão bruta'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão bruta'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão bruta'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão bruta'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS commission_fee_gross,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão líquida'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão líquida'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão líquida'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Taxa de comissão líquida'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS commission_fee_net,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço bruta'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço bruta'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço bruta'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço bruta'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS service_fee_gross,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço líquida'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço líquida'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço líquida'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Taxa de serviço líquida'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS service_fee_net,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Valor estimado do frete'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Valor estimado do frete'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Valor estimado do frete'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Valor estimado do frete'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS estimated_shipping_fee,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto de Frete Aproximado'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Desconto de Frete Aproximado'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Desconto de Frete Aproximado'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Desconto de Frete Aproximado'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS approx_shipping_discount,
    COALESCE(NULLIF(btrim(r.raw_payload ->> 'Cidade__col57'), ''), NULLIF(btrim(r.raw_payload ->> 'Cidade__col58'), ''), NULLIF(btrim(r.raw_payload ->> 'Cidade__col59'), ''), NULLIF(btrim(r.raw_payload ->> 'Cidade'), '')) AS delivery_city,
    NULLIF(btrim(r.raw_payload ->> 'UF'), '') AS delivery_state,
    NULLIF(btrim(r.raw_payload ->> 'País'), '') AS country_code
FROM raw.shopee_order_item_export r
JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id
WHERE NOT EXISTS (
    SELECT 1 FROM silver.stg_shopee_order_item_snapshots s WHERE s.raw_id = r.id
)
ON CONFLICT (raw_id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- shop_stats: raw.shopee_shop_stats_export → silver.stg_shopee_shop_stats
-- ----------------------------------------------------------------------------
INSERT INTO silver.stg_shopee_shop_stats (
    raw_id,
    file_id,
    brand,
    source_row_number,
    row_sha256,
    raw_ingested_at,
    row_type,
    stat_date,
    period_start,
    period_end,
    sales_brl,
    sales_before_shopee_discounts,
    sales_per_order,
    cancelled_sales,
    refunded_sales,
    orders_count,
    product_clicks,
    visitors,
    cancelled_orders,
    refunded_orders,
    buyers_count,
    new_buyers_count,
    existing_buyers_count,
    potential_buyers_count,
    order_conversion_rate_pct,
    repeat_purchase_rate_pct
)
SELECT
    r.id                                     AS raw_id,
    r.file_id                                AS file_id,
    r.brand                                  AS brand,
    r.source_row_number                      AS source_row_number,
    r.row_sha256                             AS row_sha256,
    r.ingested_at                            AS raw_ingested_at,
    (CASE WHEN btrim(r.raw_payload ->> 'Data') ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$' THEN 'daily' WHEN btrim(r.raw_payload ->> 'Data') ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}-[0-9]{2}/[0-9]{2}/[0-9]{4}$' THEN 'period_total' ELSE NULL END) AS row_type,
    (CASE WHEN btrim(r.raw_payload ->> 'Data') !~ '^([0-9]{2})/([0-9]{2})/([0-9]{4})$' THEN NULL ELSE make_date((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer, (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer, (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[1]::integer) END) AS stat_date,
    (CASE WHEN btrim(r.raw_payload ->> 'Data') !~ '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$' THEN NULL ELSE make_date((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[3]::integer, (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[2]::integer, (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[1]::integer) END) AS period_start,
    (CASE WHEN btrim(r.raw_payload ->> 'Data') !~ '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$' THEN NULL ELSE make_date((regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[6]::integer, (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[5]::integer, (regexp_match(btrim(r.raw_payload ->> 'Data'), '^([0-9]{2})/([0-9]{2})/([0-9]{4})-([0-9]{2})/([0-9]{2})/([0-9]{4})$'))[4]::integer) END) AS period_end,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') ~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' OR NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') ~ '^-?[0-9]+,[0-9]+$' THEN replace(replace(NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), ''), '.', ''), ',', '.')::numeric WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Vendas (BRL)'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS sales_brl,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') ~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' OR NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') ~ '^-?[0-9]+,[0-9]+$' THEN replace(replace(NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), ''), '.', ''), ',', '.')::numeric WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Vendas Sem os Descontos da Shopee'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS sales_before_shopee_discounts,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') ~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' OR NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') ~ '^-?[0-9]+,[0-9]+$' THEN replace(replace(NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), ''), '.', ''), ',', '.')::numeric WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Vendas por Pedido'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS sales_per_order,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') ~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' OR NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') ~ '^-?[0-9]+,[0-9]+$' THEN replace(replace(NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), ''), '.', ''), ',', '.')::numeric WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Vendas Canceladas'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS cancelled_sales,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') ~ '^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$' OR NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') ~ '^-?[0-9]+,[0-9]+$' THEN replace(replace(NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), ''), '.', ''), ',', '.')::numeric WHEN NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Vendas Devolvidas / Reembolsadas'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS refunded_sales,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Pedidos'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Pedidos'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Pedidos'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Pedidos'), '') || 'CONTRATO_INVALIDO')::integer) END) AS orders_count,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Cliques Por Produto'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Cliques Por Produto'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Cliques Por Produto'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Cliques Por Produto'), '') || 'CONTRATO_INVALIDO')::integer) END) AS product_clicks,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Visitantes'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Visitantes'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Visitantes'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Visitantes'), '') || 'CONTRATO_INVALIDO')::integer) END) AS visitors,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Pedidos Cancelados'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Pedidos Cancelados'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Pedidos Cancelados'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Pedidos Cancelados'), '') || 'CONTRATO_INVALIDO')::integer) END) AS cancelled_orders,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Pedidos Devolvidos / Reembolsados'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Pedidos Devolvidos / Reembolsados'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Pedidos Devolvidos / Reembolsados'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Pedidos Devolvidos / Reembolsados'), '') || 'CONTRATO_INVALIDO')::integer) END) AS refunded_orders,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> '# de compradores'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> '# de compradores'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> '# de compradores'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> '# de compradores'), '') || 'CONTRATO_INVALIDO')::integer) END) AS buyers_count,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> '# de novos compradores'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> '# de novos compradores'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> '# de novos compradores'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> '# de novos compradores'), '') || 'CONTRATO_INVALIDO')::integer) END) AS new_buyers_count,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> '# de compradores existentes'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> '# de compradores existentes'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> '# de compradores existentes'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> '# de compradores existentes'), '') || 'CONTRATO_INVALIDO')::integer) END) AS existing_buyers_count,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> '# de compradores em potencial'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> '# de compradores em potencial'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> '# de compradores em potencial'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> '# de compradores em potencial'), '') || 'CONTRATO_INVALIDO')::integer) END) AS potential_buyers_count,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão de Pedidos'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão de Pedidos'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão de Pedidos'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão de Pedidos'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS order_conversion_rate_pct,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Repetir Índice de Compras'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Repetir Índice de Compras'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Repetir Índice de Compras'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'Repetir Índice de Compras'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS repeat_purchase_rate_pct
FROM raw.shopee_shop_stats_export r
JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id
WHERE NOT EXISTS (
    SELECT 1 FROM silver.stg_shopee_shop_stats s WHERE s.raw_id = r.id
)
ON CONFLICT (raw_id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- ads: raw.shopee_ads_export → silver.stg_shopee_ads
-- ----------------------------------------------------------------------------
INSERT INTO silver.stg_shopee_ads (
    raw_id,
    file_id,
    brand,
    source_row_number,
    row_sha256,
    raw_ingested_at,
    report_period_start,
    report_period_end,
    ad_seq,
    ad_name,
    ad_status,
    ad_type,
    product_id,
    audience_segmentation,
    creative,
    bidding_method,
    placement,
    started_at,
    ended_at,
    impressions,
    clicks,
    ctr_pct,
    add_to_cart,
    add_to_cart_rate_pct,
    conversions,
    direct_conversions,
    conversion_rate_pct,
    direct_conversion_rate_pct,
    cost_per_conversion,
    cost_per_direct_conversion,
    items_sold,
    direct_items_sold,
    gmv,
    direct_revenue,
    expense,
    roas,
    direct_roas,
    acos_pct,
    direct_acos_pct,
    product_impressions,
    product_clicks,
    product_ctr_pct,
    voucher_amount,
    vouchered_sales
)
SELECT
    r.id                                     AS raw_id,
    r.file_id                                AS file_id,
    r.brand                                  AS brand,
    r.source_row_number                      AS source_row_number,
    r.row_sha256                             AS row_sha256,
    r.ingested_at                            AS raw_ingested_at,
    (CASE WHEN (f.source_metadata ->> 'period_start') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2})$' THEN NULL ELSE make_date((regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer, (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer, (regexp_match((f.source_metadata ->> 'period_start'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer) END) AS report_period_start,
    (CASE WHEN (f.source_metadata ->> 'period_end') !~ '^([0-9]{4})-([0-9]{2})-([0-9]{2})$' THEN NULL ELSE make_date((regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[1]::integer, (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[2]::integer, (regexp_match((f.source_metadata ->> 'period_end'), '^([0-9]{4})-([0-9]{2})-([0-9]{2})$'))[3]::integer) END) AS report_period_end,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> '#'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> '#'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> '#'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> '#'), '') || 'CONTRATO_INVALIDO')::integer) END) AS ad_seq,
    NULLIF(btrim(r.raw_payload ->> 'Nome do Anúncio'), '') AS ad_name,
    NULLIF(btrim(r.raw_payload ->> 'Status'), '') AS ad_status,
    NULLIF(btrim(r.raw_payload ->> 'Tipos de Anúncios'), '') AS ad_type,
    NULLIF(NULLIF(btrim(r.raw_payload ->> 'ID do produto'), ''), '-') AS product_id,
    NULLIF(NULLIF(btrim(r.raw_payload ->> 'Segmentação de Público'), ''), '-') AS audience_segmentation,
    NULLIF(NULLIF(btrim(r.raw_payload ->> 'Criativo'), ''), '-') AS creative,
    NULLIF(btrim(r.raw_payload ->> 'Método de Lance'), '') AS bidding_method,
    NULLIF(btrim(r.raw_payload ->> 'Posicionamento'), '') AS placement,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Data de Início'), '') IS NULL THEN NULL ELSE make_timestamp((regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[3]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[4]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[5]::integer, (regexp_match(NULLIF(btrim(r.raw_payload ->> 'Data de Início'), ''), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[6]::integer::double precision) END) AS started_at,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado') IS NULL THEN NULL ELSE make_timestamp((regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[3]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[2]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[1]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[4]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[5]::integer, (regexp_match(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Data de Encerramento'), ''), 'Ilimitado'), '^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$'))[6]::integer::double precision) END) AS ended_at,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Impressões'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Impressões'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Impressões'), ''))::bigint ELSE ((NULLIF(btrim(r.raw_payload ->> 'Impressões'), '') || 'CONTRATO_INVALIDO')::bigint) END) AS impressions,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Cliques'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Cliques'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Cliques'), ''))::bigint ELSE ((NULLIF(btrim(r.raw_payload ->> 'Cliques'), '') || 'CONTRATO_INVALIDO')::bigint) END) AS clicks,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS ctr_pct,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart'), ''), '-') ~ '^-?[0-9]+$' THEN (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart'), ''), '-'))::integer ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart'), ''), '-') || 'CONTRATO_INVALIDO')::integer) END) AS add_to_cart,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart Rate'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart Rate'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart Rate'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'Add to Cart Rate'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS add_to_cart_rate_pct,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Conversões'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Conversões'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Conversões'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Conversões'), '') || 'CONTRATO_INVALIDO')::integer) END) AS conversions,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Conversões Diretas'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Conversões Diretas'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Conversões Diretas'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Conversões Diretas'), '') || 'CONTRATO_INVALIDO')::integer) END) AS direct_conversions,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS conversion_rate_pct,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão Direta'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão Direta'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão Direta'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'Taxa de Conversão Direta'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS direct_conversion_rate_pct,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS cost_per_conversion,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão Direta'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão Direta'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão Direta'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Custo por Conversão Direta'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS cost_per_direct_conversion,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos'), '') || 'CONTRATO_INVALIDO')::integer) END) AS items_sold,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos Diretos'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos Diretos'), '') ~ '^-?[0-9]+$' THEN (NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos Diretos'), ''))::integer ELSE ((NULLIF(btrim(r.raw_payload ->> 'Itens Vendidos Diretos'), '') || 'CONTRATO_INVALIDO')::integer) END) AS direct_items_sold,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'GMV'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'GMV'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'GMV'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'GMV'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS gmv,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Receita direta'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Receita direta'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Receita direta'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Receita direta'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS direct_revenue,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Despesas'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Despesas'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Despesas'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Despesas'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS expense,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'ROAS'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'ROAS'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'ROAS'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'ROAS'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS roas,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'ROAS Direto'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'ROAS Direto'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'ROAS Direto'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'ROAS Direto'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS direct_roas,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS acos_pct,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS Direto'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS Direto'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS Direto'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'ACOS Direto'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS direct_acos_pct,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Impressões do Produto'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Impressões do Produto'), ''), '-') ~ '^-?[0-9]+$' THEN (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Impressões do Produto'), ''), '-'))::integer ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'Impressões do Produto'), ''), '-') || 'CONTRATO_INVALIDO')::integer) END) AS product_impressions,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Cliques de Produtos'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'Cliques de Produtos'), ''), '-') ~ '^-?[0-9]+$' THEN (NULLIF(NULLIF(btrim(r.raw_payload ->> 'Cliques de Produtos'), ''), '-'))::integer ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'Cliques de Produtos'), ''), '-') || 'CONTRATO_INVALIDO')::integer) END) AS product_clicks,
    (CASE WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR do Produto'), ''), '-') IS NULL THEN NULL WHEN NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR do Produto'), ''), '-') ~ '^-?[0-9]+([.,][0-9]+)?%?$' THEN replace(replace(NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR do Produto'), ''), '-'), '%', ''), ',', '.')::numeric ELSE ((NULLIF(NULLIF(btrim(r.raw_payload ->> 'CTR do Produto'), ''), '-') || 'CONTRATO_INVALIDO')::numeric) END) AS product_ctr_pct,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Voucher Amount'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Voucher Amount'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Voucher Amount'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Voucher Amount'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS voucher_amount,
    (CASE WHEN NULLIF(btrim(r.raw_payload ->> 'Vouchered Sales'), '') IS NULL THEN NULL WHEN NULLIF(btrim(r.raw_payload ->> 'Vouchered Sales'), '') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (NULLIF(btrim(r.raw_payload ->> 'Vouchered Sales'), ''))::numeric ELSE ((NULLIF(btrim(r.raw_payload ->> 'Vouchered Sales'), '') || 'CONTRATO_INVALIDO')::numeric) END) AS vouchered_sales
FROM raw.shopee_ads_export r
JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id
WHERE NOT EXISTS (
    SELECT 1 FROM silver.stg_shopee_ads s WHERE s.raw_id = r.id
)
ON CONFLICT (raw_id) DO NOTHING;

-- Passo 6 (1 scan por fonte, 3 no total): validações pós-insert — toda
-- linha Raw elegível deve existir na staging com os MESMOS
-- file_id/brand/source_row_number/row_sha256 (nunca uma carga parcial
-- ou uma linha corrompida manualmente que só preservou o hash).
DO $$
DECLARE
    v_count bigint;
BEGIN
    SELECT count(*) INTO v_count FROM raw.shopee_order_item_export r LEFT JOIN silver.stg_shopee_order_item_snapshots s ON s.raw_id = r.id WHERE s.raw_id IS NULL OR s.file_id <> r.file_id OR s.brand <> r.brand OR s.source_row_number <> r.source_row_number OR s.row_sha256 <> r.row_sha256;
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pos-insert falhou -- %: % linha(s)', 'orders: linha elegível sem staging correspondente (raw_id/file_id/brand/source_row_number/row_sha256) após o INSERT', v_count; END IF;
    SELECT count(*) INTO v_count FROM raw.shopee_shop_stats_export r LEFT JOIN silver.stg_shopee_shop_stats s ON s.raw_id = r.id WHERE s.raw_id IS NULL OR s.file_id <> r.file_id OR s.brand <> r.brand OR s.source_row_number <> r.source_row_number OR s.row_sha256 <> r.row_sha256;
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pos-insert falhou -- %: % linha(s)', 'shop_stats: linha elegível sem staging correspondente (raw_id/file_id/brand/source_row_number/row_sha256) após o INSERT', v_count; END IF;
    SELECT count(*) INTO v_count FROM raw.shopee_ads_export r LEFT JOIN silver.stg_shopee_ads s ON s.raw_id = r.id WHERE s.raw_id IS NULL OR s.file_id <> r.file_id OR s.brand <> r.brand OR s.source_row_number <> r.source_row_number OR s.row_sha256 <> r.row_sha256;
    IF v_count > 0 THEN RAISE EXCEPTION 'validacao pos-insert falhou -- %: % linha(s)', 'ads: linha elegível sem staging correspondente (raw_id/file_id/brand/source_row_number/row_sha256) após o INSERT', v_count; END IF;
END $$;

-- Passo 7: só chega aqui se nada acima abortou.
COMMIT;
