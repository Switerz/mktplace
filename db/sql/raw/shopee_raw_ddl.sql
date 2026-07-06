-- ============================================================================
-- Fase Raw Shopee — DDL versionado. Aplicado via
-- `python -m pipelines.ingestion.load_shopee_raw --apply --create-schema`
-- (que faz o parsing/execução estatuto-a-estatuto deste arquivo dentro de
-- UMA transação, com timeouts e advisory lock — ver
-- pipelines/ingestion/shopee_raw/write_conn.py e ddl.py). Este arquivo
-- também serve como referência para revisão manual/DBA.
--
-- Alvo: schema `raw` do Data Mart (mesmo Postgres que hoje hospeda
-- raw.tiktok_shop_orders / raw.ml_orders / ~200 outras tabelas de outras
-- fontes). NÃO é o Neon (marts.*) nem o Postgres local.
--
-- Confirmado por inspeção read-only em 2026-07-03 (transação
-- SET default_transaction_read_only=on, usuário postgres/postgres):
--   - Não existe nenhum objeto raw.*shopee* hoje.
--   - Convenção observada em raw.tiktok_shop_orders / raw.ml_orders: PK
--     serial "id", UNIQUE de negócio prefixado "uk_", índices "idx_",
--     timestamps sem timezone com default CURRENT_TIMESTAMP/now().
--   - A extensão pgcrypto NÃO está instalada (sem digest()/hmac() em SQL) —
--     por isso qualquer HMAC de PII deve ser calculado em Python antes do
--     INSERT, nunca em SQL nesta base.
--
-- Diferença deliberada em relação a raw.tiktok_shop_orders/raw.ml_orders:
-- aquelas tabelas têm UNIQUE de negócio (order_id) e são efetivamente
-- upsert/dedupe. As tabelas abaixo são propositalmente **append-only por
-- linha física de arquivo**, sem deduplicar pedidos entre exports
-- sobrepostos — ver docs/data_contracts.md e docs/runbook_shopee_raw.md.
--
-- Política de escrita (Fase Raw Shopee 2):
--   - Todo o processamento de um arquivo roda em UMA transação.
--   - raw.shopee_ingestion_file é INSERT-only e "success-only": só recebe
--     uma linha depois que TODAS as linhas-filhas daquele arquivo já foram
--     inseridas na mesma transação. Não existe status 'failed' persistido
--     — um arquivo que falha antes do fim simplesmente não deixa nenhum
--     rastro em raw.shopee_* (a transação inteira é descartada). Retry é
--     manual, fora deste processo, e é seguro por natureza: o mesmo
--     arquivo com o mesmo file_sha256+sheet ainda não está registrado.
--   - Nunca há UPDATE: o INSERT de raw.shopee_ingestion_file é a última
--     instrução da transação, funcionando como marca de commit. Isso só é
--     possível porque as FKs dos filhos para file_id são
--     DEFERRABLE INITIALLY DEFERRED — o file_id é reservado via nextval()
--     no início da transação (para popular as linhas-filhas), mas o
--     Postgres só verifica a integridade referencial no COMMIT, quando a
--     linha-mãe já foi inserida.
--   - DROP/TRUNCATE/DELETE/UPDATE nunca são emitidos pelo loader (garantia
--     de código/aplicação, verificada por teste estrutural sobre o texto
--     deste arquivo e do loader). **Isso NÃO é uma garantia absoluta de
--     permissões do banco**: o owner destas tabelas (a própria role de
--     escrita) sempre pode, tecnicamente, rodar UPDATE/DELETE/DROP/TRUNCATE
--     via outro cliente (psql, etc.) — ownership no Postgres nunca pode ser
--     revogado do próprio dono por um REVOKE. `REVOKE ALL ON <tabela> FROM
--     PUBLIC` (abaixo) só remove o acesso implícito do pseudo-role PUBLIC;
--     **não afeta em nada os grants que já existiam para roles NOMEADAS**
--     (ex: privilégios padrão de schema herdados por `postgrest`/`airflow`/
--     `web_user`/`sql_runner`/`datamart_ro`/`datamart_rw` — achado
--     confirmado após a carga real, risco aceito explicitamente pelo
--     usuário em 2026-07-03, não corrigido por este DDL). Ver
--     docs/runbook_shopee_raw.md seção 5.
-- ============================================================================

