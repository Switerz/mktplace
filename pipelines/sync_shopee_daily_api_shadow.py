"""Shadow da FAMILIA DE PEDIDOS da Shopee via API: Data Mart -> Neon (tabela propria).

Gate SHOPEE-MANUAL-RETIREMENT-1. Le' `silver.stg_shopee_orders` /
`silver.stg_shopee_order_items` (Data Mart; ingeridos pela API oficial a cada 6h
pelo `shopee_orders_etl` do Airflow) e grava `marts.fact_shopee_daily_api_shadow`
(Neon, migration 027).

O QUE E' SHADOW AQUI
--------------------
Nenhum consumidor le' a tabela de destino. A tela continua servida por
`marts.fact_marketplace_daily_performance`, que este modulo NUNCA toca. A
comparacao com o publicado e' feita por `pipelines.reconciliation.
shopee_daily_api_vs_manual`. Trocar o consumidor e' outro gate.

O QUE ESTA PUBLICACAO MEDE — mesma regra do export manual
---------------------------------------------------------
Por (dia BRT de criacao do pedido, marca), espelhando `_parser.py` do export:

  orders           pedidos com status <> cancelled   (export: status <> Cancelado)
  units_sold       soma de quantity desses pedidos
  canceled_orders  pedidos cancelled
  delivered_orders pedidos completed                 (export: Concluido)
  seller_shipping_cost  soma de estimated_shipping_fee dos nao cancelados
                        (export: "Valor estimado do frete")
  product_subtotal soma de item_total dos nao cancelados — NAO e' o GMV da tela
  to_return_orders, unpaid_orders  informativos, sem equivalente publicado

Medido no gate (01/01–12/10/2026, 4 contas, 1.140 dias-marca): janeiro a junho
fecham em 0,00% em orders, canceled, delivered, units e frete; o eixo de data
casa no MESMO dia BRT (deslocar 1 dia leva a divergencia a 27%). De julho em
diante a diferenca e' maturacao do export (o arquivo congela o status no dia
em que foi tirado), nao definicao.

🔴 POR QUE A JANELA E' RECALCULADA INTEIRA
-------------------------------------------
Status de pedido muda por semanas (cancelamento tardio, conclusao). Publicar
so' "ontem" congelaria cada dia no valor imaturo — o defeito exato do export
que este shadow mede. Por isso cada execucao recalcula `DIAS_JANELA` dias ate'
D-1 e marca `is_partial` nos ultimos `DIAS_MATURACAO`.

A kokeshi nunca entra: nao tem aplicacao Shopee (o app e' registrado por loja).

USO
---
    python -m pipelines.sync_shopee_daily_api_shadow                  # dry-run
    python -m pipelines.sync_shopee_daily_api_shadow --desde 2026-01-01  # dry-run do backfill
    python -m pipelines.sync_shopee_daily_api_shadow --apply             # exige migration 027
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from pipelines.common.db import DataMartSession, LocalSession, local_engine
from pipelines.common.logging import get_logger

logger = get_logger(__name__)

SHADOW = "marts.fact_shopee_daily_api_shadow"
ORDERS = "silver.stg_shopee_orders"
ITEMS = "silver.stg_shopee_order_items"

SOURCE_API = "api"
OPERATIONAL_TZ = timezone(timedelta(hours=-3))

#: 🔑 Dominio FECHADO, igual ao CHECK da migration 027. `kokeshi` fora de
#: proposito: sem aplicacao Shopee cadastrada.
CONTAS_API = ("apice", "barbours", "lescent", "rituaria")

#: Dias recalculados por execucao, terminando em D-1.
DIAS_JANELA = 90
#: Ultimos dias marcados `is_partial`: status ainda amadurecendo.
DIAS_MATURACAO = 30

#: 🔴 FRESCOR POR CONTA, falha FECHADA. O `shopee_orders_etl` roda a cada 6h;
#: 13h e' um ciclo perdido com folga. Conta mais velha que isso aborta a
#: execucao inteira — publicar fonte parada reescreveria a janela com dado velho.
FRESCOR_MAX_HORAS = 13

#: Guarda de colapso: se uma marca que JA' tinha pedidos publicados na janela
#: voltar com menos que esta fracao, a publicacao aborta (ausencia nao vira zero).
MIN_PEDIDOS_RATIO = 0.80

TARGET_STATEMENT_TIMEOUT_MS = 180_000
ADVISORY_LOCK_KEY = 917140027
AUDIT_MARKETPLACE_ID = 3
AUDIT_SOURCE = "shopee_daily_api_shadow"
AUDIT_STATUSES = ("running", "success", "failed")
AUDIT_ERRO_MAX_CHARS = 4000


class ShopeeDailyShadowError(RuntimeError):
    """Falha de contrato ou de publicacao."""


class ConcurrentRunError(ShopeeDailyShadowError):
    """Outra execucao ja' detem o advisory lock."""


