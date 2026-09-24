import logging

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

#: Por que cada engine nao subiu, quando nao subiu. Preenchido no import e lido
#: pela readiness. Guarda SOMENTE a categoria e o nome da classe da excecao —
#: nunca a mensagem.
#:
#: A mensagem NAO pode ser usada: `create_engine` levanta `ArgumentError` com o
#: texto "Could not parse SQLAlchemy URL from string '<a URL inteira>'". Logar
#: `str(exc)` publicaria usuario e senha no log do Render. E' por isso que a
#: unica coisa que sai daqui e' `type(exc).__name__`.
FALHA_DO_ENGINE: dict[str, str] = {}

#: Categorias de falha estrutural. Nenhuma delas se resolve sozinha: todas
#: exigem corrigir configuracao ou artefato e subir de novo.
CATEGORIA_URL_VAZIA = "url_ausente_ou_vazia"
CATEGORIA_URL_INVALIDA = "url_malformada"
CATEGORIA_DRIVER_AUSENTE = "driver_indisponivel"
CATEGORIA_DESCONHECIDA = "falha_desconhecida_ao_criar_engine"


def _categoria(exc: BaseException) -> str:
    """Classifica a falha SEM olhar a mensagem.

    `ArgumentError`     -> URL que o SQLAlchemy nao consegue interpretar:
                           aspas, espaco a esquerda, prefixo `DATABASE_URL=`,
                           wrapper `psql `, falta de esquema.
    `NoSuchModuleError` -> esquema/driver que nao existe: `postgres://` (alias
                           removido no SQLAlchemy 2.x) ou `+driver` inexistente.
    `ModuleNotFoundError`-> o DBAPI nao esta no artefato (ex.: `psycopg2`).
    """
    nome = type(exc).__name__
    if nome == "ArgumentError":
        return CATEGORIA_URL_INVALIDA
    if nome in ("NoSuchModuleError", "ModuleNotFoundError", "ImportError"):
        return CATEGORIA_DRIVER_AUSENTE
    return CATEGORIA_DESCONHECIDA


def _make_engine(url: str, connect_timeout: int | None = None, *, nome: str = "local"):
    """Cria o engine da URL informada. `connect_timeout` (em segundos) e'
    OPCIONAL e, quando fornecido, vira `connect_args={"connect_timeout": N}` —
    aplicado hoje somente ao Data Mart (Gate G4). Sem ele, o engine e' criado
    exatamente como antes, sem nenhum `connect_args` novo.

    Devolver `None` em silencio foi o que transformou um erro de configuracao
    em 503 mudo: o processo subiu, o `/health` respondeu 200, o Render promoveu
    a instancia e a API inteira ficou sem banco sem uma linha de log
    (INCIDENTE-API-DB-2). Agora a falha e' registrada e fica legivel pela
    readiness — sempre sanitizada.
    """
    if not url:
        FALHA_DO_ENGINE[nome] = CATEGORIA_URL_VAZIA
        logger.error("engine %s nao criado: %s", nome, CATEGORIA_URL_VAZIA)
        return None
    try:
        if connect_timeout is None:
            eng = create_engine(url, pool_pre_ping=True)
        else:
            eng = create_engine(
                url, pool_pre_ping=True,
                connect_args={"connect_timeout": connect_timeout},
            )
    except Exception as exc:  # noqa: BLE001 — fronteira de configuracao
        cat = _categoria(exc)
        FALHA_DO_ENGINE[nome] = cat
        # `type(exc).__name__` e' seguro; `str(exc)` NAO e' (carrega a URL).
        logger.error(
            "engine %s nao criado: %s (%s)", nome, cat, type(exc).__name__
        )
        return None
    FALHA_DO_ENGINE.pop(nome, None)
    return eng


# Banco local: destino dos dados tratados pela aplica??o, incluindo Shopee.
# Sem `connect_timeout`: o engine principal/Neon permanece EXATAMENTE como antes
# (o diagnostico do G4 mostrou as rotas do Neon respondendo em 0,42-0,82s).
engine = _make_engine(settings.database_url, nome="local")
SessionLocal = (
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
    if engine is not None
    else None
)

# Data Mart remoto: fonte read-only para ML/TikTok e gold/raw existentes.
# `connect_timeout` curto (Gate G4) — MITIGACAO, nao correcao: encurta a espera
# quando o host e' inalcancavel (caso do Render hoje). Nao ha retry, fallback
# nem cache; a falha continua propagando como falha.
datamart_engine = _make_engine(
    settings.datamart_url,
    connect_timeout=settings.datamart_connect_timeout_seconds,
    nome="datamart",
)
DataMartSessionLocal = (
    sessionmaker(autocommit=False, autoflush=False, bind=datamart_engine)
    if datamart_engine is not None
    else None
)


class Base(DeclarativeBase):
    pass


def get_db():
    if SessionLocal is None:
        yield None  # type: ignore[misc]
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_datamart_db():
    if DataMartSessionLocal is None:
        yield None  # type: ignore[misc]
        return
    db = DataMartSessionLocal()
    try:
        yield db
    finally:
        db.close()


