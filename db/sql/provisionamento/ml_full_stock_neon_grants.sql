-- ============================================================================
-- ML-FULL-PRODUCTION-1: privilegio MINIMO no Neon para marts.fact_ml_full_stock_*
-- NAO EXECUTADO. Aplicar a migration 029 e conceder privilegio sao gates
-- proprios, autorizados pelo Mario.
--
-- Pre-requisito: `alembic upgrade 029` ja' aplicado por papel administrativo
-- (dono das tabelas). O publicador NAO e' dono e NAO recebe DDL.
--
-- Uso (psql, papel administrativo, uma transacao):
--   psql "$NEON_ADMIN_URL" -v ON_ERROR_STOP=1 -1 \
--        -v papel_publicador=<papel do publish_ml_full_stock_daily> \
--        -v papel_api=<papel de leitura da API do Render> \
--        -f db/sql/provisionamento/ml_full_stock_neon_grants.sql
-- ============================================================================

REVOKE ALL ON marts.fact_ml_full_stock_inventory_daily FROM PUBLIC;
REVOKE ALL ON marts.fact_ml_full_stock_status_daily    FROM PUBLIC;
REVOKE ALL ON marts.fact_ml_full_stock_brand_daily     FROM PUBLIC;

-- Publicador: DELETE do (dia, marca) + INSERT + SELECT da reconciliacao.
-- Sem UPDATE, sem TRUNCATE. O advisory lock nao exige privilegio de tabela.
GRANT USAGE ON SCHEMA marts TO :"papel_publicador";
GRANT SELECT, INSERT, DELETE ON marts.fact_ml_full_stock_inventory_daily TO :"papel_publicador";
GRANT SELECT, INSERT, DELETE ON marts.fact_ml_full_stock_status_daily    TO :"papel_publicador";
GRANT SELECT, INSERT, DELETE ON marts.fact_ml_full_stock_brand_daily     TO :"papel_publicador";

-- Auditoria (conexao propria do publicador): INSERT ... RETURNING e UPDATE do
-- proprio registro. A sequence do sync_run_id e' privilegio SEPARADO da tabela.
GRANT USAGE ON SCHEMA audit TO :"papel_publicador";
GRANT SELECT, INSERT, UPDATE ON audit.source_sync_run TO :"papel_publicador";
SELECT format('GRANT USAGE ON SEQUENCE %s TO %I',
              pg_get_serial_sequence('audit.source_sync_run', 'sync_run_id'), :'papel_publicador')
\gexec

-- API (Render): so' leitura. Fica concedido ja', mas nenhum endpoint le estas
-- tabelas ate' o gate da API/tela.
GRANT USAGE ON SCHEMA marts TO :"papel_api";
GRANT SELECT ON marts.fact_ml_full_stock_inventory_daily TO :"papel_api";
GRANT SELECT ON marts.fact_ml_full_stock_status_daily    TO :"papel_api";
GRANT SELECT ON marts.fact_ml_full_stock_brand_daily     TO :"papel_api";

-- ---------------------------------------------------------------------------
-- VERIFICACAO (so' leitura). Esperado publicador: sel ins del = t, upd trunc = f.
-- ---------------------------------------------------------------------------
-- SELECT t,
--        has_table_privilege(:'papel_publicador', t, 'SELECT')   AS sel,
--        has_table_privilege(:'papel_publicador', t, 'INSERT')   AS ins,
--        has_table_privilege(:'papel_publicador', t, 'DELETE')   AS del,
--        has_table_privilege(:'papel_publicador', t, 'UPDATE')   AS upd,
--        has_table_privilege(:'papel_publicador', t, 'TRUNCATE') AS trunc
--   FROM unnest(ARRAY['marts.fact_ml_full_stock_inventory_daily','marts.fact_ml_full_stock_status_daily',
--                     'marts.fact_ml_full_stock_brand_daily']) AS t;
