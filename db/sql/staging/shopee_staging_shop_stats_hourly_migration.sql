-- ============================================================================
-- Gate SD1-2 — migração da tabela JÁ IMPLANTADA silver.stg_shopee_shop_stats
-- para o contrato de três row_type: 'daily' | 'hourly' | 'period_total'.
--
-- NÃO EXECUTADO NESTE GATE. Este arquivo é a evidência revisável do que será
-- aplicado UMA ÚNICA VEZ, com autorização explícita, antes da próxima carga
-- Silver que incluir arquivos de shop-stats no layout horário.
--
-- Contexto: o export de shop-stats passou a vir com a coluna 'Tempo'
-- (DD/MM/YYYY HH:MM), 24 linhas horárias + 1 linha de total do período por
-- arquivo. O grão arquivístico da Silver continua sendo a linha física do
-- arquivo — (file_id, source_row_number) —, portanto as 24 horas são
-- PRESERVADAS como evidência. Nenhuma agregação acontece nesta camada e
-- nenhum total é fabricado: a linha de total é a que o próprio arquivo traz.
--
-- Escopo: EXCLUSIVAMENTE silver.stg_shopee_shop_stats.
--   - adiciona a coluna stat_hour;
--   - substitui ck_stg_shopee_shop_stats_row_type pela regra de 3 ramos;
--   - adiciona ck_stg_shopee_shop_stats_stat_hour_cheia;
--   - atualiza COMMENTs de tabela/colunas afetadas.
--
-- NÃO faz: DELETE, TRUNCATE, DROP TABLE, UPDATE de dados, recriação da
-- tabela, alteração de PK/unique/índices, nem qualquer toque em raw.*,
-- gold.*, marts.* ou nas demais tabelas silver.*.
--
-- Contrato final:
--   row_type='daily'         -> stat_date NOT NULL, stat_hour NULL,     period_* NULL
--   row_type='hourly'        -> stat_date NOT NULL, stat_hour NOT NULL, period_* NULL
--   row_type='period_total'  -> stat_date NULL,     stat_hour NULL,     period_* NOT NULL
--
-- Estado esperado ANTES da aplicação (medido por inspeção read-only em
-- 2026-08-11): 1.191 linhas — 1.145 'daily' + 46 'period_total'; nenhuma
-- linha 'hourly'; (file_id, source_row_number) sem duplicidade. As linhas
-- históricas já satisfazem o novo contrato com stat_hour NULL, por isso
-- NENHUM UPDATE é necessário.
--
-- Aplicação: executar este arquivo INTEGRALMENTE, com a credencial de
-- escrita da Silver, via cliente de banco com ON_ERROR_STOP=1. É uma única
-- transação: ou tudo é aplicado, ou nada é. Reexecutar após sucesso aborta
-- explicitamente no Passo 1 (item C) — não é um no-op silencioso.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '600s';

-- Passo 0: serializa contra o transform Silver e contra outra execução desta
-- mesma migração. A chave é EXATAMENTE a mesma usada por
-- db/sql/staging/shopee_staging_transform.sql — (84772001, 1). Chaves
-- diferentes no mesmo namespace NÃO se excluem mutuamente: usar outra chave
-- deixaria a migração rodar em paralelo com o transform, que é justamente o
-- cenário a evitar (DROP/ADD de constraint enquanto o transform insere).
-- Sem retry e sem backoff: se o lock não vier dentro de lock_timeout, a
-- transação falha e a migração é reagendada por decisão humana.
SELECT pg_advisory_xact_lock(84772001, 1);

-- ----------------------------------------------------------------------------
-- Passo 1 — PRECHECKS fail-fast. Qualquer estado inesperado aborta a
-- transação inteira ANTES de qualquer DDL. Nada é "consertado" aqui.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    v_count integer;
    v_def   text;
    v_norm  text;
    -- Forma NORMALIZADA da constraint antiga, coletada read-only do banco
    -- real em 2026-08-11 (não é suposição): pg_get_constraintdef() com os
    -- parênteses removidos e as sequências de espaço colapsadas em um só.
    -- A normalização absorve apenas o parentesado e o espaçamento que o
    -- PostgreSQL escolhe ao reimprimir a expressão; QUALQUER diferença
    -- material — ramo a mais, condição extra, ramo faltando, menção a
    -- hourly/stat_hour — muda esta string e reprova.
    v_esperado CONSTANT text :=
        'CHECK row_type::text = ''daily''::text AND stat_date IS NOT NULL '
        'AND period_start IS NULL AND period_end IS NULL '
        'OR row_type::text = ''period_total''::text AND stat_date IS NULL '
        'AND period_start IS NOT NULL AND period_end IS NOT NULL';
