"""TIKTOK-ADS-PRODUCTIZATION-1 — publica o shadow diario de TikTok Ads. INERTE.

Le `raw.tiktok_ads_campaign_daily` e `raw.tiktok_ads_coverage` no Data Mart
(sessao READ ONLY), monta (dia, marca, escopo) por
`pipelines.transforms.tiktok_ads_shadow` e, SO' com as tres travas, grava em
`marts.fact_tiktok_ads_daily_shadow` (migration 031, NAO aplicada) -- uma
tabela que NENHUM consumidor le'.

TRES TRAVAS (todas obrigatorias)
---------------------------------
1. `--apply`;  2. `TIKTOK_ADS_SHADOW_PUBLISH_ENABLED=true`;
3. a tabela da 031 existindo no Neon (`to_regclass`).

Fora isso: dry-run (agregados) ou recusa explicita. Nada e' gravado.

PUBLICACAO: transacao unica, `pg_try_advisory_xact_lock` primeiro, DELETE da
janela das marcas presentes + INSERT, reconciliacao (linhas e spend medido)
antes do COMMIT, auditoria em conexao propria (marketplace 1 = TikTok).
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from psycopg2.extras import execute_values
from sqlalchemy import text

from pipelines.common.db import DataMartSession, LocalSession, local_engine
from pipelines.common.logging import get_logger
from pipelines.transforms import tiktok_ads_shadow as T

logger = get_logger(__name__)

FACT = "marts.fact_tiktok_ads_daily_shadow"
FLAG_AMBIENTE = "TIKTOK_ADS_SHADOW_PUBLISH_ENABLED"
FONTE_TABELAS = ("raw.tiktok_ads_campaign_daily", "raw.tiktok_ads_coverage")
JANELA_DIAS = 35
ADVISORY_LOCK_KEY = 916140030
TARGET_STATEMENT_TIMEOUT_MS = 180_000
AUDIT_MARKETPLACE_ID = 1     # audit.source_sync_run: 1=tiktok, 2=ml, 3=shopee
AUDIT_SOURCE = "tiktok_ads_daily_shadow"
AUDIT_ERRO_MAX_CHARS = 4000

COLS = ("stat_date", "brand", "escopo", "estado", "spend", "impressions", "clicks",
        "onsite_shopping", "onsite_shopping_value", "campanhas", "anunciantes", "moeda",
        "fuso", "source_run_id")

SQL_PRESENCA = text("SELECT " + ", ".join(f"to_regclass('{t}') IS NOT NULL" for t in FONTE_TABELAS))
SQL_LINHAS = text("""
SELECT brand, advertiser_id, campaign_id, stat_date, escopo, spend, impressions, clicks,
       onsite_shopping, total_onsite_shopping_value, moeda, fuso
  FROM raw.tiktok_ads_campaign_daily
 WHERE stat_date >= :inicio AND stat_date <= :fim AND brand = ANY(:marcas)
""")
SQL_COBERTURA = text("""
SELECT brand, advertiser_id, stat_date, linhas
  FROM raw.tiktok_ads_coverage
 WHERE stat_date >= :inicio AND stat_date <= :fim AND brand = ANY(:marcas)
