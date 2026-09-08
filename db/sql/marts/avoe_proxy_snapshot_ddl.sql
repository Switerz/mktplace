-- ============================================================================
-- Gate AVH-4A — fundacao auditavel para snapshots manuais da Avoe
-- ============================================================================
--
-- ESPECIFICACAO EXECUTAVEL. A migration `015_create_avoe_proxy_snapshots.py` e'
-- uma transcricao funcional deste arquivo. Qualquer divergencia entre os dois
-- e' defeito da migration, nao deste arquivo.
--
-- DUAS TABELAS, E SO DUAS
-- ---------------------------------------------------------------------------
--     marts.proxy_avoe_brand_monthly_target_snapshot     (meta mensal)
--     marts.proxy_avoe_extra_channel_monthly_snapshot    (faturamento informado)
--
-- Ambas sao APPEND-ONLY e IMUTAVEIS. Nenhum caminho de escrita faz UPDATE ou
-- DELETE. Reimportar exige um `captured_at` novo; um snapshot novo nunca apaga
-- nem sobrescreve um anterior.
--
-- O PREFIXO `proxy_avoe_` E' PARTE DO CONTRATO
-- ---------------------------------------------------------------------------
-- Estas tabelas vivem em `marts` porque e' de la' que o serving le', mas NAO sao
-- fatos oficiais de marketplace. O prefixo existe para que nenhuma consulta
-- possa unir estes numeros aos fatos oficiais por descuido de nomenclatura.
-- Nenhum objeto oficial e' alterado por esta especificacao.
--
-- O QUE ESTAS TABELAS NAO SAO
-- ---------------------------------------------------------------------------
-- Nao ha realizado aqui, e a ausencia e' estrutural: nao existe coluna para
-- guardar GMV, receita ou faturamento apurado pela Torre. O realizado oficial
-- vive em `marts.fact_marketplace_daily_performance` e e' consultado de la'
-- no momento da leitura — nunca copiado para dentro de um snapshot da Avoe.
--
-- A tabela de metas TAMBEM nao guarda o campo `faturamento` que existe na
-- origem (`resumo_marca_mes.faturamento`, 12 linhas preenchidas no snapshot de
-- 2026-09-01). Esse campo e' o realizado que a Avoe digita, e importa-lo criaria
-- uma segunda verdade de realizado ao lado da oficial. Ele e' descartado na
-- leitura e nao ha coluna que o receba.
--
-- `reported_amount` NAO E' GMV OFICIAL
-- ---------------------------------------------------------------------------
-- E' "Faturamento informado pela Avoe": numero digitado por terceiro, com
-- definicao nao confirmada pela fonte (bruto? liquido? inclui cancelado?).
-- `is_proxy` e' obrigatoriamente TRUE e `definition_status` obrigatoriamente
-- 'unconfirmed' — os dois por CHECK, nao por convencao. A allowlist de `channel`
-- EXCLUI tiktok, mercado_livre e shopee: por construcao, um canal oficial nao
-- consegue entrar nesta tabela, e portanto nao ha soma possivel com o GMV
-- oficial por acidente de carga.
--
-- AUSENCIA NAO E' ZERO
-- ---------------------------------------------------------------------------
-- `reported_amount` e' anulavel. NULL significa competencia sem valor
-- disponivel; zero significa zero informado pela fonte. Marca ou canal que a
-- Avoe nao reportou simplesmente NAO GERA LINHA — nao gera linha com zero.
--
-- MOEDA ASSUMIDA, NAO CONFIRMADA
-- ---------------------------------------------------------------------------
-- A origem nao declara moeda em nenhum campo, rotulo ou tooltip. O contrato
-- aceita a inferencia BRL de forma explicita, e o estado epistemologico viaja
-- em coluna separada do valor: `currency_status = 'assumed_unconfirmed'` com
-- `currency_warning` obrigatoriamente preenchido. Quem consumir e' obrigado a
-- ver que a moeda foi inferida.
--
-- VIGENCIA DAS METAS: 2026-08-01
-- ---------------------------------------------------------------------------
-- Junho e julho estao REJEITADOS por incompatibilidade de escala medida
-- (Apice sai de R$ 100 mil em julho para R$ 5 milhoes em agosto; Barbours de
-- R$ 1,03 mi para R$ 10 mi). O CHECK e' estreito de proposito: admitir uma
-- competencia anterior exige migration, exatamente como ampliar a allowlist de
-- canal. Nao ha flag de runtime que afrouxe isso.
--
-- SEM TOTAL CONSOLIDADO
-- ---------------------------------------------------------------------------
-- Nenhuma view, nenhuma coluna agregada, nenhum total materializado entre
-- marcas ou entre canais. O grao gravado e' o grao declarado, e qualquer
-- agregacao e' responsabilidade explicita de quem consulta.
--
-- ZERO PII
-- ---------------------------------------------------------------------------
-- Nao existe coluna de pessoa, cliente, pedido, usuario, e-mail, documento ou
-- endereco. As unicas entidades sao marca, canal e competencia. O importador
-- recusa qualquer coluna de origem fora da allowlist, de modo que um campo novo
-- na fonte nao entra por inercia.
--
-- ============================================================================


