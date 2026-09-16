"""Gate PMA-2C1A — sync das ofertas de Shopee e TikTok: Data Mart -> Neon.

    Data Mart (read-only)  ->  este CLI  ->  marts.* no Neon  ->  API

Mesma arquitetura de `sync_ml_listing_price_serving.py`: o backend no Render nao
consulta o Data Mart, e este CLI e' a unica travessia.

INERTE NESTA RODADA
-------------------
`--apply` e' RECUSADO enquanto a migration da tabela destino nao existir. O head
Alembic e' 015 em todas as refs e `marts.fact_channel_offer_observation` nao
existe no Neon (verificado por `to_regclass`). A recusa nao e' um lembrete: e'
uma barreira que consulta o estado real do banco antes de qualquer escrita.

Nao ha `CREATE TABLE` em runtime. Criar o destino a partir do sync tiraria o
schema do controle do Alembic e produziria duas fontes de verdade sobre a forma
da tabela — exatamente o que a serializacao da migration quer evitar.

O QUE `--diagnose` FAZ
---------------------
Le, transforma e RECONCILIA. Abre as duas conexoes em `READ ONLY` de verdade
(`SET TRANSACTION READ ONLY` na sessao, nao apenas convencao), monta os registros
que a futura tabela receberia e prova as particoes do PMA-2B-R2.

Desde o Gate PMA-2C2-R o modulo CARREGA o SQL de substituicao
(`SQL_DELETE_SCOPE`) como constante, para que o contrato de publicacao seja
revisavel e testavel. Ele nao e' EXECUTADO por caminho nenhum nesta rodada:
`--apply` e' recusado antes de qualquer conexao, `assert_apply_authorized` exige
a revisao 017 carimbada E a relacao existente, e as duas unicas conexoes que o
modulo abre sao `READ ONLY` impostas pelo servidor. NAO ha `CREATE TABLE`,
`INSERT`, `UPDATE`, `TRUNCATE` nem `COPY` em lugar algum.

O ML NAO PASSA POR AQUI
-----------------------
`marts.fact_marketplace_listing_price_daily` continua sendo a fonte unica do
Mercado Livre. Uma oferta nunca existe nas duas fatos, e `CHANNEL_MARKETPLACES`
e' a fronteira que garante isso.

ESTADO DA FOTOGRAFIA, POR CONTA
-------------------------------
As quatro contas da Shopee terminam a carga em lotes distintos — medido: janela
de 16,5s entre a primeira e a ultima. Concluir `stale` comparando com o
`MAX(ingested_at)` GLOBAL marcaria 572 linhas como atrasadas quando somente 3
realmente estao. Por isso o watermark e' SEMPRE por conta.

E `absent` so' e' afirmavel com fotografia COMPLETA da conta: carga parcial
produz `partial_load` e conta que nao executou produz `account_did_not_run`.
`absent` afirma remocao; os outros dois afirmam desconhecimento, e confundi-los
apagaria um item do monitoramento por causa de uma falha de coleta.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]
                       / "apps" / "api"))

from app.services import pma_domain as dom  # noqa: E402
from app.services import pma_match as pm  # noqa: E402

TARGET_TABLE = "marts.fact_channel_offer_observation"
REFERENCE_TABLE = "marts.fact_suggested_price_reference_snapshot"

#: Revisao Alembic que criara o destino. Ainda NAO existe.
#:
#: E' **017**, e nao 016: a frente Full ja' reservou a 016
#: (`016_create_fact_ml_fulfillment_daily.py`, `down_revision = "015"`, cria
#: `marts.fact_ml_fulfillment_daily`), hoje como arquivo nao versionado na
#: worktree `gate-full-1a`. O PMA nao pode usar o mesmo numero: duas revisoes
#: com o mesmo id, ou duas com `down_revision = "015"`, produziriam heads
#: concorrentes e o Alembic recusaria o upgrade.
#:
#: Portanto a migration do PMA sera' `017` com `down_revision = "016"`, criada
#: SOMENTE depois que a 016 de Full for integrada. Nada disso acontece nesta
#: rodada — nem o arquivo 017 e' criado.
#:
#: Enquanto `alembic_version` nao alcancar este valor, `--apply` e' recusado.
REQUIRED_MIGRATION = "017"

#: Revisao de Full que precisa entrar ANTES. Registrada aqui para que a
#: dependencia entre frentes fique explicita no codigo, e nao so' num relatorio.
BLOCKING_MIGRATION_OWNED_BY_OTHER_TRACK = "016"

CHANNEL_MARKETPLACES = dom.CHANNEL_OFFER_MARKETPLACES

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_USAGE = 5

#: Colunas do registro, na ordem do futuro INSERT. Explicitar a tupla evita
#: `SELECT *` e evita que uma coluna nova entre sem passar por revisao.
RECORD_COLUMNS = (
    "observed_date", "observed_at", "marketplace", "brand", "offer_key",
    "parent_item_id", "model_id", "seller_sku", "gtin", "listing_title",
    "shop_account", "observation_mode", "snapshot_status", "account_watermark_at",
    "batch_id", "is_active", "product_type", "product_type_source",
    "observed_price", "observed_price_source", "list_price", "promo_context",
    "promo_id", "promo_discount_pct", "business_scope",
)

#: Campos que NUNCA podem entrar no contrato. Nome de cliente, endereco, CPF,
#: telefone, e-mail: nada disso e' necessario para comparar preco com PDV, e o
#: teste `test_nenhuma_pii_no_contrato` trava a lista.
FORBIDDEN_FIELD_TOKENS = (
    "cpf", "cnpj", "telefone", "phone", "email", "endereco", "address",
    "buyer", "cliente", "customer", "recipient", "destinatario", "cep",
)


class ChannelSyncError(RuntimeError):
    """Recusa do sync. Mensagem sempre FIXA: nunca carrega DSN, host ou SQL."""


class ApplyNotAuthorizedError(ChannelSyncError):
    """`--apply` pedido sem a migration do destino."""


@dataclass
class AccountClock:
    """Relogio de UMA conta. `watermark_at` e' o fim da carga DESTA conta."""

    marketplace: str
    account: str
    watermark_at: datetime | None
    rows_seen: int = 0
    batch_id: str | None = None
    complete: bool = True

    def snapshot_status_for(self, row_ingested_at: datetime | None) -> str:
        """Estado de UMA linha contra o relogio da PROPRIA conta.

        Nunca compara com o maximo global — ver o cabecalho do modulo.
        """
        if self.watermark_at is None:
            return dom.SNAPSHOT_ACCOUNT_DID_NOT_RUN
        if not self.complete:
            # Fotografia parcial NAO pode produzir `absent`: a linha pode
            # simplesmente nao ter sido coletada nesta passada.
            return dom.SNAPSHOT_PARTIAL_LOAD
        if row_ingested_at is None:
            return dom.SNAPSHOT_STALE
        return (dom.SNAPSHOT_CURRENT if row_ingested_at >= self.watermark_at
                else dom.SNAPSHOT_STALE)


