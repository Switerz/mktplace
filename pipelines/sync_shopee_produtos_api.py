"""Publica `gold.shopee_product_daily` (Data Mart) em `marts.fact_shopee_product_monthly` (Neon).

Gate SHOPEE-API-PRODUTOS-2. Substitui, para as contas cobertas pela API oficial
da Shopee, o caminho do export XLSX congelado.

O QUE ESTA PUBLICACAO MEDE
---------------------------
Faturamento e unidades por (marca, competencia mensal, SKU), no recorte
**CONCLUIDO** -- `gmv_completed` / `units_completed` / `orders_completed` do
gold. E' deliberadamente o MESMO recorte que a tela ja' publica hoje
(`status = Concluido` no export), para que a troca de fonte nao troque a
definicao junto. `is_sale` NAO e' usado aqui, em nenhum caminho.

🔴 POR QUE A COMPETENCIA ANTERIOR PRECISA SER REPROCESSADA
------------------------------------------------------------
Pedido de marketplace conclui DEPOIS. Medido em 25/09/2026 sobre as 4 contas:
o recorte `completed` vale 100% do `is_sale` em julho e agosto, mas so' 70,6%
a 79,1% em setembro. Um pedido de setembro que concluir em outubro pertence a
competencia de SETEMBRO -- e so' aparece nela se setembro for recalculado.

Por isso a janela padrao e' de `JANELA_MESES` competencias, sempre recalculadas
por inteiro, e nunca "apenas o mes corrente". Publicar so' o mes corrente
congelaria cada mes no valor imaturo que ele tinha ao virar -- que e'
exatamente o defeito do export que este publisher substitui.

PROCEDENCIA E O INTERRUPTOR
----------------------------
A fato guarda as duas procedencias lado a lado (`source`), e quem decide qual
delas a tela le' e' `marts.shopee_product_source_mode`, marca a marca. Este
publisher NAO mexe no interruptor: ele so' publica as linhas `api`. Ligar uma
marca e' um UPDATE de uma linha, feito depois da reconciliacao.

Consequencia: rodar este publisher com todas as marcas em `manual_export` e'
uma publicacao em SHADOW -- as linhas existem, e nenhuma tela as le'.

A `kokeshi` nunca entra: ela nao tem aplicacao Shopee cadastrada (o app e'
registrado por loja, no console da empresa dona) e por isso nao existe na
fonte. As linhas `manual_export` dela nunca sao tocadas por este codigo.

O QUE ESTA FONTE NAO ENTREGA
-----------------------------
`canceled_orders`, `cancel_rate_pct` e `unique_buyers` ficam **NULL** nas
linhas `api`, e isso e' contrato, nao lacuna esquecida:

  · o gold filtra `is_sale` na origem, entao pedido cancelado nao chega ate'
    ele -- nao ha de onde tirar `canceled_orders`;
  · a API de pedidos nao expoe identidade de comprador utilizavel
    (`buyer_user_id` e' pseudonimo por loja), entao nao ha `unique_buyers`;
  · `cancel_rate_pct` deriva das duas primeiras.

🔴 NULL, NUNCA ZERO. Zero afirmaria "nenhum pedido cancelado", que e' falso e
nao medido. NULL diz "esta fonte nao mede isto", que e' verdade. Quem le' tem
de distinguir os dois casos, e por isso `gold_service` foi corrigido junto
(ele convertia NULL em 0 silenciosamente).

USO
---
    python -m pipelines.sync_shopee_produtos_api                  # dry-run
    python -m pipelines.sync_shopee_produtos_api --apply
    python -m pipelines.sync_shopee_produtos_api --apply --meses 6
    python -m pipelines.sync_shopee_produtos_api --apply --marcas apice
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from psycopg2.extras import execute_values
from sqlalchemy import text

from pipelines.common.db import DataMartSession, LocalSession, local_engine
from pipelines.common.logging import get_logger

logger = get_logger(__name__)

FACT = "marts.fact_shopee_product_monthly"
MODE_TABLE = "marts.shopee_product_source_mode"
GOLD = "gold.shopee_product_daily"

#: Procedencia gravada por este publisher. Espelha o CHECK da migration 023.
SOURCE_API = "api"
SOURCE_MANUAL = "manual_export"

#: Fuso operacional da Torre.
OPERATIONAL_TZ = timezone(timedelta(hours=-3))

#: 🔑 Dominio FECHADO. A fonte e' o gold, mas o publisher so' escreve por estas
#: contas: conta nova aparecendo no gold nao vira publicacao por acidente.
#: `kokeshi` esta ausente de proposito -- ver o cabecalho.
CONTAS_API = ("apice", "barbours", "lescent", "rituaria")

#: Competencias recalculadas por execucao, contando a corrente. Tres cobre a
#: maturacao observada (o mes fecha em ~100% ja' no mes seguinte) com folga.
JANELA_MESES = 3

#: Guarda contra colapso silencioso da fonte. Se uma (marca, competencia) que
#: JA' TEM linhas publicadas voltar com menos de esta fracao das linhas
#: anteriores, a publicacao inteira aborta.
#:
#: 🔴 O modo de falha que isto pega: a fonte devolver vazio ou quase vazio por
#: instabilidade. Sem a guarda, o DELETE + INSERT apagaria a competencia e
#: gravaria o pouco que veio -- e o resultado seria indistinguivel de "a marca
#: vendeu menos". Foi assim que agosto/2026 virou zero na tabela atual.
MIN_LINHAS_RATIO = 0.80

#: 🔴 FRESCOR DA FONTE — falha FECHADA se o gold estiver velho.
#:
#: `gold.shopee_product_daily` e' reconstruido dentro do `shopee_orders_etl`, que
#: roda a cada 6h. Se o dia mais recente do gold estiver mais de
#: `FRESCOR_MAX_DIAS` atras do dia BRT de hoje, a fonte parou — e publicar um
#: gold parado REESCREVE a competencia corrente com dado velho, que e' pior do
#: que nao publicar.
#:
#: 2 dias e' o teto: com 4 execucoes por dia, um gold de anteontem significa
#: pelo menos 8 ciclos perdidos. Nao e' atraso, e' pane.
FRESCOR_MAX_DIAS = 2

STAGING_PAGE_SIZE = 500
TARGET_STATEMENT_TIMEOUT_MS = 180_000

ADVISORY_LOCK_KEY = 916140021
AUDIT_MARKETPLACE_ID = 3
AUDIT_SOURCE = "shopee_produtos_api"
AUDIT_STATUSES = ("running", "success", "failed")
AUDIT_ERRO_MAX_CHARS = 4000


class ShopeeProdutosSyncError(RuntimeError):
    """Falha de contrato ou de publicacao."""


class ConcurrentRunError(ShopeeProdutosSyncError):
    """Outra execucao ja' detem o advisory lock."""


