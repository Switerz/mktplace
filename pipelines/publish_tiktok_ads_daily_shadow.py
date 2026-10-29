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

LEITURA: fato e cobertura da raw na MESMA transacao do Data Mart, aberta com
`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY` (um snapshot so';
SERIALIZABLE nao existe em hot standby; `set_session` nao e' usado porque
persiste na conexao do pool). A contagem RAW por par tem de bater com a
cobertura declarada (`tiktok_ads_shadow.montar_shadow`); divergencia = recusa.

PUBLICACAO: transacao unica, `pg_try_advisory_xact_lock` primeiro. Substitui
exatamente os pares (anunciante, dia) CONSULTADOS nesta leitura: DELETE da
fato e da cobertura desses pares + INSERT. Par nao consultado nesta leitura
fica como estava (ausencia nao apaga nem zera). Mesma entrada -> mesmo
conteudo; rerun nao duplica (PK + DELETE do par). Contagem por par conferida
de novo antes do DELETE; reconciliacao por (dia, anunciante, escopo, moeda) --
linhas e spend -- e da cobertura antes do COMMIT.

AUDITORIA (`audit.source_sync_run`, marketplace 1 = TikTok):
- `running` e' gravada em conexao propria antes da escrita;
- `success` e' gravada DENTRO da transacao da publicacao, no mesmo COMMIT dos
  dados: success <=> dados confirmados. Nao existe "commit feito, auditoria
  nao";
- `failed` (recusa por fonte inconsistente, reconciliacao, lock, erro) vai em
  conexao propria, so' sobre linha ainda `running`, com o motivo;
- `running` remanescente, lido DEPOIS que a sessao da publicacao terminou no
  servidor => nada confirmado (success sai no mesmo COMMIT dos dados). Lido
  antes disso, o COMMIT ainda pode estar em curso.
