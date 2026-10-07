import os


def get_database_url() -> str:
    """Baca DATABASE_URL dari environment dan sesuaikan untuk SQLAlchemy + psycopg v3."""
    database_url = os.getenv("DATABASE_URL", "").strip()

    # Hilangkan tanda kutip yang kadang ikut terbawa dari file .env / dashboard hosting
    if len(database_url) >= 2 and database_url[0] == database_url[-1] and database_url[0] in ("'", '"'):
        database_url = database_url[1:-1].strip()

    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not configured.")

    # Kompatibilitas dengan SQLAlchemy + psycopg v3
    # (Render/Heroku memakai "postgres://", SQLAlchemy butuh dialect+driver eksplisit)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    return database_url
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine

    if _engine is None:
        _engine = create_engine(
            get_database_url(),
            pool_pre_ping=True,
        )

    return _engine
