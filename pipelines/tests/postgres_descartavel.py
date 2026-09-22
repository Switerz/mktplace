"""Gate PMA-2C5B — cluster PostgreSQL DESCARTAVEL para teste.

Por que um banco de verdade e nao um dublê: advisory lock e maquina de estados
de auditoria sao comportamento do SERVIDOR, nao do codigo. Um fake devolve o
que o teste mandou — ele nao sabe que `pg_try_advisory_lock` falha para a
segunda sessao, nem que um `COMMIT` confirmado nao volta atras. Provar exclusao
mutua contra um dublê provaria apenas que o dublê concorda com o teste.

O cluster nasce num diretorio temporario, escuta numa porta livre, vive dentro
de um unico teste e e' destruido no fim. Nunca toca Neon, Data Mart ou qualquer
banco configurado: a URL sai daqui, e so' daqui.

Ausencia dos binarios NAO e' falha: o teste pula com motivo. A maquina de
desenvolvimento tem `.local/postgres16`; um runner sem Postgres nao deve
reprovar o PR por isso.
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_EXES = ("initdb.exe", "pg_ctl.exe", "postgres.exe")

MOTIVO_SEM_POSTGRES = (
    "PostgreSQL descartavel indisponivel: nenhum initdb/pg_ctl encontrado em "
    "PMA_TEST_PG_BIN, .local/postgres16 ou no PATH. O teste exige servidor "
    "real — advisory lock e COMMIT nao se provam contra dublê."
)


def _candidatos_pg_bin() -> list[Path]:
    """Onde procurar o Postgres embarcado, em ordem de precedencia.

    A worktree de um gate nao carrega `.local/` (ignorado pelo git), entao
    procurar SO' em `REPO_ROOT` faria o teste pular exatamente onde ele
    precisa rodar. `PMA_TEST_PG_BIN` existe para o operador apontar um cluster
    proprio sem editar codigo.
    """
    caminhos: list[Path] = []
    do_ambiente = os.environ.get("PMA_TEST_PG_BIN")
    if do_ambiente:
        caminhos.append(Path(do_ambiente))
    caminhos.append(REPO_ROOT / ".local" / "postgres16" / "pgsql" / "bin")
    achado = shutil.which("pg_ctl")
    if achado:
        caminhos.append(Path(achado).parent)
    return caminhos


def _resolver_pg_bin() -> Path | None:
    for caminho in _candidatos_pg_bin():
        if caminho.is_dir() and all((caminho / exe).is_file() for exe in _EXES):
            return caminho
    return None


#: Resolvido uma vez; `None` quando nao ha Postgres utilizavel.
PG_BIN = _resolver_pg_bin()


def postgres_disponivel() -> bool:
    return PG_BIN is not None


def _porta_livre() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def cluster_descartavel(timeout_segundos: int = 60):
    """Sobe um cluster proprio e devolve a URL. Destroi tudo na saida.

    O `finally` roda em QUALQUER desfecho, inclusive excecao no corpo do teste:
    um cluster orfao segurando porta e diretorio seria pior que o teste que
    falhou.
    """
    if not postgres_disponivel():
        raise RuntimeError(MOTIVO_SEM_POSTGRES)

    base = Path(tempfile.mkdtemp(prefix="pma_pg_"))
    data = base / "data"
    porta = _porta_livre()

    env = dict(os.environ)
    # Em cluster efemero, o log do servidor nao deve poluir a saida do teste.
    log = base / "server.log"

    try:
        # `-N` (sem fsync) e' o que torna o `initdb` viavel dentro de um teste:
        # com fsync o cluster leva minutos nesta maquina. Num cluster que sera'
        # destruido no fim do teste, durabilidade nao compra nada.
        #
        # `-A trust` sem `--pwfile`: o servidor so' escuta em 127.0.0.1 numa
        # porta efemera e morre junto com o teste. Um arquivo de senha aqui so'
        # criaria um segredo em disco sem proteger nada.
        # `capture_output=True` NAO serve aqui, e a razao custou uma
        # investigacao: no Windows, `subprocess.run` com PIPE so' retorna
        # quando TODOS os escritores do pipe fecham — e `initdb`/`pg_ctl`
        # deixam processos filhos que herdam esses handles. O comando termina,
        # o pipe nao, e a chamada fica pendurada indefinidamente. Redirecionar
        # para ARQUIVO nao tem esse problema: nao ha pipe para ficar aberto.
        saida = base / "initdb.log"
        with saida.open("wb") as fh:
            subprocess.run(
                [str(PG_BIN / "initdb.exe"), "-D", str(data), "-U", "postgres",
                 "-A", "trust", "-E", "UTF8", "--no-locale", "-N"],
                check=True, stdout=fh, stderr=subprocess.STDOUT, env=env,
                timeout=timeout_segundos,
            )
        with (base / "pgctl.log").open("wb") as fh:
            subprocess.run(
                [str(PG_BIN / "pg_ctl.exe"), "-D", str(data), "-l", str(log),
                 "-o", f"-p {porta} -c listen_addresses=127.0.0.1 -c fsync=off",
                 "-w", "-t", str(timeout_segundos), "start"],
                check=True, stdout=fh, stderr=subprocess.STDOUT, env=env,
                timeout=timeout_segundos + 10,
            )
        url = f"postgresql://postgres@127.0.0.1:{porta}/postgres"
        # `pg_ctl -w` ja espera, mas uma conexao de prova elimina a corrida
        # entre "servidor aceitou socket" e "servidor aceita consulta".
        import psycopg2
        for _ in range(30):
            try:
                with closing(psycopg2.connect(url, connect_timeout=2)) as c:
                    with c.cursor() as cur:
                        cur.execute("SELECT 1")
                break
            except Exception:
                time.sleep(0.5)
        yield url
    finally:
        try:
            # Sem PIPE, pelo mesmo motivo do `initdb` acima: um teardown
            # pendurado seria pior que o teste que falhou.
            subprocess.run(
                [str(PG_BIN / "pg_ctl.exe"), "-D", str(data), "-m", "immediate",
                 "-w", "-t", "20", "stop"],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                env=env, timeout=40,
            )
        except Exception:  # noqa: BLE001 — teardown nunca derruba a suite
            pass
        shutil.rmtree(base, ignore_errors=True)


#: Cluster UNICO por sessao de teste.
#:
#: Cada `cluster_descartavel()` custa um `initdb` inteiro — dezenas de segundos
#: a minutos nesta maquina. Seis testes com cluster proprio transformariam a
#: suite em algo que ninguem roda, e teste que ninguem roda nao protege nada.
#: O cluster e' criado na primeira chamada, reaproveitado por todas as
#: seguintes e destruido no fim do processo.
#:
#: Reaproveitar NAO enfraquece as provas: advisory lock e' por SESSAO de
#: conexao, nao por cluster, e cada teste abre e fecha as suas. O que o teste
#: precisa e' de um servidor PostgreSQL de verdade, e um basta.
_CLUSTER_DA_SESSAO: dict = {}


@contextmanager
def cluster_da_sessao():
    """Devolve a URL do cluster da sessao, criando-o na primeira chamada."""
    if "url" not in _CLUSTER_DA_SESSAO:
        gerenciador = cluster_descartavel()
        url = gerenciador.__enter__()
        _CLUSTER_DA_SESSAO["url"] = url
        _CLUSTER_DA_SESSAO["gerenciador"] = gerenciador
        atexit.register(_derrubar_cluster_da_sessao)
    yield _CLUSTER_DA_SESSAO["url"]


def _derrubar_cluster_da_sessao() -> None:
    gerenciador = _CLUSTER_DA_SESSAO.pop("gerenciador", None)
    _CLUSTER_DA_SESSAO.pop("url", None)
    if gerenciador is not None:
        try:
            gerenciador.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 — teardown nunca derruba a suite
            pass


DDL_AUDITORIA = """
CREATE SCHEMA IF NOT EXISTS audit;
CREATE TABLE IF NOT EXISTS audit.source_sync_run (
    sync_run_id     bigserial PRIMARY KEY,
    source_name     text NOT NULL,
    marketplace_id  int,
    loja_id         int,
    status          text NOT NULL,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    rows_extracted  bigint,
    rows_loaded     bigint,
    error_message   text,
    source_min_date date,
    source_max_date date,
    CONSTRAINT source_sync_run_status_check
        CHECK (status IN ('running', 'success', 'failed'))
);
"""