BEGIN
    -- A) não aplicar em réplica.
    IF pg_is_in_recovery() THEN
        RAISE EXCEPTION 'SD1-2 precheck A: alvo esta em recovery (replica) -- aplicar somente no primary';
    END IF;

    -- B) a tabela existe.
    SELECT count(*) INTO v_count
    FROM information_schema.tables
    WHERE table_schema = 'silver' AND table_name = 'stg_shopee_shop_stats';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'SD1-2 precheck B: silver.stg_shopee_shop_stats nao encontrada';
    END IF;

    -- C) idempotência explícita: a coluna ainda NÃO pode existir.
    SELECT count(*) INTO v_count
    FROM information_schema.columns
    WHERE table_schema = 'silver' AND table_name = 'stg_shopee_shop_stats'
      AND column_name = 'stat_hour';
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'SD1-2 precheck C: stat_hour ja existe -- migracao provavelmente ja aplicada; abortando em vez de reaplicar';
    END IF;

    -- D) a constraint antiga existe EXATAMENTE na forma esperada. Comparação
    --    por igualdade da forma normalizada — não por LIKE, que aceitaria
    --    uma definição materialmente diferente desde que contivesse o
    --    trecho procurado. Se alguém já a alterou por fora, este script não
    --    é o correto para o estado e aborta ANTES de qualquer DDL.
    SELECT pg_get_constraintdef(oid) INTO v_def
    FROM pg_constraint
    WHERE conrelid = 'silver.stg_shopee_shop_stats'::regclass
      AND conname = 'ck_stg_shopee_shop_stats_row_type';
    IF v_def IS NULL THEN
        RAISE EXCEPTION 'SD1-2 precheck D: ck_stg_shopee_shop_stats_row_type nao encontrada';
    END IF;

    v_norm := btrim(regexp_replace(regexp_replace(v_def, '[()]', '', 'g'), '\s+', ' ', 'g'));

    IF v_norm IS DISTINCT FROM v_esperado THEN
        RAISE EXCEPTION 'SD1-2 precheck D: ck_stg_shopee_shop_stats_row_type nao esta na forma antiga esperada (comparacao normalizada) -- revisar manualmente antes de migrar';
    END IF;

    -- Guarda redundante e explícita: nenhuma menção ao contrato novo pode
    -- existir na constraint antiga. Já implicada pela igualdade acima, mas
    -- mantida para tornar a intenção inequívoca em leitura de auditoria.
    IF v_norm LIKE '%hourly%' OR v_norm LIKE '%stat_hour%' THEN
        RAISE EXCEPTION 'SD1-2 precheck D: constraint ja menciona hourly/stat_hour -- migracao provavelmente ja aplicada';
    END IF;

    -- E) nenhum row_type fora do domínio antigo.
    SELECT count(*) INTO v_count
    FROM silver.stg_shopee_shop_stats
    WHERE row_type NOT IN ('daily', 'period_total');
    IF v_count > 0 THEN
        RAISE EXCEPTION 'SD1-2 precheck E: % linha(s) com row_type fora de daily/period_total', v_count;
    END IF;

    -- F) as linhas históricas já satisfazem o NOVO contrato (com stat_hour
    --    NULL). Se alguma não satisfizer, a migração para aqui: corrigir dado
    --    NÃO é escopo deste script.
    SELECT count(*) INTO v_count
    FROM silver.stg_shopee_shop_stats
    WHERE NOT (
        (row_type = 'daily'
             AND stat_date IS NOT NULL
             AND period_start IS NULL AND period_end IS NULL)
        OR (row_type = 'period_total'
             AND stat_date IS NULL
             AND period_start IS NOT NULL AND period_end IS NOT NULL)
    );
    IF v_count > 0 THEN
        RAISE EXCEPTION 'SD1-2 precheck F: % linha(s) historica(s) nao satisfazem o novo contrato', v_count;
    END IF;

    -- G) o grão arquivístico continua íntegro antes de mexer em qualquer coisa.
    SELECT count(*) INTO v_count FROM (
        SELECT file_id, source_row_number
        FROM silver.stg_shopee_shop_stats
        GROUP BY 1, 2 HAVING count(*) > 1
    ) d;
    IF v_count > 0 THEN
        RAISE EXCEPTION 'SD1-2 precheck G: % par(es) (file_id, source_row_number) duplicado(s)', v_count;
    END IF;
