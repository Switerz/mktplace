"""TIKTOK-ADS-PRODUCTIZATION-1 — publica o shadow diario de TikTok Ads. INERTE.

Le `raw.tiktok_ads_campaign_daily` e `raw.tiktok_ads_coverage` no Data Mart
(sessao READ ONLY), monta campanha x dia + cobertura por
`pipelines.transforms.tiktok_ads_shadow` e, SO' com as tres travas, grava em
`marts.fact_tiktok_ads_daily_shadow` e `marts.fact_tiktok_ads_coverage_shadow`
(migration 031, NAO aplicada) -- tabelas que NENHUM consumidor le'.

TRES TRAVAS (todas obrigatorias)
---------------------------------
1. `--apply`;  2. `TIKTOK_ADS_SHADOW_PUBLISH_ENABLED=true`;
3. a tabela da 031 existindo no Neon (`to_regclass`).

Fora isso: dry-run (agregados) ou recusa explicita. Nada e' gravado.

PUBLICACAO: transacao unica, `pg_try_advisory_xact_lock` primeiro. Substitui
exatamente os pares (anunciante, dia) CONSULTADOS nesta leitura: DELETE da
fato e da cobertura desses pares + INSERT. Par nao consultado nesta leitura
fica como estava (ausencia nao apaga nem zera). Mesma entrada -> mesmo
conteudo; rerun nao duplica (PK + DELETE do par). Reconciliacao por
(dia, anunciante, escopo, moeda) -- linhas e spend -- e da cobertura antes do
COMMIT; auditoria em conexao propria (marketplace 1 = TikTok).
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

FACT_COBERTURA = "marts.fact_tiktok_ads_coverage_shadow"
COLS = ("stat_date", "brand", "advertiser_id", "campaign_id", "escopo", "moeda", "fuso", "spend",
        "impressions", "clicks", "onsite_shopping", "onsite_shopping_value", "source_run_id")
COLS_COBERTURA = ("stat_date", "brand", "advertiser_id", "linhas", "source_run_id")
#: Pares (anunciante, dia) desta publicacao, como tabela, para DELETE e reconciliacao.
PARES = "unnest(CAST(:advs AS text[]), CAST(:dias AS date[])) AS k(advertiser_id, stat_date)"

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


def _chave_rec(dia, adv, escopo, moeda):
    return (str(dia), str(adv), escopo, moeda)


def publicar_em_transacao(conn, fato, cobertura, run_id) -> dict:
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    p = {"advs": [c["advertiser_id"] for c in cobertura], "dias": [c["stat_date"] for c in cobertura]}
    # Fato antes da cobertura (FK); cobertura antes da fato no INSERT.
    for tabela in (FACT, FACT_COBERTURA):
        conn.execute(text(f"""DELETE FROM {tabela} t USING {PARES}
                               WHERE t.advertiser_id = k.advertiser_id AND t.stat_date = k.stat_date"""), p)
    with conn.connection.cursor() as cur:
        for tabela, cols, linhas in ((FACT_COBERTURA, COLS_COBERTURA, cobertura), (FACT, COLS, fato)):
            if linhas:
                execute_values(cur, f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES %s",
                               [tuple({**x, "source_run_id": run_id}[c] for c in cols) for x in linhas],
                               page_size=500)
    destino = {
        _chave_rec(r["stat_date"], r["advertiser_id"], r["escopo"], r["moeda"]): (r["n"], Decimal(r["spend"]))
        for r in conn.execute(text(f"""
            SELECT f.stat_date, f.advertiser_id, f.escopo, f.moeda, COUNT(*) AS n, SUM(f.spend) AS spend
              FROM {FACT} f JOIN {PARES} ON f.advertiser_id = k.advertiser_id AND f.stat_date = k.stat_date
             GROUP BY 1, 2, 3, 4"""), p).mappings().all()
    }
    esperado = {}
    for f in fato:
        k = _chave_rec(f["stat_date"], f["advertiser_id"], f["escopo"], f["moeda"])
        n, sp = esperado.get(k, (0, Decimal("0")))
        esperado[k] = (n + 1, sp + f["spend"])
    if destino != esperado:
        divergentes = sorted(set(destino.items()) ^ set(esperado.items()))[:5]
        raise ReconciliacaoFalhou(f"fato diverge por (dia, anunciante, escopo, moeda): {divergentes}")
    cob = conn.execute(text(f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(c.linhas), 0) AS linhas
          FROM {FACT_COBERTURA} c JOIN {PARES} ON c.advertiser_id = k.advertiser_id AND c.stat_date = k.stat_date
    """), p).mappings().one()
    if cob["n"] != len(cobertura) or cob["linhas"] != len(fato):
        raise ReconciliacaoFalhou(f"cobertura n={cob['n']} linhas={cob['linhas']}; "
                                  f"esperado n={len(cobertura)} linhas={len(fato)}")
    return {"linhas_publicadas": len(fato), "pares_publicados": len(cobertura),
            "marcas_publicadas": sorted({c["brand"] for c in cobertura}),
            "spend_medido_publicado": sum((f["spend"] for f in fato), Decimal("0"))}


def _publicar(fato, cobertura, run_id):
    with local_engine().begin() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
            raise ConcurrentRunError(f"advisory lock {ADVISORY_LOCK_KEY} ocupado")
        return publicar_em_transacao(conn, fato, cobertura, run_id)


def tabela_destino_existe() -> bool:
    with local_engine().connect() as conn:
        return bool(conn.execute(text(f"SELECT to_regclass('{FACT}') IS NOT NULL "
                                      f"AND to_regclass('{FACT_COBERTURA}') IS NOT NULL")).scalar())


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
    fato, cobertura, inconsistente = [], [], None
    if fonte is None:
        resumo["estado"] = "fonte_inexistente"
    else:
        try:
            fato, cobertura, r = T.montar_shadow(fonte["linhas"], fonte["cobertura"])
            resumo.update(r, estado="medido" if cobertura else "sem_linhas_publicaveis")
        except T.FonteInconsistente as exc:
            inconsistente = str(exc)
            resumo.update(estado="fonte_inconsistente", motivo=inconsistente)
    if not apply:
        return resumo
    if not flag_ligada():
        raise PublicacaoRecusada(f"{FLAG_AMBIENTE} desligada")
    if fonte is None:
        raise PublicacaoRecusada("fonte_inexistente: tabelas raw.tiktok_ads_* ausentes")
    if inconsistente is not None:
        raise PublicacaoRecusada(f"fonte_inconsistente: {inconsistente}")
    if not tabela_destino_existe():
        raise PublicacaoRecusada("fato_inexistente: migration 031 nao aplicada")
    if not cobertura:
        raise PublicacaoRecusada("sem_linhas_publicaveis: nenhum anunciante x dia consultado")
    sid = audit_start(len(fato))
    try:
        publicado = _publicar(fato, cobertura, run_id)
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
