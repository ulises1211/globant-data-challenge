"""PostgreSQL connection pool and schema bootstrap."""
from contextlib import contextmanager
from pathlib import Path

from psycopg_pool import ConnectionPool

from .config import get_settings

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        # Small pool: Lambda handles one request per container at a time.
        # `check` transparently replaces connections dropped while frozen.
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=4,
            open=True,
            check=ConnectionPool.check_connection,
            kwargs={"options": "-c timezone=UTC"},
        )
    return _pool


@contextmanager
def get_connection():
    """Yield a connection; the block is one transaction (commit / rollback)."""
    with _get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema() -> None:
    """Create tables if they do not exist (idempotent)."""
    with get_connection() as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