_CREDENCIAL_EM_DSN = re.compile(r"//[^/\s:@]+:[^/\s@]+@")


def sanitizar(exc: BaseException | str) -> str:
    """Remove credencial embutida em DSN antes de logar ou auditar."""
    txt = str(exc)
    return _CREDENCIAL_EM_DSN.sub("//<redigido>@", txt)


def _hoje_brt(agora: datetime | None = None) -> date:
    return (agora or datetime.now(OPERATIONAL_TZ)).astimezone(OPERATIONAL_TZ).date()


def competencias(hoje: date, meses: int = JANELA_MESES) -> list[date]:
    """As `meses` competencias que terminam na do dia `hoje`, mais antiga primeiro.

    Devolve o PRIMEIRO dia de cada mes, que e' o formato de `ref_month`.
    """
    if meses < 1:
        raise ShopeeProdutosSyncError(f"meses precisa ser >= 1, recebido {meses}")
    out: list[date] = []
    ano, mes = hoje.year, hoje.month
    for _ in range(meses):
        out.append(date(ano, mes, 1))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return sorted(out)


@dataclass(frozen=True)
class ProdutoRow:
    ref_month: date
    brand: str
    sku_ref: str
    sku_ref_key: str
    product_name: str
    variation_name: str | None
    gmv: float
    units_sold: int
    completed_orders: int
    avg_price: float | None
    is_partial: bool


