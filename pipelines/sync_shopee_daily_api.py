"""`daily_shopee` — familia de pedidos da Shopee via API na fato diaria do Gerencial.

FRESHNESS-SHOPEE-DAILY-1. Le' os pedidos que o `shopee_orders_etl` do Airflow ja'
grava no Data Mart a cada 6h (`silver.stg_shopee_orders` / `_order_items`) e
publica em `marts.fact_marketplace_daily_performance` (Neon), o MESMO destino de
`daily_ml` e `daily_tiktok`. Nao cria coleta nova da API nem toca em token.

A leitura da fonte e' a do shadow (`pipelines.sync_shopee_daily_api_shadow`), ja'
reconciliada contra o export manual: frescor por conta (falha fechada), um pedido
por linha antes de somar itens, pedido nao cancelado sem item ou sem frete aborta.

🔑 DONO DE COLUNA, POR LOJA
---------------------------
A fato nao tem coluna de procedencia. Para nao misturar fontes, cada coluna de
cada loja tem UM dono:

* lojas com API (apice, barbours, lescent, rituaria — `LOJAS_COM_API`):
  - ESTE modulo escreve `COLUNAS_API` e anula `COLUNAS_ANULADAS`;
  - os carregadores de arquivo deixam de escrever essas colunas para elas
    (`daily_performance.colunas_da_api_por_loja`);
  - funil (visitantes, conversao, compradores) e Ads seguem dos arquivos;
* kokeshi: inteira pelos arquivos. Este modulo nunca le' nem escreve a loja 3.

🔴 O QUE ESTA PUBLICACAO MEDE
------------------------------
Por (dia BRT de CRIACAO do pedido, loja) — o mesmo eixo do export:

  gmv                 soma de `item_total` (preco com desconto x quantidade) dos
                      pedidos nao cancelados. Valor de produto, SEM frete.
                      ⚠️ NAO desconta reembolso/devolucao: a API de pedidos nao
                      tem esse dado. O GMV do shop-stats (arquivo) desconta, e
                      mede "vendas" de vitrine; medido em jun/2026 o daqui fica
                      +1,9% a +4,2% acima. Pedidos e unidades casam exato.
  orders              pedidos com status <> cancelled
  units_sold          soma de quantity desses pedidos
  canceled_orders     pedidos cancelled
  delivered_orders    pedidos completed
  seller_shipping_cost  soma de estimated_shipping_fee dos nao cancelados
  avg_ticket          gmv / orders (NULL se orders = 0)
  cancel_rate_pct     canceled / (orders + canceled) x 100
  shipping_pct_of_gmv seller_shipping_cost / gmv x 100 (NULL se gmv = 0)

`COLUNAS_ANULADAS` (devolvidos, repasse, taxas e seus percentuais) sao da familia
de pedidos mas NAO existem na API de pedidos (repasse e taxa vivem no escrow, nao
ingerido). Mantê-las do arquivo misturaria duas contagens de pedido na mesma
linha; zerar seria inventar. Ficam NULL.

Dia sem nenhum pedido criado na fonte NAO gera linha nem altera a publicada:
ausencia nao vira zero. Dia com pedidos todos cancelados publica `orders = 0`:
zero provado pela fonte.

🔴 JANELA MOVEL DE 60 DIAS
--------------------------
Status muda por semanas (cancelamento tardio, conclusao). Cada execucao
recalcula `DIAS_JANELA` dias ate' D-1 e so' grava as linhas cujo conteudo mudou
(idempotente: a segunda execucao igual grava zero linha). Datas anteriores:
`--desde YYYY-MM-DD`, explicito.

TRAVAS
------
* `fato_diaria_lock(3)` — o MESMO advisory lock dos carregadores de arquivo da
  Shopee: nunca escrevem ao mesmo tempo;
* guarda de colapso: loja que volta com menos de 80% dos pedidos publicados na
  janela aborta (ausencia nao vira zero);
* reconciliacao PRE-COMMIT: o que se le' de volta tem de ser o que foi montado;
* `--apply` so' escreve com `SHOPEE_DAILY_API_ENABLED=true` no ambiente; sem
  ela vira dry-run declarado e sai 0.

USO
---
    python -m pipelines.sync_shopee_daily_api                      # dry-run + reconciliacao
    python -m pipelines.sync_shopee_daily_api --desde 2026-01-01   # dry-run do backfill
    SHOPEE_DAILY_API_ENABLED=true python -m pipelines.sync_shopee_daily_api --apply

Saida: 0 ok (inclui dry-run, flag desligada e NO-OP); 1 contrato/falha, nada
escrito; 75 lock da fato Shopee ocupado, nada lido nem escrito.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from pipelines import sync_shopee_daily_api_shadow as SH
from pipelines.common.db import LocalSession, local_engine
from pipelines.common.logging import get_logger
from pipelines.ingestion.fato_diaria_lock import (
    EXIT_CODE_LOCK_UNAVAILABLE,
    FatoDiariaLockUnavailable,
    fato_diaria_lock,
)
from pipelines.transforms.shopee_orders_daily import (
    BRAND_TO_LOJA,
    CONTAS_COM_API,
    EMPRESA_ID,
    LOJAS_COM_API,
    MARKETPLACE_ID,
)

logger = get_logger(__name__)

FATO = "marts.fact_marketplace_daily_performance"
DIAS_JANELA = 60
FLAG_HABILITADO = "SHOPEE_DAILY_API_ENABLED"
AUDIT_SOURCE = "shopee_daily_api"
TARGET_STATEMENT_TIMEOUT_MS = 180_000
AUDIT_ERRO_MAX_CHARS = 4000

COLUNAS_INTEIRAS = ("orders", "units_sold", "canceled_orders", "delivered_orders", "returned_orders")
COLUNAS_API = ("gmv", "orders", "units_sold", "avg_ticket", "canceled_orders", "cancel_rate_pct",
               "delivered_orders", "seller_shipping_cost", "shipping_pct_of_gmv")
COLUNAS_ANULADAS = ("returned_orders", "total_settlement", "total_fees", "avg_fee_pct",
                    "avg_settlement_pct")
COLUNAS_DONAS = COLUNAS_API + COLUNAS_ANULADAS


class ShopeeDailyApiError(RuntimeError):
    """Falha de contrato ou de publicacao. Nada foi escrito."""


# --------------------------------------------------------------------------- #
# Grao da fato                                                                 #
# --------------------------------------------------------------------------- #

def _pct(numerador: float, denominador: float):
    return round(numerador / denominador * 100, 2) if denominador else None


def linha_da_fato(r: SH.LinhaDiaria) -> dict:
    """Uma linha do shadow -> as colunas de que a API e' dona na fato."""
    if r.brand not in CONTAS_COM_API:
        raise ShopeeDailyApiError(f"conta fora da allowlist da API: {r.brand!r}")
    gmv = round(r.product_subtotal, 2)
    frete = round(r.seller_shipping_cost, 2)
    linha = {
        "date": r.date, "loja_id": BRAND_TO_LOJA[r.brand],
        "gmv": gmv, "orders": r.orders, "units_sold": r.units_sold,
        "avg_ticket": round(gmv / r.orders, 2) if r.orders else None,
        "canceled_orders": r.canceled_orders,
        "cancel_rate_pct": _pct(r.canceled_orders, r.orders + r.canceled_orders),
        "delivered_orders": r.delivered_orders,
        "seller_shipping_cost": frete,
        "shipping_pct_of_gmv": _pct(frete, gmv),
    }
    linha.update({c: None for c in COLUNAS_ANULADAS})
    return linha