COMMIT AMBIGUO: se o cliente recebe erro no proprio COMMIT (ACK perdido), o
servidor pode ter confirmado ou nao. `failed` gravado depois prova que nao
confirmou (o UPDATE so' acha linha `running`); se o `failed` nao fecha
(linha ja' terminal) ou a auditoria esta' inacessivel, o estado e'
INDETERMINADO ate' conferir, so' leitura, `audit.source_sync_run`
(sync_run_id) e a contagem do destino por `source_run_id` (= run_id) antes de
republicar. O log desse caminho diz isso; nao afirma confirmacao nem ausencia.
Exit code: 0 = dry-run ou publicado (JSON com `auditoria: success`);
2 = recusa (`PublicacaoRecusada`: nada escrito no shadow, motivo no log e,
para fonte inconsistente, na auditoria `failed`); 1 = excecao (lock,
reconciliacao, auditoria que nao fecha, rede). Excecao ANTES do COMMIT nao
confirma nada; excecao NO COMMIT deixa o estado indeterminado (acima).
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

#: Primeiro comando da transacao de leitura: um snapshot para fato e cobertura.
SQL_SNAPSHOT = text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
SQL_CONFERE_SNAPSHOT = text("SELECT current_setting('transaction_isolation'), "
                            "current_setting('transaction_read_only')")
SNAPSHOT_ESPERADO = ("repeatable read", "on")
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


class LeituraSemSnapshot(RuntimeError):
    """A leitura da raw nao esta' num snapshot REPEATABLE READ READ ONLY."""


class AuditoriaNaoFechou(RuntimeError):
    """O UPDATE da auditoria nao achou a linha `running` do run."""


def flag_ligada() -> bool:
    return os.environ.get(FLAG_AMBIENTE, "").strip().lower() == "true"


def janela(hoje: date) -> tuple[date, date]:
    return hoje - timedelta(days=JANELA_DIAS), hoje - timedelta(days=1)


def ler_fonte(conn, inicio: date, fim: date):
    """Fato e cobertura da raw num snapshot so'. `conn` tem de estar no INICIO da transacao.

    O PostgreSQL recusa `SET TRANSACTION ISOLATION LEVEL` depois de qualquer
    consulta na transacao -- leitura fora de snapshot falha alto, nao degrada.
    """
    conn.execute(SQL_SNAPSHOT)
    snapshot = tuple(conn.execute(SQL_CONFERE_SNAPSHOT).fetchone())
    if snapshot != SNAPSHOT_ESPERADO:
        raise LeituraSemSnapshot(f"isolamento/read_only = {snapshot}; esperado {SNAPSHOT_ESPERADO}")
    if not all(conn.execute(SQL_PRESENCA).fetchone()):
        return None
    p = {"inicio": inicio, "fim": fim, "marcas": list(T.MARCAS)}
    as_dicts = lambda rs: [dict(r._mapping) for r in rs]
    return {"linhas": as_dicts(conn.execute(SQL_LINHAS, p)),
            "cobertura": as_dicts(conn.execute(SQL_COBERTURA, p))}


def _ler(inicio, fim):
    dm = DataMartSession()
    try:
        return ler_fonte(dm.connection(), inicio, fim)
    finally:
        dm.rollback()
        dm.close()


def _chave_rec(dia, adv, escopo, moeda):
    return (str(dia), str(adv), escopo, moeda)


def conferir_pares(fato, cobertura) -> None:
    """Invariante antes do DELETE: cada par tem na fato exatamente as `linhas` da cobertura."""
    div = T.divergencias_por_par(cobertura, fato)
    fora = {(str(f["advertiser_id"]), f["stat_date"]) for f in fato} - \
        {(str(c["advertiser_id"]), c["stat_date"]) for c in cobertura}
    if div or fora:
        raise ReconciliacaoFalhou(f"contagem por par incoerente antes do DELETE: divergentes={div[:5]} "
                                  f"pares_da_fato_sem_cobertura={sorted(fora, key=str)[:5]}")


def publicar_em_transacao(conn, fato, cobertura, run_id) -> dict:
    conferir_pares(fato, cobertura)
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


SQL_AUDIT_SUCESSO = text("""
UPDATE audit.source_sync_run SET status = 'success', finished_at = NOW(), rows_loaded = :n, error_message = NULL
 WHERE sync_run_id = :sid AND status = 'running'""")


def _publicar(fato, cobertura, run_id, sid):
    with local_engine().begin() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
            raise ConcurrentRunError(f"advisory lock {ADVISORY_LOCK_KEY} ocupado")
        publicado = publicar_em_transacao(conn, fato, cobertura, run_id)
        # success no MESMO commit dos dados: um nao existe sem o outro.
        if conn.execute(SQL_AUDIT_SUCESSO, {"n": publicado["linhas_publicadas"], "sid": sid}).rowcount != 1:
            raise AuditoriaNaoFechou(f"sync_run_id {sid} nao esta' 'running'; publicacao desfeita")
        return publicado


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
    """Fecha a auditoria em conexao propria. So' toca linha ainda `running`:
    nunca rebaixa um `success` que ja' saiu no COMMIT da publicacao."""
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE audit.source_sync_run SET status = %s, finished_at = NOW(),
                               rows_loaded = %s, error_message = %s
                           WHERE sync_run_id = %s AND status = 'running'""",
                        (status, rows_loaded, error_message, sid))
            if cur.rowcount != 1:
                raise AuditoriaNaoFechou(f"sync_run_id {sid} nao esta' 'running' (ja' terminal ou ausente)")
        conn.commit()
    finally:
        conn.close()


#: Procedimento (so' leitura) para quando o desfecho da publicacao nao esta' provado.
CONFERIR_ANTES_DE_REPUBLICAR = (
    "conferir audit.source_sync_run (sync_run_id=%s) e a contagem do destino "
    f"({FACT_COBERTURA} e {FACT} por source_run_id=%s) antes de republicar")


def _auditar_falha(sid: int, motivo: str, run_id: str | None = None) -> None:
    """Grava `failed` com o motivo. Falha da auditoria e' logada e nunca mascara a causa.

    `failed` gravado prova que a publicacao nao confirmou (so' casa linha `running`).
    Nos outros dois desfechos o log NAO afirma nem confirmacao nem ausencia dela.
    """
    rid = run_id or "n/d"
    try:
        audit_finish(sid, "failed", rows_loaded=0, error_message=motivo[:AUDIT_ERRO_MAX_CHARS])
    except AuditoriaNaoFechou:
        # Linha ja' terminal: ou o COMMIT da publicacao chegou ao banco e o cliente viu
        # erro depois dele (linha = 'success'), ou outro processo a fechou. Fica como esta'.
        logger.error("estado INDETERMINADO: sync_run_id %s ja' nao estava 'running' (run_id=%s); o COMMIT "
                     "da publicacao (se este run publicou) pode ter sido confirmado ou outro processo "
                     "fechou a linha; " + CONFERIR_ANTES_DE_REPUBLICAR, sid, rid, sid, rid)
    except Exception as aud:  # noqa: BLE001
        # O UPDATE do 'failed' pode nem ter chegado ou ter confirmado sem ACK; a linha pode
        # estar 'running', 'failed' ou, se o COMMIT da publicacao chegou ao banco, 'success'.
        logger.error("estado INDETERMINADO: auditoria 'failed' do sync_run_id %s NAO confirmada (%s; "
                     "run_id=%s); o COMMIT da publicacao (se este run publicou) pode ter sido "
                     "confirmado; " + CONFERIR_ANTES_DE_REPUBLICAR, sid, type(aud).__name__, rid, sid, rid)


def _recusar_auditado(n_extraidas: int, motivo: str):
    """Recusa ANTES de qualquer escrita no shadow, com auditoria `failed` registrando o motivo."""
    try:
        sid = audit_start(n_extraidas)
    except Exception as aud:  # noqa: BLE001
        # O INSERT 'running' pode ter confirmado sem ACK; o shadow nao foi tocado (recusa vem antes).
        logger.error("auditoria da recusa NAO confirmada (%s): pode ter ficado linha 'running' orfa; conferir "
                     "audit.source_sync_run (source_name=%s, status='running'); o shadow nao foi tocado "
                     "por esta recusa", type(aud).__name__, AUDIT_SOURCE)
    else:
        _auditar_falha(sid, f"PublicacaoRecusada: {motivo}")
    raise PublicacaoRecusada(motivo)


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
        _recusar_auditado(len(fonte["linhas"]), f"fonte_inconsistente: {inconsistente} (run_id={run_id})")
    if not tabela_destino_existe():
        raise PublicacaoRecusada("fato_inexistente: migration 031 nao aplicada")
    if not cobertura:
        raise PublicacaoRecusada("sem_linhas_publicaveis: nenhum anunciante x dia consultado")
    sid = audit_start(len(fato))
    try:
        publicado = _publicar(fato, cobertura, run_id, sid)
    except BaseException as exc:
        _auditar_falha(sid, f"{type(exc).__name__}: {exc} (run_id={run_id})", run_id=run_id)
        raise
    resumo.update(publicado, applied=True, modo="escrita", sync_run_id=sid, auditoria="success")
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
