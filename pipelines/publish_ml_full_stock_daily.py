"""ML-FULL-STOCK-PRODUCTIZATION-1 — publica o estoque Full do ML no Neon. INERTE.

Le a fotografia do airflow (`raw.ml_full_*`, Data Mart, sessao READ ONLY)
pelo mesmo leitor do dry-run (`sync_ml_full_stock_daily.ler_fonte`), calcula
os indicadores por inventario (`pipelines.transforms.ml_full_stock`) e, SO'
com as tres travas abaixo, publica nas fatos da migration 029.

TRES TRAVAS PARA ESCREVER (todas obrigatorias)
-----------------------------------------------
1. `--apply` na linha de comando;
2. `ML_FULL_STOCK_PUBLISH_ENABLED=true` no ambiente;
3. as tres tabelas da 029 existindo no Neon (`to_regclass`) -- a migration
   NAO foi aplicada.
Sem qualquer uma delas o run e' dry-run (ou recusa explicita), sem escrita.

PUBLICACAO
----------
- `engine.begin()`: uma transacao; `pg_try_advisory_xact_lock` e' o PRIMEIRO
  comando (devolvido pelo banco no COMMIT e no ROLLBACK).
- So' marcas com fotografia no dia. DELETE do (ref_date, marca) + INSERT:
  reexecutar substitui, nunca duplica, e nunca toca marca sem fotografia.
- Reconciliacao PRE-COMMIT: linhas e soma do disponivel no destino tem de
  bater com o calculado; qualquer diferenca aborta.
- Auditoria em `audit.source_sync_run` numa conexao PROPRIA: o rollback das
  fatos nao apaga o registro de que a execucao existiu e falhou.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import date, datetime

from psycopg2.extras import execute_values
from sqlalchemy import text

from pipelines import sync_ml_full_stock_daily as fonte_mod
from pipelines.common.db import DataMartSession, LocalSession, local_engine
from pipelines.common.logging import get_logger
from pipelines.transforms import ml_full_stock as T

logger = get_logger(__name__)

FACT_INVENTARIO = "marts.fact_ml_full_stock_inventory_daily"
FACT_STATUS = "marts.fact_ml_full_stock_status_daily"
FACT_MARCA = "marts.fact_ml_full_stock_brand_daily"
TABELAS = (FACT_INVENTARIO, FACT_STATUS, FACT_MARCA)
FLAG_AMBIENTE = "ML_FULL_STOCK_PUBLISH_ENABLED"

ADVISORY_LOCK_KEY = 916140029
TARGET_STATEMENT_TIMEOUT_MS = 180_000
PAGE_SIZE = 500
#: `audit.source_sync_run.marketplace_id`: 1=tiktok, 2=ml, 3=shopee (documentado
#: em `sync_serving_snapshots.py` e no seed `db/seeds/01_marketplaces.sql`).
#: ⚠️ `sync_ml_fulfillment_daily` usa 1 para o ML: defeito a corrigir la'.
AUDIT_MARKETPLACE_ID = 2
AUDIT_SOURCE = "ml_full_stock_daily"
AUDIT_ERRO_MAX_CHARS = 4000

COLS_INV = ("ref_date", "brand", "seller_id", "inventory_id", "read_status", "disponivel",
            "nao_disponivel", "transfer", "total_coerente", "detalhe_coerente", "eh_full", "anuncios",
            "anuncios_ativos", "anuncios_full", "unidades_28d", "media_diaria_28d",
            "cobertura_torre_dias", "classificacao_torre", "vinculo_vendas", "source_run_id")
COLS_STATUS = ("ref_date", "brand", "seller_id", "inventory_id", "status_raw", "quantidade",
               "source_run_id")
COLS_MARCA = ("ref_date", "brand", "inventarios", "inventarios_full_lidos",
              "inventarios_compartilhados", "disponivel_full", "nao_disponivel_full",
              "transfer_full", "unidades_28d_alocadas", "unidades_28d_item_varios_inventarios",
              "unidades_28d_sem_inventory_id", "unidades_28d_fora_da_ponte",
              "cobertura_da_alocacao", "source_run_id")


class PublicacaoRecusada(RuntimeError):
    """Trava de escrita nao satisfeita: nada foi gravado."""


class ConcurrentRunError(RuntimeError):
    pass


class ReconciliacaoFalhou(RuntimeError):
    pass


def sanitizar(exc) -> str:
    return f"{type(exc).__name__}: {str(exc)[:AUDIT_ERRO_MAX_CHARS]}"


def flag_ligada() -> bool:
    return os.environ.get(FLAG_AMBIENTE, "").strip().lower() == "true"


def montar(fonte, ref_date: date, run_id: str) -> dict:
    """Linhas prontas para as tres fatos. So' marcas com fotografia no dia."""
    linhas, resumo = T.indicadores(fonte["snapshot"], fonte["status"], fonte["ponte"], fonte["vendas"])
    marcas = sorted({l["brand"] for l in linhas})
    ok = {(l["brand"], l["seller_id"], l["inventory_id"]) for l in linhas if l["read_status"] == "OK"}
    inv = [tuple({**l, "ref_date": ref_date, "source_run_id": run_id}[c] for c in COLS_INV) for l in linhas]
    st = [(ref_date, s["brand"], s["seller_id"], s["inventory_id"], s["status_raw"], int(s["quantity"]), run_id)
          for s in fonte["status"] if (s["brand"], s["seller_id"], s["inventory_id"]) in ok]
    mar = []
    for m in marcas:
        r = resumo[m]
        na = r["unidades_28d_nao_alocadas"]
        mar.append((ref_date, m, r["inventarios"], r["inventarios_full_lidos"],
                    r["inventarios_compartilhados"], r["disponivel_full"], r["nao_disponivel_full"],
                    r["transfer_full"], r["unidades_28d_alocadas"],
                    na.get("item_com_varios_inventarios", 0), na.get("anuncio_sem_inventory_id", 0),
                    na.get("anuncio_fora_da_ponte", 0), r["cobertura_da_alocacao"], run_id))
    soma_disp = sum(l["disponivel"] or 0 for l in linhas if l["read_status"] == "OK")
    return {"marcas": marcas, "inventario": inv, "status": st, "marca": mar,
            "esperado": {"inventarios": len(inv), "soma_disponivel": soma_disp}}


