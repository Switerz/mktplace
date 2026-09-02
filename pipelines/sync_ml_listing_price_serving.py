"""Gate PMA-1A — sync do preco ANUNCIADO dos anuncios PROPRIOS: Data Mart -> Neon.

ARQUITETURA PRESERVADA
----------------------
    Data Mart (read-only)  ->  este sync explicito  ->  marts.* no Neon  ->  API

O backend no Render NAO consulta o Data Mart. Este CLI e' a unica travessia, roda
de maquina com acesso ao Data Mart, e o endpoint le exclusivamente `marts.*`.

FONTE COMPROVADA POR LEITURA (2026-09-02)
-----------------------------------------
`silver.stg_ml_item_price_history`  58.706 linhas | 908 itens | 4 marcas
                                    74 dias (2026-06-20 a 2026-09-02)
                                    58.706 pares (item_id, ref_date) distintos
                                    -> o grao e' UNICO por (item_id, ref_date)
                                    `variation_id` = 0 em 100% das linhas
                                    `price` sem nulo | `sale_price` 100% NULO
                                    `original_price` nulo em 39.215 (66,8%)
                                    0 linhas com price < 0 | 0 linhas <> 'BRL'
`silver.stg_ml_items`               908 linhas | 908 item_id distintos
                                    join total nos dois sentidos, 0 divergencia
                                    de `brand`; seller_id/title/catalog_listing/
                                    updated_at sem nulo; permalink em 908/908
                                    attributes: SELLER_SKU em 907, GTIN em 903

`silver.stg_ml_item_variations` esta VAZIA (0 linhas). A ponte documentada de
`seller_sku` nao existe, e por isso a chave sai de `stg_ml_items.attributes`.

NENHUMA LEITURA DE GMV OU UNIDADES
----------------------------------
`price` e' preco anunciado lido diretamente. Nao existe neste modulo divisao de
receita por quantidade, e nao pode passar a existir: preco medio realizado nao e'
preco anunciado, e trocar um pelo outro inventaria uma observacao de vitrine que
nunca foi feita.

PRECO ANUNCIADO, NAO PRECO DE CHECKOUT
--------------------------------------
A fonte nao tem frete, cupom de vitrine, subsidio de plataforma nem preco de
checkout. As colunas correspondentes NAO EXISTEM no destino, para que ausencia
nao possa ser lida como zero. `coverage_status = 'advertised_only'`.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

# Helpers de seguranca REUTILIZADOS do sync de serving, nunca reimplementados:
# duplicar as regexes de sanitizacao garantiria que as duas copias divergissem, e
# a que ficasse atrasada vazaria topologia no log.
from pipelines.sync_tiktok_serving import (
    CONNECT_TIMEOUT_SECONDS,
    TZ_OPERACIONAL,
    _get_datamart_url,
    _get_neon_url,
    hoje_operacional,
    last_closed_date,
    sanitize_error_message,
    sanitize_run_id,
    validate_identifier,
    validate_qualified,
)

# ---------------------------------------------------------------------------
# Especificacao — LITERAL, fixa, versionada
# ---------------------------------------------------------------------------

SOURCE_PRICE_RELATION = "silver.stg_ml_item_price_history"
SOURCE_ITEMS_RELATION = "silver.stg_ml_items"
TARGET_TABLE = "marts.fact_marketplace_listing_price_daily"
STAGING_TABLE = "sync_ml_listing_price_staging"

MARKETPLACE = "ml"

#: Primeiro dia com dado na fonte (medido). Janela anterior a isso e' recusada.
SOURCE_MIN_DATE = date(2026, 6, 20)

#: Marcas com catalogo ML proprio (medidas na fonte). Minimizacao de dado: a
#: fonte pode ganhar outras marcas, e copiar excedente sem consumidor autorizado
#: ampliaria a superficie do Neon sem servir a nenhuma tela.
ALLOWED_BRANDS = ("barbours", "kokeshi", "lescent", "rituaria")

#: Dominio de status observado na fonte.
ALLOWED_LISTING_STATUS = ("active", "paused", "under_review", "inactive")

CURRENCY = "BRL"

KEY_COLUMNS = ("ref_date", "marketplace", "seller_id", "item_id")

BUSINESS_COLUMNS = (
    "ref_date", "marketplace", "brand", "seller_id", "item_id",
    "seller_sku", "gtin", "listing_title", "permalink",
    "advertised_price", "original_price", "currency", "listing_status",
    "catalog_listing",
    # PMA-1A-R, F2 — dois tempos DISTINTOS, nomeados pelo que cada um e'.
    # `price_captured_at` vem de `stg_ml_item_price_history.extracted_at`: e' a
    # captura do PRECO. `listing_metadata_updated_at` vem de
    # `stg_ml_items.updated_at`: e' a alteracao do CADASTRO do anuncio. A versao
    # anterior tinha uma unica coluna `source_updated_at` alimentada pelo
    # segundo e exposta como se fosse o primeiro — semanticamente falso.
    "price_captured_at", "listing_metadata_updated_at",
)
AUDIT_COLUMNS = ("source_run_id",)

#: Advisory lock proprio. Chave distinta das do S2 (907/908), da UE2-C (912) e do
#: importador de referencia (913): esta rotina escreve uma tabela sua e nao deve
#: bloquear nem ser bloqueada pelas outras.
ADVISORY_LOCK_KEY = 914_120_014

#: Janela incremental default, em dias FECHADOS terminando em D-1. 30 dias
#: cobrem a serie inteira que a fonte tem hoje (74 dias) com folga operacional e
#: sao suficientes para absorver reafirmacao de dia fechado. A sobreposicao entre
#: execucoes e' DELIBERADA: cada execucao reescreve a janela por inteiro, e a
#: convergencia vem da idempotencia, nao de detectar o que mudou.
DEFAULT_LOOKBACK_DAYS = 30
MIN_LOOKBACK_DAYS = 2

SOURCE_STATEMENT_TIMEOUT = "300s"
TARGET_STATEMENT_TIMEOUT = "300s"
LOCK_TIMEOUT = "30s"
INSERT_PAGE_SIZE = 1000

#: Teto de linhas por janela. 30 dias x ~855 itens = ~25.650. O teto recusa em
#: vez de truncar: truncar publicaria uma janela parcial com cara de completa.
MAX_ROWS_PER_WINDOW = 200_000


class SyncError(RuntimeError):
    """Falha do sync. Mensagem sempre sanitizada antes de chegar ao operador."""


@dataclass(frozen=True)
class SourceSnapshot:
    rows: list[dict]
    date_from: date
    date_to: date
    aggregates: dict


# ---------------------------------------------------------------------------
# Janela — regra unica de D-1, em America/Sao_Paulo
# ---------------------------------------------------------------------------

def validate_window(date_from: date, date_to: date,
                    today: date | None = None) -> tuple[date, date]:
    """Janela fechada e sa. Recusa invertida, futura e o DIA CORRENTE.

    O teto e' D-1 sempre. O dia corrente e' recusado mesmo que a fonte ja tenha
    linhas dele: o `stg_ml_item_price_history` e' extraido as ~06:03 e uma
    republicacao mais tarde no mesmo dia mudaria o valor de um dia que a tela
    ja teria mostrado como fechado.
    """
    today = today or hoje_operacional()
    fechado = last_closed_date(today)
    if not isinstance(date_from, date) or not isinstance(date_to, date):
        raise SyncError("date_from e date_to precisam ser datas.")
    if date_from > date_to:
        raise SyncError(f"janela invertida: {date_from} > {date_to}.")
    if date_to >= today:
        raise SyncError(
            f"dia corrente ou futuro recusado: date_to={date_to}, hoje={today}. "
            f"A janela vai no maximo ate o ultimo dia fechado ({fechado})."
        )
    if date_to > fechado:
        raise SyncError(f"date_to ({date_to}) passa do ultimo dia fechado ({fechado}).")
    if date_from < SOURCE_MIN_DATE:
        raise SyncError(
            f"date_from ({date_from}) e' anterior ao primeiro dado da fonte "
            f"({SOURCE_MIN_DATE})."
        )
    return date_from, date_to


def incremental_window(today: date | None = None,
                       lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[date, date]:
    if lookback_days < MIN_LOOKBACK_DAYS:
        raise SyncError(
            f"lookback_days precisa ser >= {MIN_LOOKBACK_DAYS}: recebido {lookback_days}."
        )
    today = today or hoje_operacional()
    fechado = last_closed_date(today)
    if fechado < SOURCE_MIN_DATE:
        raise SyncError(
            f"ainda nao existe dia fechado a partir de {SOURCE_MIN_DATE}: o ultimo "
            f"dia fechado seria {fechado}."
        )
    inicio = max(SOURCE_MIN_DATE, fechado - timedelta(days=lookback_days - 1))
    return validate_window(inicio, fechado, today)


# ---------------------------------------------------------------------------
# Conexoes — nada conecta no import deste modulo
# ---------------------------------------------------------------------------

def _datamart_snapshot(url: str):
    """Leitura do Data Mart com snapshot consistente e READ-ONLY de verdade.

    `readonly=True` garante que Silver/Gold nao possam ser escritas nem por
    acidente; `REPEATABLE READ` torna a fotografia consistente entre a consulta
    de detalhe e a de agregado, que e' o que permite reconciliar as duas.
    """
    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
    return conn


def _neon_writable(url: str):
    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    conn.autocommit = False
    return conn


def _neon_readonly(url: str):
    """Somente diagnostico (sem `--apply`). Nao toma lock e nao escreve."""
    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    conn.set_session(readonly=True)
    return conn


def default_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return sanitize_run_id(f"sync_ml_listing_price:{stamp}")


# ---------------------------------------------------------------------------
# Fonte
# ---------------------------------------------------------------------------

def assert_snapshot_session(cur) -> dict:
    """PROVA que a sessao da fonte concede snapshot e e' read-only.

    Isolamento e' propriedade da transacao: um pooler ou um
    `default_transaction_isolation` do servidor pode rebaixa-lo silenciosamente.
    Aqui isso falha alto em vez de degradar a fotografia sem aviso.
    """
    cur.execute(
        "SELECT current_setting('transaction_isolation') AS isolation, "
        "current_setting('transaction_read_only') AS read_only"
    )
    linha = cur.fetchone()
    isolamento = str(linha["isolation"]).lower()
    somente_leitura = str(linha["read_only"]).lower()
    if isolamento != "repeatable read":
        raise SyncError(
            f"fotografia inconsistente: a fonte esta em isolamento {isolamento!r} "
            f"e o contrato exige 'repeatable read'."
        )
    if somente_leitura != "on":
        raise SyncError(
            "transacao da fonte NAO esta read-only: a leitura do Data Mart nunca "
            "pode ter permissao de escrita."
        )
    return {"isolation": isolamento, "read_only": somente_leitura}


def build_source_query() -> str:
    """SQL de detalhe. Identificadores validados; valores sempre por parametro.

    O `LEFT JOIN LATERAL` extrai SELLER_SKU e GTIN de `attributes` uma unica vez
    por item. `stg_ml_item_variations` NAO e' usada: esta vazia (0 linhas).

    `h.status` (do historico diario) e' a fonte de `listing_status`, nao
    `i.status`: o status precisa ser o de `ref_date`, nao o de hoje.

    Os dois timestamps sao lidos de tabelas DIFERENTES de proposito (F2):
    `h.extracted_at` e' a captura do PRECO e alimenta `price_captured_at`;
    `i.updated_at` e' a alteracao do CADASTRO e alimenta
    `listing_metadata_updated_at`. Nunca o segundo no lugar do primeiro.
    """
    price = validate_qualified(SOURCE_PRICE_RELATION)
    items = validate_qualified(SOURCE_ITEMS_RELATION)
    return f"""
        SELECT h.ref_date                        AS ref_date,
               %(marketplace)s::varchar          AS marketplace,
               h.brand                           AS brand,
               i.seller_id                       AS seller_id,
               h.item_id                         AS item_id,
               a.seller_sku                      AS seller_sku,
               a.gtin                            AS gtin,
               i.title                           AS listing_title,
               i.permalink                       AS permalink,
               h.price                           AS advertised_price,
               h.original_price                  AS original_price,
               h.currency_id                     AS currency,
               h.status                          AS listing_status,
               i.catalog_listing                 AS catalog_listing,
               h.extracted_at                    AS price_captured_at,
               i.updated_at                      AS listing_metadata_updated_at
          FROM {price} h
          JOIN {items} i
            ON i.item_id = h.item_id
           AND i.brand   = h.brand
          LEFT JOIN LATERAL (
               SELECT max(CASE WHEN at->>'id' = 'SELLER_SKU'
                               THEN NULLIF(btrim(at->>'value_name'), '') END) AS seller_sku,
                      max(CASE WHEN at->>'id' = 'GTIN'
                               THEN NULLIF(regexp_replace(
                                        coalesce(at->>'value_name', ''), '\\D', '', 'g'
                                    ), '') END)                               AS gtin
                 FROM jsonb_array_elements(i.attributes) AS at
          ) a ON TRUE
         WHERE h.ref_date BETWEEN %(date_from)s AND %(date_to)s
           AND h.brand = ANY(%(brands)s)
         ORDER BY h.ref_date, h.item_id
    """


def build_source_aggregate_query() -> str:
    """Agregado INDEPENDENTE, na mesma fotografia. Reconciliado contra o detalhe.

    `sum(price)` aqui e' CHECKSUM, nao metrica de negocio: somar preco anunciado
    nao significa nada comercialmente, e este numero existe unicamente para
    provar que o detalhe transportado e' o mesmo conjunto que a fonte tinha.
    """
    price = validate_qualified(SOURCE_PRICE_RELATION)
    items = validate_qualified(SOURCE_ITEMS_RELATION)
    return f"""
        SELECT count(*)                             AS row_count,
               count(DISTINCT h.item_id)            AS item_count,
               count(DISTINCT h.brand)              AS brand_count,
               min(h.ref_date)                      AS min_ref_date,
               max(h.ref_date)                      AS max_ref_date,
               coalesce(sum(h.price), 0)            AS checksum_advertised_price,
               count(h.original_price)              AS original_price_not_null
          FROM {price} h
          JOIN {items} i
            ON i.item_id = h.item_id
           AND i.brand   = h.brand
         WHERE h.ref_date BETWEEN %(date_from)s AND %(date_to)s
           AND h.brand = ANY(%(brands)s)
    """


def build_unknown_brand_query() -> str:
    """Marcas na janela da fonte que NAO estao na allowlist.

    Sem allowlist no WHERE, de proposito: e' exatamente o oposto da consulta de
    detalhe. A de detalhe FILTRA por marca conhecida; esta procura o que ficaria
    de fora.
    """
    price = validate_qualified(SOURCE_PRICE_RELATION)
    return f"""
        SELECT h.brand                AS brand,
               count(*)               AS linhas
          FROM {price} h
         WHERE h.ref_date BETWEEN %(date_from)s AND %(date_to)s
           AND NOT (h.brand = ANY(%(brands)s))
         GROUP BY h.brand
         ORDER BY h.brand
    """


def assert_no_unknown_brands(cur, date_from: date, date_to: date) -> None:
    """FAIL-CLOSED: marca nova na fonte ABORTA o sync.  (PMA-1A-R, F10)

    Roda dentro da fotografia da FONTE, portanto ANTES de a transacao gravavel
    do destino ser aberta e muito antes do DELETE da janela. Consequencia
    concreta: uma marca nova nunca derruba dado ja publicado — a execucao morre
    na leitura e o destino fica intacto.

    A consulta de detalhe filtra `h.brand = ANY(allowlist)`, o que por si so
    EXCLUIRIA a marca nova em silencio. Silencio e' o defeito: a Torre passaria
    a publicar um recorte incompleto sem ninguem notar. Aqui a marca nova e' um
    erro alto e nomeado.

    Incluir uma marca exige mudanca DELIBERADA em tres lugares independentes:
    `ALLOWED_BRANDS` aqui, `MONITORED_BRANDS` no contrato, e o `CHECK` da tabela
    (por migration). Cada um falha alto sozinho.
    """
    cur.execute(build_unknown_brand_query(), _params(date_from, date_to))
    desconhecidas = cur.fetchall()
    if desconhecidas:
        nomes = ", ".join(sorted(str(r["brand"]) for r in desconhecidas))
        raise SyncError(
            f"marca(s) fora da allowlist presentes na fonte na janela "
            f"{date_from}..{date_to}: {nomes}. O sync ABORTA sem escrever nada — "
            f"nenhum DELETE foi executado e o destino esta intacto. Incluir marca "
            f"nova exige decisao explicita em ALLOWED_BRANDS, no contrato e no "
            f"CHECK da tabela (por migration)."
        )


def _params(date_from: date, date_to: date) -> dict:
    return {
        "date_from": date_from,
        "date_to": date_to,
        "brands": list(ALLOWED_BRANDS),
        "marketplace": MARKETPLACE,
    }


def aggregates_from_rows(rows: list[dict]) -> dict:
    """Agregado recomputado a partir do detalhe EM MEMORIA."""
    if not rows:
        return {
            "row_count": 0, "item_count": 0, "brand_count": 0,
            "min_ref_date": None, "max_ref_date": None,
            "checksum_advertised_price": Decimal("0"), "original_price_not_null": 0,
        }
    datas = [r["ref_date"] for r in rows]
    return {
        "row_count": len(rows),
        "item_count": len({r["item_id"] for r in rows}),
        "brand_count": len({r["brand"] for r in rows}),
        "min_ref_date": min(datas),
        "max_ref_date": max(datas),
        "checksum_advertised_price": sum(
            (Decimal(str(r["advertised_price"])) for r in rows), Decimal("0")
        ),
        "original_price_not_null": sum(1 for r in rows if r["original_price"] is not None),
    }


def compare_aggregates(fonte: dict, recomputado: dict) -> list[str]:
    problemas: list[str] = []
    for campo in ("row_count", "item_count", "brand_count", "min_ref_date",
                  "max_ref_date", "original_price_not_null"):
        if fonte[campo] != recomputado[campo]:
            problemas.append(
                f"{campo}: fonte={fonte[campo]} recomputado={recomputado[campo]}"
            )
    a = Decimal(str(fonte["checksum_advertised_price"]))
    b = Decimal(str(recomputado["checksum_advertised_price"]))
    if a != b:
        problemas.append(f"checksum_advertised_price: fonte={a} recomputado={b}")
    return problemas


def validate_rows(rows: list[dict], date_from: date, date_to: date) -> dict:
    """Fronteira de entrada: contrato da fonte antes de qualquer escrita."""
    if len(rows) > MAX_ROWS_PER_WINDOW:
        raise SyncError(
            f"{len(rows)} linhas excedem o teto de {MAX_ROWS_PER_WINDOW} por janela: "
            f"recusado em vez de truncado."
        )
    chaves: set[tuple] = set()
    for r in rows:
        chave = tuple(r[c] for c in KEY_COLUMNS)
        if chave in chaves:
            raise SyncError(
                "chave duplicada no detalhe da fonte: o grao "
                "(ref_date, marketplace, seller_id, item_id) precisa ser unico e a "
                "publicacao violaria a PK do destino."
            )
        chaves.add(chave)

        if not (date_from <= r["ref_date"] <= date_to):
            raise SyncError("linha fora da janela solicitada no detalhe da fonte.")
        if r["brand"] not in ALLOWED_BRANDS:
            raise SyncError(f"marca fora da allowlist no detalhe: {r['brand']!r}.")
        if r["marketplace"] != MARKETPLACE:
            raise SyncError(f"marketplace inesperado: {r['marketplace']!r}.")
        if r["listing_status"] not in ALLOWED_LISTING_STATUS:
            raise SyncError(f"listing_status fora do dominio: {r['listing_status']!r}.")
        if r["currency"] != CURRENCY:
            raise SyncError(f"moeda inesperada: {r['currency']!r}.")
        if r["seller_id"] is None:
            raise SyncError("seller_id nulo: a chave do destino exige seller_id.")
        if r["advertised_price"] is None:
            raise SyncError("advertised_price nulo: preco anunciado e' obrigatorio.")
        # Medido 100% preenchido na fonte; o destino o exige NOT NULL porque e'
        # o unico timestamp que descreve a observacao do preco (F2).
        if r["price_captured_at"] is None:
            raise SyncError(
                "price_captured_at nulo: sem o instante de captura a observacao "
                "de preco nao e' auditavel."
            )
        for campo in ("advertised_price", "original_price"):
            valor = r[campo]
            if valor is None:
                continue
            d = Decimal(str(valor))
            if d.is_nan():
                raise SyncError(f"NaN em {campo}: NaN nao e' preco.")
            if d < 0:
                raise SyncError(f"{campo} negativo: preco anunciado nao e' negativo.")
        for campo in ("listing_title", "permalink"):
            if not r[campo] or not str(r[campo]).strip():
                raise SyncError(f"{campo} vazio: o destino exige NOT NULL.")
        # Vazio nao e' chave de match: se o atributo nao existe, tem de ser NULO.
        if r["seller_sku"] is not None and not str(r["seller_sku"]).strip():
            raise SyncError("seller_sku vazio: use NULO para atributo ausente.")
    return {
        "rows": len(rows),
        "distinct_keys": len(chaves),
        "seller_sku_null": sum(1 for r in rows if r["seller_sku"] is None),
        "gtin_null": sum(1 for r in rows if r["gtin"] is None),
    }


def read_source(datamart_conn, date_from: date, date_to: date) -> SourceSnapshot:
    """Le detalhe e agregado NA MESMA fotografia e reconcilia os dois."""
    cur = datamart_conn.cursor()
    try:
        cur.execute(f"SET LOCAL statement_timeout = '{SOURCE_STATEMENT_TIMEOUT}'")
        assert_snapshot_session(cur)
        params = _params(date_from, date_to)

        # Fail-closed de marca ANTES de qualquer outra coisa: se a fonte ganhou
        # uma marca, nem lemos o resto.
        assert_no_unknown_brands(cur, date_from, date_to)

        cur.execute(build_source_aggregate_query(), params)
        agregado = dict(cur.fetchone())

        cur.execute(build_source_query(), params)
        rows = [dict(r) for r in cur.fetchall()]

        validate_rows(rows, date_from, date_to)
        recomputado = aggregates_from_rows(rows)
        problemas = compare_aggregates(agregado, recomputado)
        if problemas:
            raise SyncError(
                "detalhe divergiu do agregado na MESMA fotografia da fonte: "
                + "; ".join(problemas)
            )
        return SourceSnapshot(rows, date_from, date_to, agregado)
    finally:
        cur.close()
        # Encerra a transacao de leitura sem escrever nada. `rollback` numa sessao
        # read-only e' o encerramento correto: nao ha o que confirmar.
        datamart_conn.rollback()


# ---------------------------------------------------------------------------
# Publicacao — UMA transacao, rollback integral em qualquer falha
# ---------------------------------------------------------------------------

def _cols_sql(cols) -> str:
    return ", ".join(validate_identifier(c) for c in cols)


def except_both_ways(cur, date_from: date, date_to: date) -> tuple[int, int]:
    """Igualdade EXATA de conjunto entre staging e a janela do destino.

    Mais forte que qualquer agregado: prova linha a linha, coluna a coluna, nos
    dois sentidos. Um agregado igual com linhas trocadas passaria; isto nao.
    """
    staging = f"pg_temp.{validate_identifier(STAGING_TABLE)}"
    target = validate_qualified(TARGET_TABLE)
    cols = _cols_sql(BUSINESS_COLUMNS)
    escopo = (
        f"(SELECT {cols} FROM {target} "
        f"  WHERE {validate_identifier('ref_date')} "
        f"        BETWEEN %(date_from)s AND %(date_to)s) t"
    )
    params = {"date_from": date_from, "date_to": date_to}

    cur.execute(
        f"SELECT count(*) AS n FROM ("
        f"  SELECT {cols} FROM {staging} EXCEPT SELECT {cols} FROM {escopo}"
        f") d", params,
    )
    staging_menos_destino = cur.fetchone()["n"]
    cur.execute(
        f"SELECT count(*) AS n FROM ("
        f"  SELECT {cols} FROM {escopo} EXCEPT SELECT {cols} FROM {staging}"
        f") d", params,
    )
    destino_menos_staging = cur.fetchone()["n"]
    return staging_menos_destino, destino_menos_staging


def publish_window(neon_conn, snapshot: SourceSnapshot, run_id: str) -> dict:
    """lock -> staging -> validacao -> DELETE da janela -> INSERT -> verificacao.

    Qualquer falha faz ROLLBACK integral e o destino fica exatamente como estava.
    `ON COMMIT DROP` limpa a staging tambem no rollback. ZERO retry: falha sobe.

    O DELETE e' restrito a `ref_date BETWEEN date_from AND date_to`. Nunca ha
    `TRUNCATE` nem `DELETE` sem `WHERE` neste modulo: um erro de janela deve
    afetar so a janela.
    """
    staging = f"pg_temp.{validate_identifier(STAGING_TABLE)}"
    target = validate_qualified(TARGET_TABLE)
    cols = list(BUSINESS_COLUMNS) + list(AUDIT_COLUMNS)
    resultado = {"table": TARGET_TABLE, "deleted": 0, "published": 0, "checks": {}}

    cur = neon_conn.cursor()
    try:
        cur.execute(f"SET LOCAL statement_timeout = '{TARGET_STATEMENT_TIMEOUT}'")
        cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        # Lock de TRANSACAO: a leitura da fonte ja terminou antes de abrir esta
        # transacao, entao a janela de ociosidade em transacao e' curta.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))

        cur.execute(f"""
            CREATE TEMP TABLE {validate_identifier(STAGING_TABLE)}
                (LIKE {target} INCLUDING DEFAULTS)
            ON COMMIT DROP
        """)

        if snapshot.rows:
            execute_values(
                cur,
                f"INSERT INTO {staging} ({_cols_sql(cols)}) VALUES %s",
                [tuple(r[c] for c in BUSINESS_COLUMNS) + (run_id,)
                 for r in snapshot.rows],
                page_size=INSERT_PAGE_SIZE,
            )

        cur.execute(f"SELECT count(*) AS n FROM {staging}")
        na_staging = cur.fetchone()["n"]
        if na_staging != len(snapshot.rows):
            raise SyncError(
                f"staging divergiu do detalhe lido: {na_staging} contra "
                f"{len(snapshot.rows)}."
            )

        cur.execute(
            f"SELECT count(*) AS n FROM {staging} "
            f"WHERE ref_date < %(date_from)s OR ref_date > %(date_to)s",
            {"date_from": snapshot.date_from, "date_to": snapshot.date_to},
        )
        if cur.fetchone()["n"]:
            raise SyncError("staging contem linha fora da janela: publicacao abortada.")

        cur.execute(
            f"DELETE FROM {target} "
            f"WHERE ref_date BETWEEN %(date_from)s AND %(date_to)s "
            f"  AND marketplace = %(marketplace)s",
            {"date_from": snapshot.date_from, "date_to": snapshot.date_to,
             "marketplace": MARKETPLACE},
        )
        resultado["deleted"] = cur.rowcount

        cur.execute(
            f"INSERT INTO {target} ({_cols_sql(cols)}) "
            f"SELECT {_cols_sql(cols)} FROM {staging}"
        )
        resultado["published"] = cur.rowcount

        if resultado["published"] != len(snapshot.rows):
            raise SyncError(
                f"insert publicou {resultado['published']} linhas contra "
                f"{len(snapshot.rows)} lidas."
            )

        a_nao_b, b_nao_a = except_both_ways(cur, snapshot.date_from, snapshot.date_to)
        if a_nao_b or b_nao_a:
            raise SyncError(
                f"EXCEPT bidirecional divergiu: staging-destino={a_nao_b} "
                f"destino-staging={b_nao_a}."
            )

        resultado["checks"] = {
            "staging_rows": na_staging,
            "except_both_ways": (a_nao_b, b_nao_a),
            "source_aggregates": snapshot.aggregates,
        }
        neon_conn.commit()
        return resultado
    except Exception:
        neon_conn.rollback()
        raise
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Modos
# ---------------------------------------------------------------------------

def run_diagnostic(date_from: date, date_to: date) -> dict:
    """SOMENTE leitura, nos dois bancos. Nenhuma staging, nenhum lock, zero escrita."""
    dm = _datamart_snapshot(_get_datamart_url())
    try:
        snapshot = read_source(dm, date_from, date_to)
    finally:
        dm.close()

    destino = {"rows_in_window": None}
    neon = _neon_readonly(_get_neon_url())
    try:
        cur = neon.cursor()
        try:
            cur.execute(
                f"SELECT count(*) AS n FROM {validate_qualified(TARGET_TABLE)} "
                f"WHERE ref_date BETWEEN %(date_from)s AND %(date_to)s",
                {"date_from": date_from, "date_to": date_to},
            )
            destino["rows_in_window"] = cur.fetchone()["n"]
        finally:
            cur.close()
    except Exception as exc:
        # Destino ausente e' informacao legitima do diagnostico (a tabela so
        # nasce quando a migration do PMA-1B rodar), nao motivo de falha.
        destino["error"] = sanitize_error_message(exc)
    finally:
        neon.close()

    return {
        "mode": "diagnostic", "applied": False,
        "window": (date_from, date_to),
        "source": snapshot.aggregates,
        "validation": validate_rows(snapshot.rows, date_from, date_to),
        "target": destino,
    }


def run_apply(date_from: date, date_to: date, run_id: str) -> dict:
    dm = _datamart_snapshot(_get_datamart_url())
    try:
        snapshot = read_source(dm, date_from, date_to)
    finally:
        dm.close()

    neon = _neon_writable(_get_neon_url())
    try:
        publicado = publish_window(neon, snapshot, run_id)
    finally:
        neon.close()

    return {
        "mode": "apply", "applied": True, "run_id": run_id,
        "window": (date_from, date_to),
        "source": snapshot.aggregates,
        "publish": publicado,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sync-ml-listing-price-serving",
        description=(
            "Publica o PRECO ANUNCIADO diario dos anuncios PROPRIOS do Mercado "
            "Livre em marts.fact_marketplace_listing_price_daily. Fonte "
            "read-only no Data Mart; destino Neon. Sem --apply nada e' escrito."
        ),
    )
    p.add_argument("--date-from", default=None, help="AAAA-MM-DD (exige --date-to)")
    p.add_argument("--date-to", default=None, help="AAAA-MM-DD, no maximo D-1")
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help=f"janela incremental terminando em D-1 (default {DEFAULT_LOOKBACK_DAYS})")
    p.add_argument("--run-id", default=None)
    p.add_argument("--apply", action="store_true",
                   help="escreve no Neon. Sem esta flag, o CLI e' somente leitura.")
    return p


def resolve_window(args, today: date | None = None) -> tuple[date, date]:
    if bool(args.date_from) != bool(args.date_to):
        raise SyncError("--date-from e --date-to precisam ser usados juntos.")
    if args.date_from:
        return validate_window(
            date.fromisoformat(args.date_from),
            date.fromisoformat(args.date_to),
            today,
        )
    return incremental_window(today, args.lookback_days)


def _print_report(rel: dict) -> None:
    de, ate = rel["window"]
    print(f"[sync-ml-listing-price] modo={rel['mode']} janela={de} a {ate}")
    print(f"  timezone operacional: {TZ_OPERACIONAL.key}")
    print(f"  fonte: {SOURCE_PRICE_RELATION} + {SOURCE_ITEMS_RELATION} (READ-ONLY)")
    print(f"  destino: {TARGET_TABLE}")
    print(f"  cobertura: advertised_only (sem frete, cupom, subsidio ou checkout)")
    src = rel["source"]
    print(f"  fonte: linhas={src['row_count']} itens={src['item_count']} "
          f"marcas={src['brand_count']} datas={src['min_ref_date']}..{src['max_ref_date']}")
    print(f"  original_price nao-nulo={src['original_price_not_null']} "
          f"(nulo = sem promocao, NUNCA zero)")
    if rel["mode"] == "diagnostic":
        v = rel["validation"]
        print(f"  validacao: chaves distintas={v['distinct_keys']} "
              f"seller_sku nulo={v['seller_sku_null']} gtin nulo={v['gtin_null']}")
        print(f"  destino hoje na janela: {rel['target']}")
        print("  ESCRITA: nenhuma (sem --apply).")
    else:
        pub = rel["publish"]
        print(f"  ESCRITA: deleted={pub['deleted']} published={pub['published']} "
              f"EXCEPT bidirecional={pub['checks']['except_both_ways']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        de, ate = resolve_window(args)
        run_id = sanitize_run_id(args.run_id) if args.run_id else default_run_id()
        rel = run_apply(de, ate, run_id) if args.apply else run_diagnostic(de, ate)
        _print_report(rel)
        return 0
    except SyncError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — sanitiza antes de imprimir
        print(f"ERRO: {sanitize_error_message(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
