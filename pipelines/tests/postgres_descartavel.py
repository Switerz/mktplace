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
    senha = base / "pwd.txt"
    senha.write_text("postgres", encoding="utf-8")

    env = dict(os.environ)
    # Em cluster efemero, o log do servidor nao deve poluir a saida do teste.
    log = base / "server.log"

    try:
        subprocess.run(
            [str(PG_BIN / "initdb.exe"), "-D", str(data), "-U", "postgres",
             "--pwfile", str(senha), "-A", "trust", "-E", "UTF8",
             "--no-locale"],
            check=True, capture_output=True, env=env, timeout=timeout_segundos,
        )
        subprocess.run(
            [str(PG_BIN / "pg_ctl.exe"), "-D", str(data), "-l", str(log),
             "-o", f"-p {porta} -c listen_addresses=127.0.0.1 -c fsync=off",
             "-w", "-t", str(timeout_segundos), "start"],
            check=True, capture_output=True, env=env,
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
            subprocess.run(
                [str(PG_BIN / "pg_ctl.exe"), "-D", str(data), "-m", "immediate",
                 "-w", "-t", "20", "stop"],
                capture_output=True, env=env, timeout=40,
            )
        except Exception:
            pass
        shutil.rmtree(base, ignore_errors=True)


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
