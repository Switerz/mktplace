"""Gate PMA-2C5B-R/V — o que SO' um PostgreSQL de verdade prova.

O dublê concorda com o que o teste mandar. Liberacao de lock em excecao,
timeout finito, ausencia de residuo e idempotencia sao comportamento do
SERVIDOR: ou se exercita contra um cluster real, ou nao se afirma.

O cluster e' descartavel, escuta em 127.0.0.1 numa porta efemera e morre com a
sessao de teste. Nenhuma conexao sai da maquina.
"""
from __future__ import annotations

import pytest

from pipelines.tests.postgres_descartavel import (
    DDL_AUDITORIA, MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel,
)

pytestmark = pytest.mark.skipif(not postgres_disponivel(),
                                reason=MOTIVO_SEM_POSTGRES)

LOCK_CANAIS = 917120017
LOCK_ML = 914_120_014


def _locks_tomados(cur, chave: int) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM pg_locks WHERE locktype = 'advisory' "
        "AND classid = 0 AND objid = %s", (chave,))
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# 1. Liberacao do lock de SESSAO (canais) — sucesso e excecao
# ---------------------------------------------------------------------------

def test_o_lock_de_sessao_e_liberado_ao_fechar_a_conexao():
    """`pg_try_advisory_lock` e' de SESSAO: quem o segura e' a conexao. Fechar
    a conexao tem de liberar, senao um processo morto deixaria o canal travado
    para sempre."""
    import psycopg2
    with cluster_da_sessao() as url:
        a = psycopg2.connect(url)
        with a.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_CANAIS,))
            assert cur.fetchone()[0] is True
        a.close()

        b = psycopg2.connect(url)
        try:
            with b.cursor() as cur:
                assert _locks_tomados(cur, LOCK_CANAIS) == 0, "lock residual"
                cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_CANAIS,))
                assert cur.fetchone()[0] is True, (
                    "a proxima execucao precisa conseguir o lock")
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_CANAIS,))
        finally:
            b.close()


def test_o_lock_e_liberado_mesmo_quando_a_execucao_levanta():
    """Excecao no meio do trabalho nao pode deixar residuo."""
    import psycopg2
    with cluster_da_sessao() as url:
        a = psycopg2.connect(url)
        try:
            with a.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_CANAIS,))
                assert cur.fetchone()[0] is True
                with pytest.raises(psycopg2.Error):
                    cur.execute("SELECT 1/0")
        finally:
            a.close()  # o `finally` do publisher faz exatamente isto

        b = psycopg2.connect(url)
        try:
            with b.cursor() as cur:
                assert _locks_tomados(cur, LOCK_CANAIS) == 0
        finally:
            b.close()


def test_zero_lock_residual_ao_fim_das_provas():
    import psycopg2
    with cluster_da_sessao() as url:
        c = psycopg2.connect(url)
        try:
            with c.cursor() as cur:
                assert _locks_tomados(cur, LOCK_CANAIS) == 0
                assert _locks_tomados(cur, LOCK_ML) == 0
        finally:
            c.close()


# ---------------------------------------------------------------------------
# 2. Lock TRANSACIONAL do ML — espera, mas com teto
# ---------------------------------------------------------------------------

def test_o_lock_do_ml_espera_e_nao_desiste_sozinho():
    """Diferenca real entre os dois publishers, e a razao de o lock logico do
    `run_task.ps1` importar mais no ML: `pg_advisory_xact_lock` BLOQUEIA."""
    import psycopg2
    with cluster_da_sessao() as url:
        a = psycopg2.connect(url)
        b = psycopg2.connect(url)
        try:
            with a.cursor() as ca:
                ca.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ML,))
            # A segunda sessao tenta com teto CURTO: sem `lock_timeout` ela
            # esperaria indefinidamente.
            with b.cursor() as cb:
                cb.execute("SET LOCAL lock_timeout = '800ms'")
                with pytest.raises(psycopg2.Error):
                    cb.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ML,))
            b.rollback()
        finally:
            a.rollback()
            a.close()
            b.close()


def test_o_publisher_do_ml_define_lock_timeout_antes_de_pedir_o_lock():
    """Contraprova estrutural: a espera precisa ter teto NO CODIGO, nao so' no
    teste. Sem `lock_timeout`, uma execucao concorrente fica pendurada ate o
    timeout do step do orquestrador."""
    import inspect

    from pipelines import sync_ml_listing_price_serving as ml
    fonte = inspect.getsource(ml.publish_window)
    pos_timeout = fonte.index("lock_timeout")
    pos_lock = fonte.index("pg_advisory_xact_lock")
    assert pos_timeout < pos_lock, (
        "`SET LOCAL lock_timeout` tem de vir ANTES de pedir o lock")


def test_o_lock_transacional_some_ao_fim_da_transacao():
    import psycopg2
    with cluster_da_sessao() as url:
        a = psycopg2.connect(url)
        try:
            with a.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ML,))
                assert _locks_tomados(cur, LOCK_ML) == 1
            a.rollback()
            with a.cursor() as cur:
                assert _locks_tomados(cur, LOCK_ML) == 0, (
                    "lock transacional tem de morrer com a transacao")
        finally:
            a.close()


