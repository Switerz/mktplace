-- ============================================================================
-- APLICADA na primary em 2026-07-07 (Fase Staging Shopee 2A, Gate 2B) —
-- coluna e constraint confirmadas e validadas; backfill dos 10 manifestos
-- ads ainda pendente (autorização separada). Revisado em 2026-07-06
-- (2ª rodada) após review pré-implementação.
--
-- Esta migration (já aplicada): adiciona `source_metadata jsonb` a
-- raw.shopee_ingestion_file para guardar metadados de nível de ARQUIVO que
-- hoje só existem no preâmbulo dos CSVs de ads e nunca foram persistidos —
-- especificamente o período REAL do relatório ("Período,DD/MM/YYYY -
-- DD/MM/YYYY"), que hoje só é aproximado via regex sobre o NOME do arquivo
-- (falha para os 2 arquivos da kokeshi, cujo nome não tem o ano).
--
-- Decisão de design (ver docs/staging_shopee_contract.md — a ser
-- atualizado quando este gate for aprovado):
--   - Coluna única `jsonb`, NULL por padrão, no MANIFESTO (não em cada
--     linha-filha) — evita duplicar o mesmo metadado em centenas de linhas
--     de anúncio do mesmo arquivo.
--   - Populada hoje só para source_type='ads' (o único que tem preâmbulo
--     estruturado); orders/shop_stats continuam NULL — a coluna é
--     genérica o suficiente para uso futuro por qualquer source_type que
--     precise de metadado de arquivo não capturado em headers_json.
--   - Minimização (revisão de 2026-07-06): só 4 chaves são gravadas —
--     period_start, period_end, report_created_at, shop_id. Não gravamos
--     shop_username/shop_display_name — nenhuma necessidade concreta
--     identificada que `brand` (já no manifesto) e `shop_id` não atendam.
--   - CHECK estrutural: quando não-NULL, `source_metadata` deve ser um
--     objeto jsonb (`jsonb_typeof(...) = 'object'`) — protege contra um
--     valor top-level array/escalar entrar por engano (ex.: um bug no
--     backfill serializando uma lista). NÃO valida as chaves internas
--     (period_start/period_end/etc.) — isso é responsabilidade do parser
--     (pipelines/ingestion/shopee_raw/ads_metadata.py) e da pré-validação
--     da staging (pipelines/staging/shopee/validations.py), não do banco.
--   - Alternativas descartadas: (a) tabela dedicada
--     raw.shopee_ads_report_metadata — over-engineering para um único
--     source_type hoje; (b) inferir o ano do arquivo da kokeshi por
--     heurística (ex.: usar o ano do arquivo vizinho) — não é uma correção
--     real, é adivinhação; (c) duplicar o metadado em raw_payload de cada
--     linha de anúncio — polui o grão "1 linha = 1 anúncio" com dado de
--     nível de relatório, e contraria a instrução de não duplicar.
--
-- Nenhum dado de comprador é armazenado aqui: os campos são só metadados
-- operacionais do RELATÓRIO/LOJA (ID da loja, datas do relatório) — ver
-- pipelines/ingestion/shopee_raw/ads_metadata.py para a extração.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE raw.shopee_ingestion_file
    ADD COLUMN source_metadata jsonb;

ALTER TABLE raw.shopee_ingestion_file
    ADD CONSTRAINT ck_shopee_ingestion_file_source_metadata_is_object
    CHECK (source_metadata IS NULL OR jsonb_typeof(source_metadata) = 'object');

COMMENT ON COLUMN raw.shopee_ingestion_file.source_metadata IS
    'Metadados de nível de ARQUIVO extraídos de fora do corpo tabular '
    '(ex.: preâmbulo dos CSVs de ads — período real do relatório, data de '
    'criação, ID da loja). NULL para arquivos sem esse tipo de metadado '
    '(orders, shop_stats) ou ainda não backfilled. Nunca contém dado de '
    'comprador/PII de cliente, nem username/nome de loja (minimização — '
    'ver pipelines/ingestion/shopee_raw/ads_metadata.py). Chaves usadas '
    'hoje: period_start, period_end, report_created_at (naive, timezone '
    'do Seller Center desconhecido — nunca assumir UTC), shop_id.';

-- Sem REVOKE/GRANT adicional: coluna nova numa tabela existente herda as
-- mesmas permissões já concedidas na tabela (ver nota de permissões em
-- shopee_raw_ddl.sql — REVOKE ALL FROM PUBLIC já aplicado à tabela toda).

COMMIT;

-- ============================================================================
-- Pré-requisitos operacionais — status em 2026-07-07: migration JÁ
-- APLICADA na primary (coluna + constraint confirmadas e validadas); o
-- backfill (passo 2 abaixo) segue pendente de autorização separada.
--   1. Esta ALTER TABLE precisa rodar com a credencial de escrita da Raw
--      (DATAMART_SHOPEE_WRITE_URL via .env.shopee-write.local), a mesma
--      usada para o DDL original — nunca com DATAMART_DATABASE_URL.
--      **Satisfeito.**
--   2. Depois da migration, rodar o backfill histórico e controlado (CLI
--      em pipelines/ingestion/shopee_raw/backfill_ads_metadata.py,
--      `--apply-confirmed`) para popular source_metadata dos 10
--      manifestos ads já existentes — o plano exige exatamente 10
--      manifestos / 5 marcas / 2 arquivos por
--      marca e aborta se o estado divergir (ver docstring daquele módulo).
--   3. Autorização explícita do usuário antes de qualquer execução real —
--      este arquivo é só o draft da migration, não uma aprovação.
--   4. O DDL base (db/sql/raw/shopee_raw_ddl.sql) JÁ FOI ATUALIZADO neste
--      working tree (revisão de 2026-07-06) para que AMBIENTES NOVOS já
--      nasçam com a coluna — não é uma pendência, é só um registro de que
--      as DUAS mudanças coexistem: esta migration cobre o ambiente JÁ
--      CARREGADO (2026-07-03/04, que precisa de ALTER TABLE porque um
--      CREATE TABLE não pode ser reaplicado); o DDL base cobre um
--      ambiente futuro criado do zero. Uma não substitui a outra.
--
-- Ordem operacional completa (ver docstring de
-- backfill_ads_metadata.py para o detalhamento e os riscos entre
-- passos): (1) commit/revisão do código — feito; (2) aplicar SOMENTE esta
-- migration — feito em 2026-07-07; (3) validar coluna+constraint — feito;
-- (4) rodar o backfill histórico dos 10 manifestos (`--apply-confirmed`) —
-- pendente, autorização separada; (5) reconciliar 10/10; (6) rodar o
-- preview read-only completo contra 100% da Raw (gate obrigatório);
-- (7) só depois considerar o DDL/transform da staging. Nenhuma nova
-- ingestão Raw deveria ter rodado entre os passos 1 e 2 — confirmado que
-- não rodou (contagens de manifesto inalteradas no Gate 3 pós-migration).
--
-- Rollback (revisado em 2026-07-06 — NÃO é mais "DROP COLUMN"):
--   Enquanto a coluna estiver NULL para todas as linhas (antes de qualquer
--   backfill real), `ALTER TABLE ... DROP COLUMN source_metadata` seria
--   seguro e reversível. Mas a partir do momento em que o backfill (Gate
--   2B, fase de escrita) tiver populado QUALQUER linha, DROP COLUMN deixa
--   de ser uma operação de rollback aceitável — destruiria dado derivado
--   que já pode ter sido consumido por leituras/relatórios/pela staging,
--   sem possibilidade de audit trail do que existia. Rollback operacional
--   pós-backfill é, em vez disso, uma das duas opções abaixo:
--     (a) restaurar os valores afetados a partir do backup auditável — a
--         Fase B do backfill (revisão de 2026-07-06, 3ª rodada) EXIGE
--         `audit_path` (não é mais opcional): grava, ANTES de qualquer
--         UPDATE, um arquivo JSON atômico (escrita em temp + rename) com
--         os 10 registros afetados (identificadores técnicos + metadata
--         anterior E aplicada — nunca payload/PII), relido e revalidado
--         do disco antes de prosseguir. `restore_from_backup_atomic`
--         (mesmo módulo) implementa a reversão a partir desse arquivo —
--         documentada e testada contra conexão falsa, NUNCA executada
--         nesta fase; ou
--     (b) abandonar a coluna como está (ela é aditiva e nunca é lida por
--         nenhum pipeline existente fora da staging opcional desta fase) —
--         "não fazer nada" é sempre uma opção de rollback válida para uma
--         coluna nova que ainda não tem consumidor obrigatório.
--   DROP COLUMN só volta a ser uma opção de rollback aceitável se for
--   explicitamente decidido que o dado backfilled deve ser destruído (não
--   apenas revertido) — isso exige autorização explícita separada, nunca
--   é o padrão.
-- ============================================================================