@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime
    ref_months: list[date]
    marcas: tuple[str, ...]
    mes_corrente: date
    rows: list[ProdutoRow] = field(default_factory=list)


#: 🔴 AGREGACAO DIA -> MES SOBRE AS COLUNAS DO RECORTE CONCLUIDO.
#:
#: `orders_completed` e' somado, e a ressalva esta' no schema do gold: ele e'
#: distinto POR SKU, entao somar dias conta o pedido que comprou o mesmo SKU em
#: dias diferentes uma vez por dia. E' o mesmo comportamento do
#: `completed_orders` do export, que tambem conta por linha de produto -- ou
#: seja, a troca de fonte NAO muda a semantica desta coluna.
#:
#: O filtro `gmv_completed > 0` elimina o SKU que so' teve brinde ou so' teve
#: pedido ainda em transito no mes. A tela ja' filtra `gmv > 0`, entao gravar
#: essas linhas so' aumentaria a tabela sem mudar nenhuma leitura.
SQL_GOLD = text(f"""
    SELECT
        date_trunc('month', date)::date          AS ref_month,
        brand,
        sku,
        max(product_name)                        AS product_name,
        max(variation_name)                      AS variation_name,
        round(sum(gmv_completed)::numeric, 2)    AS gmv,
        sum(units_completed)::bigint             AS units_sold,
        sum(orders_completed)::bigint            AS completed_orders
      FROM {GOLD}
     WHERE date >= :inicio
       AND date <  :fim
       AND brand = ANY(:marcas)
     GROUP BY 1, 2, 3
    HAVING sum(gmv_completed) > 0
     ORDER BY 1, 2, 3
""")


