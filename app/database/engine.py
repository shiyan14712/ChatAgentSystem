"""
Async SQLAlchemy engine and session management.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from structlog import get_logger

from app.config import get_settings

logger = get_logger()

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    """
    Initialize the database engine, session factory, and create tables.

    Must be called once during application startup.
    """
    global _engine, _session_factory

    settings = get_settings()
    db_url = settings.database.url

    _engine = create_async_engine(
        db_url,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        echo=settings.database.echo,
    )
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Auto-create tables (safe for development; use Alembic in production)
    from .models import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized", url=db_url.split("@")[-1])  # hide credentials


async def close_db() -> None:
    """Dispose of the engine and release connections."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection closed")


def get_engine() -> AsyncEngine:
    """Return the current engine (raises if not initialized)."""
    if _engine is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the current session factory (raises if not initialized)."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _session_factory
