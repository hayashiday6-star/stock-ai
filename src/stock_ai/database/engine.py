"""Database engine, session lifecycle, and schema creation.

The :class:`Database` wraps a SQLAlchemy engine plus a session factory. Pass a
URL to target a specific database (e.g. ``"sqlite:///:memory:"`` in tests);
omit it to use the on-disk project database under ``data/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from stock_ai.config.constants import DATA_DIR
from stock_ai.core.logging import get_logger
from stock_ai.database.models import Base

logger = get_logger(__name__)


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
        """Bring the schema up to date: create missing tables, add missing columns.

        ``create_all`` alone only ever creates whole tables, so a database
        written by an older version keeps its old columns and every query
        touching a newly mapped one fails with "no such column". Adding the
        gap-filling step here means an existing ``data/stock_ai.db`` survives an
        upgrade instead of having to be deleted.

        The migration is deliberately additive-only: it adds nullable columns
        and new tables. Renames, drops, and type changes are out of scope and
        would need a real migration tool.
        """
        Base.metadata.create_all(self.engine)
        added = self._add_missing_columns()
        if added:
            logger.info("Schema updated with new column(s): %s", ", ".join(added))

    def _add_missing_columns(self) -> list[str]:
        """Add columns the models declare but an existing table lacks."""
        inspector = inspect(self.engine)
        added: list[str] = []

        with self.engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue  # create_all() already built it in full
                existing = {column["name"] for column in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    if not column.nullable and column.server_default is None:
                        # SQLite cannot back-fill a NOT NULL column for rows
                        # that already exist; refusing beats corrupting.
                        logger.warning(
                            "Cannot add NOT NULL column %s.%s to an existing table",
                            table.name,
                            column.name,
                        )
                        continue
                    ddl = column.type.compile(dialect=self.engine.dialect)
                    connection.execute(
                        text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl}')
                    )
                    added.append(f"{table.name}.{column.name}")
        return added

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
