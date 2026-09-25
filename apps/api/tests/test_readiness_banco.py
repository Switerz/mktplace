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

    # `engine` presente (nao e' falha estrutural) e a SONDA e' quem falha.
    monkeypatch.setattr(database, "engine", object())
    monkeypatch.setattr(database, "readiness_engine", _EngineQuebrado())
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
            return _Conn()

    monkeypatch.setattr(database, "engine", object())
    monkeypatch.setattr(database, "readiness_engine", _EngineBom())
    r = cliente.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": database.PRONTO}


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
# ---------------------------------------------------------------------------
# F1 — os limites da sonda sao REAIS, nao decorativos
# ---------------------------------------------------------------------------
def test_a_sonda_tem_engine_dedicado_com_nullpool():
    """Sem pool proprio, um probe travado prenderia conexao de producao."""
    from sqlalchemy.pool import NullPool

    assert database.readiness_engine is not None
    assert isinstance(database.readiness_engine.pool, NullPool)


def test_a_sonda_nao_e_o_engine_da_aplicacao():
    assert database.readiness_engine is not database.engine


#: Chaves que nos interessam. O `cparams` completo carrega usuario, host e
#: SENHA — ele nunca vai para `assert`, mensagem, log ou repr. So' estas duas
#: saem daqui, e so' elas podem aparecer numa falha de teste.
CHAVES_DE_LIMITE = ("connect_timeout", "options")


def _limites_entregues_ao_driver(engine) -> dict:
    """O que o engine ENTREGA ao driver, medido no evento `do_connect`.

    `do_connect` roda com `cparams` ja' montado e ANTES do `dbapi.connect`.
    Levantar ali aborta a conexao: o teste nao precisa de banco e mesmo assim
    mede exatamente o que chegaria ao libpq.

    Isto substitui a versao anterior, que era FALSAMENTE VERDE: ela procurava
    `connect_timeout` e `statement_timeout` com `ast.dump()` e busca de texto
    no arquivo, e os dois nomes aparecem na docstring e nos comentarios de
    `_make_readiness_engine`. Medido: remover as duas configuracoes REAIS do
    `connect_args` deixava a suite com 28/28 verdes. Agora a prova e'
    comportamental.

    Devolve SOMENTE `CHAVES_DE_LIMITE`, para que nenhuma falha de teste possa
    imprimir credencial.
    """
    from sqlalchemy import event

    class _Abortar(Exception):
        """Interrompe em `do_connect`: o teste quer os parametros, nao a conexao."""

    capturado: dict = {}

    def _espiao(dialect, conn_rec, cargs, cparams):
        capturado.update(
            {k: v for k, v in cparams.items() if k in CHAVES_DE_LIMITE}
        )
        raise _Abortar

    event.listen(engine, "do_connect", _espiao)
    try:
        with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — a conexao e' abortada de proposito
        pass
    finally:
        event.remove(engine, "do_connect", _espiao)
    return capturado


def test_a_sonda_entrega_connect_timeout_de_3s_ao_driver():
    """`connect_timeout` precisa CHEGAR ao libpq, nao so' existir no arquivo."""
    limites = _limites_entregues_ao_driver(database.readiness_engine)
    assert "connect_timeout" in limites, (
        "o engine da readiness nao entregou `connect_timeout` ao driver"
    )
    assert limites["connect_timeout"] == 3
    assert limites["connect_timeout"] == database.READINESS_CONNECT_TIMEOUT_S


def test_a_sonda_entrega_statement_timeout_de_3000ms_ao_driver():
    """`statement_timeout` vai em `options`, que o libpq repassa como GUC."""
    limites = _limites_entregues_ao_driver(database.readiness_engine)
    assert "options" in limites, (
        "o engine da readiness nao entregou `options` ao driver"
    )
    assert "statement_timeout=3000" in limites["options"]
    assert (
        "statement_timeout=%d" % database.READINESS_STATEMENT_TIMEOUT_MS
        in limites["options"]
    )


def test_o_engine_principal_nao_recebe_os_limites_da_sonda():
    """Os limites sao da SONDA. Impo-los ao engine principal mudaria o
    comportamento de todas as consultas da API — que e' justamente o motivo de
    a sonda ter engine proprio."""
    limites = _limites_entregues_ao_driver(database.engine)
    assert "connect_timeout" not in limites
    assert "options" not in limites


def test_a_sonda_usa_nullpool_e_o_principal_nao():
    """Sem pool proprio, um probe travado prenderia conexao de producao."""
    from sqlalchemy.pool import NullPool

    assert isinstance(database.readiness_engine.pool, NullPool)
    assert not isinstance(database.engine.pool, NullPool)


