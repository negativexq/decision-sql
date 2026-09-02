from collections.abc import Generator

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings


def build_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True, future=True)


def build_reader_engine(settings: Settings | None = None) -> Engine:
    config = settings or get_settings()
    return build_engine(config.database_url)


def build_admin_engine(settings: Settings | None = None) -> Engine:
    config = settings or get_settings()
    return build_engine(config.admin_database_url)


reader_engine = build_reader_engine()
ReaderSession = sessionmaker(bind=reader_engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with ReaderSession() as session:
        yield session


def check_reader_connection(engine: Engine | Connection | None = None) -> bool:
    target = engine or reader_engine
    try:
        if isinstance(target, Connection):
            target.execute(text("SELECT 1"))
        else:
            with target.connect() as connection:
                connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