BEGIN;

-- Timeouts locais a esta transação (nunca vazam para fora dela). Se o lock
-- de schema não for adquirido em 5s (outra sessão concorrente), o próprio
-- Postgres cancela — sem travar indefinidamente e sem retry automático.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

-- ----------------------------------------------------------------------------
-- raw.shopee_ingestion_file — um registro por arquivo físico (+ sheet).
-- ----------------------------------------------------------------------------
CREATE TABLE raw.shopee_ingestion_file (
    file_id             bigserial PRIMARY KEY,
    batch_id            uuid NOT NULL,
    source_type         varchar(20) NOT NULL
                         CHECK (source_type IN ('orders', 'shop_stats', 'ads')),
    brand               varchar(50) NOT NULL,
    source_filename     varchar(500) NOT NULL,
    file_sha256         char(64) NOT NULL,
    file_size_bytes     bigint NOT NULL,
    source_modified_at  timestamptz,
    sheet_name          varchar(200),
    source_row_count    integer,
    headers_json        jsonb NOT NULL,
    schema_fingerprint  char(64),
    ingestion_status    varchar(20) NOT NULL DEFAULT 'success'
                         CHECK (ingestion_status = 'success'),
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    error_message       text,
    CONSTRAINT uk_shopee_ingestion_file_sha256_sheet UNIQUE (file_sha256, sheet_name)
);

COMMENT ON TABLE raw.shopee_ingestion_file IS
    'Grao: um arquivo fisico (+ sheet) de export Shopee. Append-only e '
    'success-only: mesmo file_sha256+sheet nunca entra duas vezes '
    '(idempotencia tecnica), e so existe linha aqui para arquivos totalmente '
    'ingeridos. Nao deduplica pedidos nem descarta exports sobrepostos. '
    'source_filename e sempre caminho relativo a SHOPEE_DATA_PATH.';
COMMENT ON COLUMN raw.shopee_ingestion_file.source_filename IS
    'Caminho relativo, ex: apice/Order.all.20260101_20260131.xlsx. Nunca caminho absoluto.';
COMMENT ON COLUMN raw.shopee_ingestion_file.headers_json IS
    'Lista JSON dos headers originais, na ordem exportada pela Shopee.';
COMMENT ON COLUMN raw.shopee_ingestion_file.ingestion_status IS
    'Sempre "success" nesta fase — politica success-only, sem UPDATE de '
    'status. error_message existe para evolucao futura do schema, mas nao '
    'e usado neste modo de operacao (fica sempre NULL).';

CREATE INDEX idx_shopee_ingestion_file_brand ON raw.shopee_ingestion_file USING btree (brand);
CREATE INDEX idx_shopee_ingestion_file_source_type ON raw.shopee_ingestion_file USING btree (source_type);
CREATE INDEX idx_shopee_ingestion_file_ingested_at ON raw.shopee_ingestion_file USING btree (ingested_at);
CREATE INDEX idx_shopee_ingestion_file_batch_id ON raw.shopee_ingestion_file USING btree (batch_id);

-- Chave de idempotência NULL-SAFE (adicionada em 2026-07-04, pós-carga).
-- UNIQUE(file_sha256, sheet_name) sozinha NÃO bloqueia duplicidade quando
-- sheet_name é NULL: o Postgres trata cada NULL como distinto de qualquer
-- outro em uma UNIQUE constraint (inclusive de outro NULL). Isso afeta o
-- source_type 'ads' (CSV, sem sheet — sheet_name sempre NULL): dois
-- arquivos ads *byte-a-byte idênticos* (mesmo file_sha256) poderiam, antes
-- desta correção, ser inseridos duas vezes. Este índice usa
-- COALESCE(sheet_name, '') para tratar NULL como uma chave normal.
-- Mantido AO LADO da constraint original (não a substitui) — nenhuma linha
-- foi alterada para adicionar este índice; diagnóstico read-only prévio
-- confirmou zero duplicidades na base já carregada.
CREATE UNIQUE INDEX uk_shopee_ingestion_file_sha256_sheet_nullsafe
    ON raw.shopee_ingestion_file (file_sha256, (COALESCE(sheet_name, '')));