_CREDENCIAL_EM_DSN = re.compile(r"//[^/\s:@]+:[^/\s@]+@")


def sanitizar(exc: BaseException | str) -> str:
    """Remove credencial embutida em DSN antes de logar ou auditar."""
    return _CREDENCIAL_EM_DSN.sub("//<redigido>@", str(exc))


def hoje_brt(agora: datetime | None = None) -> date:
    return (agora or datetime.now(OPERATIONAL_TZ)).astimezone(OPERATIONAL_TZ).date()


def janela(hoje: date, desde: date | None = None, dias: int = DIAS_JANELA) -> tuple[date, date]:
    """[inicio, fim] inclusivos, terminando SEMPRE em D-1 (dia fechado em BRT)."""
    if dias < 1:
        raise ShopeeDailyShadowError(f"dias precisa ser >= 1, recebido {dias}")
    fim = hoje - timedelta(days=1)
    inicio = desde if desde is not None else fim - timedelta(days=dias - 1)
    if inicio > fim:
        raise ShopeeDailyShadowError(f"janela vazia: inicio {inicio} depois do fim {fim}")
    return inicio, fim


@dataclass(frozen=True)
class LinhaDiaria:
    date: date
    brand: str
    orders: int
    units_sold: int
    canceled_orders: int
    delivered_orders: int
    to_return_orders: int
    unpaid_orders: int
    seller_shipping_cost: float
    product_subtotal: float
    is_partial: bool


@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime
    inicio: date
    fim: date
    marcas: tuple[str, ...]
    max_ingested_at: datetime
    rows: list[LinhaDiaria] = field(default_factory=list)


#: Um pedido, uma linha: os itens sao agregados por pedido ANTES do join, para
#: que um pedido com N itens nao multiplique o frete nem a contagem.
SQL_DIARIO = text(f"""
    WITH o AS (
        SELECT shop_account AS brand, order_sn, created_date_brt AS d, order_status AS st,
               estimated_shipping_fee AS frete
          FROM {ORDERS}
         WHERE created_date_brt BETWEEN :inicio AND :fim
           AND shop_account = ANY(:marcas)
    ), it AS (
        SELECT i.shop_account AS brand, i.order_sn,
               SUM(i.quantity) AS qty, SUM(i.item_total) AS subtotal
          FROM {ITEMS} i
          JOIN o ON o.brand = i.shop_account AND o.order_sn = i.order_sn
         GROUP BY 1, 2
    )
    SELECT o.brand, o.d,
           COUNT(*) FILTER (WHERE o.st <> 'cancelled')                       AS orders,
           COUNT(*) FILTER (WHERE o.st = 'cancelled')                        AS canceled_orders,
           COUNT(*) FILTER (WHERE o.st = 'completed')                        AS delivered_orders,
           COUNT(*) FILTER (WHERE o.st = 'to_return')                        AS to_return_orders,
           COUNT(*) FILTER (WHERE o.st = 'unpaid')                           AS unpaid_orders,
           COALESCE(SUM(it.qty) FILTER (WHERE o.st <> 'cancelled'), 0)       AS units_sold,
           COALESCE(SUM(o.frete) FILTER (WHERE o.st <> 'cancelled'), 0)      AS seller_shipping_cost,
           COALESCE(SUM(it.subtotal) FILTER (WHERE o.st <> 'cancelled'), 0)  AS product_subtotal,
           COUNT(*) FILTER (WHERE o.st <> 'cancelled' AND it.order_sn IS NULL) AS sem_item,
           COUNT(*) FILTER (WHERE o.frete IS NULL AND o.st <> 'cancelled')   AS frete_nulo
      FROM o
      LEFT JOIN it ON it.brand = o.brand AND it.order_sn = o.order_sn
     GROUP BY 1, 2
     ORDER BY 1, 2
""")

