"""
Gate SH-AUTO-1-R/V — provas que exigem PostgreSQL real E processos separados.

Threads compartilham o interpretador e o pool de conexões; dois **processos**
não compartilham nada além do servidor. É a forma mais próxima do que vai
acontecer quando o Task Scheduler do notebook e o worker do Airflow existirem
ao mesmo tempo.

Requer `FATO_DIARIA_LOCK_TEST_DSN`. Ver `banco_descartavel.py`.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from pipelines.ingestion import fato_diaria_lock as L
from pipelines.tests.banco_descartavel import (
    DSN,
    SKIP_REASON,
    conectar,
    criar_esquema,
    locks_ativos,
)

pytestmark = pytest.mark.skipif(not DSN, reason=SKIP_REASON)

RAIZ = Path(__file__).resolve().parents[2]
CHAVE_SHOPEE = 918130003


@pytest.fixture()
def banco():
    eng = criar_esquema()
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _sem_lock_preso():
    yield
    with conectar() as conn:
        conn.execute(text("SELECT pg_advisory_unlock_all()"))


def _env():
    env = dict(os.environ)
    env["DATABASE_URL"] = DSN or ""
    env["DATAMART_DATABASE_URL"] = DSN or ""
    env["PYTHONPATH"] = str(RAIZ)
    return env


def _rodar(source: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """Executa o writer como o orquestrador executa: `python -m`, processo novo."""
    return subprocess.run(
        [sys.executable, "-m", "pipelines.ingestion.daily_performance",
         "--source", source, "--mode", "incremental"],
        cwd=str(RAIZ), env=_env(), capture_output=True, timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 🔴 O finding alto, medido contra o banco: close() não libera, invalidate() sim
# ---------------------------------------------------------------------------
def test_close_sozinho_nao_libera_o_lock_sob_pooling():
    """Prova do defeito que a revisão encontrou no PR original.

    O engine de `pipelines/common/db.py` usa `QueuePool`. `close()` devolve a
    conexão ao pool e a sessão continua VIVA no servidor — logo o advisory lock
    DE SESSÃO sobrevive. Este teste fixa o comportamento para que ninguém
    "simplifique" o cleanup de volta para só `close()`.
    """
    eng = create_engine(DSN, pool_pre_ping=True)
    conn = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    assert conn.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": CHAVE_SHOPEE}
    ).scalar_one() is True
    assert locks_ativos(CHAVE_SHOPEE) == 1

    conn.close()  # devolve ao pool, NÃO encerra a sessão
    assert locks_ativos(CHAVE_SHOPEE) == 1, (
        "se este assert falhar, o pooling mudou de comportamento e o cleanup "
        "pode ser simplificado — revise o contrato antes"
    )

    conn2 = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn2.invalidate()
    conn2.close()
    eng.dispose()
    assert locks_ativos(CHAVE_SHOPEE) == 0, "invalidate() fecha o socket de verdade"


def test_cleanup_do_contextmanager_solta_o_lock_mesmo_com_unlock_falhando():
    """Cenário real: o `pg_advisory_unlock` não confirma (sessão derrubada no
    meio). O `finally` invalida a conexão e o lock some — sem intervenção."""
    eng = create_engine(DSN, pool_pre_ping=True)

    def conectar_pooled():
        return eng.connect().execution_options(isolation_level="AUTOCOMMIT")

    class Erro(RuntimeError):
        pass

    with pytest.raises(Erro):
        with L.fato_diaria_lock(3, connect=conectar_pooled):
            assert locks_ativos(CHAVE_SHOPEE) == 1
            # derruba a própria sessão pelas costas: o unlock seguinte falha
            with conectar() as outra:
                pid = None
                for linha in outra.execute(text(
                    "SELECT pid FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
                    "WHERE l.locktype='advisory' AND l.objsubid=1 AND l.granted "
                    "AND ((l.classid::bigint<<32)|l.objid::bigint)=:k"
                ), {"k": CHAVE_SHOPEE}):
                    pid = linha[0]
                assert pid is not None
                outra.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
            raise Erro("falha depois da sessão morrer")

    eng.dispose()
    assert locks_ativos(CHAVE_SHOPEE) == 0, "zero lock residual"


def test_sessao_encerrada_abruptamente_libera_o_lock():
    """Se o processo morrer, o sistema operacional fecha o socket e o PostgreSQL
    libera. É a rede de segurança por baixo do `invalidate()`."""
    eng = create_engine(DSN)
    conn = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": CHAVE_SHOPEE})
    pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
    assert locks_ativos(CHAVE_SHOPEE) == 1

    with conectar() as outra:
        outra.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
    eng.dispose()
    assert locks_ativos(CHAVE_SHOPEE) == 0


# ---------------------------------------------------------------------------
# 🟡 objsubid: as duas formas coexistem e a contagem tem de distinguir
# ---------------------------------------------------------------------------
def test_contagem_distingue_as_duas_formas_de_chave():
    """MEDIDO: `pg_try_advisory_lock(918130003)` e
    `pg_try_advisory_lock(0, 918130003)` projetam para o MESMO
    `(classid << 32) | objid`. Sem o filtro `objsubid = 1` a contagem devolve 2
    para dois locks que não se excluem — e num incidente apontaria a sessão
    errada para quem fosse investigar."""
    with conectar() as conn:
        conn.execute(text("SELECT pg_try_advisory_lock(918130003)"))
        conn.execute(text("SELECT pg_try_advisory_lock(0, 918130003)"))

        sem_filtro = conn.execute(text(
            "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted "
            "AND ((classid::bigint<<32)|objid::bigint)=918130003"
        )).scalar_one()
        com_filtro = conn.execute(text(
            "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted "
            "AND ((classid::bigint<<32)|objid::bigint)=918130003 AND objsubid=1"
        )).scalar_one()
        assert sem_filtro == 2
        assert com_filtro == 1
        assert locks_ativos(CHAVE_SHOPEE) == 1

        conn.execute(text("SELECT pg_advisory_unlock_all()"))


def test_o_sql_de_diagnostico_do_runbook_filtra_objsubid():
    doc = Path("docs/contrato_lock_fato_diaria.md").read_text(encoding="utf-8")
    assert "objsubid" in doc, (
        "o SQL de diagnóstico do runbook precisa distinguir as duas formas, "
        "senão aponta a sessão errada num incidente"
    )


# ---------------------------------------------------------------------------
# Dois PROCESSOS independentes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "segurando,tentando",
    [
        ("shopee", "shopee-stats"),
        ("shopee-stats", "shopee"),
        ("shopee", "shopee-ads"),
        ("shopee-ads", "shopee"),
        ("shopee-stats", "shopee-ads"),
    ],
)
def test_par_de_writers_shopee_em_processos_separados(banco, segurando, tentando):
    """Um processo segura o lock (simulando o writer `segurando`) e outro,
    processo de verdade, tenta publicar. Só um entra."""
    with L.fato_diaria_lock(3, connect=conectar):
        proc = _rodar(tentando)
    assert proc.returncode == L.EXIT_CODE_LOCK_UNAVAILABLE, (
        f"{tentando} entrou enquanto {segurando} publicava\n"
        f"stderr={proc.stderr.decode('utf-8', 'replace')[-1500:]}"
    )
    with banco.connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM audit.source_sync_run")
        ).scalar_one() == 0
        assert conn.execute(
            text("SELECT count(*) FROM marts.fact_marketplace_daily_performance")
        ).scalar_one() == 0
    assert locks_ativos(CHAVE_SHOPEE) == 0


def test_runner_do_airflow_simulado_bloqueia_o_processo_manual(banco):
    """O runner do Airflow será um processo em outra máquina executando o
    LITERAL do contrato. Aqui ele é uma conexão crua fazendo exatamente isso."""
    with conectar() as airflow:
        assert airflow.execute(
            text("SELECT pg_try_advisory_lock(918130003)")
        ).scalar_one() is True
        proc = _rodar("shopee")
        assert proc.returncode == L.EXIT_CODE_LOCK_UNAVAILABLE
        airflow.execute(text("SELECT pg_advisory_unlock(918130003)"))

    with banco.connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM audit.source_sync_run")
        ).scalar_one() == 0


def test_processo_manual_bloqueia_o_runner_do_airflow_simulado():
    with L.fato_diaria_lock(3, connect=conectar):
        with conectar() as airflow:
            assert airflow.execute(
                text("SELECT pg_try_advisory_lock(918130003)")
            ).scalar_one() is False


def test_depois_do_primeiro_terminar_o_segundo_adquire(banco):
    """A exclusão não pode virar bloqueio permanente: terminado o primeiro, uma
    nova execução tem de conseguir o lock."""
    with L.fato_diaria_lock(3, connect=conectar):
        recusado = _rodar("shopee-stats")
    assert recusado.returncode == L.EXIT_CODE_LOCK_UNAVAILABLE
    assert locks_ativos(CHAVE_SHOPEE) == 0

    with L.fato_diaria_lock(3, connect=conectar) as chave:
        assert chave == CHAVE_SHOPEE
        assert locks_ativos(CHAVE_SHOPEE) == 1


def test_ml_e_tiktok_nao_sao_bloqueados_pelo_lock_da_shopee(banco):
    """Canais distintos publicam em paralelo — é o desenho, não um descuido."""
    with L.fato_diaria_lock(3, connect=conectar):
        with L.fato_diaria_lock(2, connect=conectar):
            with L.fato_diaria_lock(1, connect=conectar):
                assert locks_ativos(918130001) == 1
                assert locks_ativos(918130002) == 1
                assert locks_ativos(918130003) == 1


# ---------------------------------------------------------------------------
# Saída do processo recusado
# ---------------------------------------------------------------------------
def test_saida_do_processo_recusado_e_limpa():
    """Sem stack trace, sem DSN, sem host, sem credencial. O operador lê uma
    linha e sabe o que aconteceu."""
    with L.fato_diaria_lock(3, connect=conectar):
        proc = _rodar("shopee")
    assert proc.returncode == L.EXIT_CODE_LOCK_UNAVAILABLE
    saida = (proc.stdout + proc.stderr).decode("utf-8", "replace")

    assert "Traceback" not in saida, "lock ocupado não é defeito; sem stack trace"
    assert "FatoDiariaLockUnavailable" not in saida, "nem o nome da classe vaza"
    assert "918130003" in saida
    for proibido in ("postgresql://", "postgres:postgres", "5432", "localhost"):
        assert proibido not in saida, f"a saída vaza {proibido!r}"