def test_o_timeout_decorativo_nao_voltou():
    """`execution_options(timeout=...)` nao limitava nada: `connect()` ja tinha
    acontecido, e o dialeto psycopg2 nao honra esse option.

    Barreira estrutural, complementar as tres provas comportamentais acima:
    olha as CHAMADAS na arvore sintatica, nunca o texto — o comentario do
    modulo cita `execution_options` de proposito, para registrar por que ele
    foi removido."""
    import ast
    import inspect
    from pathlib import Path

    fonte = Path(inspect.getfile(database)).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    chamadas = {
        n.func.attr for n in ast.walk(arvore)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "execution_options" not in chamadas, "o timeout decorativo voltou"


def test_a_sonda_nao_cria_engine_por_requisicao(monkeypatch):
    """`create_engine` a cada health check e' alocacao inutil num caminho que
    o Render bate sem parar."""
    chamadas = []
    monkeypatch.setattr(
        database, "create_engine",
        lambda *a, **k: chamadas.append(1),
    )

    class _Conn:
        def execute(self, *_a, **_k):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(database, "readiness_engine",
                        type("E", (), {"connect": lambda self: _Conn()})())
    monkeypatch.setattr(database, "engine", object())
    for _ in range(5):
        database.readiness()
    assert chamadas == []


def test_os_limites_declarados_sao_curtos():
    assert 1 <= database.READINESS_CONNECT_TIMEOUT_S <= 5
    assert 500 <= database.READINESS_STATEMENT_TIMEOUT_MS <= 5000


def test_o_modulo_documenta_o_limite_de_dns():
    """`connect_timeout` do libpq so' comeca DEPOIS da resolucao de nome.
    Anunciar teto total sem essa ressalva seria promessa falsa."""
    import inspect

    assert "DNS" in inspect.getdoc(database.readiness)


# ---------------------------------------------------------------------------
# N1 — `str(e)` legado nas checagens de conexao
# ---------------------------------------------------------------------------
class _ExcecaoComDsn(Exception):
    """Imita psycopg2/SQLAlchemy, cuja mensagem carrega o DSN inteiro."""

    def __init__(self):
        super().__init__(
            "connection to server failed: "
            "postgresql://usuario_secreto:senha_secreta@host.interno:5432/banco"
        )


def test_check_connection_nao_devolve_a_mensagem_da_excecao(monkeypatch):
    class _Engine:
        def connect(self):
            raise _ExcecaoComDsn()

    monkeypatch.setattr(database, "engine", _Engine())
    ok, msg = database.check_connection()
    assert ok is False
    for proibido in ("senha_secreta", "usuario_secreto", "host.interno",
                     "postgresql://", "@"):
        assert proibido not in msg, proibido
    assert "DatabaseError" in msg or "_ExcecaoComDsn" in msg


def test_check_datamart_connection_tambem_e_sanitizada(monkeypatch):
    class _Engine:
        def connect(self):
            raise _ExcecaoComDsn()

    monkeypatch.setattr(database, "datamart_engine", _Engine())
    ok, msg = database.check_datamart_connection()
    assert ok is False
    for proibido in ("senha_secreta", "usuario_secreto", "host.interno", "@"):
        assert proibido not in msg, proibido


def test_engine_ausente_devolve_rotulo_fechado(monkeypatch):
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.setattr(database, "datamart_engine", None)
    assert database.check_connection() == (False, database.MSG_ENGINE_LOCAL_AUSENTE)
    assert database.check_datamart_connection() == (
        False, database.MSG_ENGINE_DATAMART_AUSENTE
    )


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


def test_a_pilha_instalada_consegue_montar_um_engine_postgresql():
    """Guarda de REGRESSAO DE DEPENDENCIA (INCIDENTE-API-DB-4C).

    O SQLAlchemy 2.1.0 trocou o DBAPI padrao de `postgresql://` de `psycopg2`
    para `psycopg` (v3). Como `apps/api/pyproject.toml` declara
    `sqlalchemy>=2.0` e o Render constroi com `pip install -e .` — que IGNORA
    o `uv.lock` —, um build resolveu 2.1.0 e `create_engine` passou a levantar
    `ModuleNotFoundError: psycopg`. O engine nunca nasceu, `SessionLocal` ficou
    `None`, e TODAS as rotas com banco devolveram 503.

    Reproduzido com DSN sintetico:
        SA 2.0.43 + psycopg2          -> OK   (driver psycopg2)
        SA 2.1.0  + psycopg2          -> ModuleNotFoundError name='psycopg'
        SA 2.1.0  + psycopg2, com     -> OK   (driver psycopg2)
                  `+psycopg2://`
        SA 2.1.0  + psycopg v3        -> OK   (driver psycopg)

    Este teste afirma o COMPORTAMENTO, nao a versao: ele nao pina nada e passa
    com qualquer combinacao coerente de SQLAlchemy e driver. Falha exatamente
    quando o artefato instalado nao consegue mais montar o engine que a
    aplicacao monta no import — que e' a condicao do incidente.
    """
    from sqlalchemy import create_engine

    # DSN sintetico. Nenhuma credencial real, e `create_engine` nao conecta.
    create_engine("postgresql://u:p@host.example:5432/d", pool_pre_ping=True)


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
