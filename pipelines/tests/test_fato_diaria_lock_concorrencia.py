"""
Gate SH-AUTO-1 — exclusão mútua contra PostgreSQL DE VERDADE.

🔴 POR QUE ESTE ARQUIVO EXISTE SEPARADO DOS DUBLÊS

Um advisory lock só é lock quando duas conexões reais disputam a mesma chave no
mesmo servidor. Dublê nenhum prova isso: ele devolve o booleano que o teste
mandou devolver. Foi essa exata lacuna que deixou o piloto dos runners de ML
passar em 596 testes e falhar no primeiro contato real com o banco.

Os testes aqui abrem **duas conexões simultâneas** e medem quem entra.

COMO RODAR

    docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=postgres \\
        --name pg-lock-test postgres:16
    FATO_DIARIA_LOCK_TEST_DSN=postgresql://postgres:postgres@localhost:55432/postgres \\
        python -m pytest pipelines/tests/test_fato_diaria_lock_concorrencia.py

Sem a variável, tudo aqui é SKIP — a suíte normal continua offline e sem banco.

⚠️ O DSN tem de apontar para um banco DESCARTÁVEL. Estes testes criam schema,
tabela e linhas. Nenhum deles toca `DATABASE_URL` ou `DATAMART_DATABASE_URL`.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from pipelines.ingestion import fato_diaria_lock as L

from pipelines.tests.banco_descartavel import (
    DSN,
    SKIP_REASON,
    conectar as _conectar,
    criar_esquema,
    locks_ativos as _locks_ativos,
)

pytestmark = pytest.mark.skipif(not DSN, reason=SKIP_REASON)


@pytest.fixture()
def banco():
    eng = criar_esquema()
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _sem_lock_preso():
    """Nenhum teste pode deixar lock vivo para o seguinte."""
    yield
    from sqlalchemy import text

    with _conectar() as conn:
        conn.execute(text("SELECT pg_advisory_unlock_all()"))


# ---------------------------------------------------------------------------
# 1. Livre, ocupado, liberado — duas conexões reais
# ---------------------------------------------------------------------------
def test_lock_livre_e_adquirido_de_verdade():
    with L.fato_diaria_lock(3, connect=_conectar):
        assert _locks_ativos(918130003) == 1


def test_lock_liberado_ao_sair_do_bloco():
    with L.fato_diaria_lock(3, connect=_conectar):
        pass
    assert _locks_ativos(918130003) == 0


def test_segunda_conexao_e_recusada_enquanto_a_primeira_segura():
    with L.fato_diaria_lock(3, connect=_conectar):
        with pytest.raises(L.FatoDiariaLockUnavailable):
            with L.fato_diaria_lock(3, connect=_conectar):
                pytest.fail("a segunda conexão não podia entrar")


def test_segunda_conexao_entra_depois_que_a_primeira_solta():
    with L.fato_diaria_lock(3, connect=_conectar):
        pass
    with L.fato_diaria_lock(3, connect=_conectar):
        assert _locks_ativos(918130003) == 1


def test_marketplaces_distintos_nao_se_bloqueiam():
    """ML publicando não pode impedir a Shopee: são linhas diferentes."""
    with L.fato_diaria_lock(2, connect=_conectar):
        with L.fato_diaria_lock(3, connect=_conectar):
            assert _locks_ativos(918130002) == 1
            assert _locks_ativos(918130003) == 1


# ---------------------------------------------------------------------------
# 2. Os três writers Shopee entre si
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "primeiro,segundo",
    [
        ("shopee", "shopee-stats"),
        ("shopee", "shopee-ads"),
        ("shopee-stats", "shopee-ads"),
    ],
)
def test_writers_shopee_se_excluem_entre_si(primeiro, segundo):
    """Orders × stats e orders × ads são o cenário que motiva o gate: cada um
    escreve metade da linha e conta com a metade do outro."""
    with L.fato_diaria_lock(3, connect=_conectar):  # `primeiro` segurando
        with pytest.raises(L.FatoDiariaLockUnavailable):
            with L.fato_diaria_lock(3, connect=_conectar):  # `segundo` tentando
                pytest.fail(f"{segundo} entrou enquanto {primeiro} publicava")


# ---------------------------------------------------------------------------
# 3. Manual × runner do Airflow simulado
# ---------------------------------------------------------------------------
def test_writer_do_airflow_simulado_bloqueia_o_manual():
    """O runner do Airflow não importa este módulo: ele executa o LITERAL. Este
    teste usa exatamente a chamada documentada no contrato — se o literal e a
    constante divergirem, as duas esteiras publicam juntas e só este teste vê."""
    from sqlalchemy import text

    with _conectar() as airflow:
        adquirido = airflow.execute(
            text("SELECT pg_try_advisory_lock(918130003)")
        ).scalar_one()
        assert adquirido is True
        with pytest.raises(L.FatoDiariaLockUnavailable):
            with L.fato_diaria_lock(3, connect=_conectar):
                pytest.fail("o writer manual entrou por cima do runner do Airflow")
        airflow.execute(text("SELECT pg_advisory_unlock(918130003)"))


def test_manual_bloqueia_o_writer_do_airflow_simulado():
    from sqlalchemy import text

    with L.fato_diaria_lock(3, connect=_conectar):
        with _conectar() as airflow:
            adquirido = airflow.execute(
                text("SELECT pg_try_advisory_lock(918130003)")
            ).scalar_one()
            assert adquirido is False, "o runner do Airflow entraria durante o refresh manual"


def test_forma_de_dois_argumentos_nao_se_exclui_com_a_nossa():
    """Contraprova do contrato: `pg_try_advisory_lock(918130, 3)` ocupa OUTRO
    espaço de chaves. Documentar a forma de um argumento não é preciosismo."""
    from sqlalchemy import text

    with L.fato_diaria_lock(3, connect=_conectar):
        with _conectar() as outro:
            entrou = outro.execute(
                text("SELECT pg_try_advisory_lock(918130, 3)")
            ).scalar_one()
            assert entrou is True, (
                "se este assert falhar, as duas formas passaram a colidir e o "
                "aviso do contrato pode ser revisto"
            )
            outro.execute(text("SELECT pg_advisory_unlock(918130, 3)"))


# ---------------------------------------------------------------------------
# 4. Concorrência de verdade, em threads
# ---------------------------------------------------------------------------
def test_duas_threads_disputando_apenas_uma_entra():
    entraram: list[str] = []
    recusadas: list[str] = []
    portao = threading.Event()

    def tentar(nome: str) -> None:
        portao.wait()
        try:
            with L.fato_diaria_lock(3, connect=_conectar):
                entraram.append(nome)
                time.sleep(0.3)
        except L.FatoDiariaLockUnavailable:
            recusadas.append(nome)

    t1 = threading.Thread(target=tentar, args=("A",))
    t2 = threading.Thread(target=tentar, args=("B",))
    t1.start()
    t2.start()
    portao.set()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert len(entraram) == 1, f"entraram={entraram} recusadas={recusadas}"
    assert len(recusadas) == 1
    assert _locks_ativos(918130003) == 0


# ---------------------------------------------------------------------------
# 5. Falha, rollback e o estado da fotografia
# ---------------------------------------------------------------------------
def test_falha_no_corpo_libera_o_lock(banco):
    class Erro(RuntimeError):
        pass

    with pytest.raises(Erro):
        with L.fato_diaria_lock(3, connect=_conectar):
            raise Erro("quebrou antes do upsert")
    assert _locks_ativos(918130003) == 0


def test_falha_depois_do_upsert_libera_o_lock_e_preserva_o_commitado(banco):
    from sqlalchemy import text

    class Erro(RuntimeError):
        pass

    with pytest.raises(Erro):
        with L.fato_diaria_lock(3, connect=_conectar):
            with banco.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO marts.fact_marketplace_daily_performance "
                        "(date, loja_id, marketplace_id, empresa_id, gmv) "
                        "VALUES ('2026-09-01', 2, 3, 1, 100)"
                    )
                )
                conn.commit()
            raise Erro("quebrou depois do upsert")

    assert _locks_ativos(918130003) == 0
    with banco.connect() as conn:
        gmv = conn.execute(
            text("SELECT gmv FROM marts.fact_marketplace_daily_performance")
        ).scalar_one()
    assert gmv == 100, "o que já estava commitado não é desfeito pelo lock"


def test_rollback_preserva_a_fotografia_anterior(banco):
    from sqlalchemy import text

    with banco.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO marts.fact_marketplace_daily_performance "
                "(date, loja_id, marketplace_id, empresa_id, gmv, orders) "
                "VALUES ('2026-09-01', 2, 3, 1, 500, 10)"
            )
        )
        conn.commit()

    class Erro(RuntimeError):
        pass

    with pytest.raises(Erro):
        with L.fato_diaria_lock(3, connect=_conectar):
            with banco.connect() as conn:
                conn.execute(
                    text(
                        "UPDATE marts.fact_marketplace_daily_performance "
                        "SET gmv = 999 WHERE date='2026-09-01'"
                    )
                )
                raise Erro("falha antes do commit")

    with banco.connect() as conn:
        gmv, orders = conn.execute(
            text("SELECT gmv, orders FROM marts.fact_marketplace_daily_performance")
        ).first()
    assert gmv == 500 and orders == 10


def test_commit_indeterminado_nao_e_repetido(banco):
    """O contextmanager não tenta de novo e não engole a exceção: a decisão de
    republicar é humana, depois de reconciliar."""
    from sqlalchemy import text

    tentativas = {"n": 0}

    class CommitIndeterminado(RuntimeError):
        pass

    with pytest.raises(CommitIndeterminado):
        with L.fato_diaria_lock(3, connect=_conectar):
            tentativas["n"] += 1
            with banco.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO marts.fact_marketplace_daily_performance "
                        "(date, loja_id, marketplace_id, empresa_id, gmv) "
                        "VALUES ('2026-09-02', 2, 3, 1, 42)"
                    )
                )
                conn.commit()
            raise CommitIndeterminado("commit sem resposta")

    assert tentativas["n"] == 1
    assert _locks_ativos(918130003) == 0
    with banco.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM marts.fact_marketplace_daily_performance "
                 "WHERE date='2026-09-02'")
        ).scalar_one()
    assert n == 1, "nenhuma segunda publicação"


# ---------------------------------------------------------------------------
# 6. Zero auditoria quando o lock não foi adquirido — run() de verdade
# ---------------------------------------------------------------------------
def test_run_recusado_nao_abre_linha_de_auditoria(banco, monkeypatch):
    """A prova do requisito: lock antes da auditoria. Se o `with` estivesse
    depois de `_start_sync_run`, cada tentativa recusada deixaria uma linha
    `running` órfã — e o health check as lê como sync travado."""
    from contextlib import contextmanager

    from sqlalchemy import text

    from pipelines.ingestion import daily_performance as dp

    @contextmanager
    def sessao_do_banco_de_teste():
        conn = banco.connect()
        try:
            yield _SessaoSQLAlchemy(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    monkeypatch.setattr(dp, "local_session", sessao_do_banco_de_teste)
    monkeypatch.setattr(
        dp, "fato_diaria_lock", lambda mid: L.fato_diaria_lock(mid, connect=_conectar)
    )

    def nao_deve_ser_chamado(*a, **kw):
        pytest.fail("a fonte foi lida apesar do lock ocupado")

    monkeypatch.setattr(dp.shopee_connector, "fetch_incremental", nao_deve_ser_chamado)

    with L.fato_diaria_lock(3, connect=_conectar):  # outra esteira publicando
        with pytest.raises(L.FatoDiariaLockUnavailable):
            dp.run(source="shopee", mode="incremental")

    with banco.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM audit.source_sync_run")).scalar_one()
    assert n == 0, "nenhuma linha de auditoria pode nascer de uma tentativa recusada"


class _SessaoSQLAlchemy:
    """Adaptador mínimo: `run()` fala a API de Session, o teste tem Connection."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, clause, params=None):
        return self._conn.execute(clause, params or {})

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 7. Exit code — processo de verdade, como o orquestrador o executa
# ---------------------------------------------------------------------------
def test_processo_recusado_sai_com_exit_code_de_lock_ocupado():
    """`pipelines/ops/orchestrate.py` roda cada step com `subprocess.run` e lê o
    `returncode`. Um lock ocupado tem de sair com 75 (`EX_TEMPFAIL`), e não com
    1 genérico: é a diferença, no log do full_daily, entre "outra esteira estava
    publicando" e "o step quebrou".

    Roda o módulo DE VERDADE, com o lock já tomado por outra conexão. A fonte
    nunca é lida — não há arquivo XLSX neste ambiente e o teste passa mesmo
    assim, o que é em si a prova de que o lock vem antes da leitura.
    """
    import subprocess
    import sys
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["DATABASE_URL"] = DSN or ""
    env["DATAMART_DATABASE_URL"] = DSN or ""
    env["PYTHONPATH"] = str(raiz)

    with L.fato_diaria_lock(3, connect=_conectar):
        proc = subprocess.run(
            [sys.executable, "-m", "pipelines.ingestion.daily_performance",
             "--source", "shopee", "--mode", "incremental"],
            cwd=str(raiz), env=env, capture_output=True, timeout=180,
        )

    assert proc.returncode == L.EXIT_CODE_LOCK_UNAVAILABLE, (
        f"returncode={proc.returncode}\n"
        f"stdout={proc.stdout.decode('utf-8', 'replace')[-2000:]}\n"
        f"stderr={proc.stderr.decode('utf-8', 'replace')[-2000:]}"
    )
    saida = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    assert "918130003" in saida, "a mensagem precisa nomear a chave disputada"


def test_processo_recusado_nao_deixa_auditoria_nem_lock(banco):
    import subprocess
    import sys
    from pathlib import Path

    from sqlalchemy import text

    raiz = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["DATABASE_URL"] = DSN or ""
    env["DATAMART_DATABASE_URL"] = DSN or ""
    env["PYTHONPATH"] = str(raiz)

    with L.fato_diaria_lock(3, connect=_conectar):
        proc = subprocess.run(
            [sys.executable, "-m", "pipelines.ingestion.daily_performance",
             "--source", "shopee-stats", "--mode", "incremental"],
            cwd=str(raiz), env=env, capture_output=True, timeout=180,
        )
    assert proc.returncode == L.EXIT_CODE_LOCK_UNAVAILABLE

    with banco.connect() as conn:
        n_audit = conn.execute(
            text("SELECT count(*) FROM audit.source_sync_run")
        ).scalar_one()
        n_fato = conn.execute(
            text("SELECT count(*) FROM marts.fact_marketplace_daily_performance")
        ).scalar_one()
    assert n_audit == 0, "zero auditoria quando o lock não foi adquirido"
    assert n_fato == 0, "zero escrita"
    assert _locks_ativos(918130003) == 0
