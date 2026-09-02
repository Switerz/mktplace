-- ============================================================================
-- Gate PMA-1A / PMA-1A-R — camada de serving do monitoramento de precos proprios
-- ============================================================================
--
-- ESTE ARQUIVO NAO E' UMA MIGRATION ALEMBIC, E ISSO E' DELIBERADO.
-- ---------------------------------------------------------------------------
-- O head Alembic em `origin/main` e' `012`. A revisao `013` JA EXISTE, commitada
-- na branch `unit-economics-audit` (`013_create_fact_tiktok_order_discounts_daily.py`,
-- commit 9e4e699, revision="013", down_revision="012") e, na reverificacao do
-- PMA-1A-R, CONTINUA fora de `origin/main`.
--
-- As tres saidas possiveis eram todas defeituosas:
--   * criar `013`            -> colisao direta de revisao com a frente UE8;
--   * criar `014` com down_revision="013" -> dependencia fabricada numa revisao
--     que nao existe em `origin/main`; `alembic upgrade head` quebraria em
--     qualquer ambiente que nao tenha a branch da UE8;
--   * criar `014` com down_revision="012" -> DUAS revisoes com o mesmo
--     `down_revision`, ou seja, arvore Alembic ramificada — explicitamente
--     proibido pelo gate.
--
-- Por isso o DDL vive aqui, no formato que o repo ja usa para DDL versionado
-- (`db/sql/**`), e a conversao em migration e' o primeiro item do PMA-1B, depois
-- que a `013` da UE8 entrar em `origin/main` por fast-forward. O conteudo abaixo
-- e' a especificacao executavel: a migration futura deve ser a transcricao
-- literal dele, apenas escolhendo a proxima revisao livre de verdade.
--
-- NADA AQUI FOI APLICADO. Nenhuma conexao foi aberta, nenhum objeto foi criado.
--
-- FAIL-FAST: sem `IF NOT EXISTS` em nenhum CREATE, por convencao das migrations
-- existentes. Reaplicar num ambiente que ja tenha os objetos deve falhar alto.
--
-- DUAS TABELAS, NAO UMA
-- ---------------------------------------------------------------------------
-- Observacao de anuncio e referencia B2B nunca se fundem. Sao fatos de natureza
-- diferente: a observacao e' uma serie diaria reescrita por janela (idempotente);
-- a referencia e' um snapshot append-only que precisa ser auditavel para tras.
--
-- O QUE ESTA CAMADA NAO E'
-- ---------------------------------------------------------------------------
-- Nao ha PMA aqui. `reference_type = 'suggested_retail_pdv'` e' o preco sugerido
-- de revenda ("Preco na Ponta / PDV") das tabelas B2B, medido no PMA-0 como
-- markup aritmetico sobre o preco de atacado — variavel por marca (1,50 em
-- Barbours; 1,60 em Yenzah/Rituaria/Kokeshi; 1,34-1,57 em Apice) e sem vigencia.
-- Nao e' preco minimo anunciado, nao foi aprovado como politica e nao sustenta
-- sancao. `policy_status = 'not_applicable_to_own_store_monitoring'`.
--
-- COMPARACAO PARCIAL, DE DIRECAO INDETERMINADA  (PMA-1A-R, F8)
-- ---------------------------------------------------------------------------
-- Nao ha frete, cupom de vitrine, subsidio de plataforma nem preco de checkout:
-- a fonte ML nao os oferece. As colunas correspondentes NAO EXISTEM nestas
-- tabelas, para que ausencia nao possa ser lida como zero.
--
-- O preco de checkout se compoe como `produto + frete - cupom`. Frete ELEVA e
-- cupom REDUZ, portanto, sem os dois, a direcao liquida do desvio e'
-- INDETERMINADA. Esta comparacao e' parcial, baseada somente no preco anunciado
-- do produto, nao representa o preco final de checkout, e sua diferenca pode
-- mudar de valor E DE SINAL quando frete ou cupom forem considerados.
-- `coverage_status = 'advertised_only'`.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- A. marts.fact_marketplace_listing_price_daily
--    Historico diario do preco ANUNCIADO dos anuncios PROPRIOS.
--
-- GRAO: ref_date x marketplace x seller_id x item_id
--   Comprovado na fonte (2026-09-02): `silver.stg_ml_item_price_history` tem
--   58.706 linhas e 58.706 pares (item_id, ref_date) distintos. `variation_id`
--   e' 0 em 100% das linhas — o preco da fonte e' de nivel ITEM, nunca de
--   variacao. Por isso `variation_id` nao existe nesta tabela: guardar uma
--   coluna constante sugeriria uma granularidade que a fonte nao entrega.
--
-- TRES TEMPOS DISTINTOS, NOMEADOS PELO QUE CADA UM E'  (PMA-1A-R, F2)
--   `ref_date`                     -> o DIA observado. Chave do grao.
--   `price_captured_at`            -> quando o PRECO foi capturado, direto de
--                                     `stg_ml_item_price_history.extracted_at`.
--   `listing_metadata_updated_at`  -> quando o CADASTRO do anuncio mudou, de
--                                     `stg_ml_items.updated_at`. NAO e' hora do
--                                     preco e nao pode ser apresentado como tal.
--   `synced_at`                    -> quando ESTA linha foi publicada no Neon.
--
--   A versao anterior mapeava `stg_ml_items.updated_at` para `source_updated_at`
--   e o expunha como `observed_at`. Era semanticamente FALSO: aquele timestamp
--   descreve o estado cadastral corrente do item, nao a captura do preco
--   historico. `extracted_at` foi medido e serve: 58.706/58.706 nao-nulos,
--   11.261 instantes distintos, 74 dias de extracao para 74 `ref_date`, e ZERO
--   linhas em que `extracted_at::date <> ref_date`.
--
-- ATRIBUTOS DE ESTADO CORRENTE — LIMITACAO DECLARADA
--   `seller_sku`, `gtin`, `listing_title`, `permalink`, `seller_id` e
--   `catalog_listing` vem de `silver.stg_ml_items`, que tem UMA linha por
--   item_id (908 linhas, 908 item_id distintos) e representa o estado de HOJE,
--   nao o estado em `ref_date`. Reprocessar uma janela antiga reescreve esses
--   atributos com o valor corrente; `listing_metadata_updated_at` e `synced_at`
--   tornam a deriva auditavel. Somente `advertised_price`, `original_price`,
--   `currency`, `listing_status` e `price_captured_at` sao pontuais de `ref_date`.
-- ----------------------------------------------------------------------------
CREATE TABLE marts.fact_marketplace_listing_price_daily (
    ref_date            date            NOT NULL,
    marketplace         varchar(16)     NOT NULL,
    brand               varchar(50)     NOT NULL,
    seller_id           bigint          NOT NULL,
    item_id             varchar(32)     NOT NULL,

    -- Anulaveis porque a fonte pode nao ter o atributo cadastrado no anuncio.
    -- Medicao 2026-09-02: SELLER_SKU presente em 907/908 itens, GTIN em 903/908.
    -- NULO significa "nao cadastrado no anuncio", nunca "vazio".
    seller_sku          varchar(64),
    gtin                varchar(14),

    listing_title       text            NOT NULL,
    permalink           text            NOT NULL,

    advertised_price    numeric(14,2)   NOT NULL,
    -- `original_price` e' o preco "de" quando existe promocao. Medido nulo em
    -- 39.215 de 58.706 linhas (66,8%): ausencia de promocao, NAO desconto zero.
    original_price      numeric(14,2),
    currency            char(3)         NOT NULL,
    listing_status      varchar(32)     NOT NULL,
    catalog_listing     boolean,

    -- `timestamp` SEM fuso, de proposito: as duas colunas de origem sao
    -- `timestamp without time zone` na Silver e a fonte NAO declara o fuso do
    -- seu relogio. Converter para `timestamptz` exigiria escolher um fuso, e
    -- inventar fuso e' inventar dado. Consequencia declarada: sao instantes
    -- LOCAIS DA FONTE, nao devem ser renderizados como instante absoluto nem
    -- misturados com `synced_at`.
    price_captured_at           timestamp   NOT NULL,
    listing_metadata_updated_at timestamp,

    -- Gerado por nos, portanto com fuso.
    synced_at           timestamptz     NOT NULL DEFAULT now(),
    source_run_id       text            NOT NULL,

    CONSTRAINT pk_fact_marketplace_listing_price_daily
        PRIMARY KEY (ref_date, marketplace, seller_id, item_id),

    -- MVP: um unico canal. A fonte de preco ANUNCIADO so existe para o ML —
    -- Shopee tem apenas preco transacional de export de pedido, TikTok nao tem
    -- preco no catalogo e Amazon nao tem nenhuma tabela na Torre (PMA-0 Fase 4).
    -- O CHECK e' estreito de proposito: ampliar o canal exige migration, o que
    -- forca a decisao a ser explicita em vez de acidental.
    CONSTRAINT ck_fmlpd_marketplace
        CHECK (marketplace = 'ml'),

    -- Preco anunciado nunca e' negativo (medido: 0 linhas < 0 na fonte).
    -- `<> 'NaN'` e' obrigatorio e NAO redundante: em Postgres 'NaN'::numeric >= 0
    -- avalia TRUE, entao um CHECK de nao-negatividade sozinho aceitaria NaN.
    CONSTRAINT ck_fmlpd_advertised_price
        CHECK (advertised_price >= 0 AND advertised_price <> 'NaN'),
    CONSTRAINT ck_fmlpd_original_price
        CHECK (original_price IS NULL
               OR (original_price >= 0 AND original_price <> 'NaN')),

    -- Medido: 1 unica moeda na fonte, 0 linhas <> 'BRL'.
    CONSTRAINT ck_fmlpd_currency
        CHECK (currency = 'BRL'),

    -- Dominio observado na fonte: active, paused, under_review, inactive.
    CONSTRAINT ck_fmlpd_listing_status
        CHECK (listing_status IN ('active', 'paused', 'under_review', 'inactive')),

    -- Marcas com catalogo ML proprio comprovado (PMA-0): as 4 medidas na fonte.
    -- `apice` e `yenzah` NAO entram: nao possuem catalogo ML na Torre, e por isso
    -- sao `out_of_scope_no_ml_catalog` no contrato do endpoint.
    --
    -- FAIL-CLOSED DELIBERADO (PMA-1A-R, F10): incluir marca nova exige mudanca
    -- em TRES lugares — este CHECK (por migration), a allowlist do sync e a
    -- allowlist do contrato — e cada um falha alto sozinho. Uma marca nova na
    -- fonte NUNCA derruba dado publicado: o sync a detecta e aborta ANTES de
    -- abrir a transacao gravavel, portanto antes de qualquer DELETE.
    CONSTRAINT ck_fmlpd_brand
        CHECK (brand IN ('barbours', 'kokeshi', 'lescent', 'rituaria')),

    -- Strings de identidade nao podem ser vazias: '' passaria por NOT NULL e
    -- viraria uma chave silenciosamente invalida.
    CONSTRAINT ck_fmlpd_item_id_nao_vazio
        CHECK (btrim(item_id) <> ''),
    CONSTRAINT ck_fmlpd_run_id_nao_vazio
        CHECK (btrim(source_run_id) <> ''),

    -- Vazio nao e' uma chave de match: se o atributo nao existe, deve ser NULO.
    CONSTRAINT ck_fmlpd_seller_sku_nao_vazio
        CHECK (seller_sku IS NULL OR btrim(seller_sku) <> ''),

    -- GTIN aqui e' somente digito. Nao ha CHECK de tamanho 13: o lado da
    -- OBSERVACAO guarda o que o anuncio declara, e classificar 14 digitos como
    -- DUN e' responsabilidade do lado da REFERENCIA (tabela B), que e' onde a
    -- regra de match se decide.
    CONSTRAINT ck_fmlpd_gtin_digitos
        CHECK (gtin IS NULL OR gtin ~ '^[0-9]{8,14}$')
);