# ---------------------------------------------------------------------------
# 3. Auditoria — persistencia, independencia e idempotencia
# ---------------------------------------------------------------------------

def _limpa(conn):
    with conn.cursor() as cur:
        cur.execute(DDL_AUDITORIA)
        cur.execute("DELETE FROM audit.source_sync_run")
    conn.commit()


def test_a_auditoria_sobrevive_ao_rollback_dos_dados():
    """A razao de a auditoria ter conexao PROPRIA: se vivesse na transacao dos
    dados, um `failed` seria desfeito junto com o rollback e a tentativa nao
    deixaria rastro."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from pipelines import sync_ml_listing_price_serving as ml
    with cluster_da_sessao() as url:
        auditoria = psycopg2.connect(url, cursor_factory=RealDictCursor)
        dados = psycopg2.connect(url)
        try:
            _limpa(auditoria)
            sync_run_id = ml.audit_start(auditoria, 100)

            # A transacao dos DADOS falha e e' desfeita por inteiro.
            with dados.cursor() as cur:
                cur.execute("CREATE TEMP TABLE t (x int)")
                cur.execute("INSERT INTO t VALUES (1)")
            dados.rollback()

            ml.audit_finish(auditoria, sync_run_id, "failed", rows_loaded=0,
                            error_message="divergencia")
            with auditoria.cursor() as cur:
                cur.execute("SELECT status, rows_loaded FROM "
                            "audit.source_sync_run WHERE sync_run_id = %s",
                            (sync_run_id,))
                linha = cur.fetchone()
            assert linha["status"] == "failed", (
                "o rastro da tentativa precisa sobreviver ao rollback")
            assert linha["rows_loaded"] == 0
        finally:
            auditoria.close()
            dados.close()


def test_duas_execucoes_deixam_dois_registros_distintos():
    """Idempotencia do REGISTRO: reexecutar nao sobrescreve a tentativa
    anterior — o historico e' o que permite reconciliar um indeterminado."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from pipelines import sync_ml_listing_price_serving as ml
    with cluster_da_sessao() as url:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        try:
            _limpa(conn)
            primeiro = ml.audit_start(conn, 10)
            ml.audit_finish(conn, primeiro, "success", rows_loaded=10)
            segundo = ml.audit_start(conn, 10)
            ml.audit_finish(conn, segundo, "success", rows_loaded=10)
            assert primeiro != segundo
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM audit.source_sync_run "
                            "WHERE source_name = %s", (ml.AUDIT_SOURCE_NAME,))
                assert cur.fetchone()["n"] == 2
        finally:
            conn.close()


def test_a_nota_de_indeterminado_nao_reescreve_registro_ja_fechado():
    """O UPDATE e' condicionado a `running`. Sem isso, uma nota tardia poderia
    apagar a conclusao que um humano ja registrou."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from pipelines import sync_ml_listing_price_serving as ml
    with cluster_da_sessao() as url:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        try:
            _limpa(conn)
            sync_run_id = ml.audit_start(conn, 5)
            ml.audit_finish(conn, sync_run_id, "success", rows_loaded=5)
            ml.audit_note_indeterminate(conn, sync_run_id, "nota tardia")
            with conn.cursor() as cur:
                cur.execute("SELECT status, error_message FROM "
                            "audit.source_sync_run WHERE sync_run_id = %s",
                            (sync_run_id,))
                linha = cur.fetchone()
            assert linha["status"] == "success"
            assert linha["error_message"] is None, (
                "registro fechado nao pode receber nota de indeterminado")
        finally:
            conn.close()


def test_audit_start_que_falha_impede_a_publicacao():
    """Se o registro nao abre, nada deve ser publicado: uma publicacao sem
    rastro e' pior que uma publicacao que nao aconteceu."""
    import psycopg2

    from pipelines import sync_ml_listing_price_serving as ml
    with cluster_da_sessao() as url:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS audit")
                cur.execute("DROP TABLE IF EXISTS audit.source_sync_run")
            conn.commit()
            with pytest.raises(psycopg2.Error):
                ml.audit_start(conn, 10)
            conn.rollback()
            # restaura para os demais testes da sessao
            _limpa(conn)
        finally:
            conn.close()


def test_rows_loaded_e_datas_reconciliam_com_a_janela():
    import psycopg2
    from datetime import date
    from psycopg2.extras import RealDictCursor

    from pipelines import sync_ml_listing_price_serving as ml
    with cluster_da_sessao() as url:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        try:
            _limpa(conn)
            de, ate = date(2026, 9, 19), date(2026, 9, 21)
            sync_run_id = ml.audit_start(conn, 872)
            ml.audit_finish(conn, sync_run_id, "success", rows_loaded=872,
                            source_min_date=de, source_max_date=ate)
            with conn.cursor() as cur:
                cur.execute("SELECT rows_extracted, rows_loaded, "
                            "source_min_date, source_max_date FROM "
                            "audit.source_sync_run WHERE sync_run_id = %s",
                            (sync_run_id,))
                linha = cur.fetchone()
            assert linha["rows_extracted"] == 872
            assert linha["rows_loaded"] == 872
            assert linha["source_min_date"] == de
            assert linha["source_max_date"] == ate
        finally:
            conn.close()