def read_source(conn, ref_months: list[date], marcas: tuple[str, ...],
                mes_corrente: date, captured_at: datetime) -> Snapshot:
    """Le' o gold e agrega para o grao da fato. Nenhuma escrita."""
    inicio = min(ref_months)
    ultimo = max(ref_months)
    fim = date(ultimo.year + (ultimo.month // 12),
               (ultimo.month % 12) + 1, 1)

    linhas = conn.execute(
        SQL_GOLD,
        {"inicio": inicio, "fim": fim, "marcas": list(marcas)},
    ).mappings().all()

    rows: list[ProdutoRow] = []
    for r in linhas:
        units = int(r["units_sold"] or 0)
        gmv = float(r["gmv"] or 0)
        sku = (r["sku"] or "").strip()
        if not sku:
            # O gold garante `sku` preenchido; se algum dia deixar de garantir,
            # a linha nao pode virar uma chave vazia na fato (que e' NOT NULL).
            raise ShopeeProdutosSyncError(
                f"linha do gold sem sku em {r['brand']}/{r['ref_month']}")
        nome = (r["product_name"] or "").strip() or sku
        rows.append(ProdutoRow(
            ref_month=r["ref_month"],
            brand=r["brand"],
            sku_ref=sku,
            # A fato chaveia por `sku_ref_key`. O gold ja' resolve o SKU pelo
            # catalogo; aqui so' normalizamos a CAIXA, porque no export
            # `Kit112` e `KIT112` convivem como se fossem dois produtos.
            sku_ref_key=sku.upper(),
            product_name=nome,
            variation_name=(r["variation_name"] or None),
            gmv=gmv,
            units_sold=units,
            completed_orders=int(r["completed_orders"] or 0),
            avg_price=round(gmv / units, 2) if units > 0 else None,
            is_partial=(r["ref_month"] == mes_corrente),
        ))

    return Snapshot(captured_at=captured_at, ref_months=ref_months,
                    marcas=marcas, mes_corrente=mes_corrente, rows=rows)


def validate_contract(snap: Snapshot) -> list[str]:
    """Checagens que NAO dependem do destino. Devolve avisos; erro levanta."""
    avisos: list[str] = []

    if not snap.rows:
        raise ShopeeProdutosSyncError(
            "fonte vazia: o gold nao devolveu nenhuma linha na janela. "
            "Publicar isto apagaria as competencias da janela inteira."
        )

    # Chave duplicada seria violacao do UNIQUE do destino, e e' melhor descobrir
    # aqui do que no meio da transacao.
    chaves = [(r.ref_month, r.brand, r.sku_ref_key, r.product_name)
              for r in snap.rows]
    if len(chaves) != len(set(chaves)):
        dups = len(chaves) - len(set(chaves))
        raise ShopeeProdutosSyncError(
            f"{dups} chave(s) (ref_month, brand, sku_ref_key, product_name) "
            "duplicada(s) na fonte — o UNIQUE do destino as recusaria"
        )

    # Toda conta da allowlist deveria aparecer. Ausencia nao aborta (a marca
    # pode nao ter vendido no periodo), mas nunca passa em silencio.
    presentes = {r.brand for r in snap.rows}
    ausentes = sorted(set(snap.marcas) - presentes)
    if ausentes:
        avisos.append(
            f"contas sem nenhuma linha na janela: {ausentes} — "
            "confira se e' ausencia de venda ou falha de ingestao"
        )

    for r in snap.rows:
        if r.gmv <= 0 or r.units_sold <= 0:
            raise ShopeeProdutosSyncError(
                f"linha com gmv/unidades nao positivos apos o HAVING: "
                f"{r.brand}/{r.ref_month}/{r.sku_ref_key}"
            )
        if r.completed_orders <= 0:
            raise ShopeeProdutosSyncError(
                f"linha com faturamento concluido e zero pedidos concluidos: "
                f"{r.brand}/{r.ref_month}/{r.sku_ref_key}"
            )

    return avisos


_COLS = ("ref_month", "brand", "sku_ref", "sku_ref_key", "product_name",
         "variation_name", "gmv", "units_sold", "completed_orders",
         "canceled_orders", "cancel_rate_pct", "unique_buyers", "avg_price",
         "source", "source_run_id", "source_captured_at", "is_partial")


def _linha(r: ProdutoRow, run_id: str, captured_at: datetime) -> tuple:
    return (
        r.ref_month, r.brand, r.sku_ref, r.sku_ref_key, r.product_name,
        r.variation_name, r.gmv, r.units_sold, r.completed_orders,
        # 🔴 Os tres NULL sao contrato, nao lacuna. Ver o cabecalho.
        None, None, None,
        r.avg_price,
        SOURCE_API, run_id, captured_at, r.is_partial,
    )


SQL_FRESCOR = text(f"SELECT max(date) AS ultimo FROM {GOLD}")


def validate_frescor(conn, hoje: date) -> date:
    """Idade da fonte. Fonte velha ou vazia LEVANTA — nunca publica.

    🔴 Falha FECHADA de proposito. Um gold parado publicado por cima da
    competencia corrente a REESCREVE com dado velho, e o resultado e'
    indistinguivel de "vendeu menos" — que foi exatamente como agosto/2026
    virou zero na tabela que este publisher substitui.
    """
    ultimo = conn.execute(SQL_FRESCOR).scalar()
    if ultimo is None:
        raise ShopeeProdutosSyncError(
            f"{GOLD} esta' VAZIA: a fonte nao foi materializada. "
            "Nada foi publicado."
        )
    atraso = (hoje - ultimo).days
    if atraso > FRESCOR_MAX_DIAS:
        raise ShopeeProdutosSyncError(
            f"fonte VELHA: o dia mais recente de {GOLD} e' {ultimo}, "
            f"{atraso} dia(s) atras de {hoje} (teto {FRESCOR_MAX_DIAS}). "
            "Com 4 execucoes por dia do upstream, isso e' pane, nao atraso. "
            "Nada foi publicado."
        )
    return ultimo


def escopo_publicado(conn, snap: Snapshot) -> dict[tuple, int]:
    """Quantas linhas `api` ja' existem por (brand, ref_month) no destino."""
    linhas = conn.execute(text(f"""
        SELECT brand, ref_month, COUNT(*) AS n
          FROM {FACT}
         WHERE source = :src
           AND brand = ANY(:marcas)
           AND ref_month = ANY(:meses)
         GROUP BY 1, 2
    """), {"src": SOURCE_API, "marcas": list(snap.marcas),
           "meses": snap.ref_months}).mappings().all()
    return {(r["brand"], r["ref_month"]): int(r["n"]) for r in linhas}


#: Colunas que definem o CONTEUDO de uma linha publicada. `source_run_id` e
#: `source_captured_at` ficam de fora de proposito: eles mudam a cada execucao
#: mesmo quando o dado e' identico, e incluí-los faria a comparacao de
#: equivalencia nunca dar igual — que e' o bug classico de fingerprint.
_COLS_CONTEUDO = ("ref_month", "brand", "sku_ref_key", "product_name",
                  "gmv", "units_sold", "completed_orders", "is_partial")


def _conteudo_da_fonte(snap: Snapshot) -> set[tuple]:
    return {
        (r.ref_month, r.brand, r.sku_ref_key, r.product_name,
         round(r.gmv, 2), r.units_sold, r.completed_orders, r.is_partial)
        for r in snap.rows
    }


def _conteudo_publicado(conn, snap: Snapshot) -> set[tuple]:
    linhas = conn.execute(text(f"""
        SELECT {', '.join(_COLS_CONTEUDO)}
          FROM {FACT}
         WHERE source = :src
           AND brand = ANY(:marcas)
           AND ref_month = ANY(:meses)
    """), {"src": SOURCE_API, "marcas": list(snap.marcas),
           "meses": snap.ref_months}).mappings().all()
    return {
        (r["ref_month"], r["brand"], r["sku_ref_key"], r["product_name"],
         round(float(r["gmv"]), 2), int(r["units_sold"]),
         int(r["completed_orders"]), bool(r["is_partial"]))
        for r in linhas
    }


def upstream_avancou(conn, snap: Snapshot) -> bool:
    """O que a fonte traz difere do que ja' esta' publicado?

    🔑 ESTA FUNCAO FAZ DUAS COISAS AO MESMO TEMPO, e as duas importam.

    **NO-OP seguro.** Se o upstream nao avancou desde a ultima publicacao, o
    conjunto lido e o conjunto publicado sao identicos. Republicar seria um
    DELETE + INSERT que reescreve as mesmas linhas — trabalho, lock e risco por
    nada. A execucao agendada simplesmente nao publica e diz por que.

    **Retry seguro depois de um timeout INDETERMINADO.** O ponto cego de um
    publisher transacional e' o timeout: a transacao pode ter commitado antes
    de o processo morrer, e ninguem sabe. Com esta comparacao, a repeticao
    descobre sozinha — se o commit anterior passou, o conteudo ja' esta' la' e
    a repeticao vira NO-OP; se nao passou, ela publica. E' o que permite que a
    DAG tenha retry sem apostar no desfecho do run anterior.

    A comparacao e' por CONTEUDO, nao por `run_id` nem por `captured_at`:
    esses dois mudam a cada execucao mesmo com dado identico.
    """
    return _conteudo_da_fonte(snap) != _conteudo_publicado(conn, snap)


def publish_in_transaction(conn, snap: Snapshot, run_id: str) -> dict:
    """DELETE + INSERT das linhas `api` da janela. UMA transacao.

    🔴 O `DELETE` e' escopado por `source = 'api'`. Nunca toca linha
    `manual_export` -- nem das marcas da API, nem da `kokeshi`. E' isso que faz
    o rollback ser instantaneo: a fonte antiga continua inteira na tabela, e
    voltar e' um UPDATE no interruptor.
    """
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))

    antes = escopo_publicado(conn, snap)

    # Guarda de colapso: so' se aplica a (marca, competencia) que JA' tinha
    # linhas. Primeira publicacao nao tem baseline e nao e' comparada.
    novos: dict[tuple, int] = {}
    for r in snap.rows:
        novos[(r.brand, r.ref_month)] = novos.get((r.brand, r.ref_month), 0) + 1
    for chave, n_antes in antes.items():
        n_agora = novos.get(chave, 0)
        if n_antes > 0 and n_agora < n_antes * MIN_LINHAS_RATIO:
            raise ShopeeProdutosSyncError(
                f"colapso da fonte em {chave[0]}/{chave[1]}: {n_agora} linha(s) "
                f"contra {n_antes} publicadas antes (piso "
                f"{MIN_LINHAS_RATIO:.0%}). Publicacao abortada — ausencia nao "
                "vira zero."
            )

    conn.execute(text(f"""
        DELETE FROM {FACT}
         WHERE source = :src
           AND brand = ANY(:marcas)
           AND ref_month = ANY(:meses)
    """), {"src": SOURCE_API, "marcas": list(snap.marcas),
           "meses": snap.ref_months})

    raw = conn.connection
    with raw.cursor() as cur:
        execute_values(
            cur,
            f"INSERT INTO {FACT} ({', '.join(_COLS)}) VALUES %s",
            [_linha(r, run_id, snap.captured_at) for r in snap.rows],
            page_size=STAGING_PAGE_SIZE,
        )

    # Reconciliacao PRE-COMMIT contra a fotografia lida. Diferenca em qualquer
    # direcao aborta a transacao.
    destino = conn.execute(text(f"""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(gmv), 0)        AS gmv,
               COALESCE(SUM(units_sold), 0) AS units
          FROM {FACT}
         WHERE source = :src
           AND brand = ANY(:marcas)
           AND ref_month = ANY(:meses)
    """), {"src": SOURCE_API, "marcas": list(snap.marcas),
           "meses": snap.ref_months}).mappings().one()

    esperado_n = len(snap.rows)
    esperado_gmv = round(sum(r.gmv for r in snap.rows), 2)
    esperado_units = sum(r.units_sold for r in snap.rows)
    if (destino["n"] != esperado_n
            or round(float(destino["gmv"]), 2) != esperado_gmv
            or int(destino["units"]) != esperado_units):
        raise ShopeeProdutosSyncError(
            f"reconciliacao pre-commit falhou: destino n={destino['n']} "
            f"gmv={destino['gmv']} units={destino['units']}; esperado "
            f"n={esperado_n} gmv={esperado_gmv} units={esperado_units}"
        )

    return {"linhas_publicadas": esperado_n,
            "gmv_publicado": esperado_gmv,
            "units_publicadas": esperado_units,
            "linhas_antes": sum(antes.values())}


