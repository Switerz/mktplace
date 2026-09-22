"""EXP-3B2-I1 - trava estrutural e contraprovas do isolamento da Expedicao.

O incidente: `test_cli_recusa_apply_do_mercadolivre` chamava
`cli.run_apply(Channel.MERCADOLIVRE, AGORA)` sem injetar fabrica. Enquanto o ML
nao tinha adaptador isso parava em EXIT_PRECONDICAO antes de qualquer conexao.
O PR #24 deu adaptador ao ML e a mesma linha passou a abrir o Neon e publicar a
fila inteira - cinco vezes, com o relogio fixo da fixture (2026-09-17 18:00).

Este arquivo existe para que a porta nao volte a abrir por uma mudanca de
contrato num arquivo distante. Sao duas camadas:

  - ESTRUTURAL: nenhuma chamada de apply na suite pode omitir as fabricas;
  - COMPORTAMENTAL: a guarda autouse do conftest realmente fecha as tres.

A camada estrutural sozinha nao basta (um helper pode mascarar a omissao) e a
comportamental sozinha tambem nao (alguem pode remover a guarda). Juntas, uma
denuncia o que a outra deixa passar.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import cli
from pipelines.tests import conftest as guarda
from pipelines.expedicao.contract import Channel

AGORA = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)
FABRICAS = ("open_target", "open_source", "open_audit")
DIR_TESTES = Path(__file__).resolve().parent
RECUSA = "conexao REAL da Expedicao"
#: Unica isencao aceita pela trava estrutural. Escrever o marcador na
#: propria chamada torna a excecao visivel num grep e obriga quem a usa a
#: declarar a intencao - diferente de um allowlist por nome de arquivo,
#: que envelhece em silencio.
MARCADOR = "isolamento-suite: chamada deliberada"


def _chamadas_de_apply():
    """Toda chamada a `cli.run_apply` na suite, com os kwargs que ela passa."""
    achados = []
    for arquivo in sorted(DIR_TESTES.glob("test_*.py")):
        fonte = arquivo.read_text(encoding="utf-8")
        linhas = fonte.splitlines()
        arvore = ast.parse(fonte)
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            alvo = no.func
            if not isinstance(alvo, ast.Attribute) or alvo.attr != "run_apply":
                continue
            if getattr(alvo.value, "id", None) != "cli":
                continue
            intervalo = linhas[no.lineno - 1:no.end_lineno]
            if any(MARCADOR in l for l in intervalo):
                continue
            nomeados = {k.arg for k in no.keywords if k.arg}
            estrela = any(k.arg is None for k in no.keywords)
            achados.append((arquivo.name, no.lineno, nomeados, estrela))
    return achados


def test_a_suite_tem_pelo_menos_uma_chamada_de_apply():
    """Guarda da guarda: um scanner que nao acha nada passa por engano."""
    assert _chamadas_de_apply(), (
        "o scanner nao encontrou nenhuma chamada de cli.run_apply - se a API "
        "mudou de nome, esta trava virou decoracao e precisa ser reescrita"
    )


def _violacoes(chamadas):
    """A REGRA, isolada num lugar so.

    Fica separada da varredura para que um teste possa exercita-la contra uma
    amostra sintetica. Sem isso a trava passaria por vazio: hoje nao existe
    chamada irregular na suite, entao desligar a regra nao quebraria nada e o
    teste continuaria verde sem proteger coisa alguma.
    """
    return [
        (arq, linha, sorted(set(FABRICAS) - nomeados))
        for arq, linha, nomeados, estrela in chamadas
        if not estrela and set(FABRICAS) - nomeados
    ]


def test_o_detector_reconhece_uma_chamada_sem_fabrica():
    """Contraprova da propria regra, contra amostra sintetica."""
    sem_nada = ("sintetico.py", 1, set(), False)
    parcial = ("sintetico.py", 2, {"open_target"}, False)
    completa = ("sintetico.py", 3, set(FABRICAS), False)
    via_estrela = ("sintetico.py", 4, set(), True)

    assert _violacoes([sem_nada]), "chamada sem fabrica alguma tem de violar"
    assert _violacoes([parcial]), "injecao parcial tem de violar"
    assert not _violacoes([completa]), "injecao completa nao pode violar"
    assert not _violacoes([via_estrela]), "desempacotamento e' saida legitima"


def test_nenhuma_chamada_de_apply_omite_as_fabricas():
    """A trava estrutural do incidente."""
    faltantes = _violacoes(_chamadas_de_apply())
    assert not faltantes, (
        "chamada de cli.run_apply sem injetar as fabricas - em maquina com "
        f".env isso abre o Neon de producao: {faltantes}"
    )


def test_guarda_bloqueia_apply_sem_nenhuma_injecao():
    """Contraprova ponta a ponta: a linha exata do incidente agora levanta."""
    with pytest.raises(BaseException, match=RECUSA):
        cli.run_apply(  # isolamento-suite: chamada deliberada
            Channel.MERCADOLIVRE, AGORA
        )


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_guarda_fecha_cada_fabrica_individualmente(fabrica):
    """Injecao PARCIAL nao salva: cada default amarrado recusa sozinho.

    Testar via `__kwdefaults__` em vez de uma execucao completa e' deliberado:
    e' o unico jeito de provar que a AUDITORIA tambem esta fechada sem montar
    dublês de destino e fonte que passem pelo preflight inteiro.
    """
    amarrado = cli.run_apply.__kwdefaults__[fabrica]
    with pytest.raises(BaseException, match=RECUSA):
        amarrado()


@pytest.mark.parametrize("entrada", ["_run_diagnose", "_run_reconcile"])
def test_entradas_sem_injecao_dependem_das_env_de_producao(entrada):
    """Por que estas duas dependem da barreira de DSN, e nao de um duble.

    `_run_diagnose` e `_run_reconcile` montam a conexao inline e nao aceitam
    fabrica nenhuma. Substitui-las na guarda seria tentador, mas quebraria os
    testes que leem o CODIGO-FONTE delas para provar que abrem read-only. Este
    teste amarra a decisao: enquanto elas lerem as variaveis de producao, quem
    as protege e' a barreira de DSN.
    """
    import inspect

    fonte = inspect.getsource(getattr(cli, entrada))
    assert any(v in fonte for v in guarda.ENV_DE_PRODUCAO), (
        f"{entrada} deixou de ler as env de producao - reveja se a barreira "
        "de DSN ainda e' a protecao certa para ela"
    )


def test_barreira_reconhece_exatamente_as_dsns_de_producao():
    """A regra da barreira, exercitada direto."""
    assert guarda.dsns_de_producao({}) == set()
    assert guarda.dsns_de_producao({"DATABASE_URL": ""}) == set()
    assert guarda.dsns_de_producao(
        {"DATABASE_URL": "neon", "DATAMART_DATABASE_URL": "rds"}
    ) == {"neon", "rds"}


def test_barreira_nao_bloqueia_postgres_descartavel():
    """Integracao legitima nao pode ser atingida: a DSN e' outra."""
    ambiente = {"DATABASE_URL": "neon", "DATAMART_DATABASE_URL": "rds"}
    descartavel = "postgresql://postgres@127.0.0.1:55432/teste"
    assert descartavel not in guarda.dsns_de_producao(ambiente)