SQL_FRESCOR = text(f"""
    SELECT shop_account AS brand, max(ingested_at) AS ultima_carga
      FROM {ORDERS}
     WHERE shop_account = ANY(:marcas)
     GROUP BY 1
""")


def validate_frescor(conn, marcas: tuple[str, ...], agora: datetime) -> datetime:
    """Carga mais recente por conta. Conta ausente ou velha LEVANTA — nunca publica."""
    cargas = {r["brand"]: r["ultima_carga"] for r in conn.execute(
        SQL_FRESCOR, {"marcas": list(marcas)}).mappings().all()}
    problemas = []
    for m in marcas:
        c = cargas.get(m)
        if c is None:
            problemas.append(f"{m}: sem nenhuma carga em {ORDERS}")
            continue
        idade_h = (agora - c).total_seconds() / 3600
        if idade_h > FRESCOR_MAX_HORAS:
            problemas.append(f"{m}: ultima carga {c.isoformat()} ha {idade_h:.1f} h (teto {FRESCOR_MAX_HORAS} h)")
    if problemas:
        raise ShopeeDailyShadowError(
            "fonte VELHA ou ausente — nada foi publicado: " + "; ".join(problemas))
    return min(cargas[m] for m in marcas)


def montar_linhas(linhas: list[dict], marcas: tuple[str, ...], hoje: date) -> list[LinhaDiaria]:
    """Linhas cruas do SQL -> grao da tabela. Falha FECHADA em inconsistencia."""
    corte_parcial = hoje - timedelta(days=DIAS_MATURACAO)
    out: list[LinhaDiaria] = []
    for r in linhas:
        if r["brand"] not in marcas:
            raise ShopeeDailyShadowError(f"conta fora da allowlist na fonte: {r['brand']!r}")
        if int(r.get("sem_item") or 0) > 0:
            # Pedido sem item nao tem unidades: publicar seria subcontar em silencio.
            raise ShopeeDailyShadowError(
                f"{r['brand']}/{r['d']}: {r['sem_item']} pedido(s) nao cancelado(s) sem item na fonte")
        if int(r.get("frete_nulo") or 0) > 0:
            raise ShopeeDailyShadowError(
                f"{r['brand']}/{r['d']}: {r['frete_nulo']} pedido(s) sem estimated_shipping_fee — "
                "ausencia nao vira zero")
        out.append(LinhaDiaria(
            date=r["d"], brand=r["brand"],
            orders=int(r["orders"]), units_sold=int(r["units_sold"]),
            canceled_orders=int(r["canceled_orders"]), delivered_orders=int(r["delivered_orders"]),
            to_return_orders=int(r["to_return_orders"]), unpaid_orders=int(r["unpaid_orders"]),
            seller_shipping_cost=round(float(r["seller_shipping_cost"]), 2),
            product_subtotal=round(float(r["product_subtotal"]), 2),
            is_partial=r["d"] > corte_parcial,
        ))
    return out


def validate_contract(snap: Snapshot) -> list[str]:
    """Checagens que nao dependem do destino. Devolve avisos; erro levanta."""
    if not snap.rows:
        raise ShopeeDailyShadowError(
            "fonte vazia na janela: publicar apagaria o shadow da janela inteira")
    chaves = [(r.date, r.brand) for r in snap.rows]
    if len(chaves) != len(set(chaves)):
        raise ShopeeDailyShadowError("chave (date, brand) duplicada na fonte")
    for r in snap.rows:
        if r.date < snap.inicio or r.date > snap.fim:
            raise ShopeeDailyShadowError(f"linha fora da janela: {r.brand}/{r.date}")
        if min(r.orders, r.units_sold, r.canceled_orders, r.delivered_orders,
               r.to_return_orders, r.unpaid_orders) < 0:
            raise ShopeeDailyShadowError(f"contagem negativa em {r.brand}/{r.date}")
        if r.delivered_orders > r.orders or r.to_return_orders > r.orders or r.unpaid_orders > r.orders:
            raise ShopeeDailyShadowError(f"subconjunto maior que o total em {r.brand}/{r.date}")
        if r.seller_shipping_cost < 0 or r.product_subtotal < 0:
            raise ShopeeDailyShadowError(f"valor negativo em {r.brand}/{r.date}")
    avisos = []
    ausentes = sorted(set(snap.marcas) - {r.brand for r in snap.rows})
    if ausentes:
        avisos.append(f"contas sem nenhum pedido na janela: {ausentes}")
    return avisos