# --------------------------------------------------------------------------- #
# AUDITORIA — conexao INDEPENDENTE, commit proprio                              #
# --------------------------------------------------------------------------- #
# Mesma escolha de `sync_shopee_fbs_stock_daily.py`, pelo mesmo motivo: a
# auditoria nao pode viver na transacao da publicacao, senao o rollback que
# protege a fato apagaria junto o registro de que a execucao existiu e falhou.

def _audit_conn():
    return LocalSession().get_bind().raw_connection()


def audit_start(rows_extracted: int) -> int:
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit.source_sync_run
                    (source_name, marketplace_id, loja_id, status, started_at,
                     rows_extracted)
                VALUES (%s, %s, NULL, 'running', NOW(), %s)
                RETURNING sync_run_id
                """,
                (AUDIT_SOURCE, AUDIT_MARKETPLACE_ID, rows_extracted),
            )
            sync_run_id = cur.fetchone()[0]
        conn.commit()
        return sync_run_id
    finally:
        conn.close()


def audit_finish(sync_run_id: int, status: str,
                 rows_loaded: int | None = None,
                 error_message: str | None = None) -> None:
    if status not in AUDIT_STATUSES:
        raise ShopeeProdutosSyncError(
            f"status de auditoria fora do dominio da tabela: {status!r}")
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE audit.source_sync_run
                   SET status = %s, finished_at = NOW(),
                       rows_loaded = %s, error_message = %s
                 WHERE sync_run_id = %s
                """,
                (status, rows_loaded,
                 (error_message or None) and error_message[:AUDIT_ERRO_MAX_CHARS],
                 sync_run_id),
            )
            if cur.rowcount != 1:
                raise ShopeeProdutosSyncError(
                    "UPDATE de auditoria nao afetou exatamente 1 linha")
        conn.commit()
    finally:
        conn.close()


