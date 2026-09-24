"""INCIDENTE-API-DB-2/3 — readiness do banco e sanitizacao da falha.

O que estes testes travam, em uma frase: uma instancia sem banco nunca mais
pode se apresentar como saudavel, e nenhuma URL, host, usuario ou senha pode
sair no log ou na resposta.

O incidente que os motivou: um deploy subiu com o engine nao inicializado,
`/health` respondeu 200, o Render promoveu a instancia e a Torre inteira
passou a devolver 503 — Shopee e Mercado Livre inclusive — sem uma linha de
log dizendo por que.
"""
from __future__ import annotations

import importlib
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import ArgumentError, NoSuchModuleError

from app import database, main

#: URL sintetica com "senha" reconhecivel. Nenhum valor real aparece aqui.
URL_COM_SEGREDO = "postgresql://usuario_secreto:senha_secreta@host.interno:5432/banco"


@pytest.fixture
def cliente():
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# Classificacao da falha
# ---------------------------------------------------------------------------
def test_url_vazia_nao_cria_engine_e_registra_categoria():
    """Ausencia PURA nao passa por aqui — o Settings tem default valido. Este
    caso e' a variavel definida como string vazia."""
    database.FALHA_DO_ENGINE.clear()
    assert database._make_engine("", nome="teste") is None
    assert database.FALHA_DO_ENGINE["teste"] == database.CATEGORIA_URL_VAZIA


def test_url_malformada_e_classificada_como_tal():
    database.FALHA_DO_ENGINE.clear()
    # aspas nas pontas: o caso mais comum de copiar/colar no painel
    assert database._make_engine('"postgresql://u:p@h:5432/d"', nome="teste") is None
    assert database.FALHA_DO_ENGINE["teste"] == database.CATEGORIA_URL_INVALIDA


def test_esquema_postgres_legado_e_classificado_como_driver():
    """`postgres://` foi removido no SQLAlchemy 2.x e vira `NoSuchModuleError`."""
    database.FALHA_DO_ENGINE.clear()
    assert database._make_engine("postgres://u:p@h:5432/d", nome="teste") is None
    assert database.FALHA_DO_ENGINE["teste"] == database.CATEGORIA_DRIVER_AUSENTE


def test_engine_bom_limpa_a_categoria_anterior():
    database.FALHA_DO_ENGINE["teste"] = database.CATEGORIA_URL_VAZIA
    assert database._make_engine("postgresql://u:p@h:5432/d", nome="teste") is not None
    assert "teste" not in database.FALHA_DO_ENGINE


@pytest.mark.parametrize(
    "exc,esperado",
    [
        (ArgumentError("x"), "url_malformada"),
        (NoSuchModuleError("x"), "driver_indisponivel"),
        (ModuleNotFoundError("psycopg2"), "driver_indisponivel"),
        (RuntimeError("x"), "falha_desconhecida_ao_criar_engine"),
    ],
)
def test_categoria_olha_a_classe_e_nao_a_mensagem(exc, esperado):
    assert database._categoria(exc) == esperado


# ---------------------------------------------------------------------------
# Sanitizacao — o requisito inegociavel
# ---------------------------------------------------------------------------
def test_o_log_nunca_carrega_a_url(caplog):
    """`ArgumentError` do SQLAlchemy traz a URL INTEIRA na mensagem.

    Logar `str(exc)` publicaria usuario e senha no log do Render. O teste usa
    uma URL malformada QUE CONTEM o segredo e exige que ele nao apareca.
    """
    with caplog.at_level(logging.ERROR):
        database.FALHA_DO_ENGINE.clear()
        database._make_engine('"' + URL_COM_SEGREDO + '"', nome="teste")
    texto = caplog.text
    assert texto, "a falha precisa ser registrada — engolir foi o defeito original"
    for proibido in ("senha_secreta", "usuario_secreto", "host.interno", "banco"):
        assert proibido not in texto, proibido
    # o que DEVE aparecer
    assert "url_malformada" in texto
    assert "ArgumentError" in texto


def test_a_resposta_de_readiness_nunca_carrega_a_url(monkeypatch, cliente):
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.setitem(database.FALHA_DO_ENGINE, "local",
                        database.CATEGORIA_URL_INVALIDA)
    r = cliente.get("/ready")
    bruto = r.text
    for proibido in ("senha", "usuario", "host.interno", "postgresql://", "@"):
        assert proibido not in bruto, proibido


