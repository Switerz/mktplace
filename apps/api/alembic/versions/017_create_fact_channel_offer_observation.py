"""Gate PMA-2C2 — cria marts.fact_channel_offer_observation.

Materializa a OBSERVACAO DE OFERTA de Shopee e TikTok. O Mercado Livre nao entra
aqui: ele permanece exclusivamente em `marts.fact_marketplace_listing_price_daily`
(revisao 014), e uma oferta nunca existe nas duas fatos.

ESTA TABELA GUARDA PRECO ANUNCIADO, NAO PRECO REALIZADO
--------------------------------------------------------
`observed_price` e' o preco exibido na vitrine no instante da observacao.
`list_price` e' o preco "de" quando o canal o publica. Preco de checkout, valor
efetivamente pago, cupom individual e frete NAO existem nesta superficie e nao
tem coluna: inventa-las produziria um numero que a fonte nao sustenta.

O GRAO E' (observed_date, marketplace, offer_key), E `brand` FICA DE FORA
-------------------------------------------------------------------------
Medido na fonte antes de escrever esta migration:

    Shopee  item_id sozinho e' unico ............... 321/321
            (item_id, model_id) sozinho e' unico ... 371/371
            contas com mais de uma marca ........... 0
            marcas em mais de uma conta ............ 0
    TikTok  sku_id sozinho e' unico ................ 1.203/1.203
            sku_id com duas marcas, serie inteira .. 0 (36 dias)

Como nenhuma chave de canal precisa da marca para ser unica, `brand` e'
ATRIBUTO. A diferenca e' pratica: com a marca na PK, corrigir a marca de uma
oferta criaria linha nova e deixaria a antiga orfa DENTRO da mesma fotografia.
Como atributo, a correcao atualiza a linha que ja' existe.

PAI E MODELO DA SHOPEE NUNCA COLIDEM
-------------------------------------
Um item sem variacao vira uma oferta com `offer_key = item_id`. Um item COM
variacao e' container: ele proprio nao vira oferta, e cada modelo dele vira uma,
com `offer_key = 'item_id:model_id'`. Os dois conjuntos sao disjuntos por
construcao — `has_model` e' exclusivo — e por isso 321 pais simples + 371
modelos dao 692 ofertas, nunca 659 + 371 = 1.030.

O CHECK `ck_fcoo_model_key_shape` transforma essa regra em invariante do banco:
quem tem `model_id` precisa ter a chave composta, e quem nao tem precisa ter a
chave simples. Uma linha de modelo com chave de pai seria recusada.

DUAS DATAS, E ELAS NAO SAO A MESMA COISA
-----------------------------------------
`observed_date` e' o dia da FOTOGRAFIA, derivado do watermark da conta no fuso
America/Sao_Paulo. E' DATE sem fuso porque representa competencia diaria.
`observed_at` e' o instante em que AQUELA LINHA foi vista pela ultima vez na
origem, e pode recuar: na Shopee ha modelos com carimbo de 18 dias atras. E'
TIMESTAMPTZ porque representa um instante absoluto.

Sem essa separacao, uma unica execucao espalharia suas linhas por nove datas
diferentes e a pergunta "como estava o catalogo no dia X" perderia resposta.

IDEMPOTENCIA E COEXISTENCIA
----------------------------
Reexecutar a MESMA fotografia produz a mesma PK, entao a republicacao e' um
upsert e nao duplica. Fotografias de dias diferentes tem PK diferente e
COEXISTEM: nao ha last-newest-wins destrutivo, e por isso nao existe coluna de
versao nem de "linha vigente".

AUSENCIA NUNCA VIRA ZERO
-------------------------
Todo campo que a fonte pode nao fornecer e' NULLABLE: `observed_price`,
`list_price`, `gtin`, `promo_id`, `promo_discount_pct`, `batch_id`,
`account_watermark_at`, `model_id`, `seller_sku`, `listing_title`. Um zero em
`observed_price` afirmaria "o anuncio custa R$ 0,00"; NULL diz "nao observamos
preco", e o servico transforma isso em `invalid_channel_price` em vez de
comparar. Os CHECKs monetarios sao condicionais a presenca justamente para que
NULL continue legal e zero continue proibido.

`snapshot_status = 'absent'` esta no dominio mas HOJE nao e' produzido por
caminho nenhum: afirmar que um item sumiu exige fotografia completa da conta, e
a captura atual nao a registra. O valor fica no CHECK para quando a evidencia
existir; ninguem o fabrica agora.

O ESCOPO DA SUBSTITUICAO E' (marketplace, observed_date, shop_account)
-----------------------------------------------------------------------
Trocar uma fotografia e' apagar esse escopo e reinserir. A conta ENTRA no
escopo porque as quatro contas da Shopee carregam de forma independente: sem
ela, a carga de uma conta apagaria a fotografia de outra que nem executou.

Um escopo SAUDAVEL que hoje nao tem oferta tem seu DELETE executado e nenhum
INSERT depois — resultado `rows_loaded = 0` com execucao bem-sucedida. Isso e'
diferente de fonte indisponivel, que nao entra na lista de escopos saudaveis e
portanto nao sofre DELETE nenhum: a fotografia anterior permanece. Nao existe
linha sentinela representando "zero ofertas".

NENHUM DADO PESSOAL
--------------------
Nao ha comprador, pedido, pagamento, CPF, endereco ou telefone. As unicas
colunas textuais livres sao titulo de anuncio, SKU do vendedor e conta de loja.
"""
from __future__ import annotations