def _publicar(snap: Snapshot, run_id: str) -> dict:
    """UMA transacao explicita, com advisory lock de TRANSACAO como 1o comando.

    `pg_try_advisory_xact_lock` e nao `pg_try_advisory_lock`: o banco o libera
    no COMMIT e no ROLLBACK, sem depender de um `finally` chegar a rodar.

    `engine.begin()` e nao `session.connection().begin()`: numa Session do ORM o
    `connection()` ja' fez autobegin e o `.begin()` levanta.
    """
    with local_engine().begin() as conn:
        obtido = conn.execute(
            text("SELECT pg_try_advisory_xact_lock(:k)"),
            {"k": ADVISORY_LOCK_KEY},
        ).scalar()
        if not obtido:
            raise ConcurrentRunError(
                f"advisory lock {ADVISORY_LOCK_KEY} ocupado: outra execucao em curso"
            )
        # NO-OP DENTRO DO LOCK, e nao antes dele: checar fora seria uma corrida
        # — outra execucao poderia publicar entre a checagem e o DELETE.
        if not upstream_avancou(conn, snap):
            return {"noop": True, "linhas_publicadas": 0,
                    "motivo": "o upstream nao avancou: o conteudo lido do gold "
                              "e' identico ao ja' publicado nesta janela"}
        return publish_in_transaction(conn, snap, run_id)