def read_source(conn, marcas: tuple[str, ...], inicio: date, fim: date, hoje: date,
                captured_at: datetime, max_ingested_at: datetime) -> Snapshot:
    linhas = conn.execute(SQL_DIARIO, {"inicio": inicio, "fim": fim,
                                       "marcas": list(marcas)}).mappings().all()
    rows = montar_linhas([dict(r) for r in linhas], marcas, hoje)
    return Snapshot(captured_at=captured_at, inicio=inicio, fim=fim, marcas=marcas,
                    max_ingested_at=max_ingested_at, rows=rows)


# --------------------------------------------------------------------------- #
# Destino                                                                      #
# --------------------------------------------------------------------------- #

_COLS = ("date", "brand", "orders", "units_sold", "canceled_orders", "delivered_orders",
         "to_return_orders", "unpaid_orders", "seller_shipping_cost", "product_subtotal",
         "source", "source_run_id", "source_captured_at", "source_max_ingested_at", "is_partial")

#: Conteudo de uma linha. `source_run_id`/`captured_at`/`max_ingested_at` ficam
#: de fora: mudam a cada execucao mesmo com dado identico.
_COLS_CONTEUDO = _COLS[:10] + ("is_partial",)


def _linha(r: LinhaDiaria, run_id: str, snap: Snapshot) -> tuple:
    return (r.date, r.brand, r.orders, r.units_sold, r.canceled_orders, r.delivered_orders,
            r.to_return_orders, r.unpaid_orders, r.seller_shipping_cost, r.product_subtotal,
            SOURCE_API, run_id, snap.captured_at, snap.max_ingested_at, r.is_partial)


def _conteudo(r: LinhaDiaria) -> tuple:
    return (r.date, r.brand, r.orders, r.units_sold, r.canceled_orders, r.delivered_orders,
            r.to_return_orders, r.unpaid_orders, round(r.seller_shipping_cost, 2),
            round(r.product_subtotal, 2), r.is_partial)


def tabela_existe(conn) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": SHADOW}).scalar())


def conteudo_publicado(conn, snap: Snapshot) -> set[tuple]:
    linhas = conn.execute(text(f"""
        SELECT {', '.join(_COLS_CONTEUDO)} FROM {SHADOW}
         WHERE brand = ANY(:marcas) AND date BETWEEN :inicio AND :fim
    """), {"marcas": list(snap.marcas), "inicio": snap.inicio, "fim": snap.fim}).mappings().all()
    return {(r["date"], r["brand"], int(r["orders"]), int(r["units_sold"]), int(r["canceled_orders"]),
             int(r["delivered_orders"]), int(r["to_return_orders"]), int(r["unpaid_orders"]),
             round(float(r["seller_shipping_cost"]), 2), round(float(r["product_subtotal"]), 2),
             bool(r["is_partial"])) for r in linhas}


def pedidos_publicados_por_marca(conn, snap: Snapshot) -> dict[str, int]:
    linhas = conn.execute(text(f"""
        SELECT brand, SUM(orders) AS n FROM {SHADOW}
         WHERE brand = ANY(:marcas) AND date BETWEEN :inicio AND :fim
         GROUP BY 1
    """), {"marcas": list(snap.marcas), "inicio": snap.inicio, "fim": snap.fim}).mappings().all()
    return {r["brand"]: int(r["n"] or 0) for r in linhas}


