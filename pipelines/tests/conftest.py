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


class ConexaoRealBloqueada(BaseException):
    """Recusa de conexao real. Deriva de BaseException DE PROPOSITO.

    `cli.run_apply` termina com `except Exception` - a fronteira que impede um
    job agendado de morrer com stack trace. Uma recusa derivada de `Exception`
    seria engolida ali e virava um exit code: o teste passaria a "falhar
    bonito" em vez de denunciar que tentou abrir producao. Derivando de
    `BaseException` a recusa atravessa a fronteira e chega ao pytest.
    """


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
@pytest.fixture(autouse=True)
def _sem_conexao_real_na_expedicao(monkeypatch):
    """Nenhum teste pode abrir as conexoes de producao da Expedicao.

    MESMA classe de defeito da guarda acima, com uma armadilha a mais.

    `cli.run_apply` declara as fabricas como DEFAULT DE PARAMETRO:

        def run_apply(canal, efetivo, *, open_target=open_target_default, ...)

    Defaults sao avaliados na DEFINICAO da funcao e ficam gravados em
    `run_apply.__kwdefaults__`. Trocar `cli.open_target_default` depois do
    import NAO muda o que a chamada sem kwargs vai usar. Por isso a guarda
    remenda os dois lugares: o nome no modulo e o valor ja amarrado.

    Foi exatamente esse buraco que publicou cinco vezes em producao no
    incidente EXP-3B2-I1: `test_cli_recusa_apply_do_mercadolivre` chamava
    `cli.run_apply(Channel.MERCADOLIVRE, AGORA)` sem injetar nada. Enquanto o
    ML nao tinha adaptador, a guarda de allowlist devolvia EXIT_PRECONDICAO
    antes de abrir conexao e o teste era inofensivo. O PR #24 deu adaptador ao
    ML, a guarda deixou de disparar e a MESMA linha passou a abrir o Neon e a
    publicar a fila inteira, com o relogio fixo da fixture.

    `_run_diagnose` e `_run_reconcile` constroem a conexao inline e nao aceitam
    injecao nenhuma: para eles nao ha uso legitimo dentro da suite, entao a
    guarda simplesmente recusa.

    Saida legitima para quem precisa exercitar o fluxo: injetar as tres
    fabricas, como faz `test_expedicao_ml_apply.py`, ou aponta-las para um
    PostgreSQL descartavel.
    """
    try:
        from pipelines.expedicao import cli
    except Exception:  # noqa: BLE001 - sem o modulo nao ha o que proteger
        return

    def recusar(alvo: str):
        def _recusa(*_a, **_k):
            raise ConexaoRealBloqueada(
                f"um teste tentou abrir a conexao REAL da Expedicao ({alvo}), "
                "que aponta para DATABASE_URL/DATAMART_DATABASE_URL - em "
                "maquina com `.env`, o Neon de producao e o Data Mart.\n"
                "Injete as fabricas: cli.run_apply(canal, efetivo, "
                "open_target=..., open_source=..., open_audit=...). "
                "Ver pipelines/tests/conftest.py."
            )

        return _recusa

    # 1) os nomes no modulo, para quem os referencie diretamente.
    for nome in ("open_target_default", "open_source_default", "open_audit_default"):
        monkeypatch.setattr(cli, nome, recusar(nome))

    # 2) os valores JA AMARRADOS em run_apply. Sem este passo o item 1 nao tem
    #    efeito algum sobre uma chamada que omite os kwargs - que e' o caso que
    #    causou o incidente.
    amarrados = dict(cli.run_apply.__kwdefaults__ or {})
    for chave in ("open_target", "open_source", "open_audit"):
        if chave in amarrados:
            amarrados[chave] = recusar(f"{chave} (default amarrado)")
    monkeypatch.setattr(cli.run_apply, "__kwdefaults__", amarrados)

    # `_run_diagnose` e `_run_reconcile` constroem a conexao inline e nao
    # aceitam injecao. NAO sao substituidos aqui: ha testes legitimos que
    # leem o CODIGO-FONTE delas (`inspect.getsource`) para provar que abrem
    # read-only e nao publicam - trocar a funcao faria esses testes lerem a
    # guarda em vez do alvo. Quem as fecha e' a barreira de DSN abaixo, que
    # pega qualquer caminho ate' producao, inclusive os que ainda nao
    # existem.
#: Variaveis que apontam para producao. Uma conexao para qualquer uma delas
#: dentro da suite e' sempre um defeito.
ENV_DE_PRODUCAO = ("DATABASE_URL", "DATAMART_DATABASE_URL")


def dsns_de_producao(ambiente):
    """As DSNs de producao presentes no ambiente. Vazio = nada a bloquear."""
    return {v for v in (ambiente.get(k) for k in ENV_DE_PRODUCAO) if v}


@pytest.fixture(autouse=True)
def _sem_conexao_com_producao(monkeypatch):
    """Ultima linha: nenhuma conexao para uma DSN de producao, por nenhum caminho.

    As guardas por funcao fecham as portas CONHECIDAS. Esta fecha a porta pelo
    DESTINO, que e' o que de fato importa: qualquer `psycopg2.connect` cuja DSN
    seja a de `DATABASE_URL` ou `DATAMART_DATABASE_URL` e' recusado, venha de
    onde vier - inclusive de um caminho criado depois desta guarda.

    Bloquear por DSN, e nao `psycopg2.connect` inteiro, e' deliberado: os testes
    de integracao legitimos usam PostgreSQL descartavel, cuja DSN e' outra, e
    seguem funcionando. Numa maquina sem `.env` o conjunto e' vazio e a guarda
    nao faz nada - o que tambem explica por que ela nao substitui as demais:
    e' justamente na maquina COM `.env` que o incidente acontece.
    """
    import os

    proibidas = dsns_de_producao(os.environ)
    if not proibidas:
        return

    try:
        import psycopg2
    except Exception:  # noqa: BLE001
        return

    original = psycopg2.connect

    def connect(*args, **kwargs):
        dsn = args[0] if args else (kwargs.get("dsn") or kwargs.get("dbname"))
        if dsn in proibidas:
            raise ConexaoRealBloqueada(
                "um teste tentou conectar numa DSN de PRODUCAO "
                "(DATABASE_URL/DATAMART_DATABASE_URL). Testes de integracao "
                "usam PostgreSQL descartavel; testes focais injetam dubles. "
                "Ver pipelines/tests/conftest.py."
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(psycopg2, "connect", connect)
