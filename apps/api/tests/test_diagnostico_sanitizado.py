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
SEGREDOS = (
    "usuario_marcado", "senha_marcada", "host.marcado", "203.0.113.77",
    "5432", "banco_marcado", "chave_marcada", "valor_marcado",
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
