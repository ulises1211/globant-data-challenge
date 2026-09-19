"""Analytics queries (the SQL lives in sql/*.sql)."""
from pathlib import Path

from psycopg.rows import dict_row

from .db import get_connection

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> list[dict]:
    query = (_SQL_DIR / filename).read_text(encoding="utf-8")
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query)
            return cur.fetchall()


def hires_by_quarter() -> list[dict]:
    return _run("hires_by_quarter.sql")


def departments_above_average() -> list[dict]:
    return _run("departments_above_average.sql")