def _norm(coluna: str, valor):
    if valor is None:
        return None
    if coluna in COLUNAS_INTEIRAS:
        return int(valor)
    return round(Decimal(str(valor)), 2)


def conteudo(linha: dict) -> tuple:
    return tuple(_norm(c, linha[c]) for c in COLUNAS_DONAS)


# --------------------------------------------------------------------------- #
# Destino                                                                      #
# --------------------------------------------------------------------------- #

def publicado(conn, inicio: date, fim: date) -> dict:
    """(date, loja_id) -> linha publicada, SO' das lojas com API, na janela."""
    linhas = conn.execute(text(f"""
        SELECT date, loja_id, {', '.join(COLUNAS_DONAS)} FROM {FATO}
         WHERE marketplace_id = :mkt AND loja_id = ANY(:lojas) AND date BETWEEN :inicio AND :fim
    """), {"mkt": MARKETPLACE_ID, "lojas": sorted(LOJAS_COM_API), "inicio": inicio,
           "fim": fim}).mappings().all()
    return {(r["date"], r["loja_id"]): dict(r) for r in linhas}


def verificar_colapso(antes: dict, linhas: list[dict]) -> None:
    """Loja com pedidos publicados na janela que volta com < 80% aborta."""
    n_antes: dict[int, int] = defaultdict(int)
    for (_d, loja), r in antes.items():
        n_antes[loja] += int(r["orders"] or 0)
    n_agora: dict[int, int] = defaultdict(int)
    for r in linhas:
        n_agora[r["loja_id"]] += r["orders"]
    for loja, n in n_antes.items():
        if n > 0 and n_agora.get(loja, 0) < n * SH.MIN_PEDIDOS_RATIO:
            raise ShopeeDailyApiError(
                f"colapso da fonte na loja {loja}: {n_agora.get(loja, 0)} pedido(s) na janela contra "
                f"{n} publicados (piso {SH.MIN_PEDIDOS_RATIO:.0%}). Nada escrito — ausencia nao vira zero.")


