"""EXP-3B2-I1 - trava estrutural e contraprovas do isolamento da Expedicao.

O incidente: `test_cli_recusa_apply_do_mercadolivre` chamava
`cli.run_apply(Channel.MERCADOLIVRE, AGORA)` sem injetar fabrica. Enquanto o ML
nao tinha adaptador isso parava em EXIT_PRECONDICAO antes de qualquer conexao.
O PR #24 deu adaptador ao ML e a mesma linha passou a abrir o Neon e publicar a
fila inteira - cinco vezes, com o relogio fixo da fixture (2026-09-17 18:00).

Tres camadas, porque nenhuma basta sozinha:

  - ESTRUTURAL: nenhuma chamada de apply na suite pode omitir as fabricas;
  - POR FUNCAO: a guarda autouse fecha os defaults ja amarrados;
  - POR DESTINO: a barreira recusa qualquer banco que nao seja local.

Um helper pode mascarar a omissao estrutural; alguem pode remover a guarda por
funcao; um caminho novo pode nascer sem passar por nenhuma das duas. A barreira
por destino e' a que sobra nesses casos.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import cli
from pipelines.expedicao.contract import Channel
from pipelines.tests import conftest as guarda

AGORA = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)
FABRICAS = ("open_target", "open_source", "open_audit")
NOMES_NO_MODULO = (
    "open_target_default", "open_source_default", "open_audit_default",
)
DIR_TESTES = Path(__file__).resolve().parent
RECUSA_EXPEDICAO = "conexao REAL da Expedicao"
RECUSA_DESTINO = "NAO e. local"
#: Unica isencao aceita pela trava estrutural. Escrever o marcador na propria
#: chamada torna a excecao visivel num grep e obriga quem a usa a declarar a
#: intencao - diferente de um allowlist por nome de arquivo, que envelhece em
#: silencio.
MARCADOR = "isolamento-suite: chamada deliberada"


# ---------------------------------------------------------------------------
# Camada 1 - trava estrutural
# ---------------------------------------------------------------------------
def _chamadas_de_apply():
    """Toda chamada a `run_apply` na suite, com os kwargs que ela passa.

    Reconhece as duas formas de alcancar a funcao: pelo modulo (`cli.run_apply`)
    e por import direto (`from ...cli import run_apply`). A segunda entrou
    porque um scanner que so' olhasse a primeira daria carta branca a quem
    trocasse o estilo do import.
    """
    achados = []
    for arquivo in sorted(DIR_TESTES.glob("test_*.py")):
        fonte = arquivo.read_text(encoding="utf-8")
        linhas = fonte.splitlines()
        arvore = ast.parse(fonte)
        importado_direto = any(
            isinstance(no, ast.ImportFrom)
            and no.module == "pipelines.expedicao.cli"
            and any(a.name == "run_apply" for a in no.names)
            for no in ast.walk(arvore)
        )
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            alvo = no.func
            if isinstance(alvo, ast.Attribute):
                if alvo.attr != "run_apply":
                    continue
                if getattr(alvo.value, "id", None) != "cli":
                    continue
            elif isinstance(alvo, ast.Name):
                if alvo.id != "run_apply" or not importado_direto:
                    continue
            else:
                continue
            intervalo = linhas[no.lineno - 1:no.end_lineno]
            if any(MARCADOR in linha for linha in intervalo):
                continue
            nomeados = {k.arg for k in no.keywords if k.arg}
            estrela = any(k.arg is None for k in no.keywords)
            achados.append((arquivo.name, no.lineno, nomeados, estrela))
    return achados


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


def test_a_suite_tem_pelo_menos_uma_chamada_de_apply():
    """Guarda da guarda: um scanner que nao acha nada passa por engano."""
    assert _chamadas_de_apply(), (
        "o scanner nao encontrou nenhuma chamada de run_apply - se a API mudou "
        "de nome, esta trava virou decoracao e precisa ser reescrita"
    )


def test_o_detector_reconhece_uma_chamada_sem_fabrica():
    """Contraprova da propria regra, contra amostra sintetica."""
    assert _violacoes([("s.py", 1, set(), False)]), "sem fabrica tem de violar"
    assert _violacoes([("s.py", 2, {"open_target"}, False)]), "parcial viola"
    assert not _violacoes([("s.py", 3, set(FABRICAS), False)])
    assert not _violacoes([("s.py", 4, set(), True)]), "desempacotar e' legitimo"


def test_nenhuma_chamada_de_apply_omite_as_fabricas():
    """A trava estrutural do incidente."""
    faltantes = _violacoes(_chamadas_de_apply())
    assert not faltantes, (
        "chamada de run_apply sem injetar as fabricas - em maquina com .env "
        f"isso abre o Neon de producao: {faltantes}"
    )


def test_nenhum_teste_depende_da_antiga_recusa_do_ml():
    """O ML tem adaptador desde o #24: quem afirmar o contrario esta' obsoleto."""
    obsoletos = []
    for arquivo in sorted(DIR_TESTES.glob("test_expedicao*.py")):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nome = no.name.lower()
                if "recusa" in nome and "mercadolivre" in nome:
                    obsoletos.append(f"{arquivo.name}::{no.name}")
    assert not obsoletos, (
        "teste afirma que o ML e' recusado, o que deixou de ser verdade no "
        f"PR #24: {obsoletos}"
    )


