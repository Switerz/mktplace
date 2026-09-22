"""Guarda de isolamento da suíte de `pipelines`.

🔴 POR QUE ESTE ARQUIVO EXISTE

O Gate SH-AUTO-1 fez `daily_performance.run()` adquirir um advisory lock antes
de qualquer outra coisa. Quem chamasse `run()` sem substituir o lock passaria a
abrir uma conexão **real** com o banco de `DATABASE_URL` — e, num ambiente com
`.env` presente, esse banco é o **Neon de produção**.

Foi o que aconteceu: cinco testes de
`test_daily_performance_shopee_orders_patch.py` continuaram verdes na máquina de
quem tinha `.env` (tocando produção a cada execução) e quebraram na máquina sem
`.env`. O nome do arquivo — testes focais do texto de um SQL — não sugeria nada
disso, e a suíte não tinha como avisar.

A guarda abaixo fecha essa porta para sempre: `_default_connect` é substituída
por uma função que **levanta**. Um teste que precise do lock tem duas saídas
legítimas, as duas explícitas:

  1. injetar um dublê — `fato_diaria_lock(mid, connect=lambda: ConexaoDeLock())`;
  2. injetar uma conexão de banco descartável — o que os testes de integração
     fazem, via `banco_descartavel.conectar`.

Nenhuma das duas passa por `_default_connect`, então nenhuma é afetada.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Gate PMA-2C5B-R/V — guarda GERAL de host, acima da guarda pontual abaixo
#
# A guarda do #26 fecha UMA porta: `fato_diaria_lock._default_connect`. Ela
# resolve o caso que a motivou e nao alcanca os outros — qualquer modulo que
# chame `psycopg2.connect` com uma URL vinda do ambiente continua podendo abrir
# conexao com Neon ou Data Mart quando existe `.env` na maquina. O incidente
# EXP-3B2-I1 e' desta familia.
#
# Esta guarda fecha a porta pelo DESTINO, que e' o que realmente importa: um
# teste pode falar com PostgreSQL LOCAL (o cluster descartavel de
# `postgres_descartavel.py` escuta em 127.0.0.1 numa porta efemera), e nao pode
# falar com host remoto nenhum. Nao ha allowlist de credencial, nao ha excecao
# por nome de teste, e a mensagem nao imprime a URL — so' o host.
#
# Quem precisa de banco real num teste tem duas saidas legitimas, as duas
# explicitas: dublê injetado, ou `cluster_descartavel()`.
# ---------------------------------------------------------------------------

_HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1", "", None}


def _host_da_url(url: str) -> str | None:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:  # noqa: BLE001 — URL ilegivel nao e' local
        return "(ilegivel)"


@pytest.fixture(autouse=True)
def _sem_conexao_com_host_remoto(monkeypatch):
    """Nenhum teste abre conexao com banco que nao seja local."""
    try:
        import psycopg2
    except Exception:  # noqa: BLE001
        return

    real = psycopg2.connect

    def vigiado(dsn=None, *args, **kwargs):
        alvo = dsn if isinstance(dsn, str) else kwargs.get("dsn") or kwargs.get("host", "")
        host = _host_da_url(alvo) if "://" in str(alvo) else str(alvo or "").lower()
        if host not in _HOSTS_LOCAIS:
            raise AssertionError(
                f"um teste tentou abrir conexao com o host remoto {host!r}. "
                "A suite de `pipelines` so' pode falar com PostgreSQL LOCAL.\n"
                "Com `.env` na maquina, uma URL de ambiente aponta para o Neon "
                "de producao ou para o Data Mart — foi assim que o incidente "
                "EXP-3B2-I1 passou despercebido.\n"
                "Injete um dublê, ou use "
                "`pipelines.tests.postgres_descartavel.cluster_descartavel()`."
            )
        return real(dsn, *args, **kwargs) if dsn is not None else real(*args, **kwargs)

    monkeypatch.setattr(psycopg2, "connect", vigiado)


@pytest.fixture(autouse=True)
def _sem_conexao_real_no_lock(monkeypatch):
    """Nenhum teste pode abrir a conexão de produção do advisory lock."""
    try:
        from pipelines.ingestion import fato_diaria_lock
    except Exception:  # noqa: BLE001 — sem o módulo não há o que proteger
        return

    def recusar():
        raise AssertionError(
            "um teste tentou abrir a conexao REAL do advisory lock "
            "(`fato_diaria_lock._default_connect`), que aponta para "
            "`DATABASE_URL` — em maquina com `.env`, o Neon de producao.\n"
            "Injete a conexao: `fato_diaria_lock(marketplace_id, connect=...)`, "
            "com `ConexaoDeLock` (dublê) ou `banco_descartavel.conectar` "
            "(PostgreSQL descartavel). Ver pipelines/tests/conftest.py."
        )

    monkeypatch.setattr(fato_diaria_lock, "_default_connect", recusar)
