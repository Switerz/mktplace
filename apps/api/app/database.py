from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _make_engine(url: str):
    if not url:
        return None
    try:
        return create_engine(url, pool_pre_ping=True)
    except Exception:
        return None


# Banco local: destino dos dados tratados pela aplica??o, incluindo Shopee.
engine = _make_engine(settings.database_url)
SessionLocal = (
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
    if engine is not None
    else None
)

# Data Mart remoto: fonte read-only para ML/TikTok e gold/raw existentes.
datamart_engine = _make_engine(settings.datamart_url)
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