#: Sinais de que um subprocesso sobe um INTERPRETADOR PYTHON. Só esses
#: importam: um filho em Python pode importar `pipelines` e conectar. Um filho
#: que so' faz parse de PowerShell herda o ambiente e nao faz nada com ele -
#: incluir esses casos encheria a trava de ruido e ela seria desligada.
SINAIS_DE_PYTHON = ("sys.executable", "python", "-m")


def test_nenhum_subprocesso_python_herda_o_ambiente_cru():
    """Subprocesso herda `os.environ` - e com ele o `.env` real.

    A guarda e a barreira vivem no processo do pytest e NAO atravessam um
    `subprocess`. Quem sobe um Python filho precisa montar o ambiente de forma
    explicita (`env=`) em vez de deixar o filho herdar o do pytest.
    """
    herdeiros = []
    for arquivo in sorted(DIR_TESTES.glob("test_*.py")):
        fonte = arquivo.read_text(encoding="utf-8")
        if "subprocess" not in fonte:
            continue
        linhas = fonte.splitlines()
        for no in ast.walk(ast.parse(fonte)):
            if not isinstance(no, ast.Call):
                continue
            alvo = no.func
            if not isinstance(alvo, ast.Attribute):
                continue
            if alvo.attr not in ("run", "Popen", "check_output", "check_call"):
                continue
            if getattr(alvo.value, "id", None) != "subprocess":
                continue
            if any(k.arg == "env" for k in no.keywords):
                continue
            trecho = " ".join(linhas[no.lineno - 1:no.end_lineno])
            if any(sinal in trecho for sinal in SINAIS_DE_PYTHON):
                herdeiros.append(f"{arquivo.name}:{no.lineno}")
    assert not herdeiros, (
        "subprocesso Python sem `env=` explicito herda o ambiente do pytest, "
        f"inclusive as credenciais de producao: {herdeiros}"
    )


# ---------------------------------------------------------------------------
# Camada 2 - guarda por funcao
# ---------------------------------------------------------------------------
def test_guarda_bloqueia_apply_sem_nenhuma_injecao():
    """Contraprova ponta a ponta: a linha exata do incidente agora levanta."""
    with pytest.raises(BaseException, match=RECUSA_EXPEDICAO):
        cli.run_apply(  # isolamento-suite: chamada deliberada
            Channel.MERCADOLIVRE, AGORA
        )


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_guarda_fecha_cada_fabrica_individualmente(fabrica):
    """Injecao PARCIAL nao salva: cada default amarrado recusa sozinho.

    Testar via `__kwdefaults__` em vez de uma execucao completa e' deliberado:
    e' o unico jeito de provar que a AUDITORIA tambem esta' fechada sem montar
    dubles de destino e fonte que passem pelo preflight inteiro.
    """
    with pytest.raises(BaseException, match=RECUSA_EXPEDICAO):
        cli.run_apply.__kwdefaults__[fabrica]()


@pytest.mark.parametrize("nome", NOMES_NO_MODULO)
def test_guarda_fecha_tambem_os_nomes_do_modulo(nome):
    """Quem chamar a fabrica pelo nome do modulo tambem e' recusado."""
    with pytest.raises(BaseException, match=RECUSA_EXPEDICAO):
        getattr(cli, nome)()


@pytest.mark.parametrize("entrada", ["_run_diagnose", "_run_reconcile"])
def test_entradas_sem_injecao_montam_a_conexao_inline(entrada):
    """Por que estas duas dependem da barreira de destino, e nao de um duble.

    `_run_diagnose` e `_run_reconcile` montam a conexao inline e nao aceitam
    fabrica nenhuma. Substitui-las na guarda seria tentador, mas quebraria os
    testes que leem o CODIGO-FONTE delas para provar que abrem read-only. Este
    teste amarra a decisao: enquanto elas conectarem por conta propria, quem as
    protege e' a barreira de destino.
    """
    import inspect

    fonte = inspect.getsource(getattr(cli, entrada))
    assert "psycopg2.connect" in fonte, (
        f"{entrada} deixou de conectar por conta propria - reveja se a barreira "
        "de destino ainda e' a protecao certa para ela"
    )


