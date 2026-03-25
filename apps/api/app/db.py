from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import get_settings

settings = get_settings()


def _build_engine():
    url = settings.database_url
    try:
        if url.startswith("sqlite"):
            return create_engine(url, future=True, connect_args={"check_same_thread": False})
        return create_engine(url, future=True, pool_pre_ping=True)
    except ModuleNotFoundError:
        # Local fallback for environments missing database driver packages.
        return create_engine("sqlite:///./a1phquest_dev.db", future=True, connect_args={"check_same_thread": False})


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
