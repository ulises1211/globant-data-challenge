"""
EN: PostgreSQL connection pool and schema bootstrap.
ES: Pool de conexiones a PostgreSQL y creación del esquema.
"""
from contextlib import contextmanager
from pathlib import Path

from psycopg_pool import ConnectionPool

from .config import get_settings

# EN: SQL file that creates every table (idempotent). | ES: Archivo SQL que crea las tablas (idempotente).
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"

# EN: One pool per process, created lazily on first use. | ES: Un pool por proceso, creado al primer uso.
_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    """
    EN: Return the process-wide connection pool, creating it on first use. It is small
        (1 to 4 connections) because a Lambda container handles one request at a time. The
        `check` option transparently replaces connections that were dropped while the
        container was frozen. Every connection uses the UTC time zone.
    ES: Devuelve el pool de conexiones del proceso, creándolo al primer uso. Es pequeño
        (1 a 4 conexiones) porque un contenedor Lambda atiende una petición a la vez. La
        opción `check` reemplaza de forma transparente las conexiones que se cerraron mientras
        el contenedor estaba congelado. Toda conexión usa la zona horaria UTC.
    """
    global _pool
    if _pool is None:
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
    """
    EN: Context manager that lends a connection from the pool. Everything inside the `with`
        block is ONE transaction: it is committed on success and rolled back if an exception
        escapes. This is what makes "valid rows + rejected log" atomic.
    ES: Administrador de contexto que presta una conexión del pool. Todo lo que está dentro del
        bloque `with` es UNA transacción: se confirma si termina bien y se revierte si sale
        una excepción. Así "filas válidas + log de rechazos" es atómico.
    """
    with _get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    """
    EN: Close the pool and forget it. Used by the CLI loader on exit and by tests to start
        from a clean state.
    ES: Cierra el pool y lo descarta. Lo usa el loader por línea de comandos al terminar y las
        pruebas para empezar desde un estado limpio.
    """
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema() -> None:
    """
    EN: Create the tables and indexes if they do not exist yet (safe to run many times).
    ES: Crea las tablas e índices si aún no existen (se puede ejecutar muchas veces sin riesgo).
    """
    with get_connection() as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