def a_gravar(antes: dict, linhas: list[dict]) -> list[dict]:
    """So' as linhas cujo conteudo difere do publicado (idempotencia)."""
    return [r for r in linhas
            if (r["date"], r["loja_id"]) not in antes
            or conteudo(antes[(r["date"], r["loja_id"])]) != conteudo(r)]


_COLS_INSERT = ("date", "loja_id", "marketplace_id", "empresa_id", *COLUNAS_DONAS, "source_updated_at")

UPSERT_SQL = text(f"""
    INSERT INTO {FATO} ({', '.join(_COLS_INSERT)})
    VALUES ({', '.join(':' + c for c in _COLS_INSERT)})
    ON CONFLICT (date, loja_id, marketplace_id) DO UPDATE SET
        empresa_id = EXCLUDED.empresa_id,
        {', '.join(f'{c} = EXCLUDED.{c}' for c in COLUNAS_DONAS)},
        source_updated_at = EXCLUDED.source_updated_at,
        ingested_at = NOW()
""")


def publicar_em_transacao(conn, linhas: list[dict], inicio: date, fim: date,
                          source_updated_at: datetime) -> dict:
    """Upsert SO' das colunas donas, SO' do que mudou, numa transacao do chamador."""
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    antes = publicado(conn, inicio, fim)
    verificar_colapso(antes, linhas)
    mudou = a_gravar(antes, linhas)
    if mudou:
        conn.execute(UPSERT_SQL, [{**r, "marketplace_id": MARKETPLACE_ID, "empresa_id": EMPRESA_ID,
                                   "source_updated_at": source_updated_at} for r in mudou])
    # Reconciliacao PRE-COMMIT: cada linha da fonte tem de estar no destino como montada.
    depois = publicado(conn, inicio, fim)
    divergentes = [(r["date"], r["loja_id"]) for r in linhas
                   if (r["date"], r["loja_id"]) not in depois
                   or conteudo(depois[(r["date"], r["loja_id"])]) != conteudo(r)]
    if divergentes:
        raise ShopeeDailyApiError(
            f"reconciliacao pre-commit falhou em {len(divergentes)} linha(s), ex.: {divergentes[:3]}")
    return {"linhas_gravadas": len(mudou), "linhas_identicas": len(linhas) - len(mudou)}