-- ---------------------------------------------------------------------------
-- A. Metas mensais por marca — snapshot imutavel
-- ---------------------------------------------------------------------------
CREATE TABLE marts.proxy_avoe_brand_monthly_target_snapshot (
    -- Identidade do snapshot
    source                  TEXT        NOT NULL,
    captured_at             TIMESTAMPTZ NOT NULL,
    ref_month               DATE        NOT NULL,
    brand                   TEXT        NOT NULL,

    -- Crosswalk explicito. NULL quando nao existe regra declarada para a marca:
    -- ausencia de mapeamento e' informacao, nao motivo para inventar chave.
    brand_key               TEXT        NULL,

    -- Negocio
    target_amount           NUMERIC(18,2) NOT NULL,

    -- Moeda: valor e estado epistemologico em colunas separadas
    currency_code           TEXT        NOT NULL,
    currency_status         TEXT        NOT NULL,
    currency_warning        TEXT        NULL,

    -- Proveniencia
    source_recorded_at      TIMESTAMPTZ NULL,
    source_file             TEXT        NOT NULL,
    source_file_hash        TEXT        NOT NULL,
    snapshot_id             TEXT        NOT NULL,
    import_run_id           TEXT        NOT NULL,
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_proxy_avoe_target_snapshot
        PRIMARY KEY (source, captured_at, ref_month, brand),

    -- Origem estreita: outra fonte exige migration.
    CONSTRAINT ck_pabmts_source
        CHECK (source = 'avoe_hub'),

    -- Competencia e' sempre o primeiro dia do mes.
    CONSTRAINT ck_pabmts_ref_month_truncado
        CHECK (ref_month = date_trunc('month', ref_month)::date),

    -- Vigencia imutavel do contrato. Junho e julho rejeitados.
    CONSTRAINT ck_pabmts_vigencia
        CHECK (ref_month >= DATE '2026-08-01'),

    -- 'NaN'::numeric >= 0 avalia TRUE, entao o CHECK de nao-negatividade
    -- sozinho aceitaria NaN. Os dois predicados sao necessarios.
    CONSTRAINT ck_pabmts_target_amount
        CHECK (target_amount >= 0 AND target_amount <> 'NaN'),

    CONSTRAINT ck_pabmts_currency_code
        CHECK (currency_code = 'BRL'),
    CONSTRAINT ck_pabmts_currency_status
        CHECK (currency_status IN ('confirmed', 'assumed_unconfirmed')),

    -- Moeda inferida obriga aviso. Nao ha snapshot com moeda assumida e
    -- warning vazio.
    CONSTRAINT ck_pabmts_currency_warning_obrigatorio
        CHECK (currency_status <> 'assumed_unconfirmed'
               OR (currency_warning IS NOT NULL AND btrim(currency_warning) <> '')),

    CONSTRAINT ck_pabmts_brand_nao_vazio
        CHECK (btrim(brand) <> ''),
    CONSTRAINT ck_pabmts_brand_key_nao_vazio
        CHECK (brand_key IS NULL OR btrim(brand_key) <> ''),
    CONSTRAINT ck_pabmts_source_file_nao_vazio
        CHECK (btrim(source_file) <> ''),
    CONSTRAINT ck_pabmts_snapshot_id_nao_vazio
        CHECK (btrim(snapshot_id) <> ''),
    CONSTRAINT ck_pabmts_import_run_id_nao_vazio
        CHECK (btrim(import_run_id) <> ''),

    -- SHA-256 em hexadecimal minusculo, 64 caracteres.
    CONSTRAINT ck_pabmts_source_file_hash
        CHECK (source_file_hash ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE marts.proxy_avoe_brand_monthly_target_snapshot IS
    'Gate AVH-4A. Meta mensal por marca informada pela Avoe, snapshot imutavel '
    'append-only. NAO contem realizado: o realizado oficial vive em '
    'marts.fact_marketplace_daily_performance e e'' consultado na leitura. '
    'Vigencia a partir de 2026-08-01; junho/julho rejeitados por escala '
    'incompativel. Moeda BRL assumida, nao confirmada pela fonte.';

COMMENT ON COLUMN marts.proxy_avoe_brand_monthly_target_snapshot.captured_at IS
    'Momento da captura do snapshot na origem. E'' o criterio de versao — nunca '
    'imported_at.';
COMMENT ON COLUMN marts.proxy_avoe_brand_monthly_target_snapshot.brand IS
    'Nome da marca exatamente como veio da Avoe, sem normalizacao forcada.';
COMMENT ON COLUMN marts.proxy_avoe_brand_monthly_target_snapshot.brand_key IS
    'Chave da Torre, por mapa explicito. NULL quando nao ha regra declarada.';
COMMENT ON COLUMN marts.proxy_avoe_brand_monthly_target_snapshot.currency_status IS
    'assumed_unconfirmed enquanto a Avoe nao declarar moeda. Obriga warning.';

CREATE INDEX idx_pabmts_ref_month_brand
    ON marts.proxy_avoe_brand_monthly_target_snapshot (ref_month, brand);
CREATE INDEX idx_pabmts_captured_at
    ON marts.proxy_avoe_brand_monthly_target_snapshot (captured_at DESC);
CREATE INDEX idx_pabmts_brand_ref_month
    ON marts.proxy_avoe_brand_monthly_target_snapshot (brand, ref_month);


-- ---------------------------------------------------------------------------
-- B. Faturamento informado de canais adicionais — snapshot imutavel
-- ---------------------------------------------------------------------------
CREATE TABLE marts.proxy_avoe_extra_channel_monthly_snapshot (
    -- Identidade do snapshot
    source                  TEXT        NOT NULL,
    captured_at             TIMESTAMPTZ NOT NULL,
    ref_month               DATE        NOT NULL,
    brand                   TEXT        NOT NULL,
    channel                 TEXT        NOT NULL,

    brand_key               TEXT        NULL,
    channel_source_label    TEXT        NOT NULL,

    -- Negocio. ANULAVEL de proposito: NULL e' indisponivel, 0 e' zero informado.
    reported_amount         NUMERIC(18,2) NULL,

    -- Natureza do dado, por CHECK e nao por convencao
    is_proxy                BOOLEAN     NOT NULL,
    definition_status       TEXT        NOT NULL,
    definition_warning      TEXT        NOT NULL,

    currency_code           TEXT        NOT NULL,
    currency_status         TEXT        NOT NULL,

    -- Cobertura: torna a agregacao diaria->mensal auditavel
    days_covered            INTEGER     NOT NULL,
    first_business_date     DATE        NOT NULL,
    last_business_date      DATE        NOT NULL,
    coverage_status         TEXT        NOT NULL,

    -- Proveniencia
    -- `atualizado_em` valido da origem, com fallback para `criado_em` valido;
    -- NULL somente quando a origem nao carimbou nenhum dos dois. Timestamp
    -- invalido ou sem timezone FALHA na leitura, nunca vira NULL.
    source_recorded_at      TIMESTAMPTZ NULL,
    source_file             TEXT        NOT NULL,
    source_file_hash        TEXT        NOT NULL,
    snapshot_id             TEXT        NOT NULL,
    import_run_id           TEXT        NOT NULL,
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_proxy_avoe_extra_channel_snapshot
        PRIMARY KEY (source, captured_at, ref_month, brand, channel),

    CONSTRAINT ck_paecms_source
        CHECK (source = 'avoe_hub'),
    CONSTRAINT ck_paecms_ref_month_truncado
        CHECK (ref_month = date_trunc('month', ref_month)::date),

    -- Regime diario da fonte comeca em 2026-06-10; antes disso o campo e' uma
    -- janela movel de 28 dias e nao e' aditivo.
    CONSTRAINT ck_paecms_vigencia
        CHECK (ref_month >= DATE '2026-06-01'),

    CONSTRAINT ck_paecms_reported_amount
        CHECK (reported_amount IS NULL
               OR (reported_amount >= 0 AND reported_amount <> 'NaN')),

    -- Proxy nao e' opcional.
    CONSTRAINT ck_paecms_is_proxy
        CHECK (is_proxy IS TRUE),
    CONSTRAINT ck_paecms_definition_status
        CHECK (definition_status = 'unconfirmed'),
    CONSTRAINT ck_paecms_definition_warning_obrigatorio
        CHECK (btrim(definition_warning) <> ''),

    -- Allowlist estreita que EXCLUI os canais oficiais. Por construcao, tiktok,
    -- mercado_livre e shopee nao entram nesta tabela.
    CONSTRAINT ck_paecms_channel_allowlist
        CHECK (channel IN ('magalu', 'shein', 'kwai',
                           'beleza_na_web', 'rd_marketplace', 'amazon')),

    CONSTRAINT ck_paecms_currency_code
        CHECK (currency_code = 'BRL'),
    CONSTRAINT ck_paecms_currency_status
        CHECK (currency_status IN ('confirmed', 'assumed_unconfirmed')),

    CONSTRAINT ck_paecms_days_covered
        CHECK (days_covered >= 1),
    CONSTRAINT ck_paecms_datas_na_competencia
        CHECK (first_business_date <= last_business_date
               AND first_business_date >= ref_month
               AND last_business_date < (ref_month + INTERVAL '1 month')::date),
    CONSTRAINT ck_paecms_coverage_status
        CHECK (coverage_status IN ('partial_month', 'full_month')),

    CONSTRAINT ck_paecms_brand_nao_vazio
        CHECK (btrim(brand) <> ''),
    CONSTRAINT ck_paecms_brand_key_nao_vazio
        CHECK (brand_key IS NULL OR btrim(brand_key) <> ''),
    CONSTRAINT ck_paecms_channel_source_label_nao_vazio
        CHECK (btrim(channel_source_label) <> ''),
    CONSTRAINT ck_paecms_source_file_nao_vazio
        CHECK (btrim(source_file) <> ''),
    CONSTRAINT ck_paecms_snapshot_id_nao_vazio
        CHECK (btrim(snapshot_id) <> ''),
    CONSTRAINT ck_paecms_import_run_id_nao_vazio
        CHECK (btrim(import_run_id) <> ''),
    CONSTRAINT ck_paecms_source_file_hash
        CHECK (source_file_hash ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE marts.proxy_avoe_extra_channel_monthly_snapshot IS
    'Gate AVH-4A. "Faturamento informado pela Avoe" para canais que a Torre nao '
    'cobre, snapshot imutavel append-only. NAO e'' GMV oficial: is_proxy sempre '
    'TRUE e definition_status sempre unconfirmed, por CHECK. A allowlist de '
    'channel exclui tiktok/mercado_livre/shopee, de modo que nao ha soma '
    'possivel com o GMV oficial. reported_amount NULL e'' indisponivel, nunca 0.';

COMMENT ON COLUMN marts.proxy_avoe_extra_channel_monthly_snapshot.reported_amount IS
    'Faturamento informado pela Avoe. NULL = indisponivel; 0 = zero informado. '
    'Nunca somar aos fatos oficiais de marketplace.';
COMMENT ON COLUMN marts.proxy_avoe_extra_channel_monthly_snapshot.days_covered IS
    'Dias com linha na origem dentro da competencia. Torna auditavel a '
    'agregacao diaria->mensal.';
COMMENT ON COLUMN marts.proxy_avoe_extra_channel_monthly_snapshot.coverage_status IS
    'partial_month quando days_covered nao cobre a competencia inteira.';

CREATE INDEX idx_paecms_ref_month_channel_brand
    ON marts.proxy_avoe_extra_channel_monthly_snapshot (ref_month, channel, brand);
CREATE INDEX idx_paecms_captured_at
    ON marts.proxy_avoe_extra_channel_monthly_snapshot (captured_at DESC);
CREATE INDEX idx_paecms_brand_ref_month
    ON marts.proxy_avoe_extra_channel_monthly_snapshot (brand, ref_month);