def verificar_colapso(antes: dict[str, int], snap: Snapshot) -> None:
    """Marca com pedidos publicados que volta com menos de `MIN_PEDIDOS_RATIO` aborta."""
    agora: dict[str, int] = {}
    for r in snap.rows:
        agora[r.brand] = agora.get(r.brand, 0) + r.orders
    for marca, n_antes in antes.items():
        n = agora.get(marca, 0)
        if n_antes > 0 and n < n_antes * MIN_PEDIDOS_RATIO:
            raise ShopeeDailyShadowError(
                f"colapso da fonte em {marca}: {n} pedido(s) na janela contra {n_antes} publicados "
                f"(piso {MIN_PEDIDOS_RATIO:.0%}). Publicacao abortada — ausencia nao vira zero.")


def publish_in_transaction(conn, snap: Snapshot, run_id: str) -> dict:
    """DELETE + INSERT da janela, SO' na tabela de shadow. UMA transacao."""
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    verificar_colapso(pedidos_publicados_por_marca(conn, snap), snap)
    conn.execute(text(f"DELETE FROM {SHADOW} WHERE brand = ANY(:marcas) AND date BETWEEN :inicio AND :fim"),
                 {"marcas": list(snap.marcas), "inicio": snap.inicio, "fim": snap.fim})
    # executemany do SQLAlchemy, e nao execute_values do psycopg2: vale em
    # psycopg2 e psycopg3 (o SQLAlchemy 2.1 passou a preferir o psycopg3).
    conn.execute(text(f"INSERT INTO {SHADOW} ({', '.join(_COLS)}) "
                      f"VALUES ({', '.join(':' + c for c in _COLS)})"),
                 [dict(zip(_COLS, _linha(r, run_id, snap))) for r in snap.rows])
    # Reconciliacao PRE-COMMIT: o que se le' de volta tem de ser o que foi montado.
    if conteudo_publicado(conn, snap) != {_conteudo(r) for r in snap.rows}:
        raise ShopeeDailyShadowError("reconciliacao pre-commit falhou: destino difere da fonte lida")
    return {"linhas_publicadas": len(snap.rows)}


def _publicar(snap: Snapshot, run_id: str) -> dict:
    with local_engine().begin() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
            raise ConcurrentRunError(f"advisory lock {ADVISORY_LOCK_KEY} ocupado: outra execucao em curso")
        if not tabela_existe(conn):
            raise ShopeeDailyShadowError(f"{SHADOW} nao existe: a migration 027 nao foi aplicada")
        # NO-OP dentro do lock: evita corrida com outra execucao.
        if conteudo_publicado(conn, snap) == {_conteudo(r) for r in snap.rows}:
            return {"noop": True, "linhas_publicadas": 0,
                    "motivo": "conteudo da fonte identico ao ja' publicado na janela"}
        return publish_in_transaction(conn, snap, run_id)


# --------------------------------------------------------------------------- #
# Auditoria — conexao INDEPENDENTE, commit proprio                              #
# --------------------------------------------------------------------------- #

def _audit_conn():
    return LocalSession().get_bind().raw_connection()


def audit_start(rows_extracted: int) -> int:
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit.source_sync_run
                       (source_name, marketplace_id, loja_id, status, started_at, rows_extracted)
                   VALUES (%s, %s, NULL, 'running', NOW(), %s) RETURNING sync_run_id""",
                (AUDIT_SOURCE, AUDIT_MARKETPLACE_ID, rows_extracted))
            sync_run_id = cur.fetchone()[0]
        conn.commit()
        return sync_run_id
    finally:
        conn.close()


def audit_finish(sync_run_id: int, status: str, rows_loaded: int | None = None,
                 error_message: str | None = None) -> None:
    if status not in AUDIT_STATUSES:
        raise ShopeeDailyShadowError(f"status de auditoria fora do dominio: {status!r}")
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE audit.source_sync_run
                      SET status = %s, finished_at = NOW(), rows_loaded = %s, error_message = %s
                    WHERE sync_run_id = %s""",
                (status, rows_loaded, (error_message or None) and error_message[:AUDIT_ERRO_MAX_CHARS],
                 sync_run_id))
            if cur.rowcount != 1:
                raise ShopeeDailyShadowError("UPDATE de auditoria nao afetou exatamente 1 linha")
        conn.commit()
    finally:
        conn.close()


