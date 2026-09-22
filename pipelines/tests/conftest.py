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