def _inserir(conn, tabela, cols, linhas):
    if not linhas:
        return
    with conn.connection.cursor() as cur:
        execute_values(cur, f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES %s", linhas, page_size=PAGE_SIZE)


def publicar_em_transacao(conn, pacote, ref_date: date) -> dict:
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    p = {"ref_date": ref_date, "marcas": pacote["marcas"]}
    for tabela in (FACT_STATUS, FACT_INVENTARIO, FACT_MARCA):
        conn.execute(text(f"DELETE FROM {tabela} WHERE ref_date = :ref_date AND brand = ANY(:marcas)"), p)
    _inserir(conn, FACT_INVENTARIO, COLS_INV, pacote["inventario"])
    _inserir(conn, FACT_STATUS, COLS_STATUS, pacote["status"])
    _inserir(conn, FACT_MARCA, COLS_MARCA, pacote["marca"])
    destino = conn.execute(text(f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(disponivel) FILTER (WHERE read_status = 'OK'), 0) AS soma
          FROM {FACT_INVENTARIO} WHERE ref_date = :ref_date AND brand = ANY(:marcas)"""), p).mappings().one()
    esp = pacote["esperado"]
    if destino["n"] != esp["inventarios"] or int(destino["soma"]) != esp["soma_disponivel"]:
        raise ReconciliacaoFalhou(
            f"destino n={destino['n']} soma={destino['soma']}; esperado n={esp['inventarios']} "
            f"soma={esp['soma_disponivel']}")
    return {"inventarios_publicados": esp["inventarios"], "status_publicados": len(pacote["status"]),
            "marcas_publicadas": pacote["marcas"], "disponivel_publicado": esp["soma_disponivel"]}


def _publicar(pacote, ref_date):
    with local_engine().begin() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
            raise ConcurrentRunError(f"advisory lock {ADVISORY_LOCK_KEY} ocupado")
        return publicar_em_transacao(conn, pacote, ref_date)


def tabelas_destino_existem() -> bool:
    with local_engine().connect() as conn:
        linha = conn.execute(text("SELECT " + ", ".join(
            f"to_regclass('{t}') IS NOT NULL" for t in TABELAS))).fetchone()
    return bool(linha) and all(linha)


def _audit_conn():
    return LocalSession().get_bind().raw_connection()


def audit_start(rows_extracted: int) -> int:
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO audit.source_sync_run
                               (source_name, marketplace_id, loja_id, status, started_at, rows_extracted)
                           VALUES (%s, %s, NULL, 'running', NOW(), %s) RETURNING sync_run_id""",
                        (AUDIT_SOURCE, AUDIT_MARKETPLACE_ID, rows_extracted))
            sid = cur.fetchone()[0]
        conn.commit()
        return sid
    finally:
        conn.close()


def audit_finish(sid: int, status: str, rows_loaded=None, error_message=None) -> None:
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE audit.source_sync_run SET status = %s, finished_at = NOW(),
                               rows_loaded = %s, error_message = %s WHERE sync_run_id = %s""",
                        (status, rows_loaded, error_message, sid))
            if cur.rowcount != 1:
                raise RuntimeError("UPDATE de auditoria nao afetou exatamente 1 linha")
        conn.commit()
    finally:
        conn.close()


def _ler_fonte(ref_date):
    dm = DataMartSession()
    try:
        dm.connection().connection.set_session(readonly=True)
        return fonte_mod.ler_fonte(dm.connection(), ref_date)
    finally:
        dm.rollback()
        dm.close()


def run(apply: bool = False, ref_date: date | None = None, agora: datetime | None = None) -> dict:
    ref_date = ref_date or fonte_mod._hoje_brt(agora)
    run_id = uuid.uuid4().hex
    fonte = _ler_fonte(ref_date)
    resumo = fonte_mod.resumir(fonte, ref_date)
    resumo.update(run_id=run_id, applied=False)
    if not apply:
        return resumo
    # --- travas: qualquer uma ausente = recusa explicita, nada gravado -----
    if not flag_ligada():
        raise PublicacaoRecusada(f"{FLAG_AMBIENTE} desligada")
    if fonte is None:
        raise PublicacaoRecusada("fonte_inexistente: tabelas raw.ml_full_* ausentes")
    if not tabelas_destino_existem():
        raise PublicacaoRecusada("fato_inexistente: migration 029 nao aplicada")
    pacote = montar(fonte, ref_date, run_id)
    if not pacote["marcas"]:
        raise PublicacaoRecusada("sem_fotografia_no_dia")
    sid = audit_start(len(pacote["inventario"]))
    try:
        publicado = _publicar(pacote, ref_date)
    except BaseException as exc:
        audit_finish(sid, "failed", rows_loaded=0, error_message=sanitizar(exc))
        raise
    audit_finish(sid, "success", rows_loaded=publicado["inventarios_publicados"])
    resumo.update(publicado, applied=True, sync_run_id=sid)
    return resumo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publica o estoque Full do ML (exige --apply + flag + migration 029).")
    ap.add_argument("--apply", action="store_true", help="publica; sem ela o run e' dry-run")
    ap.add_argument("--ref-date", type=date.fromisoformat, default=None)
    a = ap.parse_args(argv)
    try:
        res = run(apply=a.apply, ref_date=a.ref_date)
    except PublicacaoRecusada as exc:
        logger.error("publicacao recusada: %s", exc)
        return 2
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