def test_recusa_atravessa_a_fronteira_except_exception():
    """Por que a recusa deriva de BaseException.

    `run_apply` termina em `except Exception`. Uma recusa derivada de
    `Exception` seria convertida em exit code e o teste passaria a "falhar
    bonito", escondendo que tentou abrir producao.
    """
    assert issubclass(guarda.ConexaoRealBloqueada, BaseException)
    assert not issubclass(guarda.ConexaoRealBloqueada, Exception)


def test_recusa_nao_vaza_dsn_host_nem_credencial():
    """A mensagem tem de ser util sem virar um canal de vazamento."""
    import os

    mensagens = []
    for fabrica in FABRICAS:
        try:
            cli.run_apply.__kwdefaults__[fabrica]()
        except BaseException as exc:  # noqa: BLE001
            mensagens.append(str(exc))
    try:
        import psycopg2

        psycopg2.connect("postgresql://u:s@servidor.remoto.exemplo:5432/d")
    except BaseException as exc:  # noqa: BLE001
        mensagens.append(str(exc))

    junto = " ".join(mensagens)
    assert "servidor.remoto.exemplo" not in junto, "a mensagem repetiu o host"
    assert "://" not in junto, "a mensagem repetiu uma DSN"
    for chave in ("DATABASE_URL", "DATAMART_DATABASE_URL"):
        valor = os.environ.get(chave)
        if valor:
            assert valor not in junto, f"a mensagem vazou o valor de {chave}"


# ---------------------------------------------------------------------------
# Camada 2b - restauracao entre testes
# ---------------------------------------------------------------------------
def _estado_atual():
    """O que a Expedicao expoe AGORA, nos dois lugares que a guarda remenda."""
    return (
        {c: cli.run_apply.__kwdefaults__[c] for c in FABRICAS},
        {n: getattr(cli, n) for n in NOMES_NO_MODULO},
    )


_VISTO_NO_CASO_1 = {}


@pytest.fixture(autouse=True)
def _registra_o_que_o_caso_1_viu(request):
    """Guarda o que o caso 1 enxergou, para o caso 2 comparar.

    Roda no teardown, ainda dentro do teste: e' o ultimo instante em que a
    guarda daquele caso ainda esta' montada.
    """
    yield
    if request.node.name == "test_restauracao_caso_1_de_2":
        _VISTO_NO_CASO_1["kw"], _VISTO_NO_CASO_1["mod"] = _estado_atual()


def test_restauracao_caso_1_de_2():
    """Primeiro de dois casos consecutivos: a guarda esta' ativa aqui."""
    kw, mod = _estado_atual()
    for chave in FABRICAS:
        assert kw[chave].__qualname__ != chave + "_default", "guarda inativa"
    for nome in NOMES_NO_MODULO:
        assert mod[nome].__qualname__ != nome, "guarda inativa"


def test_restauracao_caso_2_de_2():
    """Segundo caso: a guarda do caso 1 nao sobreviveu ate' aqui.

    Cada teste recebe uma guarda NOVA. Se o monkeypatch do caso 1 nao tivesse
    sido desfeito, os objetos seriam os MESMOS - e a identidade denuncia. E' a
    prova de que a restauracao acontece entre casos consecutivos, e nao apenas
    no fim da sessao.
    """
    assert _VISTO_NO_CASO_1, "o caso 1 precisa rodar antes deste"
    kw, mod = _estado_atual()
    for chave in FABRICAS:
        assert kw[chave] is not _VISTO_NO_CASO_1["kw"][chave], (
            "a guarda do teste anterior vazou para este - o monkeypatch nao "
            "restaurou entre os casos"
        )
    for nome in NOMES_NO_MODULO:
        assert mod[nome] is not _VISTO_NO_CASO_1["mod"][nome], (
            "o nome no modulo ficou preso na guarda do teste anterior"
        )


def test_defaults_verdadeiros_voltam_num_interpretador_limpo():
    """Prova direta: fora do pytest, os defaults sao os conectores reais.

    Se a guarda deixasse residuo no modulo - por exemplo escrevendo em
    `__kwdefaults__` sem passar pelo monkeypatch - o processo filho ainda veria
    os nomes trocados. O ambiente do filho e' montado a mao, sem herdar
    credencial nenhuma.
    """
    import json
    import subprocess
    import sys

    codigo = (
        "import json;"
        "from pipelines.expedicao import cli;"
        "print(json.dumps({"
        "'kw': [cli.run_apply.__kwdefaults__[c].__qualname__ "
        "for c in ('open_target', 'open_source', 'open_audit')],"
        "'mod': [getattr(cli, n).__qualname__ for n in "
        "('open_target_default', 'open_source_default', 'open_audit_default')]"
        "}))"
    )
    raiz = str(DIR_TESTES.parent.parent)
    saida = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=raiz, capture_output=True, text=True,
        env={"PYTHONPATH": raiz, "PATH": "", "SYSTEMROOT": ""},
    )
    assert saida.returncode == 0, saida.stderr[:400]
    visto = json.loads(saida.stdout)
    assert visto["kw"] == list(NOMES_NO_MODULO)
    assert visto["mod"] == list(NOMES_NO_MODULO)