COMMENT ON TABLE marts.fact_marketplace_listing_price_daily IS
    'Gate PMA-1A. Historico diario do PRECO ANUNCIADO de anuncios PROPRIOS no '
    'Mercado Livre. Grao: ref_date x marketplace x seller_id x item_id. '
    'ESCOPO: somente lojas proprias — os 4 seller_id da fonte sao as 4 contas '
    'da casa, e nao existe nesta tabela nenhuma observacao de terceiro ou '
    'revendedor. NAO E'' fiscalizacao de revendedor. '
    'COBERTURA advertised_only: sem frete, cupom de vitrine, subsidio de '
    'plataforma ou preco de checkout; as colunas correspondentes nao existem '
    'para que ausencia nunca seja lida como zero. Como checkout = produto + '
    'frete - cupom, a direcao liquida do desvio e'' INDETERMINADA sem esses '
    'componentes: a comparacao e'' PARCIAL e pode mudar de sinal. '
    'Os atributos seller_sku/gtin/listing_title/permalink/seller_id/'
    'catalog_listing sao ESTADO CORRENTE de silver.stg_ml_items, nao valores '
    'pontuais de ref_date.';

COMMENT ON COLUMN marts.fact_marketplace_listing_price_daily.advertised_price IS
    'Preco anunciado em ref_date (silver.stg_ml_item_price_history.price). NAO e'' '
    'preco de checkout: exclui frete, cupom de vitrine e subsidio de plataforma.';