""")


class PublicacaoRecusada(RuntimeError):
    pass


class ConcurrentRunError(RuntimeError):
    pass


class ReconciliacaoFalhou(RuntimeError):
    pass


def flag_ligada() -> bool:
    return os.environ.get(FLAG_AMBIENTE, "").strip().lower() == "true"


def janela(hoje: date) -> tuple[date, date]:
    return hoje - timedelta(days=JANELA_DIAS), hoje - timedelta(days=1)


def ler_fonte(conn, inicio: date, fim: date):
    if not all(conn.execute(SQL_PRESENCA).fetchone()):
        return None
    p = {"inicio": inicio, "fim": fim, "marcas": list(T.MARCAS)}
    as_dicts = lambda rs: [dict(r._mapping) for r in rs]
    return {"linhas": as_dicts(conn.execute(SQL_LINHAS, p)),
            "cobertura": as_dicts(conn.execute(SQL_COBERTURA, p))}


def _ler(inicio, fim):
    dm = DataMartSession()
    try:
        dm.connection().connection.set_session(readonly=True)
        return ler_fonte(dm.connection(), inicio, fim)
    finally:
        dm.rollback()
        dm.close()


def publicar_em_transacao(conn, saida, inicio, fim, run_id) -> dict:
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    marcas = sorted({s["brand"] for s in saida})
    p = {"inicio": inicio, "fim": fim, "marcas": marcas}
    conn.execute(text(f"DELETE FROM {FACT} WHERE stat_date BETWEEN :inicio AND :fim AND brand = ANY(:marcas)"), p)
    if saida:
        with conn.connection.cursor() as cur:
            execute_values(cur, f"INSERT INTO {FACT} ({', '.join(COLS)}) VALUES %s",
                           [tuple({**s, "source_run_id": run_id}[c] for c in COLS) for s in saida],
                           page_size=500)
    destino = conn.execute(text(f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(spend), 0) AS spend FROM {FACT}
         WHERE stat_date BETWEEN :inicio AND :fim AND brand = ANY(:marcas)"""), p).mappings().one()
    esperado_spend = sum((s["spend"] for s in saida if s["spend"] is not None), Decimal("0"))
    if destino["n"] != len(saida) or Decimal(destino["spend"]) != esperado_spend:
        raise ReconciliacaoFalhou(f"destino n={destino['n']} spend={destino['spend']}; "
                                  f"esperado n={len(saida)} spend={esperado_spend}")
    return {"linhas_publicadas": len(saida), "marcas_publicadas": marcas,
            "spend_medido_publicado": esperado_spend}


def _publicar(saida, inicio, fim, run_id):
    with local_engine().begin() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
            raise ConcurrentRunError(f"advisory lock {ADVISORY_LOCK_KEY} ocupado")
        return publicar_em_transacao(conn, saida, inicio, fim, run_id)


def tabela_destino_existe() -> bool:
    with local_engine().connect() as conn:
        return bool(conn.execute(text(f"SELECT to_regclass('{FACT}') IS NOT NULL")).scalar())


def _audit_conn():
    return LocalSession().get_bind().raw_connection()


def audit_start(n: int) -> int:
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO audit.source_sync_run
                               (source_name, marketplace_id, loja_id, status, started_at, rows_extracted)
                           VALUES (%s, %s, NULL, 'running', NOW(), %s) RETURNING sync_run_id""",
                        (AUDIT_SOURCE, AUDIT_MARKETPLACE_ID, n))
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


def run(apply: bool = False, hoje: date | None = None) -> dict:
    hoje = hoje or datetime.now(timezone.utc).date()
    inicio, fim = janela(hoje)
    run_id = uuid.uuid4().hex
    fonte = _ler(inicio, fim)
    resumo = {"janela": [str(inicio), str(fim)], "modo": "dry_run", "applied": False, "run_id": run_id,
              "fonte_gmv_max": "sem_fonte", "marcas_cobertas": list(T.MARCAS)}
    if fonte is None:
        resumo["estado"] = "fonte_inexistente"
    else:
        saida, r = T.montar_shadow(fonte["linhas"], fonte["cobertura"])
        resumo.update(r, estado="medido" if saida else "sem_linhas_publicaveis")
    if not apply:
        return resumo
    if not flag_ligada():
        raise PublicacaoRecusada(f"{FLAG_AMBIENTE} desligada")
    if fonte is None:
        raise PublicacaoRecusada("fonte_inexistente: tabelas raw.tiktok_ads_* ausentes")
    if not tabela_destino_existe():
        raise PublicacaoRecusada("fato_inexistente: migration 031 nao aplicada")
    if not saida:
        raise PublicacaoRecusada("sem_linhas_publicaveis")
    sid = audit_start(len(saida))
    try:
        publicado = _publicar(saida, inicio, fim, run_id)
    except BaseException as exc:
        audit_finish(sid, "failed", rows_loaded=0,
                     error_message=f"{type(exc).__name__}: {str(exc)[:AUDIT_ERRO_MAX_CHARS]}")
        raise
    audit_finish(sid, "success", rows_loaded=publicado["linhas_publicadas"])
    resumo.update(publicado, applied=True, modo="escrita", sync_run_id=sid)
    return resumo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Shadow TikTok Ads (exige --apply + flag + migration 031).")
    ap.add_argument("--apply", action="store_true", help="publica; sem ela o run e' dry-run")
    a = ap.parse_args(argv)
    try:
        res = run(apply=a.apply)
    except PublicacaoRecusada as exc:
        logger.error("publicacao recusada: %s", exc)
        return 2
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