REVOKE ALL ON raw.shopee_ingestion_file FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- raw.shopee_order_item_export — grão: 1 linha física de SKU de um export
-- Order.all*.xlsx, em um dado arquivo/snapshot.
-- ----------------------------------------------------------------------------
CREATE TABLE raw.shopee_order_item_export (
    id                  bigserial PRIMARY KEY,
    file_id             bigint NOT NULL REFERENCES raw.shopee_ingestion_file (file_id) DEFERRABLE INITIALLY DEFERRED,
    brand               varchar(50) NOT NULL,
    source_row_number   integer NOT NULL,
    raw_payload         jsonb NOT NULL,
    row_sha256          char(64) NOT NULL,
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_shopee_order_item_export_file_row UNIQUE (file_id, source_row_number)
);

COMMENT ON TABLE raw.shopee_order_item_export IS
    'Grao: uma linha fisica de SKU de um pedido, em um export Order.all*.xlsx '
    'especifico. Append-only: um mesmo pedido pode aparecer em varias linhas '
    '(itens) e em varios arquivos/exports sobrepostos — isso e esperado e '
    'nao deve ser deduplicado aqui. Correcao (2026-07-06, ver review pre-commit '
    'da Fase Staging Shopee 1): a staging tipada (silver.stg_shopee_'
    'order_item_snapshots) TAMBEM preserva o grao de snapshot 1:1 com esta '
    'tabela — nao existe hoje uma chave confiavel para decidir qual snapshot '
    'de um pedido e o vigente quando exports se sobrepoem. A '
    'selecao/deduplicacao de negocio por pedido fica para uma camada Gold '
    'futura, ainda nao implementada — nao para a staging, como uma versao '
    'anterior deste comentario dizia.';
COMMENT ON COLUMN raw.shopee_order_item_export.raw_payload IS
    'Todas as colunas originais do export, chave = header exato da Shopee, '
    'incluindo PII direta (nome do destinatario, telefone, endereco, CEP, '
    'CPF quando presente) — autorizado explicitamente para esta fase. '
    'REVOKE ALL FROM PUBLIC nesta tabela remove so o acesso implicito do '
    'pseudo-role PUBLIC; roles nomeadas com privilegio padrao de schema ja '
    'existente (ver nota de permissoes ao final deste arquivo) continuam '
    'com acesso. Nao ha, hoje, uma garantia de acesso restrito apenas a '
    'quem escreve.';

CREATE INDEX idx_shopee_order_item_export_file_id ON raw.shopee_order_item_export USING btree (file_id);
CREATE INDEX idx_shopee_order_item_export_brand ON raw.shopee_order_item_export USING btree (brand);
CREATE INDEX idx_shopee_order_item_export_ingested_at ON raw.shopee_order_item_export USING btree (ingested_at);

REVOKE ALL ON raw.shopee_order_item_export FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- raw.shopee_shop_stats_export — grão: 1 linha física do relatório shop-stats
-- (total do período OU um dia).
-- ----------------------------------------------------------------------------
CREATE TABLE raw.shopee_shop_stats_export (
    id                  bigserial PRIMARY KEY,
    file_id             bigint NOT NULL REFERENCES raw.shopee_ingestion_file (file_id) DEFERRABLE INITIALLY DEFERRED,
    brand               varchar(50) NOT NULL,
    source_row_number   integer NOT NULL,
    raw_payload         jsonb NOT NULL,
    row_sha256          char(64) NOT NULL,
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_shopee_shop_stats_export_file_row UNIQUE (file_id, source_row_number)
);