COMMENT ON COLUMN marts.fact_marketplace_listing_price_daily.original_price IS
    'Preco "de" quando ha promocao. NULO = sem promocao (66,8% das linhas '
    'medidas), nunca desconto zero.';
COMMENT ON COLUMN marts.fact_marketplace_listing_price_daily.gtin IS
    'GTIN declarado no anuncio (attributes->>GTIN). NULO = nao cadastrado.';
COMMENT ON COLUMN marts.fact_marketplace_listing_price_daily.price_captured_at IS
    'Instante em que o PRECO foi capturado pelo coletor da Silver '
    '(stg_ml_item_price_history.extracted_at). E'' o unico timestamp que descreve '
    'a observacao do preco. `timestamp` sem fuso porque a origem tambem e'' sem '
    'fuso e nao declara seu relogio — nao renderizar como instante absoluto.';
COMMENT ON COLUMN marts.fact_marketplace_listing_price_daily.listing_metadata_updated_at IS
    'Ultima alteracao do CADASTRO do anuncio (stg_ml_items.updated_at). NAO e'' o '
    'horario do preco e nunca deve ser exposto como tal — existe para tornar '
    'auditavel a deriva dos atributos de estado corrente.';


-- ----------------------------------------------------------------------------
-- B. marts.fact_suggested_price_reference_snapshot
--    Snapshot APPEND-ONLY do preco sugerido de revenda (PDV) das tabelas B2B.
--
-- APPEND-ONLY, SEM UPDATE E SEM DELETE POR CHAVE DE NEGOCIO
--   Cada importacao cria um `snapshot_id` novo. Nada e' sobrescrito. E' o unico
--   desenho que permite responder "qual referencia valia em tal data" — e o
--   PMA-0 mostrou que as planilhas nao tem vigencia nenhuma, portanto o
--   `captured_at` do snapshot e' a UNICA nocao de tempo que existe.
--
-- VIGENCIA: SO 'missing'  (PMA-1A-R, F5)
--   O CHECK admite exclusivamente `'missing'`. A versao anterior aceitava
--   tambem `'declared'` e `'expired'` — estados que esta tabela NAO CONSEGUE
--   PROVAR, porque nao tem `valid_from` nem `valid_to`. Um banco que aceita um
--   estado que nao pode sustentar convida a preenche-lo por engano. Quando uma
--   referencia verdadeiramente vigente existir, ela ganha colunas de vigencia,
--   migration e contrato proprios.
--
-- PRECO AUSENTE SOBREVIVE COMO NULO  (PMA-1A-R, F1)
--   `suggested_retail_amount` e' ANULAVEL e o CHECK e' BICONDICIONAL com
--   `quality_status`: `missing_suggested_price` <=> valor NULO. Assim a linha
--   sem preco continua auditavel no snapshot (o operador ve que o produto
--   existe na planilha e que o PDV ficou em branco) sem virar preco zero e sem
--   derrubar a publicacao. Ela nao entra em nenhum indice de match.
--
-- ZERO PII
--   Somente produto e preco. As planilhas de origem tem, nas linhas 1-31, um
--   bloco cadastral (Razao Social, CNPJ, I.E., CEP, Endereco, Bairro, Cidade,
--   Estado, Telefone, E-mail). Esse bloco NAO e' lido pelo importador e NAO tem
--   coluna aqui. Nenhuma deve ser adicionada.
-- ----------------------------------------------------------------------------
CREATE TABLE marts.fact_suggested_price_reference_snapshot (
    snapshot_id         varchar(64)     NOT NULL,
    -- Deterministico: sha256 de (brand | source_sku | source_gtin | linha de
    -- origem). Deterministico e' requisito, nao conveniencia: sem ele reimportar
    -- o mesmo arquivo geraria identidades novas e a comparacao entre snapshots
    -- deixaria de ser possivel.
    reference_row_id    char(64)        NOT NULL,
    captured_at         timestamptz     NOT NULL,

    brand               varchar(50)     NOT NULL,
    source_sku          varchar(64)     NOT NULL,
    -- Preenchido SOMENTE quando o codigo e' EAN de consumidor (8, 12 ou 13
    -- digitos). Codigo de 14 digitos e' DUN de caixa e fica NULO aqui, com o
    -- valor bruto registrado em `quality_notes` — 3 linhas de Barbours medidas
    -- no PMA-0. Deixar um DUN entrar como GTIN casaria caixa com unidade.
    source_gtin         varchar(13),
    product_name        text            NOT NULL,

    -- Anulaveis: podem nao estar preenchidos na planilha. NULO != 0.
    wholesale_amount        numeric(14,2),
    suggested_retail_amount numeric(14,2),

    reference_type      varchar(32)     NOT NULL,
    validity_status     varchar(16)     NOT NULL,
    quality_status      varchar(40)     NOT NULL,
    quality_notes       text,

    source_file_hash    char(64)        NOT NULL,
    synced_at           timestamptz     NOT NULL DEFAULT now(),
    source_run_id       text            NOT NULL,

    CONSTRAINT pk_fact_suggested_price_reference_snapshot
        PRIMARY KEY (snapshot_id, reference_row_id),

    -- O contrato nomeia o que a referencia E'. `pma` NAO e' um valor aceito:
    -- chamar PDV de PMA e' exatamente o erro semantico que o PMA-0 refutou com
    -- medicao. Aceitar o valor no CHECK conveniente-mente permitiria o erro.
    CONSTRAINT ck_fsprs_reference_type
        CHECK (reference_type = 'suggested_retail_pdv'),

    -- SO 'missing'. Ver a nota de vigencia no cabecalho deste bloco.
    CONSTRAINT ck_fsprs_validity_status
        CHECK (validity_status = 'missing'),

    CONSTRAINT ck_fsprs_quality_status
        CHECK (quality_status IN (
            'ok',
            'ambiguous_duplicate_sku',
            'ambiguous_duplicate_gtin',
            'ambiguous_duplicate_both',
            'missing_suggested_price'
        )),

    -- BICONDICIONAL, nao dois CHECKs soltos: `missing_suggested_price` <=> NULO.
    -- Nos dois sentidos, de proposito. Se so exigisse "NULO quando missing", uma
    -- linha `ok` com valor NULO passaria e viraria referencia fantasma; se so
    -- exigisse "valor quando nao-missing", uma linha `missing` com valor 10,00
    -- passaria e mentiria sobre a planilha. Referencia igual a zero nao e'
    -- referencia, dai `> 0`; `<> 'NaN'` porque 'NaN' >= 0 e' TRUE em Postgres.
    CONSTRAINT ck_fsprs_suggested_retail_amount
        CHECK (
            (quality_status =  'missing_suggested_price'
                AND suggested_retail_amount IS NULL)
            OR
            (quality_status <> 'missing_suggested_price'
                AND suggested_retail_amount IS NOT NULL
                AND suggested_retail_amount > 0
                AND suggested_retail_amount <> 'NaN')
        ),

    -- `wholesale_amount` e' independente do diagnostico: a planilha pode ter
    -- atacado sem PDV e vice-versa.
    CONSTRAINT ck_fsprs_wholesale_amount
        CHECK (wholesale_amount IS NULL
               OR (wholesale_amount > 0 AND wholesale_amount <> 'NaN')),

    -- As 5 marcas das tabelas B2B. Inclui apice e yenzah: a referencia delas
    -- existe e e' valida — o que falta e' catalogo ML para comparar, e isso e'
    -- decidido no endpoint (`out_of_scope_no_ml_catalog`), nao aqui.
    CONSTRAINT ck_fsprs_brand
        CHECK (brand IN ('apice', 'barbours', 'kokeshi', 'rituaria', 'yenzah')),

    CONSTRAINT ck_fsprs_source_sku_nao_vazio
        CHECK (btrim(source_sku) <> ''),
    CONSTRAINT ck_fsprs_product_name_nao_vazio
        CHECK (btrim(product_name) <> ''),
    CONSTRAINT ck_fsprs_snapshot_id_nao_vazio
        CHECK (btrim(snapshot_id) <> ''),
    CONSTRAINT ck_fsprs_run_id_nao_vazio
        CHECK (btrim(source_run_id) <> ''),

    -- EAN de consumidor: 8, 12 ou 13 digitos. 14 e' recusado pelo proprio tipo
    -- varchar(13) e por este CHECK — duas travas, porque esta e' a coluna que
    -- decide match primario.
    CONSTRAINT ck_fsprs_source_gtin_formato
        CHECK (source_gtin IS NULL OR source_gtin ~ '^[0-9]{8}$|^[0-9]{12,13}$'),

    CONSTRAINT ck_fsprs_hash_hex
        CHECK (source_file_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_fsprs_row_id_hex
        CHECK (reference_row_id ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE marts.fact_suggested_price_reference_snapshot IS
    'Gate PMA-1A. Snapshot APPEND-ONLY do PRECO SUGERIDO DE REVENDA (PDV) das '
    'tabelas B2B. reference_type = suggested_retail_pdv. NAO E'' PMA: o PMA-0 '
    'mediu o PDV como markup aritmetico sobre o preco de atacado, variavel por '
    'marca e sem vigencia, e o proprio documento de politica lista "congelar a '
    'tabela de PMA por SKU" como pendencia. policy_status = '
    'not_applicable_to_own_store_monitoring. '
    'validity_status admite SOMENTE missing: esta tabela nao tem valid_from nem '
    'valid_to e por isso nao consegue provar nenhum outro estado de vigencia. '
    'suggested_retail_amount e'' ANULAVEL, em biconditional com quality_status: '
    'missing_suggested_price <=> valor NULO. Linha sem PDV permanece auditavel '
    'sem virar preco zero. '
    'ZERO PII: somente produto e preco. O bloco cadastral das planilhas '
    '(razao social, CNPJ, I.E., endereco, CEP, telefone, e-mail) NAO e'' lido '
    'nem persistido, e nenhuma coluna para ele deve ser criada.';

COMMENT ON COLUMN marts.fact_suggested_price_reference_snapshot.source_gtin IS
    'EAN de CONSUMIDOR (8/12/13 digitos). NULO quando o codigo de origem tem 14 '
    'digitos (DUN de caixa) — o valor bruto vai para quality_notes e a linha '
    'continua elegivel a match secundario por SKU unico na marca.';
COMMENT ON COLUMN marts.fact_suggested_price_reference_snapshot.suggested_retail_amount IS
    'PDV da planilha. NULO se e somente se quality_status = '
    'missing_suggested_price. NULO nunca e'' zero e nunca entra em match.';
COMMENT ON COLUMN marts.fact_suggested_price_reference_snapshot.quality_status IS
    'Elegibilidade de match diagnosticada na importacao. ambiguous_* sinaliza '
    'que a chave correspondente esta disputada; missing_suggested_price torna a '
    'linha inutilizavel como referencia. Diagnostico REGISTRADO — a autoridade '
    'de ambiguidade em tempo de consulta e'' a CONTAGEM de candidatos, para nao '
    'existirem dois mecanismos capazes de discordar.';


-- ============================================================================
-- INDICES — nenhum secundario, e a razao esta escrita  (PMA-1A-R, F9)
-- ============================================================================
-- A versao anterior criava 9 indices secundarios. Todos foram removidos: cada
-- uma das cinco consultas que o serving realmente executa e' servida por um
-- PREFIXO de uma das duas PKs, e indice que nenhum SQL consome so custa escrita,
-- espaco e VACUUM.
--
--   PK do fato      = (ref_date, marketplace, seller_id, item_id)
--   PK do snapshot  = (snapshot_id, reference_row_id)
--
-- | # | Consulta de `monitoramento_preco_service`                      | Servida por             |
-- |---|-----------------------------------------------------------------|-------------------------|
-- | 1 | max(ref_date) ... WHERE marketplace = :m                        | PK fato, scan reverso   |
-- | 2 | max(synced_at) ... WHERE marketplace = :m AND ref_date = :d     | PK fato, prefixo 1-2    |
-- | 3 | snapshot_id, max(captured_at) ... GROUP BY snapshot_id LIMIT 1  | PK snapshot, prefixo 1  |
-- | 4 | SELECT ... WHERE marketplace = :m AND ref_date = :d [+ brand]   | PK fato, prefixo 1-2    |
-- | 5 | SELECT ... WHERE snapshot_id = :sid                             | PK snapshot, prefixo 1  |
--
-- Os indices removidos e por que nenhum se justificava:
--   ix_fmlpd_ref_date              -> prefixo exato da PK do fato. Redundante.
--   ix_fmlpd_marketplace_ref_date  -> reordenacao das duas primeiras colunas da
--                                     PK; `marketplace` tem UM valor possivel
--                                     (CHECK = 'ml'), logo seletividade zero.
--   ix_fmlpd_brand_ref_date        -> a consulta 4 fixa `ref_date` por
--                                     igualdade, e um dia tem ~855 linhas; o
--                                     filtro de marca sobre 855 linhas nao
--                                     precisa de indice.
--   ix_fmlpd_brand_gtin            -> nenhum SQL busca por gtin/sku: o servico
--   ix_fmlpd_brand_seller_sku         carrega o dia inteiro e casa em memoria.
--   ix_fsprs_snapshot_id           -> prefixo exato da PK do snapshot.
--   ix_fsprs_captured_at           -> a consulta 3 agrupa por `snapshot_id`; e' a
--                                     PK que da a ordenacao para o
--                                     GroupAggregate, nao um indice em
--                                     `captured_at`.
--   ix_fsprs_brand_gtin            -> a consulta 5 filtra por `snapshot_id` e o
--   ix_fsprs_brand_sku                match roda em memoria. Nenhum consumidor.
--
-- NAO HOUVE `EXPLAIN`: as tabelas nao existem, nada foi aplicado, e um plano
-- inventado seria pior que nenhum. A justificativa acima e' estrutural (prefixo
-- de PK contra o texto das cinco consultas), nao medida.
--
-- Sabidamente NAO indexavel: o filtro `product_query` usa `ILIKE '%...%'`, que
-- nenhum btree serve. Roda como scan sobre as ~855 linhas do dia. Se algum dia
-- doer, a resposta e' `pg_trgm` — nao um btree novo.
--
-- Quando a migration do PMA-1B nascer, indice novo entra com o SQL que o
-- consome escrito ao lado, ou nao entra.
-- ============================================================================


-- ============================================================================
-- DOWNGRADE — limitado aos objetos novos.
-- Nenhuma tabela preexistente e' tocada. Sem indices secundarios, nada mais cai.
-- ============================================================================
-- DROP TABLE marts.fact_suggested_price_reference_snapshot;
-- DROP TABLE marts.fact_marketplace_listing_price_daily;