END $$;

-- Guarda a contagem de linhas para o postcheck de "nenhum dado alterado".
CREATE TEMP TABLE _sd1_2_baseline ON COMMIT DROP AS
SELECT count(*) AS linhas,
       count(*) FILTER (WHERE row_type = 'daily')        AS linhas_daily,
       count(*) FILTER (WHERE row_type = 'period_total') AS linhas_period_total
FROM silver.stg_shopee_shop_stats;

-- ----------------------------------------------------------------------------
-- Passo 2 — coluna nova. Nullable e sem DEFAULT: no PostgreSQL 11+ não há
-- reescrita da tabela nem invalidação de índices.
-- ----------------------------------------------------------------------------
ALTER TABLE silver.stg_shopee_shop_stats
    ADD COLUMN stat_hour time without time zone;

-- ----------------------------------------------------------------------------
-- Passo 3 — substitui a regra de row_type. O ADD CONSTRAINT valida TODAS as
-- linhas existentes (varredura completa); se alguma violar, a transação
-- inteira é revertida.
-- ----------------------------------------------------------------------------
ALTER TABLE silver.stg_shopee_shop_stats
    DROP CONSTRAINT ck_stg_shopee_shop_stats_row_type;

ALTER TABLE silver.stg_shopee_shop_stats
    ADD CONSTRAINT ck_stg_shopee_shop_stats_row_type CHECK (
        (row_type = 'daily' AND stat_date IS NOT NULL AND stat_hour IS NULL
             AND period_start IS NULL AND period_end IS NULL)
        OR (row_type = 'hourly' AND stat_date IS NOT NULL AND stat_hour IS NOT NULL
             AND period_start IS NULL AND period_end IS NULL)
        OR (row_type = 'period_total' AND stat_date IS NULL AND stat_hour IS NULL
             AND period_start IS NOT NULL AND period_end IS NOT NULL)
    );

-- ----------------------------------------------------------------------------
-- Passo 4 — hora cheia. Não prejudica o histórico (stat_hour sempre NULL lá).
-- ----------------------------------------------------------------------------
ALTER TABLE silver.stg_shopee_shop_stats
    ADD CONSTRAINT ck_stg_shopee_shop_stats_stat_hour_cheia CHECK (
        stat_hour IS NULL
        OR (EXTRACT(minute FROM stat_hour) = 0 AND EXTRACT(second FROM stat_hour) = 0)
    );

-- ----------------------------------------------------------------------------
-- Passo 5 — comentários alinhados ao contrato final.
-- ----------------------------------------------------------------------------
COMMENT ON TABLE silver.stg_shopee_shop_stats IS
    'Staging tipada 1:1 da raw.shopee_shop_stats_export. row_type separa linha diaria (''daily'', coluna Data = DD/MM/YYYY), linha HORARIA (''hourly'', coluna Tempo = DD/MM/YYYY HH:MM, Gate SD1-2) e linha de total do periodo (''period_total'', Data/Tempo = range) — a Gold decide qual usar; esta camada preserva as tres, sem agregar hora em dia e sem fabricar total. Valores monetarios no formato BR (''1.234,56'') e percentuais ''3,84%'' (unidade 0-100). Sem PII.';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.row_type IS '''daily'' | ''hourly'' | ''period_total''';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.stat_date IS 'preenchida quando row_type=''daily'' ou ''hourly''';
COMMENT ON COLUMN silver.stg_shopee_shop_stats.stat_hour IS 'preenchida só quando row_type=''hourly''; sempre hora cheia';

-- ----------------------------------------------------------------------------
-- Passo 6 — POSTCHECKS. Ainda dentro da transação: qualquer desvio reverte
-- tudo. Confirma que a estrutura chegou ao estado alvo e que NENHUM dado foi
-- alterado (mesma contagem total e por row_type).
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    v_count integer;
    v_def   text;
    v_base  record;