COMMENT ON TABLE raw.shopee_shop_stats_export IS
    'Grao: uma linha fisica do relatorio shop-stats (a linha de total do '
    'periodo em source_row_number=1, ou uma linha diaria). Append-only, '
    'sem PII de comprador — apenas metricas agregadas de funil.';

CREATE INDEX idx_shopee_shop_stats_export_file_id ON raw.shopee_shop_stats_export USING btree (file_id);
CREATE INDEX idx_shopee_shop_stats_export_brand ON raw.shopee_shop_stats_export USING btree (brand);
CREATE INDEX idx_shopee_shop_stats_export_ingested_at ON raw.shopee_shop_stats_export USING btree (ingested_at);

REVOKE ALL ON raw.shopee_shop_stats_export FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- raw.shopee_ads_export — grão: 1 linha física por anúncio no CSV de ads.
-- ----------------------------------------------------------------------------
CREATE TABLE raw.shopee_ads_export (
    id                  bigserial PRIMARY KEY,
    file_id             bigint NOT NULL REFERENCES raw.shopee_ingestion_file (file_id) DEFERRABLE INITIALLY DEFERRED,
    brand               varchar(50) NOT NULL,
    source_row_number   integer NOT NULL,
    raw_payload         jsonb NOT NULL,
    row_sha256          char(64) NOT NULL,
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_shopee_ads_export_file_row UNIQUE (file_id, source_row_number)
);

COMMENT ON TABLE raw.shopee_ads_export IS
    'Grao: uma linha fisica de anuncio no CSV "Dados Gerais de Anuncios '
    'Shopee". O CSV cobre um periodo agregado (sem granularidade diaria); '
    'essa limitacao operacional permanece aqui, sem tentar distribuir/media '
    'nesta camada — isso e responsabilidade de staging/gold.';

CREATE INDEX idx_shopee_ads_export_file_id ON raw.shopee_ads_export USING btree (file_id);
CREATE INDEX idx_shopee_ads_export_brand ON raw.shopee_ads_export USING btree (brand);
CREATE INDEX idx_shopee_ads_export_ingested_at ON raw.shopee_ads_export USING btree (ingested_at);

REVOKE ALL ON raw.shopee_ads_export FROM PUBLIC;

COMMIT;

-- ============================================================================
-- Nota sobre permissões (corrigida em 2026-07-04 após revisão):
--
-- A role que executa este script (DATAMART_SHOPEE_WRITE_URL) se torna
-- automaticamente owner das 4 tabelas (regra padrão do Postgres — quem
-- cria, é dono). Nenhum GRANT adicional é concedido a mais ninguém por
-- este script.
--
-- `REVOKE ALL ON <tabela> FROM PUBLIC` (aplicado em cada tabela acima) só
-- remove o acesso implícito do pseudo-role PUBLIC. **Ele NÃO remove
-- privilégios que roles NOMEADAS já tinham via default privileges de
-- schema** (`ALTER DEFAULT PRIVILEGES ... IN SCHEMA raw`, configurado por
-- outra equipe antes desta fase, não por este DDL). Achado confirmado
-- após a carga real: as 4 tabelas herdaram acesso para `postgrest`
-- (SELECT), `airflow` e `web_user` (SELECT+INSERT+UPDATE+DELETE) e
-- `sql_runner`/`datamart_ro`/`datamart_rw` (SELECT).
--
-- Esses acessos herdados foram inspecionados e **aceitos explicitamente
-- pelo responsável pelo ambiente** (Mário Monteiro, 2026-07-03/04) — são
-- roles internas e controladas. Nenhum REVOKE/GRANT/ALTER DEFAULT
-- PRIVILEGES adicional foi executado para alterar esse estado. Se a role
-- de escrita compartilhar privilégios com outra (ex: membership em
-- rds_superuser), isso também é um risco herdado da role em si, não algo
-- introduzido por este DDL — ver docs/runbook_shopee_raw.md seção 5.
-- ============================================================================
