"""
Contrato declarativo da staging tipada da Shopee (DRAFT — Fase Staging 1,
revisado em 2026-07-06 após review pré-commit).

Fonte da verdade do mapping `source_type | raw_payload_key | staging_column |
type | nullable | regra | classificação PII`. O DDL e a transformação em
db/sql/staging/ são GERADOS a partir deste módulo (build_sql.py) — nunca
editar aqueles arquivos à mão. As expressões SQL de cada `rule` vêm de
`rules_registry.py` (que combina `semantics.py` e `sql_rules.py`) — a MESMA
fonte usada pelo preview read-only (`preview.py`) e pela transformação
(`build_sql.py`), para nunca haver duas listas divergentes de validação.

Convenção confirmada por inspeção read-only do Data Mart em 2026-07-04:
a staging tipada de marketplaces mora no schema `silver`, com prefixo
`stg_` (silver.stg_ml_orders, silver.stg_tiktok_orders, ...) — tabelas
DEDUPLICADAS por chave de negócio (ex.: `UNIQUE(brand, order_id)` em
`silver.stg_ml_orders`). O schema `staging` é usado por outra equipe
(Shopify/Yampi por brand) e NÃO é o padrão para marketplaces.

## Revisão de 2026-07-06 — grão de orders NÃO é canônico

`silver.stg_ml_orders`/`stg_tiktok_orders` são deduplicadas por chave de
negócio. A staging de orders da Shopee NÃO PODE seguir o mesmo padrão hoje:
não existe uma chave confiável para decidir qual snapshot é o "vigente"
quando o mesmo pedido aparece em exports sobrepostos (a Shopee reexporta o
período inteiro a cada novo arquivo, sem um número de revisão). Usar
`order_id` como chave única aqui esconderia essa ambiguidade sob um nome que
parece canônico. Por isso a tabela foi RENOMEADA para
`silver.stg_shopee_order_item_snapshots` (sufixo `_snapshots` explícito) e
seu `TableSpec.comment` deixa registrado, em texto que vai para
`COMMENT ON TABLE`, que ela não deve ser agregada diretamente sem resolver
a deduplicação primeiro. A regra de seleção do snapshot vigente (ex.: mais
recente por `raw_ingested_at`, ou por `file_id` mais alto, por `order_id`)
fica para uma fase futura da Gold — não implementada aqui. Ver também a nota
correspondente em `db/sql/raw/shopee_raw_ddl.sql`, atualizada nesta revisão
para não atribuir mais a "deduplicação" à staging.

`buyer_key` (HMAC de comprador único) foi REMOVIDA desta revisão — uma
coluna permanentemente NULL não entrega valor; algoritmo/segredo/rotação
continuam em aberto (ver docs/staging_shopee_contract.md) e a coluna pode
ser adicionada por migration quando aprovada. Compradores únicos, por ora,
vêm de `shop_stats.buyers_count`.

Inventário base (read-only, 2026-07-04, 100% da Raw carregada):
- orders: 383.298 linhas, 71 chaves JSONB, 2 templates (apice vs demais);
- shop_stats: 780 linhas (755 diárias + 25 totais de período), 17 chaves;
- ads: 804 linhas, 36 chaves, 2 templates (kokeshi tem "Segmentação de
  Público", sempre "-").

Classificação PII (mesma taxonomia de pipelines/ingestion/shopee_raw/pii.py,
estendida): a staging analítica NÃO carrega PII direta nem quase-
identificadores de alta granularidade — minimização por desenho. A Raw
continua íntegra para auditoria.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Classificação PII / sensibilidade
NAO_SENSIVEL = "nao_sensivel"
IDENTIFICADOR_OPERACIONAL = "identificador_operacional"
PII_DIRETA = "pii_direta"
ENDERECO_LOCALIZACAO = "endereco_localizacao"
FINANCEIRO = "financeiro"
TEXTO_LIVRE = "texto_livre"


@dataclass(frozen=True)
class StagingColumn:
    column: str
    sql_type: str
    rule: str                       # nome de regra em rules_registry (opcionalmente "nome:param:...")
    source_keys: tuple[str, ...]    # chaves do raw_payload ('' = derivada de f.source_filename)
    nullable: bool = True
    pii_class: str = NAO_SENSIVEL
    non_negative: bool = False      # gera CHECK(coluna >= 0) no DDL + checagem fail-fast prévia
    note: str = ""


@dataclass(frozen=True)
class ExcludedKey:
    source_key: str
    pii_class: str
    reason: str


@dataclass(frozen=True)
class TableSpec:
    staging_table: str              # nome qualificado silver.stg_*
    raw_table: str                  # tabela-filha da Raw
    source_type: str                # valor em raw.shopee_ingestion_file
    grain: str
    columns: tuple[StagingColumn, ...]
    excluded: tuple[ExcludedKey, ...] = field(default_factory=tuple)
    extra_ddl: tuple[str, ...] = field(default_factory=tuple)
    comment: str = ""


def _money(column: str, header: str, *, nullable: bool = True, note: str = "") -> StagingColumn:
    return StagingColumn(column, "numeric(14,2)", "numeric_dot", (header,),
                         nullable=nullable, pii_class=FINANCEIRO, note=note)


# Colunas técnicas/provenance idênticas nas 3 tabelas. raw_id referencia o
# id da tabela-filha Raw — toda linha staging é rastreável a UMA linha Raw.
_PROVENANCE: tuple[StagingColumn, ...] = (
    StagingColumn("raw_id", "bigint", "prov:r.id", (), nullable=False,
                  pii_class=IDENTIFICADOR_OPERACIONAL,
                  note="PK; id da linha na tabela raw.shopee_*_export correspondente"),
    StagingColumn("file_id", "bigint", "prov:r.file_id", (), nullable=False,
                  pii_class=IDENTIFICADOR_OPERACIONAL,
                  note="FK física para raw.shopee_ingestion_file(file_id) — ver build_sql.py"),
    StagingColumn("brand", "varchar(50)", "prov:r.brand", (), nullable=False,
                  pii_class=IDENTIFICADOR_OPERACIONAL),
    StagingColumn("source_row_number", "integer", "prov:r.source_row_number", (),
                  nullable=False, pii_class=IDENTIFICADOR_OPERACIONAL,
                  note="linha física no arquivo original"),
    StagingColumn("row_sha256", "char(64)", "prov:r.row_sha256", (), nullable=False,
                  pii_class=IDENTIFICADOR_OPERACIONAL,
                  note="hash da linha Raw — auditoria de correspondência"),
    StagingColumn("raw_ingested_at", "timestamptz", "prov:r.ingested_at", (),
                  nullable=False, pii_class=IDENTIFICADOR_OPERACIONAL),
)


# ---------------------------------------------------------------------------
# ORDERS — silver.stg_shopee_order_item_snapshots
# Grão: 1 linha física de SKU de pedido de um export Order.all*.xlsx
# (idêntico ao grão da Raw; nenhuma agregação/dedup nesta camada).
#
# NÃO CANÔNICA: ver docstring do módulo. Não fazer SUM/COUNT direto sobre
# esta tabela sem antes resolver qual snapshot de cada pedido é o vigente
# quando exports se sobrepõem — essa regra ainda não existe.
# ---------------------------------------------------------------------------

ORDERS = TableSpec(
    staging_table="silver.stg_shopee_order_item_snapshots",
    raw_table="raw.shopee_order_item_export",
    source_type="orders",
    grain="1 linha física de SKU de pedido, por arquivo/snapshot (igual à Raw)",
    comment=(
        "ATENCAO: NAO E UMA TABELA CANONICA. Staging tipada 1:1 da "
        "raw.shopee_order_item_export, grao = linha fisica de SKU por "
        "arquivo/snapshot. Exports sobrepostos NAO sao deduplicados aqui "
        "-- o mesmo pedido pode aparecer em multiplas linhas com file_id "
        "diferentes. NAO fazer SUM/COUNT/agregacao direta sobre esta "
        "tabela para metricas de negocio: o resultado pode contar o mesmo "
        "pedido mais de uma vez. A selecao do snapshot vigente por pedido "
        "(ex.: por raw_ingested_at mais recente, ou por file_id mais alto) "
        "e responsabilidade de uma camada Gold futura, ainda nao "
        "implementada. Sem PII direta: nome, telefone, CPF, endereco, CEP, "
        "bairro, username e textos livres ficam SO na Raw."
    ),
    columns=_PROVENANCE + (
        # Identificação do pedido / status
        StagingColumn("order_id", "varchar(20)", "text_required", ("ID do pedido",),
                      nullable=False, pii_class=IDENTIFICADOR_OPERACIONAL,
                      note="14 chars [0-9A-Z] em 100% da base; NAO e chave unica desta tabela "
                           "(repete entre snapshots) — ver aviso de grao no comentario da tabela"),
        StagingColumn("order_status", "text", "text_required", ("Status do pedido",),
                      nullable=False,
                      note="valor bruto Shopee; inclui frases como 'O comprador pode "
                           "pedir uma devolução até YYYY-MM-DD' — mapeamento canônico é da Gold"),
        StagingColumn("return_refund_status", "text", "text_null_blank",
                      ("Status da Devolução / Reembolso",)),
        StagingColumn("cancel_reason", "text", "text_null_blank", ("Cancelar Motivo",)),
        StagingColumn("order_type", "text", "text_null_blank", ("Tipo de pedido",),
                      note="só template apice; 100% vazio na base atual"),
        StagingColumn("is_hot_listing", "boolean", "bool_pair:Y:N", ("Hot Listing",),
                      note="conjunto documentado: Y/N — qualquer outro valor estoura"),
        StagingColumn("is_bmm_order", "boolean", "bool_pair:Y:N",
                      ("Indicador da Leve Mais por Menos",), note="Y/N; só N observado"),
        StagingColumn("is_fbs_order", "boolean", "bool_pair:Yes:No", ("Pedido FBS",),
                      note="Yes/No; ausente no template apice → NULL"),
        StagingColumn("is_shopee_owned", "boolean", "bool_pair:TRUE:FALSE", ("Shopee Owned",),
                      note="TRUE/FALSE; ausente no template apice → NULL"),
        # Datas operacionais (exportadas sem timezone; horário local do Seller Center)
        StagingColumn("order_created_at", "timestamp", "orders_ts",
                      ("Data de criação do pedido",), nullable=False,
                      note="'YYYY-MM-DD HH:MM'; calendário validado (ver semantics.py)"),
        StagingColumn("paid_at", "timestamp", "orders_ts_placeholder:-",
                      ("Hora do pagamento do pedido",),
                      note="placeholder '-' (48.359 linhas) → NULL"),
        StagingColumn("ship_by_at", "timestamp", "orders_ts", ("Data prevista de envio",)),
        StagingColumn("shipped_at", "timestamp", "orders_ts", ("Tempo de Envio",),
                      note="semântica observada: data/hora do envio"),
        StagingColumn("order_completed_at", "timestamp", "orders_ts",
                      ("Hora completa do pedido",)),
        StagingColumn("delivered_date", "date", "iso_date", ("Domestic Delivered Date",),
                      note="só template não-apice"),
        StagingColumn("cancel_completed_date", "date", "iso_date",
                      ("Data da Finalização do Cancelamento",), note="só template não-apice"),
        # Logística
        StagingColumn("tracking_number", "text", "text_null_blank",
                      ("Número de rastreamento",), pii_class=IDENTIFICADOR_OPERACIONAL,
                      note="código opaco da transportadora"),
        StagingColumn("shipping_option", "text", "text_null_blank", ("Opção de envio",)),
        StagingColumn("shipping_method", "text", "text_null_blank", ("Método de envio",)),
        # Produto / SKU
        StagingColumn("parent_sku_ref", "text", "text_null_blank",
                      ("Nº de referência do SKU principal",),
                      pii_class=IDENTIFICADOR_OPERACIONAL),
        StagingColumn("sku_ref", "text", "text_null_blank", ("Número de referência SKU",),
                      pii_class=IDENTIFICADOR_OPERACIONAL,
                      note="manter texto: 20 SKUs têm formato numérico BR ('9.401,45'-like)"),
        StagingColumn("product_name", "text", "text_required", ("Nome do Produto",),
                      nullable=False),
        StagingColumn("variation_name", "text", "text_null_blank", ("Nome da variação",)),
        StagingColumn("quantity", "integer", "int_strict", ("Quantidade",),
                      nullable=False, non_negative=True,
                      note="1–20 na base; fração estoura no cast, negativo é rejeitado por CHECK"),
        StagingColumn("returned_quantity", "integer", "int_strict", ("Returned quantity",),
                      non_negative=True, note="só template apice"),
        StagingColumn("order_products_count", "integer", "int_strict",
                      ("Número de produtos pedidos",), non_negative=True,
                      note="nível pedido, repetido em cada linha SKU"),
        StagingColumn("sku_total_weight_kg", "numeric(10,3)", "numeric_dot",
                      ("Peso total SKU",), non_negative=True, note="unidade inferida kg (0.02–24.0)"),
        StagingColumn("order_total_weight_kg", "numeric(10,3)", "numeric_dot",
                      ("Peso total do pedido",), non_negative=True,
                      note="nível pedido; unidade inferida kg"),
        # Preços e descontos (nível SKU, salvo nota)
        _money("original_price", "Preço original"),
        _money("deal_price", "Preço acordado"),
        _money("product_subtotal", "Subtotal do produto", nullable=False,
               note="GMV bruto da linha; soma auditada R$ 24.859.859,62"),
        _money("seller_discount", "Desconto do vendedor",
               note="1ª ocorrência do header duplicado"),
        StagingColumn("seller_discount_2", "numeric(14,2)", "coalesce:numeric_dot",
                      ("Desconto do vendedor__col23", "Desconto do vendedor__col26"),
                      pii_class=FINANCEIRO,
                      note="2ª ocorrência do header duplicado (col23 na apice, col26 nas "
                           "demais); semântica exata não confirmada — NÃO alimentar Gold "
                           "enquanto não confirmado com a Shopee/Seller Center"),
        _money("shopee_commercial_incentive", "Incentivo Shopee para ação comercial"),
        _money("commercial_action_adjustment", "Ajuste por participação em ação comercial"),
        _money("pix_payment_adjustment", "Ajuste por pagamento via PIX"),
        _money("bmm_shopee_discount", "Desconto Shopee da Leve Mais por Menos"),
        _money("bmm_seller_discount", "Desconto da Leve Mais por Menos do vendedor"),
        # Cupons / moedas (nível pedido, repetidos por linha SKU)
        StagingColumn("coupon_code", "text", "text_null_blank", ("Código do Cupom",),
                      pii_class=IDENTIFICADOR_OPERACIONAL),
        _money("seller_voucher", "Cupom do vendedor"),
        _money("shopee_voucher", "Cupom"),
        _money("coin_cashback_voucher_seller",
               "Coin Cashback Voucher Amount Sponsored by Seller"),
        _money("coupon_incentive", "Incentivo de cupom"),
        StagingColumn("shopee_coins_offset", "integer", "int_strict",
                      ("Compensar Moedas Shopee",), pii_class=FINANCEIRO, non_negative=True,
                      note="inteiro 0–10.000; unidade (moedas vs centavos) NÃO confirmada — "
                           "não alimentar Gold enquanto não confirmado"),
        _money("credit_card_discount_total", "Total descontado Cartão de Crédito"),
        # Totais e taxas (nível pedido, repetidos por linha SKU)
        _money("order_amount", "Valor Total", note="nível pedido"),
        _money("order_grand_total", "Total global",
               note="nível pedido; NÃO é settlement (ver docs/data_contracts.md)"),
        _money("buyer_paid_shipping_fee", "Taxa de envio pagas pelo comprador"),
        _money("reverse_shipping_fee", "Taxa de Envio Reversa"),
        _money("transaction_fee", "Taxa de transação"),
        _money("commission_fee_gross", "Taxa de comissão bruta"),
        _money("commission_fee_net", "Taxa de comissão líquida"),
        _money("service_fee_gross", "Taxa de serviço bruta"),
        _money("service_fee_net", "Taxa de serviço líquida"),
        _money("estimated_shipping_fee", "Valor estimado do frete"),
        _money("approx_shipping_discount", "Desconto de Frete Aproximado",
               note="só template apice"),
        # Localização de baixa granularidade (minimização: só cidade/UF/país)
        StagingColumn("delivery_city", "text", "coalesce:text_null_blank",
                      ("Cidade__col58", "Cidade__col59", "Cidade"),
                      pii_class=ENDERECO_LOCALIZACAO,
                      note="header 'Cidade' duplicado; 1ª ocorrência é 100% vazia — o valor "
                           "real está em Cidade__col58 (apice) / Cidade__col59 (demais)"),
        StagingColumn("delivery_state", "text", "text_null_blank", ("UF",),
                      pii_class=ENDERECO_LOCALIZACAO, note="nome por extenso (ex: 'São Paulo')"),
        StagingColumn("country_code", "varchar(2)", "text_null_blank", ("País",),
                      note="'BR' em 100% da base"),
    ),
    excluded=(
        ExcludedKey("Nome do destinatário", PII_DIRETA, "nome civil do recebedor"),
        ExcludedKey("Telefone", PII_DIRETA, "telefone (mascarado no export, ainda assim PII)"),
        ExcludedKey("CPF do Comprador", PII_DIRETA,
                    "documento; só template apice (4 não vazios em 21.914)"),
        ExcludedKey("Endereço de entrega", ENDERECO_LOCALIZACAO, "endereço completo"),
        ExcludedKey("CEP", ENDERECO_LOCALIZACAO,
                    "quase-identificador de alta granularidade"),
        ExcludedKey("Bairro", ENDERECO_LOCALIZACAO,
                    "reduz demais o universo em cidades pequenas; cidade+UF bastam"),
        ExcludedKey("Nome de usuário (comprador)", PII_DIRETA,
                    "username Shopee do comprador; HMAC/pseudonimização não aprovado — ver "
                    "docs/staging_shopee_contract.md. Compradores únicos vêm de shop_stats"),
        ExcludedKey("Observação do comprador", TEXTO_LIVRE,
                    "texto livre digitado pelo comprador — pode conter PII não estruturada"),
        ExcludedKey("Nota", TEXTO_LIVRE, "texto livre; 100% vazio na base atual"),
    ),
    extra_ddl=(
        "CREATE UNIQUE INDEX uk_stg_shopee_order_item_snapshots_file_row "
        "ON silver.stg_shopee_order_item_snapshots (file_id, source_row_number);",
        "CREATE INDEX idx_stg_shopee_order_item_snapshots_brand_created "
        "ON silver.stg_shopee_order_item_snapshots (brand, order_created_at);",
        "CREATE INDEX idx_stg_shopee_order_item_snapshots_order_id "
        "ON silver.stg_shopee_order_item_snapshots (order_id);",
        "CREATE INDEX idx_stg_shopee_order_item_snapshots_file_id "
        "ON silver.stg_shopee_order_item_snapshots (file_id);",
    ),
)


# ---------------------------------------------------------------------------
# SHOP STATS — silver.stg_shopee_shop_stats
# Grão: 1 linha física do relatório (diária OU total do período). A linha de
# total NÃO é descartada: row_type explícito deixa a escolha para a Gold.
# ---------------------------------------------------------------------------

_SS_INT = [
    ("orders_count", "Pedidos"),
    ("product_clicks", "Cliques Por Produto"),
    ("visitors", "Visitantes"),
    ("cancelled_orders", "Pedidos Cancelados"),
    ("refunded_orders", "Pedidos Devolvidos / Reembolsados"),
    ("buyers_count", "# de compradores"),
    ("new_buyers_count", "# de novos compradores"),
    ("existing_buyers_count", "# de compradores existentes"),
    ("potential_buyers_count", "# de compradores em potencial"),
]
_SS_MONEY_BR = [
    ("sales_brl", "Vendas (BRL)"),
    ("sales_before_shopee_discounts", "Vendas Sem os Descontos da Shopee"),
    ("sales_per_order", "Vendas por Pedido"),
    ("cancelled_sales", "Vendas Canceladas"),
    ("refunded_sales", "Vendas Devolvidas / Reembolsadas"),
]

SHOP_STATS = TableSpec(
    staging_table="silver.stg_shopee_shop_stats",
    raw_table="raw.shopee_shop_stats_export",
    source_type="shop_stats",
    grain="1 linha física do relatório shop-stats: um dia OU o total do período",
    comment=(
        "Staging tipada 1:1 da raw.shopee_shop_stats_export. row_type separa "
        "linha diaria ('daily', coluna Data = DD/MM/YYYY) da linha de total "
        "do periodo ('period_total', Data = range) — a Gold decide qual usar; "
        "esta camada preserva as duas. Valores monetarios no formato BR "
        "('1.234,56') e percentuais '3,84%' (unidade 0-100). Sem PII."
    ),
    columns=_PROVENANCE + (
        StagingColumn("row_type", "varchar(12)", "shop_stats_row_type", ("Data",),
                      nullable=False, note="'daily' | 'period_total'"),
        StagingColumn("stat_date", "date", "shop_stats_stat_date", ("Data",),
                      note="preenchida só quando row_type='daily'"),
        StagingColumn("period_start", "date", "shop_stats_period_start", ("Data",),
                      note="preenchida só quando row_type='period_total'"),
        StagingColumn("period_end", "date", "shop_stats_period_end", ("Data",),
                      note="preenchida só quando row_type='period_total'"),
    ) + tuple(
        StagingColumn(col, "numeric(14,2)", "numeric_br", (header,), pii_class=FINANCEIRO)
        for col, header in _SS_MONEY_BR
    ) + tuple(
        StagingColumn(col, "integer", "int_strict", (header,), non_negative=True)
        for col, header in _SS_INT
    ) + (
        StagingColumn("order_conversion_rate_pct", "numeric(8,2)", "pct_flexible",
                      ("Taxa de Conversão de Pedidos",), non_negative=True, note="unidade 0–100"),
        StagingColumn("repeat_purchase_rate_pct", "numeric(8,2)", "pct_flexible",
                      ("Repetir Índice de Compras",), non_negative=True, note="unidade 0–100"),
    ),
    excluded=(),
    extra_ddl=(
        "ALTER TABLE silver.stg_shopee_shop_stats ADD CONSTRAINT "
        "ck_stg_shopee_shop_stats_row_type CHECK ("
        "(row_type = 'daily' AND stat_date IS NOT NULL AND period_start IS NULL AND period_end IS NULL) "
        "OR (row_type = 'period_total' AND stat_date IS NULL AND period_start IS NOT NULL AND period_end IS NOT NULL));",
        "ALTER TABLE silver.stg_shopee_shop_stats ADD CONSTRAINT "
        "ck_stg_shopee_shop_stats_period_order CHECK ("
        "period_start IS NULL OR period_end IS NULL OR period_start <= period_end);",
        "CREATE UNIQUE INDEX uk_stg_shopee_shop_stats_file_row "
        "ON silver.stg_shopee_shop_stats (file_id, source_row_number);",
        "CREATE INDEX idx_stg_shopee_shop_stats_brand_date "
        "ON silver.stg_shopee_shop_stats (brand, stat_date);",
    ),
)


# ---------------------------------------------------------------------------
# ADS — silver.stg_shopee_ads
# Grão: 1 linha de anúncio agregada no período do relatório (arquivo).
# Nenhuma distribuição por dia nesta camada.
# ---------------------------------------------------------------------------

ADS = TableSpec(
    staging_table="silver.stg_shopee_ads",
    raw_table="raw.shopee_ads_export",
    source_type="ads",
    grain="1 anúncio agregado no período coberto pelo CSV (sem granularidade diária)",
    comment=(
        "Staging tipada 1:1 da raw.shopee_ads_export. O periodo do relatorio "
        "vem do NOME do arquivo (report_period_start/end) porque as linhas de "
        "metadados do CSV nao foram persistidas na Raw; arquivos fora do "
        "padrao (kokeshi) ficam com periodo NULL — gap documentado. NAO "
        "distribuir valores por dia nesta camada. Sem PII."
    ),
    columns=_PROVENANCE + (
        StagingColumn("report_period_start", "date", "ads_report_period_start", (),
                      note="extraído de f.source_filename; NULL quando fora do padrão"),
        StagingColumn("report_period_end", "date", "ads_report_period_end", (),
                      note="extraído de f.source_filename; NULL quando fora do padrão"),
        StagingColumn("ad_seq", "integer", "int_strict", ("#",), nullable=False,
                      non_negative=True, note="posição da linha no relatório"),
        StagingColumn("ad_name", "text", "text_required", ("Nome do Anúncio",),
                      nullable=False),
        StagingColumn("ad_status", "text", "text_required", ("Status",), nullable=False,
                      note="Em Andamento | Pausado | Encerrado"),
        StagingColumn("ad_type", "text", "text_null_blank", ("Tipos de Anúncios",),
                      note="vazio nos 5 anúncios shop-level (GMV Max Shop)"),
        StagingColumn("product_id", "text", "text_null_placeholder", ("ID do produto",),
                      pii_class=IDENTIFICADOR_OPERACIONAL,
                      note="'-' nos anúncios shop-level → NULL"),
        StagingColumn("audience_segmentation", "text", "text_null_placeholder",
                      ("Segmentação de Público",),
                      note="só template kokeshi; sempre '-' na base atual"),
        StagingColumn("creative", "text", "text_null_placeholder", ("Criativo",),
                      note="sempre '-' na base atual"),
        StagingColumn("bidding_method", "text", "text_null_blank", ("Método de Lance",)),
        StagingColumn("placement", "text", "text_null_blank", ("Posicionamento",)),
        StagingColumn("started_at", "timestamp", "br_ts_seconds", ("Data de Início",),
                      nullable=False, note="'DD/MM/YYYY HH:MM:SS'"),
        StagingColumn("ended_at", "timestamp", "br_ts_seconds_placeholder:Ilimitado",
                      ("Data de Encerramento",), note="'Ilimitado' → NULL (803 de 804)"),
        StagingColumn("impressions", "bigint", "bigint_strict", ("Impressões",),
                      nullable=False, non_negative=True),
        StagingColumn("clicks", "bigint", "bigint_strict", ("Cliques",), nullable=False,
                      non_negative=True),
        StagingColumn("ctr_pct", "numeric(8,2)", "pct_flexible", ("CTR",),
                      non_negative=True, note="0–100"),
        StagingColumn("add_to_cart", "integer", "int_null_placeholder", ("Add to Cart",),
                      non_negative=True),
        StagingColumn("add_to_cart_rate_pct", "numeric(8,2)", "pct_flexible",
                      ("Add to Cart Rate",), non_negative=True, note="0–100; '-' → NULL"),
        StagingColumn("conversions", "integer", "int_strict", ("Conversões",),
                      non_negative=True),
        StagingColumn("direct_conversions", "integer", "int_strict", ("Conversões Diretas",),
                      non_negative=True),
        StagingColumn("conversion_rate_pct", "numeric(8,2)", "pct_flexible",
                      ("Taxa de Conversão",), non_negative=True, note="0–100"),
        StagingColumn("direct_conversion_rate_pct", "numeric(8,2)", "pct_flexible",
                      ("Taxa de Conversão Direta",), non_negative=True, note="0–100"),
        StagingColumn("cost_per_conversion", "numeric(14,2)", "numeric_dot",
                      ("Custo por Conversão",), pii_class=FINANCEIRO),
        StagingColumn("cost_per_direct_conversion", "numeric(14,2)", "numeric_dot",
                      ("Custo por Conversão Direta",), pii_class=FINANCEIRO),
        StagingColumn("items_sold", "integer", "int_strict", ("Itens Vendidos",),
                      non_negative=True),
        StagingColumn("direct_items_sold", "integer", "int_strict",
                      ("Itens Vendidos Diretos",), non_negative=True),
        StagingColumn("gmv", "numeric(14,2)", "numeric_dot", ("GMV",),
                      pii_class=FINANCEIRO, note="soma auditada R$ 16.887.993,55"),
        StagingColumn("direct_revenue", "numeric(14,2)", "numeric_dot",
                      ("Receita direta",), pii_class=FINANCEIRO),
        StagingColumn("expense", "numeric(14,2)", "numeric_dot", ("Despesas",),
                      pii_class=FINANCEIRO),
        StagingColumn("roas", "numeric(10,4)", "numeric_dot", ("ROAS",)),
        StagingColumn("direct_roas", "numeric(10,4)", "numeric_dot", ("ROAS Direto",)),
        StagingColumn("acos_pct", "numeric(8,2)", "pct_flexible", ("ACOS",),
                      non_negative=True, note="0–100 tipicamente, mas pode ultrapassar 100% "
                                              "quando o custo excede a receita — sem CHECK de teto"),
        StagingColumn("direct_acos_pct", "numeric(8,2)", "pct_flexible", ("ACOS Direto",),
                      non_negative=True, note="idem — sem CHECK de teto"),
        StagingColumn("product_impressions", "integer", "int_null_placeholder",
                      ("Impressões do Produto",), non_negative=True,
                      note="sempre '-' na base atual"),
        StagingColumn("product_clicks", "integer", "int_null_placeholder",
                      ("Cliques de Produtos",), non_negative=True,
                      note="sempre '-' na base atual"),
        StagingColumn("product_ctr_pct", "numeric(8,2)", "pct_flexible", ("CTR do Produto",),
                      non_negative=True, note="sempre '-' na base atual → NULL"),
        StagingColumn("voucher_amount", "numeric(14,2)", "numeric_dot",
                      ("Voucher Amount",), pii_class=FINANCEIRO),
        StagingColumn("vouchered_sales", "numeric(14,2)", "numeric_dot",
                      ("Vouchered Sales",), pii_class=FINANCEIRO),
    ),
    excluded=(),
    extra_ddl=(
        "CREATE UNIQUE INDEX uk_stg_shopee_ads_file_row "
        "ON silver.stg_shopee_ads (file_id, source_row_number);",
        "CREATE INDEX idx_stg_shopee_ads_brand ON silver.stg_shopee_ads (brand);",
        "CREATE INDEX idx_stg_shopee_ads_file_id ON silver.stg_shopee_ads (file_id);",
        "ALTER TABLE silver.stg_shopee_ads ADD CONSTRAINT "
        "ck_stg_shopee_ads_report_period CHECK ("
        "(report_period_start IS NULL AND report_period_end IS NULL) "
        "OR (report_period_start IS NOT NULL AND report_period_end IS NOT NULL "
        "AND report_period_start <= report_period_end));",
        "ALTER TABLE silver.stg_shopee_ads ADD CONSTRAINT "
        "ck_stg_shopee_ads_ended_after_started CHECK (ended_at IS NULL OR ended_at >= started_at);",
    ),
)


ALL_SPECS: tuple[TableSpec, ...] = (ORDERS, SHOP_STATS, ADS)


def mapped_keys(spec: TableSpec) -> set[str]:
    keys: set[str] = set()
    for c in spec.columns:
        keys.update(c.source_keys)
    return keys


def excluded_keys(spec: TableSpec) -> set[str]:
    return {e.source_key for e in spec.excluded}


def covered_keys(spec: TableSpec) -> set[str]:
    """Chaves do raw_payload contempladas (mapeadas ou explicitamente excluídas)."""
    return mapped_keys(spec) | excluded_keys(spec)