def comparar_por_mes(antes: dict, linhas: list[dict]) -> list[dict]:
    """Publicado x calculado por (loja, mes): pedidos, unidades, cancelados, GMV."""
    acc: dict = defaultdict(lambda: defaultdict(Decimal))
    for (d, loja), r in antes.items():
        k = (loja, d.strftime("%Y-%m"))
        for c in ("orders", "units_sold", "canceled_orders", "gmv"):
            if r[c] is not None:
                acc[k][f"pub_{c}"] += Decimal(str(r[c]))
    for r in linhas:
        k = (r["loja_id"], r["date"].strftime("%Y-%m"))
        for c in ("orders", "units_sold", "canceled_orders", "gmv"):
            acc[k][f"api_{c}"] += Decimal(str(r[c]))
    saida = []
    for (loja, mes), v in sorted(acc.items()):
        item = {"loja_id": loja, "mes": mes}
        for c in ("orders", "units_sold", "canceled_orders", "gmv"):
            pub, api = v.get(f"pub_{c}", Decimal(0)), v.get(f"api_{c}", Decimal(0))
            item[c] = {"publicado": float(pub), "api": float(api),
                       "dif_pct": float(round((api / pub - 1) * 100, 2)) if pub else None}
        saida.append(item)
    return saida


def dias_publicados_sem_fonte(antes: dict, linhas: list[dict]) -> list[str]:
    """Linha publicada com pedidos num dia em que a fonte nao tem pedido: NAO e' tocada."""
    chaves = {(r["date"], r["loja_id"]) for r in linhas}
    return sorted(f"{loja}/{d}" for (d, loja), r in antes.items()
                  if (d, loja) not in chaves and (r["orders"] or 0) > 0)


# --------------------------------------------------------------------------- #
# Auditoria — conexao independente, commit proprio                             #
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
                (AUDIT_SOURCE, MARKETPLACE_ID, rows_extracted))
            sync_run_id = cur.fetchone()[0]
        conn.commit()
        return sync_run_id
    finally:
        conn.close()


