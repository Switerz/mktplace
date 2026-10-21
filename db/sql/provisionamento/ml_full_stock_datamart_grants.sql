-- ============================================================================
-- ML-FULL-PRODUCTION-1: privilegio MINIMO no Data Mart para raw.ml_full_*
-- NAO EXECUTADO. Criar papel, aplicar DDL e conceder privilegio sao gates
-- proprios, autorizados pelo Mario.
--
-- Pre-requisito: a DDL do bloco "ESTOQUE FULL (ML-FULL-STOCK-INGESTION-1)" de
-- goca-se/airflow src/mercadolivre/schema.sql ja' aplicada por um papel
-- ADMINISTRATIVO (que fica dono das tabelas). O papel do Airflow NAO e' dono:
-- so' recebe DML nas quatro tabelas, nada mais.
--
-- Uso (psql, papel administrativo, uma transacao):
--   psql "$DATAMART_ADMIN_URL" -v ON_ERROR_STOP=1 -1 \
--        -v papel_escrita=<papel da Connection data_mart do Airflow> \
--        -v papel_leitura=<papel de leitura do mktplace> \
--        -f db/sql/provisionamento/ml_full_stock_datamart_grants.sql
-- ============================================================================

-- Nada publico.
REVOKE ALL ON raw.ml_full_inventory_snapshot    FROM PUBLIC;
REVOKE ALL ON raw.ml_full_inventory_unavailable FROM PUBLIC;
REVOKE ALL ON raw.ml_full_inventory_listing     FROM PUBLIC;
REVOKE ALL ON raw.ml_full_stock_runs            FROM PUBLIC;

-- Escritor (DAG mercadolivre_full_stock_snapshot): substitui o dia da marca
-- (DELETE + INSERT) e le' o que ja' publicou (marcas_publicadas). Sem UPDATE
-- nas fotografias, sem TRUNCATE, sem DDL.
GRANT USAGE ON SCHEMA raw TO :"papel_escrita";
GRANT SELECT, INSERT, DELETE ON raw.ml_full_inventory_snapshot    TO :"papel_escrita";
GRANT SELECT, INSERT, DELETE ON raw.ml_full_inventory_unavailable TO :"papel_escrita";
GRANT SELECT, INSERT, DELETE ON raw.ml_full_inventory_listing     TO :"papel_escrita";
-- Auditoria do run: abre (INSERT) e fecha (UPDATE). Sem DELETE.
GRANT SELECT, INSERT, UPDATE ON raw.ml_full_stock_runs            TO :"papel_escrita";

-- Leitor (mktplace: sync/publish_ml_full_stock_daily, sessao READ ONLY).
GRANT USAGE ON SCHEMA raw TO :"papel_leitura";
GRANT SELECT ON raw.ml_full_inventory_snapshot    TO :"papel_leitura";
GRANT SELECT ON raw.ml_full_inventory_unavailable TO :"papel_leitura";
GRANT SELECT ON raw.ml_full_inventory_listing     TO :"papel_leitura";

-- ---------------------------------------------------------------------------
-- VERIFICACAO (rodar depois, so' leitura). Esperado: escrita = t t t f f
-- nas fotografias; leitura = t f f f f.
-- ---------------------------------------------------------------------------
-- SELECT t,
--        has_table_privilege(:'papel_escrita', t, 'SELECT')   AS sel,
--        has_table_privilege(:'papel_escrita', t, 'INSERT')   AS ins,
--        has_table_privilege(:'papel_escrita', t, 'DELETE')   AS del,
--        has_table_privilege(:'papel_escrita', t, 'UPDATE')   AS upd,
--        has_table_privilege(:'papel_escrita', t, 'TRUNCATE') AS trunc
--   FROM unnest(ARRAY['raw.ml_full_inventory_snapshot','raw.ml_full_inventory_unavailable',
--                     'raw.ml_full_inventory_listing','raw.ml_full_stock_runs']) AS t;
-- (em raw.ml_full_stock_runs o esperado e' upd = t e del = f)
