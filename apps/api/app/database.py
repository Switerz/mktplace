from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _make_engine(url: str, connect_timeout: int | None = None):
    """Cria o engine da URL informada. `connect_timeout` (em segundos) e'
    OPCIONAL e, quando fornecido, vira `connect_args={"connect_timeout": N}` —
    aplicado hoje somente ao Data Mart (Gate G4). Sem ele, o engine e' criado
    exatamente como antes, sem nenhum `connect_args` novo."""
    if not url:
        return None
    try:
        if connect_timeout is None:
            return create_engine(url, pool_pre_ping=True)
        return create_engine(
            url, pool_pre_ping=True, connect_args={"connect_timeout": connect_timeout}
        )
    except Exception:
        return None


# Banco local: destino dos dados tratados pela aplica??o, incluindo Shopee.
# Sem `connect_timeout`: o engine principal/Neon permanece EXATAMENTE como antes
# (o diagnostico do G4 mostrou as rotas do Neon respondendo em 0,42-0,82s).
engine = _make_engine(settings.database_url)
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
    settings.datamart_url, connect_timeout=settings.datamart_connect_timeout_seconds
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


def check_connection() -> tuple[bool, str | None]:
    if engine is None:
        return False, "local engine not initialized"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as e:
        return False, str(e)


def check_datamart_connection() -> tuple[bool, str | None]:
    if datamart_engine is None:
        return False, "datamart engine not initialized"
    try:
        with datamart_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as e:
        return False, str(e)