def modos_atuais() -> dict[str, str]:
    """Le' o interruptor por marca. So' informativo: este publisher nao o usa
    para decidir o que escrever -- ele escreve `api` sempre, e o interruptor
    decide quem LE'."""
    eng = local_engine()
    with eng.connect() as conn:
        linhas = conn.execute(
            text(f"SELECT brand, source FROM {MODE_TABLE} ORDER BY brand")
        ).mappings().all()
    return {r["brand"]: r["source"] for r in linhas}


def run(apply: bool = False, meses: int = JANELA_MESES,
        marcas: tuple[str, ...] | None = None,
        agora: datetime | None = None) -> dict:
    hoje = _hoje_brt(agora)
    run_id = uuid.uuid4().hex
    captured_at = datetime.now(timezone.utc)

    alvo = tuple(marcas) if marcas else CONTAS_API
    desconhecidas = sorted(set(alvo) - set(CONTAS_API))
    if desconhecidas:
        # Fail-closed: marca fora da allowlist nunca e' publicada por engano.
        raise ShopeeProdutosSyncError(
            f"marcas fora da allowlist da API: {desconhecidas}. "
            f"Cobertas: {list(CONTAS_API)}. A kokeshi e' manual por nao ter "
            "aplicacao Shopee cadastrada."
        )

    meses_ref = competencias(hoje, meses)
    mes_corrente = date(hoje.year, hoje.month, 1)

    dm = DataMartSession()
    try:
        dm.connection().connection.set_session(readonly=True)
        # 🔴 FRESCOR ANTES DE TUDO: fonte velha ou vazia levanta aqui, antes de
        # ler uma linha de dado e muito antes de abrir a transacao do destino.
        ultimo_dia_gold = validate_frescor(dm.connection(), hoje)
        snap = read_source(dm.connection(), meses_ref, alvo, mes_corrente,
                           captured_at)
    finally:
        dm.rollback()
        dm.close()

    avisos = validate_contract(snap)

    resumo = {
        "run_id": run_id,
        "hoje_brt": str(hoje),
        "captured_at": captured_at.isoformat(),
        "ultimo_dia_gold": str(ultimo_dia_gold),
        "atraso_gold_dias": (hoje - ultimo_dia_gold).days,
        "competencias": [str(m) for m in meses_ref],
        "mes_corrente_parcial": str(mes_corrente),
        "marcas": list(alvo),
        "linhas_lidas": len(snap.rows),
        "gmv_lido": round(sum(r.gmv for r in snap.rows), 2),
        "units_lidas": sum(r.units_sold for r in snap.rows),
        "avisos": avisos,
        "aplicado": False,
        "modo_por_marca": modos_atuais(),
    }

    # Recorte por competencia, util no relatorio e na reconciliacao.
    por_mes: dict[str, dict] = {}
    for r in snap.rows:
        k = f"{r.brand}/{r.ref_month}"
        d = por_mes.setdefault(k, {"linhas": 0, "gmv": 0.0, "units": 0,
                                   "parcial": r.is_partial})
        d["linhas"] += 1
        d["gmv"] = round(d["gmv"] + r.gmv, 2)
        d["units"] += r.units_sold
    resumo["por_marca_competencia"] = dict(sorted(por_mes.items()))

    if not apply:
        resumo["modo"] = "dry-run"
        return resumo

    sync_run_id = audit_start(len(snap.rows))
    try:
        pub = _publicar(snap, run_id)
    except BaseException as exc:
        # `indeterminate` nao existe nesta tabela de auditoria; o que existe e'
        # `failed`. O rastro honesto vai na mensagem, ja' sanitizada.
        audit_finish(sync_run_id, "failed", error_message=sanitizar(exc))
        raise
    audit_finish(sync_run_id, "success", rows_loaded=pub["linhas_publicadas"])

    resumo["modo"] = "apply"
    # `aplicado` so' e' verdadeiro quando houve ESCRITA. Um NO-OP termina em
    # sucesso e nao escreveu nada — chamar isso de "aplicado" faria a operacao
    # ler publicacao onde houve apenas confirmacao de que nada mudou.
    resumo["aplicado"] = not pub.get("noop", False)
    resumo["noop"] = bool(pub.get("noop", False))
    resumo["sync_run_id"] = sync_run_id
    resumo.update(pub)
    return resumo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Publica o recorte concluido do gold da API Shopee em "
                    "marts.fact_shopee_product_monthly (procedencia 'api')")
    p.add_argument("--apply", action="store_true",
                   help="publica. Sem esta flag o run e' dry-run.")
    p.add_argument("--meses", type=int, default=JANELA_MESES,
                   help=f"competencias recalculadas (default {JANELA_MESES})")
    p.add_argument("--marcas", nargs="+", default=None,
                   help=f"subconjunto de {list(CONTAS_API)}; default todas")
    args = p.parse_args(argv)
    try:
        res = run(apply=args.apply, meses=args.meses,
                  marcas=tuple(args.marcas) if args.marcas else None)
    except ShopeeProdutosSyncError as exc:
        logger.error("%s", sanitizar(exc))
        return 1
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