# ---------------------------------------------------------------------------
# Camada 3 - barreira por destino
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("args,kwargs,local", [
    (("postgresql://u:s@127.0.0.1:55432/d",), {}, True),
    (("postgresql://u:s@localhost:5432/d",), {}, True),
    (("host=localhost port=55432 dbname=d",), {}, True),
    (("dbname=d",), {}, True),
    ((), {"host": "localhost", "dbname": "d"}, True),
    ((), {"dsn": "postgresql://u:s@localhost/d"}, True),
    (("postgresql://u:s@ep-qualquer.neon.tech/d",), {}, False),
    (("postgresql://u:s@algo.rds.amazonaws.com:5432/d",), {}, False),
    (("host=servidor.remoto dbname=d",), {}, False),
    ((), {"host": "servidor.remoto", "dbname": "d"}, False),
    ((), {"hostaddr": "203.0.113.7", "dbname": "d"}, False),
    (("postgresql://u:s@localhost/d",), {"host": "servidor.remoto"}, False),
    (("isto nao e uma dsn",), {}, False),
    ((None,), {}, True),
])
def test_barreira_classifica_o_destino(args, kwargs, local, monkeypatch):
    """A regra da barreira, nas TRES formas que o psycopg2 aceita.

    Inclui os casos que uma comparacao por substring erraria: kwargs sobrepondo
    o host de uma DSN local, e uma DSN indecifravel - tratada como remota,
    porque na duvida a barreira recusa.

    `connect(None)` e `dbname=d` sem host sao LOCAIS aqui porque o ambiente nao
    tem `PGHOST`; o teste seguinte cobre o caso em que tem.
    """
    for chave in ("PGHOST", "PGHOSTADDR"):
        monkeypatch.delenv(chave, raising=False)
    assert guarda.destino_e_local(args, kwargs) is local


@pytest.mark.parametrize("args,kwargs", [
    ((None,), {}),
    (("dbname=d",), {}),
    ((), {"dbname": "d"}),
])
def test_destino_sem_host_explicito_respeita_pghost(args, kwargs, monkeypatch):
    """Sem host na DSN, quem decide o destino e' `PGHOST` - e ele pode ser remoto.

    Este e' o furo silencioso de qualquer barreira que so' olhe a string: a DSN
    nao menciona servidor nenhum e mesmo assim a conexao sai da maquina.
    """
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.setenv("PGHOST", "servidor.remoto")
    assert guarda.destino_e_local(args, kwargs) is False

    monkeypatch.setenv("PGHOST", "localhost")
    assert guarda.destino_e_local(args, kwargs) is True


def _corpo(fixture):
    """A funcao por tras da fixture, para exercita-la fora do autouse."""
    obter = getattr(fixture, "_get_wrapped_function", None)
    return obter() if obter else getattr(fixture, "__wrapped__", fixture)


def test_barreira_recusa_destino_remoto(monkeypatch):
    """Contraprova FUNCIONAL: a barreira instalada realmente recusa.

    Sem este teste, uma mutacao que esvaziasse o corpo da fixture passaria
    despercebida - os testes da regra continuariam verdes, porque a regra
    estaria certa e apenas nao seria aplicada a lugar nenhum.

    O host e' sentinela e nao existe: a recusa vem antes de qualquer socket.
    """
    import psycopg2

    _corpo(guarda._sem_conexao_com_producao)(monkeypatch)
    with pytest.raises(BaseException, match=RECUSA_DESTINO):
        psycopg2.connect("postgresql://u:s@servidor.remoto.exemplo:5432/d")


def test_barreira_deixa_passar_destino_local(monkeypatch):
    """O outro lado: integracao com banco descartavel nao pode ser atingida."""
    import psycopg2

    chamadas = []
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: chamadas.append(a[0]))
    _corpo(guarda._sem_conexao_com_producao)(monkeypatch)

    descartavel = "postgresql://postgres:postgres@localhost:55432/postgres"
    psycopg2.connect(descartavel)
    assert chamadas == [descartavel]


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