def coletar(marcas: tuple[str, ...], inicio: date, fim: date, hoje: date,
            agora_utc: datetime) -> Snapshot:
    """Leitura READ ONLY do Data Mart: frescor primeiro, depois o dado."""
    dm = DataMartSession()
    try:
        # Primeira instrucao da transacao: o banco recusa qualquer escrita.
        # SQL, e nao `set_session(readonly=True)`, para valer em psycopg2 e psycopg3.
        dm.connection().execute(text("SET TRANSACTION READ ONLY"))
        max_ing = validate_frescor(dm.connection(), marcas, agora_utc)
        return read_source(dm.connection(), marcas, inicio, fim, hoje, agora_utc, max_ing)
    finally:
        dm.rollback()
        dm.close()


def validar_marcas(marcas: tuple[str, ...] | None) -> tuple[str, ...]:
    alvo = tuple(marcas) if marcas else CONTAS_API
    fora = sorted(set(alvo) - set(CONTAS_API))
    if fora:
        raise ShopeeDailyShadowError(
            f"marcas fora da allowlist da API: {fora}. Cobertas: {list(CONTAS_API)}. "
            "A kokeshi e' manual por nao ter aplicacao Shopee cadastrada.")
    return alvo


def run(apply: bool = False, desde: date | None = None, marcas: tuple[str, ...] | None = None,
        agora: datetime | None = None) -> dict:
    agora_utc = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hoje = hoje_brt(agora_utc)
    alvo = validar_marcas(marcas)
    inicio, fim = janela(hoje, desde)
    run_id = uuid.uuid4().hex

    snap = coletar(alvo, inicio, fim, hoje, agora_utc)
    avisos = validate_contract(snap)

    por_marca: dict[str, dict] = {}
    for r in snap.rows:
        d = por_marca.setdefault(r.brand, {"dias": 0, "orders": 0, "units_sold": 0,
                                           "canceled_orders": 0, "dias_parciais": 0})
        d["dias"] += 1
        d["orders"] += r.orders
        d["units_sold"] += r.units_sold
        d["canceled_orders"] += r.canceled_orders
        d["dias_parciais"] += int(r.is_partial)
    resumo = {
        "run_id": run_id, "hoje_brt": str(hoje), "janela": [str(inicio), str(fim)],
        "marcas": list(alvo), "linhas_lidas": len(snap.rows),
        "carga_mais_antiga_da_fonte": snap.max_ingested_at.isoformat(),
        "por_marca": por_marca, "avisos": avisos, "aplicado": False,
    }
    if not apply:
        resumo["modo"] = "dry-run"
        return resumo

    sync_run_id = audit_start(len(snap.rows))
    try:
        pub = _publicar(snap, run_id)
    except BaseException as exc:
        audit_finish(sync_run_id, "failed", error_message=sanitizar(exc))
        raise
    audit_finish(sync_run_id, "success", rows_loaded=pub["linhas_publicadas"])
    resumo.update(pub)
    resumo.update({"modo": "apply", "aplicado": not pub.get("noop", False),
                   "noop": bool(pub.get("noop", False)), "sync_run_id": sync_run_id})
    return resumo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Shadow da familia de pedidos Shopee via API "
                                            "(marts.fact_shopee_daily_api_shadow)")
    p.add_argument("--apply", action="store_true", help="publica. Sem esta flag o run e' dry-run.")
    p.add_argument("--desde", type=date.fromisoformat, default=None,
                   help=f"inicio da janela (YYYY-MM-DD). Default: {DIAS_JANELA} dias ate' D-1")
    p.add_argument("--marcas", nargs="+", default=None, help=f"subconjunto de {list(CONTAS_API)}")
    args = p.parse_args(argv)
    try:
        res = run(apply=args.apply, desde=args.desde,
                  marcas=tuple(args.marcas) if args.marcas else None)
    except ShopeeDailyShadowError as exc:
        logger.error("%s", sanitizar(exc))
        return 1
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