from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None

SCHEMA = "marts"
TABELA = "fact_channel_offer_observation"
QUALIFICADA = f"{SCHEMA}.{TABELA}"

#: Enums do dominio versionado (`app.services.pma_domain`). Ficam como CHECK e
#: nao como tipo ENUM do PostgreSQL: um tipo exigiria `ALTER TYPE` a cada valor
#: novo, e o dominio ainda vai crescer (`absent` e' o proximo).
MARKETPLACES = ("shopee", "tiktok")
OBSERVATION_MODES = ("daily_series", "snapshot_current")
SNAPSHOT_STATUSES = ("current", "stale", "absent", "account_did_not_run",
                     "partial_load")
PRODUCT_TYPES = ("kit_confirmed", "kit_suspected", "no_kit_signal",
                 "product_type_unknown")
PRODUCT_TYPE_SOURCES = ("channel_flag", "internal_catalog", "internal_bom",
                        "sku_prefix", "title", "none")
PROMO_CONTEXTS = ("available", "unavailable")
BUSINESS_SCOPES = ("in_scope", "out_of_business_scope")
OBSERVED_PRICE_SOURCES = ("current_price", "sale_price")


def _em_lista(coluna: str, valores) -> str:
    itens = ", ".join(f"'{v}'" for v in valores)
    return f"{coluna} IN ({itens})"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {QUALIFICADA} (
            -- ---------------------------------------------------------------
            -- IDENTIDADE. Exatamente a tupla que o sync valida em
            -- `channel_offer_sync.OFFER_IDENTITY`.
            -- ---------------------------------------------------------------
            -- Dia da FOTOGRAFIA, fuso America/Sao_Paulo. DATE sem timezone:
            -- representa competencia diaria, nao instante.
            observed_date            DATE         NOT NULL,
            marketplace              TEXT         NOT NULL,
            -- Shopee: `item_id` (pai sem variacao) ou `item_id:model_id`.
            -- TikTok: `sku_id`.
            offer_key                TEXT         NOT NULL,

            -- ---------------------------------------------------------------
            -- ATRIBUTOS DE IDENTIFICACAO. `brand` esta AQUI, nao na PK.
            -- ---------------------------------------------------------------
            brand                    TEXT         NOT NULL,
            -- Item pai na Shopee; `product_id` no TikTok. Sempre presente: e'
            -- por onde a oferta volta ao anuncio de origem.
            parent_item_id           TEXT         NOT NULL,
            -- NULO em oferta sem variacao e em toda oferta do TikTok.
            model_id                 TEXT             NULL,
            seller_sku               TEXT             NULL,
            -- EAN de consumidor ja' normalizado (8, 12 ou 13 digitos). NULO
            -- quando a fonte nao fornece: o inventario do TikTok nao tem coluna
            -- de EAN, e os modelos da Shopee tambem nao.
            gtin                     TEXT             NULL,
            listing_title            TEXT             NULL,
            -- Conta de loja na origem, e ESCOPO da substituicao.
            --
            -- NOT NULL por correcao, nao por estilo (Gate PMA-2C2-R): o DELETE
            -- que troca uma fotografia filtra por
            -- (marketplace, observed_date, shop_account). Uma linha com conta
            -- NULA nunca casaria nesse predicado e ficaria orfa para sempre —
            -- invisivel ao publisher e viva na tela.
            --
            -- Canal sem contas proprias usa o proprio nome como conta canonica
            -- ('tiktok'), que e' o identificador da unica conta que ele tem, e
            -- nao uma sentinela de dado ausente.
            shop_account             TEXT         NOT NULL,

            -- ---------------------------------------------------------------
            -- OBSERVACAO E FRESCOR
            -- ---------------------------------------------------------------
            observation_mode         TEXT         NOT NULL,
            -- Instante em que ESTA LINHA foi vista na origem. TIMESTAMPTZ: e'
            -- instante absoluto. Pode ser anterior a `observed_date` quando a
            -- linha nao foi revista na ultima carga da conta.
            observed_at              TIMESTAMPTZ      NULL,
            snapshot_status          TEXT         NOT NULL,
            -- Fim da carga da PROPRIA conta. Um MAX global classificaria 585
            -- linhas como atrasadas em vez das 446 reais, porque as quatro
            -- contas terminam em lotes distintos (janela medida de 16,5s).
            account_watermark_at     TIMESTAMPTZ      NULL,
            -- NULO enquanto a origem nao propagar lote ate a silver. Nunca
            -- preenchido com valor inventado.
            batch_id                 TEXT             NULL,

            -- ---------------------------------------------------------------
            -- SITUACAO COMERCIAL E TIPO DE PRODUTO
            -- ---------------------------------------------------------------
            is_active                BOOLEAN      NOT NULL,
            -- `kit_confirmed` so' existe onde ha flag nativa do canal (Shopee).
            -- `product_type_unknown` significa "sem sinal de kit", NUNCA
            -- "produto simples confirmado".
            product_type             TEXT         NOT NULL,
            product_type_source      TEXT         NOT NULL,

            -- ---------------------------------------------------------------
            -- PRECO ANUNCIADO. Nada aqui e' preco de checkout.
            -- ---------------------------------------------------------------
            -- NUMERIC(14,4): 14 digitos cobrem folgadamente o maior preco
            -- observado (R$ 842,90) e 4 casas preservam o centavo mesmo apos
            -- desconto proporcional, sem o arredondamento binario do float.
            observed_price           NUMERIC(14,4)    NULL,
            -- Coluna de ORIGEM do preco, gravada com o dado: `current_price` na
            -- Shopee, `sale_price` no TikTok. Sem ela, uma troca de fonte
            -- passaria despercebida numa serie historica.
            observed_price_source    TEXT         NOT NULL,
            -- Preco "de". NULO no TikTok SEMPRE: a fonte nao o fornece, e cair
            -- para `observed_price` faria toda oferta parecer sem desconto.
            list_price               NUMERIC(14,4)    NULL,
            promo_context            TEXT         NOT NULL,
            promo_id                 TEXT             NULL,
            promo_discount_pct       NUMERIC(7,4)     NULL,

            -- ---------------------------------------------------------------
            -- ESCOPO
            -- ---------------------------------------------------------------
            -- Gocase e Denavita aparecem no TikTok e sao GRAVADAS, mas ficam
            -- fora dos KPIs. Marca-las aqui e' melhor que descarta-las: some do
            -- denominador sem sumir da auditoria de cobertura.
            business_scope           TEXT         NOT NULL,

            -- ---------------------------------------------------------------
            -- PROCEDENCIA
            -- ---------------------------------------------------------------
            source_run_id            TEXT             NULL,
            synced_at                TIMESTAMPTZ  NOT NULL DEFAULT now(),

            CONSTRAINT pk_{TABELA}
                PRIMARY KEY (observed_date, marketplace, offer_key)
        )
    """)

    # ------------------------------------------------------------------
    # Dominio dos enums. CHECK, nao tipo ENUM: o dominio ainda cresce.
    # ------------------------------------------------------------------
    for sufixo, coluna, valores in (
        ("marketplace", "marketplace", MARKETPLACES),
        ("obs_mode", "observation_mode", OBSERVATION_MODES),
        ("snapshot_status", "snapshot_status", SNAPSHOT_STATUSES),
        ("product_type", "product_type", PRODUCT_TYPES),
        ("pt_source", "product_type_source", PRODUCT_TYPE_SOURCES),
        ("promo_context", "promo_context", PROMO_CONTEXTS),
        ("business_scope", "business_scope", BUSINESS_SCOPES),
        ("price_source", "observed_price_source", OBSERVED_PRICE_SOURCES),
    ):
        op.execute(
            f"ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_{sufixo} "
            f"CHECK ({_em_lista(coluna, valores)})"
        )

    # O ML nao mora aqui. O CHECK de `marketplace` ja' o impede, e este
    # comentario existe para que a intencao nao dependa de leitura da tupla.

    # ------------------------------------------------------------------
    # Forma da chave: modelo x pai. Impede que uma linha de modelo seja
    # gravada com chave de pai, o que as tornaria indistinguiveis.
    # ------------------------------------------------------------------
    op.execute(f"""
        ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_model_key_shape CHECK (
            (model_id IS NULL     AND offer_key NOT LIKE '%:%')
         OR (model_id IS NOT NULL AND offer_key = parent_item_id || ':' || model_id)
        )
    """)

    # O TikTok nao tem variacao: toda oferta dele e' chave simples.
    op.execute(f"""
        ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_tiktok_sem_modelo CHECK (
            marketplace <> 'tiktok' OR model_id IS NULL
        )
    """)

    # ------------------------------------------------------------------
    # Dinheiro. Os CHECKs sao CONDICIONAIS a presenca: NULL continua legal
    # (ausencia de observacao) e zero continua proibido (medicao falsa).
    # `<> 'NaN'` e' explicito porque em PostgreSQL 'NaN'::numeric > 0 e' FALSE
    # mas 'NaN'::numeric >= 0 e' TRUE — um CHECK ingenuo deixaria NaN entrar.
    # ------------------------------------------------------------------
    for sufixo, coluna in (("observed_price", "observed_price"),
                           ("list_price", "list_price")):
        op.execute(
            f"ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_{sufixo}_valido "
            f"CHECK ({coluna} IS NULL OR "
            f"({coluna} > 0 AND {coluna} <> 'NaN'::numeric))"
        )
    op.execute(
        f"ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_desconto_valido "
        f"CHECK (promo_discount_pct IS NULL OR "
        f"(promo_discount_pct >= 0 AND promo_discount_pct <= 100 "
        f"AND promo_discount_pct <> 'NaN'::numeric))"
    )

    # O TikTok nunca pode alegar contexto promocional: a fonte nao o fornece.
    op.execute(f"""
        ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_tiktok_sem_promo CHECK (
            marketplace <> 'tiktok'
            OR (promo_context = 'unavailable' AND list_price IS NULL
                AND promo_id IS NULL AND promo_discount_pct IS NULL)
        )
    """)

    # Shopee e' fotografia corrente; TikTok e' serie diaria. Trocar isso
    # silenciosamente faria a tela prometer um historico que nao existe.
    op.execute(f"""
        ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_modo_por_canal CHECK (
            (marketplace = 'shopee' AND observation_mode = 'snapshot_current')
         OR (marketplace = 'tiktok' AND observation_mode = 'daily_series')
        )
    """)

    # A origem do preco e' determinada pelo canal.
    op.execute(f"""
        ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_origem_do_preco CHECK (
            (marketplace = 'shopee' AND observed_price_source = 'current_price')
         OR (marketplace = 'tiktok' AND observed_price_source = 'sale_price')
        )
    """)

    # `kit_confirmed` exige flag NATIVA, que so' a Shopee tem.
    op.execute(f"""
        ALTER TABLE {QUALIFICADA} ADD CONSTRAINT ck_fcoo_kit_confirmado CHECK (
            product_type <> 'kit_confirmed'
            OR (marketplace = 'shopee' AND product_type_source = 'channel_flag')
        )
    """)

    # ------------------------------------------------------------------
    # Indices. Um para cada filtro que a tela REALMENTE aplica hoje; nenhum
    # para consulta hipotetica. A PK ja' cobre (observed_date, marketplace, ...).
    # ------------------------------------------------------------------
    # Tela: um canal por requisicao, numa data, filtrando marca.
    op.execute(
        f"CREATE INDEX idx_fcoo_canal_data_marca ON {QUALIFICADA} "
        f"(marketplace, observed_date, brand)"
    )
    # Cartoes de situacao: contagem por tipo de produto e atividade.
    op.execute(
        f"CREATE INDEX idx_fcoo_canal_data_situacao ON {QUALIFICADA} "
        f"(marketplace, observed_date, is_active, product_type)"
    )
    # ESCOPO da substituicao — exatamente o predicado do DELETE que troca uma
    # fotografia. Sem ele, cada troca varreria a tabela inteira. Substituiu o
    # indice parcial por conta: com `shop_account NOT NULL` o predicado parcial
    # perdeu sentido, e liderar por `marketplace` serve tanto o DELETE quanto a
    # leitura por canal.
    op.execute(
        f"CREATE INDEX idx_fcoo_escopo ON {QUALIFICADA} "
        f"(marketplace, observed_date, shop_account)"
    )
    # Casamento por EAN — parcial: o TikTok nao tem GTIN e os modelos da Shopee
    # tampouco, entao um indice total desperdicaria a maior parte das linhas.
    op.execute(
        f"CREATE INDEX idx_fcoo_gtin ON {QUALIFICADA} "
        f"(marketplace, gtin) WHERE gtin IS NOT NULL"
    )


def downgrade() -> None:
    # Remove SOMENTE o que o upgrade criou, na ordem inversa. Nenhuma tabela de
    # outra frente e' tocada: a 016 do Full e a 014 do PMA permanecem intactas.
    for indice in ("idx_fcoo_gtin", "idx_fcoo_escopo",
                   "idx_fcoo_canal_data_situacao", "idx_fcoo_canal_data_marca"):
        op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.{indice}")
    op.execute(f"DROP TABLE IF EXISTS {QUALIFICADA}")
