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

    Saida legitima para quem precisa exercitar o fluxo: injetar as tres
    fabricas, como faz `test_expedicao_ml_apply.py`, ou aponta-las para um
    PostgreSQL descartavel.

    Esta guarda cobre `run_apply`. Quem fecha `_run_diagnose` e `_run_reconcile`
    e' a barreira por DESTINO, mais abaixo - o comentario no fim desta funcao
    explica por que elas nao podem ser substituidas aqui.
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
    # aceitam injecao. NAO sao substituidos aqui: ha testes legitimos que leem
    # o CODIGO-FONTE delas (`inspect.getsource`) para provar que abrem
    # read-only e nao publicam - trocar a funcao faria esses testes lerem a
    # guarda em vez do alvo. Quem as fecha e' a barreira por DESTINO abaixo,
    # que pega qualquer caminho ate' um banco remoto, inclusive os que ainda
    # nao existem.


#: Hosts que a suite pode alcancar. Tudo o mais e' recusado por default.
#: PostgreSQL descartavel dos testes de integracao sobe em localhost, entao a
#: lista cobre o uso legitimo inteiro. Host vazio = socket unix local.
HOSTS_LOCAIS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def host_do_destino(args, kwargs):
    """O host que uma chamada a `psycopg2.connect` alcancaria.

    Entende as TRES formas: DSN em URL (`postgresql://...`), DSN em palavras-
    chave (`host=... dbname=...`) e kwargs soltos (`connect(host=...)`). Quem
    faz o trabalho e' `parse_dsn` do proprio psycopg2 - comparar substring
    seria fragil e daria falso negativo para qualquer host novo.

    Devolve `None` quando a DSN nao e' decifravel, e o chamador trata isso como
    NAO local: na duvida, recusa.
    """
    from psycopg2.extensions import parse_dsn

    campos = {}
    bruto = args[0] if args else kwargs.get("dsn")
    if bruto is not None:
        if not isinstance(bruto, str):
            return None
        try:
            campos = parse_dsn(bruto)
        except Exception:  # noqa: BLE001 - indecifravel e' tratado como remoto
            return None
    for chave in ("hostaddr", "host"):
        if kwargs.get(chave):
            campos[chave] = kwargs[chave]
    host = campos.get("hostaddr") or campos.get("host") or ""
    if host:
        return host
    # Sem host explicito o libpq NAO vai direto ao socket local: ele consulta
    # `PGHOSTADDR`/`PGHOST` antes. Ignorar isso deixaria `connect("dbname=d")`
    # alcancar um servidor remoto sem que nenhuma DSN mencionasse o host.
    import os

    return os.environ.get("PGHOSTADDR") or os.environ.get("PGHOST") or ""


def destino_e_local(args, kwargs):
    host = host_do_destino(args, kwargs)
    return host is not None and host in HOSTS_LOCAIS


@pytest.fixture(autouse=True)
def _sem_conexao_com_producao(monkeypatch):
    """Ultima linha: a suite so' alcanca banco LOCAL, por qualquer caminho.

    As guardas por funcao fecham as portas conhecidas. Esta fecha pelo DESTINO,
    que e' o que importa: qualquer `psycopg2.connect` para um host que nao seja
    local e' recusado, venha de onde vier - inclusive de um caminho criado
    depois desta guarda, e inclusive quando o `.env` nao esta' presente.

    E' default-deny de proposito. Uma lista do que e' PROIBIDO envelhece: basta
    um host novo, ou a mesma DSN escrita de outro jeito, para passar batido. Uma
    lista do que e' PERMITIDO so' envelhece para o lado seguro - um destino
    legitimo novo falha em voz alta e entra aqui de forma deliberada.

    Integracao legitima usa PostgreSQL descartavel em localhost e nao e'
    afetada (ver `banco_descartavel.py`).
    """
    try:
        import psycopg2
    except Exception:  # noqa: BLE001 - sem o driver nao ha o que proteger
        return

    original = psycopg2.connect

    def connect(*args, **kwargs):
        if not destino_e_local(args, kwargs):
            raise ConexaoRealBloqueada(
                "um teste tentou conectar num banco que NAO e' local. A suite "
                "so' alcanca localhost - testes focais injetam dubles e testes "
                "de integracao usam PostgreSQL descartavel. "
                "Ver pipelines/tests/conftest.py."
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(psycopg2, "connect", connect)
