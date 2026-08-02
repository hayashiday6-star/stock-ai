"""Database engine, session lifecycle, and schema creation.

The :class:`Database` wraps a SQLAlchemy engine plus a session factory. Pass a
URL to target a specific database (e.g. ``"sqlite:///:memory:"`` in tests);
omit it to use the on-disk project database under ``data/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from stock_ai.config.constants import DATA_DIR
from stock_ai.database.models import Base


def default_sqlite_url() -> str:
    """Return the on-disk SQLite URL, creating the ``data/`` directory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DATA_DIR / 'stock_ai.db'}"


def _enable_sqlite_fk(dbapi_connection: Any, _record: Any) -> None:
    """Enforce foreign keys on each SQLite connection (off by default)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:
    """Owns the engine and hands out transactional sessions."""

    def __init__(self, url: str | None = None) -> None:
        """Create the engine.

        Args:
            url: SQLAlchemy URL. Defaults to the on-disk project database.
                In-memory URLs use a shared static pool so the schema persists
                across sessions within one process.
        """
        self.url = url or default_sqlite_url()
        kwargs: dict[str, Any] = {}
        if ":memory:" in self.url:
            kwargs = {
                "poolclass": StaticPool,
                "connect_args": {"check_same_thread": False},
            }
        self.engine = create_engine(self.url, future=True, **kwargs)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _enable_sqlite_fk)
        self._session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        """Create all tables that do not yet exist."""
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        """Close all pooled connections and release the engine."""
        self.engine.dispose()

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session, committing on success and rolling back on error."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