@dataclass
class DiagnoseReport:
    marketplace: str
    observed_date: date | None = None
    records: list = field(default_factory=list)
    clocks: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Conexoes — as duas READ ONLY de verdade
# ---------------------------------------------------------------------------
def _read_only(url: str):
    """Conexao com transacao READ ONLY imposta pelo servidor.

    `SET TRANSACTION READ ONLY` faz o PostgreSQL recusar qualquer escrita, o que
    e' mais forte que confiar em nao escrevermos. Se algum caminho tentar um
    INSERT por engano, o banco aborta em vez de gravar.
    """
    conn = psycopg2.connect(url, connect_timeout=30)
    conn.set_session(readonly=True, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
    return conn


def _sanitize(exc: BaseException) -> str:
    """Mensagem segura. O texto do driver carrega host, usuario e SQL."""
    return f"falha de origem ({type(exc).__name__}); detalhe suprimido do log"


# ---------------------------------------------------------------------------
# Barreira do --apply
# ---------------------------------------------------------------------------
def assert_apply_authorized(conn) -> None:
    """Recusa `--apply` enquanto o destino nao existir. Consulta o banco real.

    Duas provas independentes, ambas obrigatorias: a revisao Alembic carimbada e
    a existencia fisica da relacao. Uma so' nao basta — um `stamp` manual
    passaria a primeira sem criar a tabela, e uma tabela criada a mao passaria a
    segunda sem estar sob controle do Alembic.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        carimbadas = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT to_regclass(%s)", (TARGET_TABLE,))
        existe = cur.fetchone()[0]
    if REQUIRED_MIGRATION not in carimbadas:
        raise ApplyNotAuthorizedError(
            "publicacao nao autorizada: a migration do destino ainda nao foi "
            "aplicada. Esta rodada e' inerte por decisao de gate — a revisao "
            "precisa ser serializada com as demais frentes antes de existir."
        )
    if existe is None:
        raise ApplyNotAuthorizedError(
            "publicacao nao autorizada: a relacao de destino nao existe. Este "
            "CLI nao cria tabela em runtime; o schema pertence ao Alembic."
        )


# ---------------------------------------------------------------------------
# Transformacao — PURA e deterministica
# ---------------------------------------------------------------------------
def _decimal_or_none(valor) -> Decimal | None:
    if valor is None:
        return None
    try:
        dec = Decimal(str(valor))
    except Exception:
        return None
    return dec if dec.is_finite() else None


def build_shopee_records(rows, clocks: dict) -> list[dict]:
    """Pai SEM variacao vira oferta; pai COM variacao e' container.

    Cada linha de entrada ja' chega decidida por `has_model`: `is_model=False`
    so' aparece para pais sem variacao, e `is_model=True` para modelos. Um pai
    com variacao NUNCA gera registro, e por isso 659 pais + 371 modelos viram
    692 ofertas, nao 1.030.
    """
    registros = []
    for row in rows:
        if not row["is_model"] and row.get("has_model"):
            continue  # container: quem vira oferta sao os modelos dele
        conta = row.get("shop_account") or ""
        relogio = clocks.get(conta)
        offer_key = dom.build_offer_key(
            dom.MARKETPLACE_SHOPEE,
            item_id=row["item_id"],
            model_id=row.get("model_id") if row["is_model"] else None,
            has_model=bool(row["is_model"]),
        )
        tipo, fonte = dom.classify_product_type(
            dom.MARKETPLACE_SHOPEE,
            channel_kit_flag=bool(row.get("is_kit")),
            internal_is_kit=row.get("internal_is_kit"),
            internal_has_bom=row.get("internal_has_bom"),
            seller_sku=row.get("seller_sku"),
            title=row.get("listing_title"),
        )
        estado = (relogio.snapshot_status_for(row.get("ingested_at"))
                  if relogio else dom.SNAPSHOT_ACCOUNT_DID_NOT_RUN)
        registros.append({
            # `observed_date` e' o dia da FOTOGRAFIA (watermark da conta), nao o
            # dia em que a linha foi tocada pela ultima vez.
            #
            # Isso importa: os modelos carregam `ingested_at` que recua ate
            # 2026-08-28. Datar cada linha pelo proprio carimbo espalharia uma
            # unica execucao por NOVE datas diferentes, e a pergunta "como estava
            # o catalogo no dia X" deixaria de ter resposta — a fotografia
            # ficaria fatiada entre particoes.
            #
            # O carimbo proprio da linha nao se perde: vive em `observed_at`, e
            # `snapshot_status` diz se ela foi revista nesta passada.
            "observed_date": dom.observed_date_from(
                relogio.watermark_at if relogio else row.get("ingested_at")),
            "observed_at": row.get("ingested_at"),
            "marketplace": dom.MARKETPLACE_SHOPEE,
            "brand": pm.normalize_brand_key(row.get("brand")),
            "offer_key": offer_key,
            "parent_item_id": str(row["item_id"]),
            "model_id": str(row["model_id"]) if row["is_model"] else None,
            "seller_sku": row.get("seller_sku"),
            "gtin": pm.consumer_ean_or_none(row.get("gtin")),
            "listing_title": row.get("listing_title"),
            # NUNCA nulo: uma linha sem conta ficaria fora do DELETE de
            # escopo e viraria orfa permanente. `canonical_account` levanta
            # se nao houver conta, em vez de gravar um orfao silencioso.
            "shop_account": canonical_account(dom.MARKETPLACE_SHOPEE, conta),
            "observation_mode": dom.OBSERVATION_MODE_SNAPSHOT_CURRENT,
            "snapshot_status": estado,
            "account_watermark_at": relogio.watermark_at if relogio else None,
            "batch_id": relogio.batch_id if relogio else None,
            "is_active": bool(row.get("is_active")),
            "product_type": tipo,
            "product_type_source": fonte,
            "observed_price": _decimal_or_none(row.get("current_price")),
            "observed_price_source": dom.OBSERVED_PRICE_SOURCE[dom.MARKETPLACE_SHOPEE],
            "list_price": _decimal_or_none(row.get("original_price")),
            "promo_context": dom.promo_context_for(dom.MARKETPLACE_SHOPEE),
            "promo_id": row.get("promotion_id"),
            "promo_discount_pct": _decimal_or_none(row.get("discount_pct")),
            "business_scope": dom.business_scope_for(row.get("brand")),
        })
    return registros


def build_tiktok_records(rows, snapshot_day: date, clocks: dict) -> list[dict]:
    """TikTok e' serie diaria idempotente por `snapshot_date`.

    O dia vem da FONTE. Nao ha calendario sintetico e nao ha aproximacao: se o
    dia pedido nao existe, o chamador recebe vazio e a API responde
    `unavailable` — nunca a observacao mais proxima.
    """
    registros = []
    for row in rows:
        relogio = clocks.get(row.get("shop_account") or dom.MARKETPLACE_TIKTOK)
        tipo, fonte = dom.classify_product_type(
            dom.MARKETPLACE_TIKTOK,
            internal_is_kit=row.get("internal_is_kit"),
            internal_has_bom=row.get("internal_has_bom"),
            seller_sku=row.get("seller_sku"),
            title=row.get("listing_title"),
        )
        registros.append({
            "observed_date": snapshot_day,
            "observed_at": row.get("fetched_at"),
            "marketplace": dom.MARKETPLACE_TIKTOK,
            "brand": pm.normalize_brand_key(row.get("brand")),
            "offer_key": dom.build_offer_key(dom.MARKETPLACE_TIKTOK,
                                             sku_id=row["sku_id"]),
            "parent_item_id": str(row.get("product_id") or ""),
            "model_id": None,
            "seller_sku": row.get("seller_sku"),
            # O inventario do TikTok nao tem coluna de EAN — medido no PMA-2A-R.
            # O campo existe no contrato e vem NULO, para que a ausencia seja
            # explicita em vez de invisivel.
            "gtin": None,
            "listing_title": row.get("listing_title"),
            "shop_account": canonical_account(dom.MARKETPLACE_TIKTOK,
                                              row.get("shop_account")),
            "observation_mode": dom.OBSERVATION_MODE_DAILY_SERIES,
            "snapshot_status": dom.SNAPSHOT_CURRENT,
            "account_watermark_at": relogio.watermark_at if relogio else None,
            "batch_id": relogio.batch_id if relogio else None,
            "is_active": bool(row.get("is_active")),
            "product_type": tipo,
            "product_type_source": fonte,
            "observed_price": _decimal_or_none(row.get("sale_price")),
            "observed_price_source": dom.OBSERVED_PRICE_SOURCE[dom.MARKETPLACE_TIKTOK],
            # Sem preco de tabela na fonte. NUNCA cair para o preco praticado:
            # isso faria toda oferta parecer "sem desconto".
            "list_price": None,
            "promo_context": dom.promo_context_for(dom.MARKETPLACE_TIKTOK),
            "promo_id": None,
            "promo_discount_pct": None,
            "business_scope": dom.business_scope_for(row.get("brand")),
        })
    return registros


def assert_no_pii(records) -> None:
    """Nenhuma chave do registro pode sugerir dado pessoal."""
    for registro in records[:1]:
        for chave in registro:
            baixo = chave.lower()
            for token in FORBIDDEN_FIELD_TOKENS:
                if token in baixo:
                    raise ChannelSyncError(
                        "campo com aparencia de dado pessoal no contrato"
                    )


#: Chave FISICA da observacao. `brand` NAO entra.  (Gate PMA-2C2, fase 2)
#:
#: Medido na fonte: `item_id` da Shopee e' unico sozinho (321/321), o par
#: `(item_id, model_id)` tambem (371/371), e o `sku_id` do TikTok idem
#: (1203/1203) — nenhum deles carrega duas marcas, nem ao longo dos 36 dias da
#: serie. Conta e marca sao 1:1 estrito na Shopee: zero contas com mais de uma
#: marca, zero marcas em mais de uma conta.
#:
#: Por isso `brand` e' ATRIBUTO, nao identidade. A diferenca e' pratica: com a
#: marca na chave, corrigir a marca de uma oferta criaria uma linha nova e
#: deixaria a antiga orfa na mesma fotografia. Como atributo, a correcao
#: atualiza a linha existente.
OFFER_IDENTITY = ("observed_date", "marketplace", "offer_key")


def assert_offer_keys_unique(records) -> None:
    """A PK nao pode colidir. Pai e modelo nunca produzem a mesma chave.

    A tupla verificada e' exatamente `OFFER_IDENTITY`, a mesma da PK fisica:
    incluir `brand` aqui deixaria passar uma colisao que o banco recusaria.
    """
    vistas = set()
    for r in records:
        chave = tuple(r[c] for c in OFFER_IDENTITY)
        if chave in vistas:
            raise ChannelSyncError("colisao de chave de oferta na transformacao")
        vistas.add(chave)


def summarize(records, index, internal_index=None) -> tuple[dict, dict]:
    """Aplica a politica P2 e devolve `(contagens, motivos)` reconciliados."""
    contagens = {k: 0 for k in (
        "observed_offers", "active_offers", "inactive_offers", "eligible_offers",
        "excluded_by_product_type", "comparable_offers", "below_reference",
        "at_or_above_reference", "distinct_b2b_products",
    )}
    contagens.update({t: 0 for t in dom.PRODUCT_TYPES})
    motivos = {r: 0 for r in dom.NON_COMPARABLE_REASONS}
    referencias = set()
    for r in records:
        if r["business_scope"] != dom.BUSINESS_SCOPE_IN:
            continue  # out_of_business_scope nunca entra em KPI principal
        contagens["observed_offers"] += 1
        if not r["is_active"]:
            contagens["inactive_offers"] += 1
            continue
        contagens["active_offers"] += 1
        contagens[r["product_type"]] += 1
        if dom.is_excluded_from_comparison(r["product_type"]):
            contagens["excluded_by_product_type"] += 1
            continue
        contagens["eligible_offers"] += 1
        resultado = pm.resolve_match(
            {"brand": r["brand"], "gtin": r["gtin"], "seller_sku": r["seller_sku"]},
            index, internal_index=internal_index,
        )
        if resultado.ambiguous:
            motivos[dom.REASON_AMBIGUOUS] += 1
            continue
        if resultado.reference is None:
            motivos[dom.REASON_REFERENCE_MISSING] += 1
            continue
        status, razao = dom.compare_to_reference(
            r["observed_price"], resultado.reference.get("suggested_retail_amount"))
        if status is None:
            motivos[razao] += 1
            continue
        contagens["comparable_offers"] += 1
        contagens[status] += 1
        referencias.add(id(resultado.reference))
    contagens["distinct_b2b_products"] = len(referencias)
    dom.assert_partition(
        observed=contagens["observed_offers"],
        active=contagens["active_offers"],
        inactive=contagens["inactive_offers"],
        product_type_counts=contagens,
        eligible=contagens["eligible_offers"],
        excluded_by_product_type=contagens["excluded_by_product_type"],
        comparable=contagens["comparable_offers"],
        reason_counts=motivos,
    )
    if (contagens["below_reference"] + contagens["at_or_above_reference"]
            != contagens["comparable_offers"]):
        raise ChannelSyncError("below + at_or_above != comparable")
    return contagens, motivos


# ---------------------------------------------------------------------------
# ADAPTADORES DE LEITURA REAL  (Gate PMA-2C1A-R, fases 5 e 6)
# ---------------------------------------------------------------------------
# Colunas EXPLICITAS em toda consulta. `SELECT *` traria colunas que nao
# passaram por revisao — e, na Shopee, traria `inflated_current_price` e
# `inflated_original_price`, que o contrato proibe. A lista tambem e' a defesa
# contra PII: nenhuma coluna de comprador, pedido ou endereco e' nomeada.
#
# Nenhum adaptador toca `*_orders`, `*_order_items`, `*_payments` nem qualquer
# relacao de comprador ou criador.

SQL_SHOPEE_ACCOUNT_CLOCKS = """
WITH observacoes AS (
    SELECT shop_account, ingested_at
      FROM silver.stg_shopee_products
     WHERE NOT has_model
    UNION ALL
    SELECT shop_account, ingested_at
      FROM silver.stg_shopee_product_models
)
SELECT shop_account,
       max(ingested_at) AS watermark_at,
       count(*)         AS rows_seen
  FROM observacoes
 GROUP BY shop_account
"""

#: Pais SEM variacao. `has_model = false` separa oferta de container: o pai COM
#: variacao nao aparece aqui e nunca vira registro.
SQL_SHOPEE_SIMPLE_PARENTS = """
SELECT p.shop_account,
       p.brand,
       p.item_id,
       p.item_sku        AS seller_sku,
       p.gtin_code       AS gtin,
       p.item_name       AS listing_title,
       p.item_status,
       p.is_kit,
       p.current_price,
       p.original_price,
       p.currency,
       p.has_promotion,
       p.promotion_id,
       p.discount_pct,
       p.ingested_at
  FROM silver.stg_shopee_products p
 WHERE NOT p.has_model
"""

#: Modelos. O preco vem do MODELO (`m.current_price`) — provado na fase 2:
#: 371/371 preenchidos, todos positivos, `current <= original` em 371/371.
#: O pai COM variacao tem preco NULO em 338/338, entao herdar dele seria herdar
#: NULL. `is_kit`, `item_status` e a promocao vem do pai porque o modelo nao os
#: possui; `current_price` e `original_price` NUNCA vem do pai.
SQL_SHOPEE_MODELS = """
SELECT p.shop_account,
       m.brand,
       m.item_id,
       m.model_id,
       m.model_sku        AS seller_sku,
       p.item_name        AS parent_title,
       m.model_name,
       p.item_status,
       m.is_active        AS model_is_active,
       p.is_kit,
       m.current_price,
       m.original_price,
       m.currency,
       p.has_promotion,
       p.promotion_id,
       p.discount_pct,
       m.ingested_at
  FROM silver.stg_shopee_product_models m
  JOIN silver.stg_shopee_products p
    ON p.item_id = m.item_id AND p.brand = m.brand
 WHERE p.has_model
"""

#: A data vem da FONTE. Sem D-1 fabricado e sem aproximacao para o dia mais
#: proximo: data inexistente devolve vazio e a API responde `unavailable`.
SQL_TIKTOK_SNAPSHOT_DATES = """
SELECT DISTINCT snapshot_date
  FROM silver.stg_tiktok_inventory
 ORDER BY snapshot_date DESC
"""

SQL_TIKTOK_OFFERS = """
SELECT i.brand,
       i.snapshot_date,
       i.sku_id,
       i.product_id,
       i.seller_sku,
       i.product_title   AS listing_title,
       i.product_status,
       i.is_active,
       i.sale_price,
       i.currency,
       i.fetched_at
  FROM silver.stg_tiktok_inventory i
 WHERE i.snapshot_date = %(snapshot_date)s
"""

#: Cadastro interno, para `internal_is_kit` / `internal_has_bom`. SO' o sync o
#: le — a API nunca consulta o Data Mart.
SQL_INTERNAL_PRODUCT_MAP = """
SELECT lower(trim(marca))  AS brand,
       upper(trim(codigo)) AS codigo,
       produto_sk,
       ambiguo
  FROM gold.map_produto_codigo_gobeauty
 WHERE codigo IS NOT NULL
"""

SQL_INTERNAL_PRODUCT_DIM = """
SELECT produto_sk, is_kit, ean FROM gold.dim_produto_gobeauty
"""

SQL_INTERNAL_BOM_KEYS = """
SELECT DISTINCT kit_sk FROM gold.bridge_kit_componente_gobeauty
"""


def _rows(conn, sql, params=None):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or {})
        return [dict(r) for r in cur.fetchall()]


def load_internal_catalog(conn) -> dict:
    """Autoridade interna de kit. Chave ambigua e' DESCARTADA, nunca desempatada."""
    mapa, ambiguas = {}, set()
    for linha in _rows(conn, SQL_INTERNAL_PRODUCT_MAP):
        chave = (linha["brand"], linha["codigo"])
        if linha["ambiguo"]:
            ambiguas.add(chave)
            continue
        mapa[chave] = linha["produto_sk"]
    dim = {r["produto_sk"]: r for r in _rows(conn, SQL_INTERNAL_PRODUCT_DIM)}
    bom = {r["kit_sk"] for r in _rows(conn, SQL_INTERNAL_BOM_KEYS)}
    return {"map": mapa, "ambiguous": ambiguas, "dim": dim, "bom": bom}


def _internal_signals(catalogo, brand, seller_sku):
    """`(internal_is_kit, internal_has_bom)`; `(None, None)` se indisponivel.

    `None` NAO e' `False`: significa que o cadastro nao respondeu, e e' isso que
    separa `no_kit_signal` de `product_type_unknown`.
    """
    if not catalogo:
        return None, None
    marca = pm.normalize_brand_key(brand)
    codigo = pm.normalize_sku_key(seller_sku)
    if marca is None or codigo is None:
        return None, None
    chave = (marca, codigo)
    if chave in catalogo["ambiguous"]:
        return None, None
    produto_sk = catalogo["map"].get(chave)
    if produto_sk is None:
        return None, None
    return (bool(catalogo["dim"].get(produto_sk, {}).get("is_kit")),
            produto_sk in catalogo["bom"])


def load_shopee_account_clocks(conn) -> dict:
    """Um relogio por CONTA. Jamais um `MAX(ingested_at)` global.

    Medido: as quatro contas terminam a carga numa janela de 16,5s. Comparar
    cada linha com o maximo GLOBAL classifica como atrasada toda linha das
    contas que terminaram antes da ultima — 404 contra 125 reais, 3x a mais.

    O watermark cobre as DUAS tabelas da conta (pais sem variacao e modelos),
    porque elas nao terminam juntas: medido, a de modelos fecha ~25s depois da
    de pais em toda conta (apice 09:06:45 -> 09:07:10). Usar so' o maximo dos
    pais deixaria o watermark cedo demais e marcaria como `current` modelos que
    na verdade nao foram revistos.
    """
    relogios = {}
    for linha in _rows(conn, SQL_SHOPEE_ACCOUNT_CLOCKS):
        conta = linha["shop_account"]
        relogios[conta] = AccountClock(
            marketplace=dom.MARKETPLACE_SHOPEE,
            account=conta,
            watermark_at=linha["watermark_at"],
            rows_seen=linha["rows_seen"],
            batch_id=None,
            complete=True,
        )
    return relogios


def fetch_shopee_offers(conn, catalogo=None) -> list:
    """Le a fonte auditada no formato que `build_shopee_records` espera."""
    linhas = []
    for p in _rows(conn, SQL_SHOPEE_SIMPLE_PARENTS):
        interno_kit, interno_bom = _internal_signals(
            catalogo, p["brand"], p["seller_sku"])
        linhas.append({
            "is_model": False, "has_model": False,
            "shop_account": p["shop_account"], "brand": p["brand"],
            "item_id": p["item_id"], "model_id": None,
            "seller_sku": p["seller_sku"], "gtin": p["gtin"],
            "listing_title": p["listing_title"],
            "is_kit": bool(p["is_kit"]),
            "internal_is_kit": interno_kit, "internal_has_bom": interno_bom,
            "is_active": str(p["item_status"] or "").strip().lower() == "normal",
            "current_price": p["current_price"],
            "original_price": p["original_price"],
            "currency": p["currency"],
            "promotion_id": p["promotion_id"],
            "discount_pct": p["discount_pct"],
            "ingested_at": p["ingested_at"],
        })
    for m in _rows(conn, SQL_SHOPEE_MODELS):
        interno_kit, interno_bom = _internal_signals(
            catalogo, m["brand"], m["seller_sku"])
        linhas.append({
            "is_model": True, "has_model": True,
            "shop_account": m["shop_account"], "brand": m["brand"],
            "item_id": m["item_id"], "model_id": m["model_id"],
            "seller_sku": m["seller_sku"],
            # Modelo nao tem EAN — medido: 0 de 371. Vem nulo em vez de herdar
            # o do pai, que tambem nao existe para item com variacao.
            "gtin": None,
            "listing_title": " ".join(
                x for x in (m["parent_title"], m["model_name"]) if x),
            "is_kit": bool(m["is_kit"]),
            "internal_is_kit": interno_kit, "internal_has_bom": interno_bom,
            "is_active": (str(m["item_status"] or "").strip().lower() == "normal"
                          and bool(m["model_is_active"])),
            # Preco do PROPRIO modelo — ver SQL_SHOPEE_MODELS.
            "current_price": m["current_price"],
            "original_price": m["original_price"],
            "currency": m["currency"],
            "promotion_id": m["promotion_id"],
            "discount_pct": m["discount_pct"],
            "ingested_at": m["ingested_at"],
        })
    return linhas


def latest_tiktok_snapshot(conn):
    datas = _rows(conn, SQL_TIKTOK_SNAPSHOT_DATES)
    return datas[0]["snapshot_date"] if datas else None


def tiktok_snapshot_exists(conn, dia) -> bool:
    """Data pedida existe? NUNCA se aproxima para a mais proxima."""
    return any(linha["snapshot_date"] == dia
               for linha in _rows(conn, SQL_TIKTOK_SNAPSHOT_DATES))


def fetch_tiktok_offers(conn, snapshot_day, catalogo=None) -> list:
    linhas = []
    for o in _rows(conn, SQL_TIKTOK_OFFERS, {"snapshot_date": snapshot_day}):
        interno_kit, interno_bom = _internal_signals(
            catalogo, o["brand"], o["seller_sku"])
        linhas.append({
            "brand": o["brand"], "sku_id": o["sku_id"],
            "product_id": o["product_id"], "seller_sku": o["seller_sku"],
            "listing_title": o["listing_title"],
            "internal_is_kit": interno_kit, "internal_has_bom": interno_bom,
            "is_active": bool(o["is_active"]),
            "sale_price": o["sale_price"], "currency": o["currency"],
            "fetched_at": o["fetched_at"],
            "shop_account": dom.MARKETPLACE_TIKTOK,
        })
    return linhas


# ---------------------------------------------------------------------------
# GARANTIAS DE PUBLICACAO  (Gate PMA-2C2, fase 4)
# ---------------------------------------------------------------------------
# A decisao de publicar e' uma FUNCAO PURA. Ela nao abre conexao, nao escreve e
# nao depende de relogio: recebe o estado observado e devolve permitir/recusar
# com um motivo nomeado. Assim cada garantia vira um teste comportamental em vez
# de um comentario de boas intencoes.
#
# O executor que um dia usar esta decisao continua barrado por
# `assert_apply_authorized`, que exige a revisao 017 carimbada E a relacao
# existente. Esta funcao decide SE se deve publicar; aquela decide SE E' POSSIVEL.

PUBLISH_ALLOW = "allow"
PUBLISH_REFUSE = "refuse"

REFUSE_FLAG_OFF = "channel_flag_disabled"
REFUSE_EMPTY_HEALTHY = "healthy_channel_returned_zero_offers"
REFUSE_SOURCE_UNAVAILABLE = "source_unavailable"
REFUSE_OLDER_THAN_PUBLISHED = "older_snapshot_than_published"
REFUSE_NO_ACCOUNT_RAN = "no_account_executed"

PUBLISH_REFUSE_REASONS = (
    REFUSE_FLAG_OFF,
    REFUSE_EMPTY_HEALTHY,
    REFUSE_SOURCE_UNAVAILABLE,
    REFUSE_OLDER_THAN_PUBLISHED,
    REFUSE_NO_ACCOUNT_RAN,
)


@dataclass(frozen=True)
class PublishDecision:
    """Resultado da decisao. `reason` e' None somente quando `action` permite."""

    action: str
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return self.action == PUBLISH_ALLOW


def plan_publication(
    *,
    marketplace: str,
    channel_enabled: bool,
    operator_override: bool = False,
    source_available: bool = True,
    accounts_that_ran: int = 0,
    record_count: int = 0,
    snapshot_date=None,
    published_snapshot_date=None,
) -> PublishDecision:
    """Decide se esta fotografia pode ser publicada.

    As recusas existem porque cada uma delas, se ignorada, produziria um dado
    que MENTE em vez de faltar:

    - flag desligada: publicar criaria dado que a tela nao mostra e ninguem
      confere. O `operator_override` existe para a carga-piloto, que e' um
      comando operacional explicito, nunca o caminho automatico.
    - fonte indisponivel: publicar zero linhas apagaria a fotografia anterior e
      a tela diria "nenhuma oferta" quando o certo e' "nao conseguimos olhar".
    - canal saudavel com zero ofertas: e' o caso simetrico e mais traicoeiro —
      a fonte respondeu, mas vazia. Preservar a fotografia antiga a faria passar
      por atual. Recusar mantem a data anterior visivelmente velha.
    - fotografia mais antiga que a publicada: reprocessar um dia velho nao pode
      rebaixar o que ja' esta publicado.
    - nenhuma conta executou: todo `snapshot_status` seria
      `account_did_not_run`, o que e' desconhecimento, nao observacao.
    """
    if marketplace not in CHANNEL_MARKETPLACES:
        raise ChannelSyncError("canal fora da fato multicanal")

    if not channel_enabled and not operator_override:
        return PublishDecision(PUBLISH_REFUSE, REFUSE_FLAG_OFF)
    if not source_available:
        return PublishDecision(PUBLISH_REFUSE, REFUSE_SOURCE_UNAVAILABLE)
    if accounts_that_ran <= 0:
        return PublishDecision(PUBLISH_REFUSE, REFUSE_NO_ACCOUNT_RAN)
    if record_count <= 0:
        return PublishDecision(PUBLISH_REFUSE, REFUSE_EMPTY_HEALTHY)
    if (snapshot_date is not None and published_snapshot_date is not None
            and snapshot_date < published_snapshot_date):
        return PublishDecision(PUBLISH_REFUSE, REFUSE_OLDER_THAN_PUBLISHED)
    return PublishDecision(PUBLISH_ALLOW)


#: Estados do commit. `indeterminate` existe porque uma queda de conexao DEPOIS
#: do COMMIT e' indistinguivel de uma queda ANTES: repetir cegamente poderia
#: duplicar, e marcar como falha poderia rotular de `failed` um dado que ja'
#: esta publicado. O tratamento e' reconciliar por leitura, nunca retry cego.
COMMIT_COMMITTED = "committed"
COMMIT_ROLLED_BACK = "rolled_back"
COMMIT_INDETERMINATE = "indeterminate"
COMMIT_STATES = (COMMIT_COMMITTED, COMMIT_ROLLED_BACK, COMMIT_INDETERMINATE)


def audit_outcome(commit_state: str, rows_found_after: int | None) -> str:
    """Traduz o estado do commit + a releitura em veredito de auditoria.

    A regra que importa: auditoria posterior NUNCA pode marcar como `failed`
    um dado que a releitura encontrou publicado. Foi assim que o backfill da
    Shopee quase reportou perda de dado que existia.
    """
    if commit_state not in COMMIT_STATES:
        raise ChannelSyncError("estado de commit desconhecido")
    if commit_state == COMMIT_COMMITTED:
        return "published"
    if commit_state == COMMIT_ROLLED_BACK:
        return "failed"
    # indeterminado: a releitura decide, e nunca se repete a escrita as cegas
    if rows_found_after is None:
        return "needs_manual_reconciliation"
    return "published" if rows_found_after > 0 else "failed"


# ---------------------------------------------------------------------------
# EXCLUSAO MUTUA  (Gate PMA-2C2-R, fase 2)
# ---------------------------------------------------------------------------
#: Chave EXCLUSIVA desta frente. Conferida contra todas as chaves versionadas
#: do repositorio antes de ser escolhida:
#:
#:      906120006  911120011  912120012  912130013  913120001
#:      913120013 (pma/reference_import)   913120041 (avoe/snapshot_import)
#:      914120014  916140016  564738291056  987654321123
#:      -2966686022110071898 (backfill shopee, derivada de sha256)
#:
#: 917120017 nao colide com nenhuma. O sufixo 017 amarra a chave a revisao que
#: cria a tabela, para que a proxima frente enxergue o pareamento.
CHANNEL_OFFER_ADVISORY_LOCK_KEY = 917_120_017

#: Chaves de TODAS as demais frentes, versionadas aqui para que o teste de
#: colisao seja executavel e nao uma promessa de comentario.
OTHER_TRACK_ADVISORY_LOCK_KEYS = (
    906_120_006, 911_120_011, 912_120_012, 912_130_013, 913_120_001,
    913_120_013, 913_120_041, 914_120_014, 916_140_016,
    564738291056, 987654321123, -2966686022110071898,
)


class ChannelSyncLockUnavailable(ChannelSyncError):
    """Outra execucao ja' detem o lock. Encerra imediatamente, sem esperar."""


def try_acquire_publication_lock(conn) -> bool:
    """Lock de SESSAO, fail-fast. `pg_try_advisory_lock`, nunca a variante que espera.

    Duas escolhas que importam:

    - `pg_try_advisory_lock` e nao `pg_advisory_lock`: a versao bloqueante
      enfileiraria a segunda execucao, e duas cargas em fila publicariam
      fotografias em sequencia — a segunda sobrescrevendo a primeira com dados
      lidos ANTES dela. Falhar na hora e' o comportamento correto.
    - lock de SESSAO e nao `_xact_`: ele precisa sobreviver ao COMMIT dos dados
      para cobrir tambem a auditoria posterior. Um lock transacional cairia no
      commit e deixaria a janela aberta justamente no trecho em que outra
      execucao poderia comecar a ler.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)",
                    (CHANNEL_OFFER_ADVISORY_LOCK_KEY,))
        return bool(cur.fetchone()[0])


def release_publication_lock(conn) -> bool:
    """Libera o lock. Chamado SOMENTE se a aquisicao teve sucesso, e na MESMA
    conexao: um advisory lock de sessao pertence a conexao que o tomou, e
    liberar de outra e' no-op silencioso."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)",
                    (CHANNEL_OFFER_ADVISORY_LOCK_KEY,))
        return bool(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# ESCOPO DA FOTOGRAFIA  (Gate PMA-2C2-R, fase 4)
# ---------------------------------------------------------------------------
#: Canal sem contas proprias usa o proprio nome como escopo canonico. Nao e'
#: sentinela de dado: e' o identificador da unica conta que aquele canal tem.
#: `build_tiktok_records` ja' grava `shop_account = 'tiktok'` por isso.
def canonical_account(marketplace: str, shop_account=None) -> str:
    if shop_account:
        texto = str(shop_account).strip()
        if texto:
            return texto
    if marketplace == dom.MARKETPLACE_TIKTOK:
        return dom.MARKETPLACE_TIKTOK
    raise ChannelSyncError("oferta sem conta: escopo de substituicao indefinido")


@dataclass(frozen=True)
class PublicationScope:
    """Unidade de substituicao: o que um DELETE apaga e um INSERT repoe.

    E' `(marketplace, observed_date, shop_account)` — a conta ENTRA porque as
    quatro contas da Shopee carregam de forma independente. Sem ela, uma conta
    que nao executou teria sua fotografia apagada pela carga de outra.
    """

    marketplace: str
    observed_date: date
    shop_account: str

    @staticmethod
    def of(record: dict) -> "PublicationScope":
        return PublicationScope(
            marketplace=record["marketplace"],
            observed_date=record["observed_date"],
            shop_account=canonical_account(record["marketplace"],
                                           record.get("shop_account")),
        )


def scopes_of(records) -> set:
    return {PublicationScope.of(r) for r in records}


# ---------------------------------------------------------------------------
# ANTIRREGRESSAO DE WATERMARK  (Gate PMA-2C2-R, fase 3)
# ---------------------------------------------------------------------------
REFUSE_WATERMARK_REGRESSION = "watermark_regression"
REFUSE_WATERMARK_UNKNOWN = "incoming_watermark_unknown"
REFUSE_SCOPE_WITHOUT_ACCOUNT = "scope_without_account"


def _aware(instante):
    """Instante timezone-aware. Ingenuo e' tratado como UTC — assumir o fuso da
    maquina faria a comparacao mudar conforme quem roda o sync."""
    if instante is None:
        return None
    if instante.tzinfo is None:
        return instante.replace(tzinfo=timezone.utc)
    return instante


def check_watermark_progress(incoming: dict, published: dict):
    """Compara o relogio DA FOTOGRAFIA, escopo a escopo.

    `incoming` e `published` sao `{PublicationScope: watermark}`.

    O relogio e' `account_watermark_at` — o fim da carga da conta — e NUNCA o
    `observed_at` individual da oferta. Uma unica linha pode ter carimbo de 18
    dias atras sem que a fotografia seja velha; usar o carimbo da linha como
    relogio confundiria item nao revisto com execucao antiga.

    Devolve `(escopos_permitidos, PublishDecision_de_recusa_ou_None)`.
    """
    permitidos = set()
    for escopo, relogio_novo in incoming.items():
        relogio_velho = _aware(published.get(escopo))
        relogio_novo = _aware(relogio_novo)
        if relogio_velho is None:
            # Nada publicado neste escopo: qualquer relogio avanca.
            permitidos.add(escopo)
            continue
        if relogio_novo is None:
            # Desconhecido NAO supera conhecido: publicar apagaria uma
            # fotografia datada e a substituiria por outra sem data.
            return permitidos, PublishDecision(PUBLISH_REFUSE,
                                               REFUSE_WATERMARK_UNKNOWN)
        if relogio_novo < relogio_velho:
            return permitidos, PublishDecision(PUBLISH_REFUSE,
                                               REFUSE_WATERMARK_REGRESSION)
        # Igual e' rerun idempotente; maior avanca. Os dois publicam.
        permitidos.add(escopo)
    return permitidos, None


@dataclass(frozen=True)
class PublicationPlan:
    """O que uma unica transacao fara'. Montado ANTES de qualquer mutacao.

    `scopes_to_replace` inclui escopos SAUDAVEIS COM ZERO OFERTAS: para eles o
    DELETE roda e nenhum INSERT o segue, e o resultado e' `rows_loaded = 0` com
    execucao bem-sucedida. E' assim que "a conta existe e hoje nao tem oferta"
    se distingue de "nao conseguimos olhar": a segunda nem aparece aqui.
    """

    marketplace: str
    scopes_to_replace: tuple
    records: tuple
    decision: PublishDecision

    @property
    def rows_loaded(self) -> int:
        return len(self.records)


def build_publication_plan(
    *,
    marketplace: str,
    records,
    healthy_scopes,
    incoming_watermarks: dict,
    published_watermarks: dict,
    channel_enabled: bool,
    operator_override: bool = False,
    source_available: bool = True,
) -> PublicationPlan:
    """Decide e planeja. Nao abre conexao, nao escreve, nao le relogio.

    A ordem das guardas e' deliberada: flag, fonte, escopo, watermark. A
    regressao e' checada ANTES de qualquer DELETE/INSERT ser montado, entao uma
    execucao recusada nao chega perto de tocar linha existente.
    """
    if marketplace not in CHANNEL_MARKETPLACES:
        raise ChannelSyncError("canal fora da fato multicanal")

    vazio = PublicationPlan(marketplace, (), (), PublishDecision(PUBLISH_ALLOW))

    def recusa(motivo):
        return PublicationPlan(marketplace, (), (),
                               PublishDecision(PUBLISH_REFUSE, motivo))

    if not channel_enabled and not operator_override:
        return recusa(REFUSE_FLAG_OFF)
    if not source_available:
        # Fonte indisponivel NAO vira fotografia vazia: nenhum escopo entra em
        # `scopes_to_replace`, entao nada e' apagado e o snapshot anterior fica.
        return recusa(REFUSE_SOURCE_UNAVAILABLE)
    if not healthy_scopes:
        return recusa(REFUSE_NO_ACCOUNT_RAN)

    saudaveis = set(healthy_scopes)
    for r in records:
        if PublicationScope.of(r) not in saudaveis:
            raise ChannelSyncError("oferta fora dos escopos saudaveis declarados")

    permitidos, recusado = check_watermark_progress(
        {e: incoming_watermarks.get(e) for e in saudaveis}, published_watermarks)
    if recusado is not None:
        return PublicationPlan(marketplace, (), (), recusado)

    alvo = tuple(sorted(permitidos, key=lambda e: (e.shop_account,
                                                   str(e.observed_date))))
    manter = tuple(r for r in records if PublicationScope.of(r) in permitidos)
    return PublicationPlan(marketplace, alvo, manter,
                           PublishDecision(PUBLISH_ALLOW))


#: SQL da substituicao. As duas sentencas rodam na MESMA transacao: separar o
#: DELETE do INSERT em commits distintos deixaria uma janela em que a tela
#: mostraria a fotografia vazia.
SQL_DELETE_SCOPE = f"""
DELETE FROM {TARGET_TABLE}
 WHERE marketplace   = %(marketplace)s
   AND observed_date = %(observed_date)s
   AND shop_account  = %(shop_account)s
"""


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="channel_offer_sync",
        description=("Diagnostico das ofertas de Shopee e TikTok. A publicacao "
                     "permanece BLOQUEADA ate a migration do destino existir."),
    )
    parser.add_argument("--marketplace", choices=list(CHANNEL_MARKETPLACES),
                        required=True)
    parser.add_argument("--diagnose", action="store_true", default=True,
                        help="somente leitura (padrao e unico modo disponivel)")
    parser.add_argument("--apply", action="store_true",
                        help="RECUSADO enquanto a migration do destino nao existir")
    parser.add_argument("--observed-date", default=None,
                        help="YYYY-MM-DD; sem aproximacao para o dia mais proximo")
    return parser


def main(argv=None) -> int:
    """Diagnostico aqui; publicacao DELEGADA ao executor.

    Gate PMA-2C3A-R: `--apply` deixou de ser uma recusa estrutural. Ele nao e'
    reimplementado aqui — delega para `channel_offer_publisher`, que detem o
    executor aprovado. Duas implementacoes do mesmo fluxo divergiriam.
    """
    args = build_cli().parse_args(argv)
    if args.apply:
        from pipelines import channel_offer_publisher as publisher

        repassados = ["--marketplace", args.marketplace, "--apply"]
        if getattr(args, "observed_date", None):
            repassados += ["--observed-date", args.observed_date]
        return publisher.main(repassados)
    print(f"diagnose: marketplace={args.marketplace} (somente leitura)")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