#: Vocabulario FECHADO das checagens de conexao. Antes estas funcoes devolviam
#: `str(e)`, e a mensagem de varias excecoes do psycopg2/SQLAlchemy carrega o
#: DSN inteiro — usuario, senha e host (finding N1). Hoje nenhum caminho
#: devolve texto derivado da excecao: so' um destes rotulos, mais o NOME da
#: classe, que nao contem valor.
MSG_ENGINE_LOCAL_AUSENTE = "engine local nao inicializado"
MSG_ENGINE_DATAMART_AUSENTE = "engine do datamart nao inicializado"


def _falha_de_conexao(exc: BaseException) -> str:
    """Rotulo neutro + nome da classe. NUNCA `str(exc)`."""
    return f"falha de conexao ({type(exc).__name__})"


def check_connection() -> tuple[bool, str | None]:
    if engine is None:
        return False, MSG_ENGINE_LOCAL_AUSENTE
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, _falha_de_conexao(exc)


def check_datamart_connection() -> tuple[bool, str | None]:
    if datamart_engine is None:
        return False, MSG_ENGINE_DATAMART_AUSENTE
    try:
        with datamart_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, _falha_de_conexao(exc)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
#: Limite da fase de CONEXAO (TCP + handshake + autenticacao). Vai em
#: `connect_args`, que o libpq aplica ANTES de a conexao existir.
#:
#: A versao anterior usava `engine.connect().execution_options(timeout=3)` e
#: isso nao limitava nada: `connect()` ja tinha acontecido quando o
#: `execution_options` era aplicado, e o dialeto psycopg2 nao honra um
#: `timeout` generico de execucao. Era um numero decorativo (finding F1).
READINESS_CONNECT_TIMEOUT_S = 3

#: Limite da CONSULTA, imposto pelo proprio PostgreSQL via `statement_timeout`.
#: Cobre o caso em que a conexao abre e o servidor nao responde.
READINESS_STATEMENT_TIMEOUT_MS = 3000

PRONTO = "ready"
MOTIVO_ENGINE_AUSENTE = "engine_nao_inicializado"
MOTIVO_BANCO_INACESSIVEL = "banco_inacessivel"


def _make_readiness_engine():
    """Engine DEDICADO a sonda, criado UMA vez no import.

    Tres decisoes, cada uma por um motivo:

    `NullPool`        a sonda abre e fecha a propria conexao. Sem isso ela
                      consumiria (e envenenaria) o pool que serve as rotas
                      reais — um probe travado prenderia uma conexao de
                      producao.
    `connect_timeout` limita a fase de conexao. Nao da' para por isso no engine
                      principal sem mudar o comportamento de TODAS as consultas
                      da API.
    `statement_timeout` limita a consulta no servidor.

    Criado no import, e nao por requisicao: um `create_engine` a cada chamada
    do health check e' alocacao inutil num caminho que o Render bate sem parar.
    """
    url = settings.database_url
    if not url:
        return None
    try:
        return create_engine(
            url,
            poolclass=NullPool,
            connect_args={
                "connect_timeout": READINESS_CONNECT_TIMEOUT_S,
                # `options` e' repassado ao libpq; `-c` define GUC da sessao.
                "options": f"-c statement_timeout={READINESS_STATEMENT_TIMEOUT_MS}",
            },
        )
    except Exception as exc:  # noqa: BLE001
        cat = _categoria(exc)
        FALHA_DO_ENGINE["readiness"] = cat
        logger.error(
            "engine readiness nao criado: %s (%s)", cat, type(exc).__name__
        )
        return None


readiness_engine = _make_readiness_engine()


def readiness() -> tuple[bool, dict]:
    """A instancia consegue MESMO falar com o banco?

    Devolve `(pronta, detalhe_sanitizado)`. O detalhe carrega categoria e nome
    de classe — jamais mensagem de excecao, URL, host, usuario ou senha.

    Os dois estados de falha sao distintos, e a diferenca importa para quem
    esta de plantao:

      `engine_nao_inicializado`  ESTRUTURAL. Configuracao ou artefato errados;
                                 nao volta sozinho. Foi este o estado do
                                 INCIDENTE-API-DB-2.
      `banco_inacessivel`        O engine existe e o banco nao respondeu. Pode
                                 ser transitorio.

    LIMITE CONHECIDO: `connect_timeout` do libpq comeca a contar DEPOIS da
    resolucao de nome. Se o DNS pendurar, a sonda pendura junto — nao ha
    garantia de teto total, e este modulo nao finge que ha. Com IP literal ou
    DNS respondendo, o teto e' `connect_timeout` + `statement_timeout`.
    """
    if engine is None:
        # O engine PRINCIPAL e' o que a API usa. Se ele nao nasceu, a instancia
        # nao serve — nem adianta sondar.
        return False, {
            "status": MOTIVO_ENGINE_AUSENTE,
            "categoria": FALHA_DO_ENGINE.get("local", CATEGORIA_DESCONHECIDA),
        }
    sonda = readiness_engine if readiness_engine is not None else engine
    try:
        with sonda.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — fronteira da sonda
        return False, {
            "status": MOTIVO_BANCO_INACESSIVEL,
            "categoria": type(exc).__name__,
        }
    return True, {"status": PRONTO}