def audit_finish(sync_run_id: int, status: str, rows_loaded: int | None = None,
                 inicio: date | None = None, fim: date | None = None,
                 error_message: str | None = None) -> None:
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE audit.source_sync_run
                      SET status = %s, finished_at = NOW(), rows_loaded = %s, error_message = %s,
                          source_min_date = %s, source_max_date = %s
                    WHERE sync_run_id = %s""",
                (status, rows_loaded, (error_message or None) and error_message[:AUDIT_ERRO_MAX_CHARS],
                 inicio, fim, sync_run_id))
            if cur.rowcount != 1:
                raise ShopeeDailyApiError("UPDATE de auditoria nao afetou exatamente 1 linha")
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Execucao                                                                     #
# --------------------------------------------------------------------------- #

def coletar_fonte(inicio: date, fim: date, hoje: date, agora_utc: datetime) -> SH.Snapshot:
    """Data Mart, READ ONLY, frescor por conta primeiro. Ponto de injecao dos testes."""
    return SH.coletar(CONTAS_COM_API, inicio, fim, hoje, agora_utc)


#: Conexao do advisory lock. `None` = a conexao padrao de `fato_diaria_lock`.
_lock_connect = None


def _ler_e_montar(inicio, fim, hoje, agora_utc):
    snap = coletar_fonte(inicio, fim, hoje, agora_utc)
    avisos = SH.validate_contract(snap)
    linhas = [linha_da_fato(r) for r in snap.rows]
    if {r["loja_id"] for r in linhas} - LOJAS_COM_API:
        raise ShopeeDailyApiError("linha de loja sem API montada — a kokeshi nunca entra")
    return snap, avisos, linhas


def run(apply: bool = False, desde: date | None = None, agora: datetime | None = None,
        ambiente: dict | None = None, incluir_hoje: bool = True) -> dict:
    # Dia corrente (parcial) entra por padrao desde 29/10/2026, como nas fontes
    # de arquivo da Shopee; `--ate-ontem` volta a D-1. A rodada seguinte regrava
    # D0 com o dia fechado.
    agora_utc = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hoje = SH.hoje_brt(agora_utc)
    inicio, fim = SH.janela(hoje, desde, DIAS_JANELA, incluir_hoje=incluir_hoje)
    habilitado = (ambiente if ambiente is not None else os.environ).get(FLAG_HABILITADO) == "true"
    resumo = {"run_id": uuid.uuid4().hex, "hoje_brt": str(hoje), "janela": [str(inicio), str(fim)],
              "lojas": sorted(LOJAS_COM_API), "aplicado": False, "incluir_hoje": incluir_hoje}

    if not (apply and habilitado):
        snap, avisos, linhas = _ler_e_montar(inicio, fim, hoje, agora_utc)
        with local_engine().connect() as conn:
            antes = publicado(conn, inicio, fim)
        resumo.update({
            "modo": "dry-run" if not apply else f"dry-run ({FLAG_HABILITADO} != 'true')",
            "linhas_da_fonte": len(linhas), "linhas_que_mudariam": len(a_gravar(antes, linhas)),
            "carga_mais_antiga_da_fonte": snap.max_ingested_at.isoformat(), "avisos": avisos,
            "dias_publicados_sem_fonte": dias_publicados_sem_fonte(antes, linhas),
            "publicado_x_api_por_mes": comparar_por_mes(antes, linhas),
        })
        return resumo

    with fato_diaria_lock(MARKETPLACE_ID, connect=_lock_connect):
        snap, avisos, linhas = _ler_e_montar(inicio, fim, hoje, agora_utc)
        sync_run_id = audit_start(len(linhas))
        try:
            with local_engine().begin() as conn:
                pub = publicar_em_transacao(conn, linhas, inicio, fim, snap.max_ingested_at)
        except BaseException as exc:
            audit_finish(sync_run_id, "failed", inicio=inicio, fim=fim, error_message=SH.sanitizar(exc))
            raise
        audit_finish(sync_run_id, "success", rows_loaded=pub["linhas_gravadas"], inicio=inicio, fim=fim)
    resumo.update(pub)
    resumo.update({"modo": "apply", "aplicado": pub["linhas_gravadas"] > 0,
                   "noop": pub["linhas_gravadas"] == 0, "sync_run_id": sync_run_id,
                   "linhas_da_fonte": len(linhas), "avisos": avisos,
                   "carga_mais_antiga_da_fonte": snap.max_ingested_at.isoformat()})
    return resumo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="daily_shopee: familia de pedidos Shopee (API) na fato "
                                            "diaria do Gerencial, lojas com API")
    p.add_argument("--apply", action="store_true",
                   help=f"publica (exige {FLAG_HABILITADO}=true). Sem esta flag o run e' dry-run.")
    p.add_argument("--desde", type=date.fromisoformat, default=None,
                   help=f"inicio da janela (YYYY-MM-DD). Default: {DIAS_JANELA} dias ate' D-1")
    teto = p.add_mutually_exclusive_group()
    teto.add_argument("--incluir-hoje", action="store_true",
                      help="mantido por compatibilidade: o dia corrente ja' entra por padrao")
    teto.add_argument("--ate-ontem", action="store_true",
                      help="publica so' ate' D-1")
    args = p.parse_args(argv)
    try:
        res = run(apply=args.apply, desde=args.desde, incluir_hoje=not args.ate_ontem)
    except FatoDiariaLockUnavailable as exc:
        logger.error("%s", exc)
        return EXIT_CODE_LOCK_UNAVAILABLE
    except (ShopeeDailyApiError, SH.ShopeeDailyShadowError) as exc:
        logger.error("%s", SH.sanitizar(exc))
        return 1
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