# ---------------------------------------------------------------------------
# O contrato de readiness
# ---------------------------------------------------------------------------
def test_engine_ausente_reprova_a_readiness(monkeypatch, cliente):
    """Este e' exatamente o estado do INCIDENTE-API-DB-2."""
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.setitem(database.FALHA_DO_ENGINE, "local",
                        database.CATEGORIA_URL_INVALIDA)
    r = cliente.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == database.MOTIVO_ENGINE_AUSENTE
    assert r.json()["categoria"] == database.CATEGORIA_URL_INVALIDA


def test_banco_inacessivel_reprova_com_motivo_diferente(monkeypatch, cliente):
    """Separar os dois motivos importa para quem esta de plantao: um e'
    estrutural e nao volta sozinho; o outro pode ser transitorio."""
    class _EngineQuebrado:
        def connect(self):
            raise OSError("sem rota")

    monkeypatch.setattr(database, "engine", _EngineQuebrado())
    r = cliente.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == database.MOTIVO_BANCO_INACESSIVEL
    assert r.json()["categoria"] == "OSError"


def test_banco_ok_aprova_a_readiness(monkeypatch, cliente):
    class _Conn:
        def execute(self, *_a, **_k):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class _EngineBom:
        def connect(self):
            return self

        def execution_options(self, **_k):
            return _Conn()

    monkeypatch.setattr(database, "engine", _EngineBom())
    r = cliente.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": database.PRONTO}


def test_readiness_tem_espera_curta():
    """Sonda que demora trava o health check em vez de responde-lo."""
    assert 1 <= database.READINESS_TIMEOUT_SEGUNDOS <= 5


# ---------------------------------------------------------------------------
# `/health` continua sendo liveness
# ---------------------------------------------------------------------------
def test_health_continua_respondendo_sem_banco(monkeypatch, cliente):
    """De proposito. Se `/health` exigisse banco, um soluco do Neon faria o
    Render matar e recriar a instancia em laco — trocaria degradacao por queda.
    """
    monkeypatch.setattr(database, "engine", None)
    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_e_ready_sao_rotas_distintas():
    caminhos = {r.path for r in main.app.routes if hasattr(r, "path")}
    assert "/health" in caminhos
    assert "/ready" in caminhos


def test_ready_aceita_head_para_o_health_check_do_render():
    # O Render faz GET, mas balanceadores usam HEAD. Suportar os dois evita um
    # 405 ser lido como instancia doente.
    metodos = {
        m for r in main.app.routes if getattr(r, "path", None) == "/ready"
        for m in getattr(r, "methods", set())
    }
    assert {"GET", "HEAD"} <= metodos


# ---------------------------------------------------------------------------
# Barreira estrutural
# ---------------------------------------------------------------------------
def test_o_modulo_nunca_loga_a_mensagem_da_excecao():
    """`str(exc)` em log de inicializacao e' vazamento de credencial."""
    import ast
    import inspect
    from pathlib import Path

    fonte = Path(inspect.getfile(database)).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)):
            continue
        if no.func.attr not in ("error", "warning", "info", "exception", "debug"):
            continue
        for arg in no.args:
            # `str(exc)` ou f-string com a excecao inteira
            if isinstance(arg, ast.Call) and getattr(arg.func, "id", "") == "str":
                raise AssertionError("log de inicializacao usando str(...)")
            if isinstance(arg, ast.JoinedStr):
                raise AssertionError("log de inicializacao usando f-string")


def test_o_import_do_modulo_nao_levanta_com_url_ruim(monkeypatch):
    """Reimportar com URL invalida tem de degradar, nunca derrubar o processo —
    senao o Render entra em laco de restart e perdemos ate' o `/health`."""
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/d")
    import app.config as cfg

    importlib.reload(cfg)
    recarregado = importlib.reload(database)
    assert recarregado.engine is None
    assert recarregado.SessionLocal is None
    # devolve o modulo ao estado original para nao contaminar a suite
    monkeypatch.undo()
    importlib.reload(cfg)
    importlib.reload(database)
