"""F2 — o diagnostico nao pode imprimir nada derivado da credencial.

O defeito original: o rotulo do esquema saia de `valor.split("://")[0]`. Com um
valor malformado — wrapper de shell, prefixo colado, texto arbitrario — isso
publicava pedaco do proprio valor no relatorio, que e' feito justamente para
ser colado num chat sem revisao.

A correcao e' vocabulario FECHADO: ou casa exatamente com um esquema conhecido,
ou e' `nao_reconhecido`.
"""
from __future__ import annotations

import importlib.util
import io
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

FERRAMENTA = (
    Path(__file__).resolve().parents[1] / "tools" / "diagnose_database_url.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("diag_tool", FERRAMENTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


diag = _carregar()


#: Entradas adversariais. Cada uma carrega marcadores reconheciveis: usuario,
#: senha, host, IP, porta, query string e quebra de linha.
#:
#: `MARCADOR` e' sintetico de proposito: nenhuma credencial real entra em teste,
#: fixture ou log. Se ele aparecer na saida, o vazamento e' inequivoco.
MARCADOR = "SEGREDO_SYNTH_zz9"

SEGREDOS = (
    "usuario_marcado", "senha_marcada", "host.marcado", "203.0.113.77",
    "5432", "banco_marcado", "chave_marcada", "valor_marcado", MARCADOR,
)

ADVERSARIAIS = {
    "postgres legado":
        "postgres://usuario_marcado:senha_marcada@host.marcado:5432/banco_marcado",
    "aspas duplas":
        '"postgresql://usuario_marcado:senha_marcada@host.marcado:5432/banco_marcado"',
    "aspas simples":
        "'postgresql://usuario_marcado:senha_marcada@host.marcado:5432/banco_marcado'",
    "prefixo DATABASE_URL=":
        "DATABASE_URL=postgresql://usuario_marcado:senha_marcada@host.marcado/banco_marcado",
    "wrapper psql":
        "psql postgresql://usuario_marcado:senha_marcada@host.marcado/banco_marcado",
    "export":
        "export DATABASE_URL=postgresql://usuario_marcado:senha_marcada@host.marcado/b",
    "sem esquema":
        "usuario_marcado:senha_marcada@host.marcado:5432/banco_marcado",
    "ip literal":
        "postgres://usuario_marcado:senha_marcada@203.0.113.77:5432/banco_marcado",
    "query string":
        "postgres://usuario_marcado:senha_marcada@host.marcado/b?chave_marcada=valor_marcado",
    "quebra de linha":
        "postgresql://usuario_marcado:senha_marcada@host.marcado/b\nsenha_marcada",
    "texto arbitrario":
        "senha_marcada e mais texto://qualquer",
    "driver v3 ausente":
        "postgresql+psycopg://usuario_marcado:senha_marcada@host.marcado/banco_marcado",
}


#: Entradas em que o material hostil esta' no DRIVER, nao no resto da URL.
#:
#: Fechar o rotulo do esquema nao cobria este campo. A regex de URL do
#: SQLAlchemy aceita qualquer nome formado por `[\w+]`, entao `make_url`
#: atravessa sem levantar e devolve o texto inteiro em `drivername` — que a
#: ferramenta imprimia cru. Medido antes da correcao:
#:
#:   SEGREDO_SYNTH_zz9://u@h/d              -> "driver : SEGREDO_SYNTH_zz9"
#:   postgresql+SEGREDO_SYNTH_zz9://u@h/d   -> "driver : postgresql+SEGREDO_SYNTH_zz9"
#:
#: Os casos com ponto, dois-pontos, espaco ou quebra de linha NAO passam pela
#: regex e fazem `make_url` levantar. Ficam aqui mesmo assim: o contrato e' que
#: nada vaze, nao que o parse falhe de um jeito especifico.
DRIVER_ADVERSARIAIS = {
    "esquema inteiro hostil":
        MARCADOR + "://usuario_marcado@host.marcado/banco_marcado",
    "sufixo de driver hostil":
        "postgresql+" + MARCADOR + "://usuario_marcado@host.marcado/banco_marcado",
    "senha no driver":
        "senha_marcada://usuario_marcado@host.marcado/banco_marcado",
    "host no driver":
        "host.marcado://usuario_marcado@host.marcado/banco_marcado",
    "ip no driver":
        "203.0.113.77://usuario_marcado@host.marcado/banco_marcado",
    "porta no driver":
        "5432://usuario_marcado@host.marcado/banco_marcado",
    "quebra de linha no driver":
        "postgresql+\nsenha_marcada://usuario_marcado@host.marcado/banco_marcado",
    "wrapper psql com driver":
        "psql postgresql+psycopg2://usuario_marcado:senha_marcada@host.marcado/b",
    "wrapper export com driver":
        "export postgresql+psycopg2://usuario_marcado:senha_marcada@host.marcado/b",
    "prefixo DATABASE_URL= com driver":
        "DATABASE_URL=postgresql+" + MARCADOR + "://usuario_marcado@host.marcado/b",
    "driver vazio":
        "://usuario_marcado:senha_marcada@host.marcado/banco_marcado",
}


# ---------------------------------------------------------------------------
# O vocabulario e' fechado
# ---------------------------------------------------------------------------
def test_o_vocabulario_do_esquema_e_finito():
    permitidos = set(diag.ESQUEMAS_CONHECIDOS) | {
        diag.ESQUEMA_AUSENTE, diag.ESQUEMA_NAO_RECONHECIDO,
    }
    assert permitidos == {
        "postgresql", "postgresql+psycopg2", "postgresql+psycopg",
        "ausente", "nao_reconhecido",
    }


@pytest.mark.parametrize("rotulo,valor", sorted(ADVERSARIAIS.items()))
def test_entrada_adversarial_so_pode_virar_rotulo_do_vocabulario(rotulo, valor):
    obtido = diag.rotulo_do_esquema(valor)
    permitidos = set(diag.ESQUEMAS_CONHECIDOS) | {
        diag.ESQUEMA_AUSENTE, diag.ESQUEMA_NAO_RECONHECIDO,
    }
    assert obtido in permitidos, (rotulo, obtido)


@pytest.mark.parametrize("rotulo,valor", sorted(ADVERSARIAIS.items()))
def test_nenhum_fragmento_do_valor_aparece_no_rotulo(rotulo, valor):
    obtido = diag.rotulo_do_esquema(valor)
    for s in SEGREDOS:
        assert s not in obtido, (rotulo, s, obtido)


def test_os_esquemas_validos_sao_reconhecidos():
    assert diag.rotulo_do_esquema("postgresql://u:p@h/d") == "postgresql"
    assert diag.rotulo_do_esquema("postgresql+psycopg2://u:p@h/d") == "postgresql+psycopg2"
    assert diag.rotulo_do_esquema("postgresql+psycopg://u:p@h/d") == "postgresql+psycopg"


def test_ausente_e_vazio_tem_rotulo_proprio():
    assert diag.rotulo_do_esquema(None) == "ausente"
    assert diag.rotulo_do_esquema("") == "ausente"
    assert diag.rotulo_do_esquema("   ") == "ausente"


def test_o_v3_e_reconhecido_mas_nao_e_servivel_hoje():
    """O projeto instala psycopg2, nao psycopg v3. Reconhecer sem marcar como
    servivel e' o que faz o relatorio apontar a causa em vez de esconde-la."""
    assert "postgresql+psycopg" in diag.ESQUEMAS_CONHECIDOS
    assert "postgresql+psycopg" not in diag.ESQUEMAS_SERVIVEIS
    assert set(diag.ESQUEMAS_SERVIVEIS) == {"postgresql", "postgresql+psycopg2"}


# ---------------------------------------------------------------------------
# O campo `driver` tambem e' vocabulario fechado
# ---------------------------------------------------------------------------
def test_o_vocabulario_do_driver_e_finito():
    permitidos = set(diag.DRIVERS_CONHECIDOS) | {
        diag.DRIVER_AUSENTE, diag.DRIVER_NAO_RECONHECIDO,
    }
    assert permitidos == {
        "postgresql", "postgresql+psycopg2", "postgresql+psycopg",
        "ausente", "nao_reconhecido",
    }


def test_os_drivers_validos_sao_reconhecidos():
    assert diag.rotulo_do_driver("postgresql") == "postgresql"
    assert diag.rotulo_do_driver("postgresql+psycopg2") == "postgresql+psycopg2"
    assert diag.rotulo_do_driver("postgresql+psycopg") == "postgresql+psycopg"


def test_driver_ausente_ou_vazio_tem_rotulo_proprio():
    assert diag.rotulo_do_driver(None) == "ausente"
    assert diag.rotulo_do_driver("") == "ausente"
    assert diag.rotulo_do_driver("   ") == "ausente"


@pytest.mark.parametrize("rotulo,valor", sorted(DRIVER_ADVERSARIAIS.items()))
def test_o_drivername_hostil_vira_nao_reconhecido(rotulo, valor):
    """O que `make_url` extrairia como driver nao pode sair cru.

    Passa pelo parser de verdade quando ele aceita, e pelo texto antes do
    `://` quando ele recusa — os dois caminhos devem terminar no vocabulario.
    """
    try:
        from sqlalchemy.engine import make_url
        bruto_driver = make_url(valor).drivername
    except Exception:
        bruto_driver = valor.split("://", 1)[0] if "://" in valor else valor

    obtido = diag.rotulo_do_driver(bruto_driver)
    permitidos = set(diag.DRIVERS_CONHECIDOS) | {
        diag.DRIVER_AUSENTE, diag.DRIVER_NAO_RECONHECIDO,
    }
    assert obtido in permitidos, (rotulo, obtido)
    for s in SEGREDOS:
        assert s not in obtido, (rotulo, s, obtido)


@pytest.mark.parametrize("rotulo,valor", sorted(DRIVER_ADVERSARIAIS.items()))
def test_execucao_completa_com_driver_hostil_nao_vaza(rotulo, valor, monkeypatch):
    """Roda o `main()` inteiro e varre stdout E stderr.

    Este e' o teste que falhava antes da correcao: o bloco 3 imprimia
    `url.drivername` cru e o marcador aparecia na saida.
    """
    monkeypatch.setenv("DATABASE_URL", valor)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            diag.main()
        except SystemExit:
            pass
    tudo = out.getvalue() + err.getvalue()
    assert tudo.strip(), "a ferramenta precisa produzir relatorio"
    for s in SEGREDOS:
        assert s not in tudo, (rotulo, s)
    assert not re.search(r"://[^\s]*:[^\s]*@", tudo), rotulo


# ---------------------------------------------------------------------------
# A saida COMPLETA da ferramenta nao vaza
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rotulo,valor", sorted(ADVERSARIAIS.items()))
def test_a_execucao_completa_nao_vaza_em_stdout_nem_stderr(rotulo, valor, monkeypatch):
    """Roda o `main()` inteiro com o valor adversarial e varre TUDO que saiu."""
    monkeypatch.setenv("DATABASE_URL", valor)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            diag.main()
        except SystemExit:
            pass
    tudo = out.getvalue() + err.getvalue()
    assert tudo.strip(), "a ferramenta precisa produzir relatorio"
    for s in SEGREDOS:
        assert s not in tudo, (rotulo, s)
    # e nenhum DSN inteiro
    assert not re.search(r"://[^\s]*:[^\s]*@", tudo), rotulo


def test_a_execucao_com_valor_ausente_nao_quebra(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    out = io.StringIO()
    with redirect_stdout(out):
        try:
            diag.main()
        except SystemExit:
            pass
    assert "presente" in out.getvalue()


# ---------------------------------------------------------------------------
# Barreira estrutural
# ---------------------------------------------------------------------------
def test_a_ferramenta_nunca_imprime_a_mensagem_de_excecao():
    """`ArgumentError` do SQLAlchemy traz a URL inteira na mensagem."""
    import ast

    fonte = FERRAMENTA.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.Call) and getattr(no.func, "id", "") == "print"):
            continue
        for arg in ast.walk(no):
            # `str(exc)` em qualquer posicao dentro de um print
            if isinstance(arg, ast.Call) and getattr(arg.func, "id", "") == "str":
                alvo = arg.args[0] if arg.args else None
                nome = getattr(alvo, "id", "")
                assert nome not in ("exc", "e"), "print com str(excecao)"


