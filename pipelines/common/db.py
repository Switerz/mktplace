from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from pipelines.common.config import settings


def _make_engine(url: str):
    if not url:
        return None
    return create_engine(url, pool_pre_ping=True)


# Banco local (escrita)
_local_engine = _make_engine(settings.database_url)
if _local_engine is None:
    raise RuntimeError("DATABASE_URL local nao configurado.")
LocalSession = sessionmaker(bind=_local_engine)

# Data Mart (leitura)
_datamart_engine = _make_engine(settings.datamart_url)
DataMartSession = sessionmaker(bind=_datamart_engine) if _datamart_engine is not None else None


@contextmanager
def local_session():
    session = LocalSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def datamart_session():
    if DataMartSession is None:
        raise RuntimeError("Data Mart nao configurado: defina DATAMART_DATABASE_URL ou DATAMART_*.")
    session = DataMartSession()
    try:
        yield session
    finally:
        session.close()


def datamart_query(sql: str, params: dict | None = None) -> list[dict]:
    if _datamart_engine is None:
        raise RuntimeError("Data Mart nao configurado: defina DATAMART_DATABASE_URL ou DATAMART_*.")
    with _datamart_engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings()]