@pytest.mark.parametrize(
    "fixture",
    ["_sem_conexao_real_na_expedicao", "_sem_conexao_com_producao"],
)
def test_as_guardas_sao_autouse(fixture):
    """Uma guarda que precise ser pedida nao protege quem esqueceu de pedir.

    O atributo do marcador mudou de nome entre versoes do pytest, entao os dois
    sao aceitos - o que importa e' que `autouse` continue verdadeiro.
    """
    alvo = getattr(guarda, fixture)
    marca = getattr(alvo, "_fixture_function_marker", None) or getattr(
        alvo, "_pytestfixturefunction", None
    )
    assert marca is not None, f"{fixture} deixou de ser uma fixture do pytest"
    assert marca.autouse is True, f"{fixture} deixou de ser autouse"


def test_recusa_atravessa_a_fronteira_except_exception():
    """Por que a recusa deriva de BaseException.

    `run_apply` termina em `except Exception`. Uma recusa derivada de
    `Exception` seria convertida em exit code e o teste passaria a "falhar
    bonito", escondendo que tentou abrir producao.
    """
    from pipelines.tests import conftest as guarda

    assert issubclass(guarda.ConexaoRealBloqueada, BaseException)
    assert not issubclass(guarda.ConexaoRealBloqueada, Exception)
def _corpo(fixture):
    """A funcao por tras da fixture, para exercita-la fora do autouse."""
    obter = getattr(fixture, "_get_wrapped_function", None)
    return obter() if obter else getattr(fixture, "__wrapped__", fixture)


def test_barreira_recusa_conexao_para_dsn_de_producao(monkeypatch):
    """Contraprova FUNCIONAL: a barreira instalada realmente recusa.

    Sem este teste, uma mutacao que esvaziasse o corpo da fixture passaria
    despercebida - os testes da regra continuariam verdes, porque a regra
    estaria certa e apenas nao seria aplicada a lugar nenhum.

    A DSN usada e' sentinela: nao ha credencial real e nenhuma conexao chega a
    ser tentada, porque a recusa vem antes.
    """
    import psycopg2

    sentinela = "postgresql://sentinela:sentinela@127.0.0.1:1/sentinela"
    monkeypatch.setenv("DATABASE_URL", sentinela)
    monkeypatch.delenv("DATAMART_DATABASE_URL", raising=False)

    _corpo(guarda._sem_conexao_com_producao)(monkeypatch)

    with pytest.raises(BaseException, match="DSN de PRODUCAO"):
        psycopg2.connect(sentinela)


def test_barreira_deixa_passar_dsn_que_nao_e_de_producao(monkeypatch):
    """O outro lado: integracao com banco descartavel nao pode ser atingida."""
    import psycopg2

    monkeypatch.setenv("DATABASE_URL", "postgresql://producao/neon")
    monkeypatch.delenv("DATAMART_DATABASE_URL", raising=False)

    chamadas = []
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: chamadas.append(a[0]))
    _corpo(guarda._sem_conexao_com_producao)(monkeypatch)

    psycopg2.connect("postgresql://postgres@127.0.0.1:55432/descartavel")
    assert chamadas == ["postgresql://postgres@127.0.0.1:55432/descartavel"]