def test_a_ferramenta_nao_imprime_fatia_crua_do_valor():
    """Nada de `bruto[:n]`, `valor[:n]` ou split do valor indo para print."""
    fonte = FERRAMENTA.read_text(encoding="utf-8")
    assert "bruto[:" not in fonte
    assert "+ bruto" not in fonte
    assert "bruto.split" not in fonte


def test_nenhum_print_recebe_drivername():
    """`drivername` so' pode chegar ao relatorio por `rotulo_do_driver`.

    Barreira estrutural do finding: sem ela, um `print(url.drivername)` volta a
    passar despercebido porque o valor so' vaza com entrada malformada.
    """
    import ast

    fonte = FERRAMENTA.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.Call) and getattr(no.func, "id", "") == "print"):
            continue
        for arg in ast.walk(no):
            assert getattr(arg, "attr", "") != "drivername", (
                "print recebendo `drivername` cru na linha %d" % no.lineno
            )


def test_o_unico_consumidor_de_drivername_e_o_rotulo():
    """Todo acesso a `.drivername` no arquivo e' argumento de `rotulo_do_driver`."""
    import ast

    fonte = FERRAMENTA.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)

    autorizados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call) and getattr(no.func, "id", "") == "rotulo_do_driver":
            for arg in no.args:
                if getattr(arg, "attr", "") == "drivername":
                    autorizados.add(id(arg))

    for no in ast.walk(arvore):
        if isinstance(no, ast.Attribute) and no.attr == "drivername":
            assert id(no) in autorizados, (
                "acesso a `drivername` fora de `rotulo_do_driver` na linha %d"
                % no.lineno
            )