BEGIN
    SELECT * INTO v_base FROM _sd1_2_baseline;

    SELECT count(*) INTO v_count
    FROM information_schema.columns
    WHERE table_schema = 'silver' AND table_name = 'stg_shopee_shop_stats'
      AND column_name = 'stat_hour' AND data_type = 'time without time zone';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'SD1-2 postcheck: stat_hour ausente ou com tipo inesperado';
    END IF;

    SELECT pg_get_constraintdef(oid) INTO v_def
    FROM pg_constraint
    WHERE conrelid = 'silver.stg_shopee_shop_stats'::regclass
      AND conname = 'ck_stg_shopee_shop_stats_row_type';
    IF v_def IS NULL OR v_def NOT LIKE '%hourly%' THEN
        RAISE EXCEPTION 'SD1-2 postcheck: ck_stg_shopee_shop_stats_row_type nao reflete o contrato de 3 ramos';
    END IF;

    SELECT count(*) INTO v_count
    FROM pg_constraint
    WHERE conrelid = 'silver.stg_shopee_shop_stats'::regclass
      AND conname = 'ck_stg_shopee_shop_stats_stat_hour_cheia';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'SD1-2 postcheck: ck_stg_shopee_shop_stats_stat_hour_cheia ausente';
    END IF;

    -- PK, unique de grão e índice de leitura continuam existindo.
    SELECT count(*) INTO v_count
    FROM pg_indexes
    WHERE schemaname = 'silver' AND tablename = 'stg_shopee_shop_stats'
      AND indexname IN ('stg_shopee_shop_stats_pkey',
                        'uk_stg_shopee_shop_stats_file_row',
                        'idx_stg_shopee_shop_stats_brand_date');
    IF v_count <> 3 THEN
        RAISE EXCEPTION 'SD1-2 postcheck: esperado 3 indices preservados, encontrado %', v_count;
    END IF;

    -- FKs de lineage preservadas.
    SELECT count(*) INTO v_count
    FROM pg_constraint
    WHERE conrelid = 'silver.stg_shopee_shop_stats'::regclass AND contype = 'f';
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'SD1-2 postcheck: esperado 2 FKs preservadas, encontrado %', v_count;
    END IF;

    -- Nenhum dado alterado.
    SELECT count(*) INTO v_count FROM silver.stg_shopee_shop_stats;
    IF v_count <> v_base.linhas THEN
        RAISE EXCEPTION 'SD1-2 postcheck: contagem mudou de % para %', v_base.linhas, v_count;
    END IF;

    SELECT count(*) INTO v_count
    FROM silver.stg_shopee_shop_stats WHERE row_type = 'daily';
    IF v_count <> v_base.linhas_daily THEN
        RAISE EXCEPTION 'SD1-2 postcheck: contagem de daily mudou';
    END IF;

    SELECT count(*) INTO v_count
    FROM silver.stg_shopee_shop_stats WHERE row_type = 'period_total';
    IF v_count <> v_base.linhas_period_total THEN
        RAISE EXCEPTION 'SD1-2 postcheck: contagem de period_total mudou';
    END IF;

    -- stat_hour nasce integralmente NULL: nenhuma linha histórica foi tocada.
    SELECT count(*) INTO v_count
    FROM silver.stg_shopee_shop_stats WHERE stat_hour IS NOT NULL;
    IF v_count > 0 THEN
        RAISE EXCEPTION 'SD1-2 postcheck: % linha(s) historica(s) com stat_hour preenchido', v_count;
    END IF;
END $$;

COMMIT;

-- ============================================================================
-- Verificação read-only SUGERIDA, em sessão separada, após o COMMIT:
--
--   SELECT conname, pg_get_constraintdef(oid)
--   FROM pg_constraint
--   WHERE conrelid = 'silver.stg_shopee_shop_stats'::regclass AND contype = 'c'
--   ORDER BY conname;
--
--   SELECT row_type, count(*), count(stat_hour) AS com_hora
--   FROM silver.stg_shopee_shop_stats GROUP BY 1 ORDER BY 1;
--
-- Esperado logo após a migração e ANTES da primeira carga horária:
--   daily        1145  com_hora 0
--   period_total   46  com_hora 0
-- ============================================================================
