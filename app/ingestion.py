"""Validate a batch of records and persist the valid ones.

Invalid records are never inserted: they are written to `rejected_records`
and logged. Valid + rejected writes happen in the same transaction.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from psycopg.types.json import Jsonb

from .config import MAX_BATCH_SIZE
from .db import get_connection
from .validators import TABLES, VALIDATORS, ValidationError

logger = logging.getLogger("ingestion")

_INSERT_SQL = {
    "departments": "INSERT INTO departments (id, department) VALUES (%(id)s, %(department)s)",
    "jobs": "INSERT INTO jobs (id, job) VALUES (%(id)s, %(job)s)",
    "hired_employees": (
        "INSERT INTO hired_employees (id, name, datetime, department_id, job_id) "
        "VALUES (%(id)s, %(name)s, %(datetime)s, %(department_id)s, %(job_id)s)"
    ),
}
# Restore in "merge" mode overwrites rows that already exist.
_UPSERT_SUFFIX = {
    "departments": " ON CONFLICT (id) DO UPDATE SET department = EXCLUDED.department",
    "jobs": " ON CONFLICT (id) DO UPDATE SET job = EXCLUDED.job",
    "hired_employees": (
        " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, datetime = EXCLUDED.datetime,"
        " department_id = EXCLUDED.department_id, job_id = EXCLUDED.job_id"
    ),
}


@dataclass
class IngestResult:
    table: str
    received: int = 0
    inserted: int = 0
    rejected: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add(self, other: "IngestResult") -> None:
        self.received += other.received
        self.inserted += other.inserted
        self.rejected += other.rejected
        self.errors.extend(other.errors)

    def as_dict(self) -> dict:
        return {
            "table": self.table,
            "received": self.received,
            "inserted": self.inserted,
            "rejected": self.rejected,
            "errors": self.errors,
        }


class BatchTooLargeError(ValueError):
    pass


def ingest(
    table: str,
    records: Iterable[Any],
    source: str,
    *,
    upsert: bool = False,
    conn=None,
) -> IngestResult:
    """Validate `records` for `table` and insert the valid ones.

    Pass `conn` to run inside an existing transaction (used by restore).
    """
    if table not in TABLES:
        raise KeyError(table)
    records = list(records)
    if not 1 <= len(records) <= MAX_BATCH_SIZE:
        raise BatchTooLargeError(f"a batch must contain between 1 and {MAX_BATCH_SIZE} records")

    if conn is None:
        with get_connection() as own_conn:
            return _ingest(own_conn, table, records, source, upsert)
    return _ingest(conn, table, records, source, upsert)


def _ingest(conn, table: str, records: list[Any], source: str, upsert: bool) -> IngestResult:
    result = IngestResult(table=table, received=len(records))
    valid_rows: list[dict] = []
    rejects: list[tuple[Any, str]] = []
    context = _validation_context(conn, table)
    seen_ids: set[int] = set()
    row_index: dict[int, int] = {}  # row id -> position in the incoming batch

    for index, record in enumerate(records):
        try:
            if not isinstance(record, dict):
                raise ValidationError("record must be a JSON object")
            row = VALIDATORS[table](record, **context)
            if row["id"] in seen_ids:
                raise ValidationError(f"duplicate id {row['id']} in the same batch")
            seen_ids.add(row["id"])
            row_index[row["id"]] = index
            valid_rows.append(row)
        except ValidationError as exc:
            rejects.append((record, str(exc)))
            result.errors.append({"index": index, "record": _jsonable(record), "reason": str(exc)})

    if valid_rows and not upsert:
        # Only look up the ids of this batch (not the whole table).
        taken = _existing_ids(conn, table, list(seen_ids))
        if taken:
            kept = []
            for row in valid_rows:
                if row["id"] in taken:
                    reason = f"id {row['id']} already exists in {table}"
                    original = records[row_index[row["id"]]]
                    rejects.append((original, reason))
                    result.errors.append(
                        {"index": row_index[row["id"]], "record": _jsonable(original), "reason": reason}
                    )
                else:
                    kept.append(row)
            valid_rows = kept

    sql = _INSERT_SQL[table] + (_UPSERT_SUFFIX[table] if upsert else "")
    with conn.cursor() as cur:
        if valid_rows:
            cur.executemany(sql, valid_rows)
        if rejects:
            cur.executemany(
                "INSERT INTO rejected_records (table_name, source, raw_record, reason) "
                "VALUES (%s, %s, %s, %s)",
                [(table, source, Jsonb(_jsonable(rec)), reason) for rec, reason in rejects],
            )

    result.inserted = len(valid_rows)
    result.rejected = len(rejects)
    for rec, reason in rejects:
        logger.warning(
            "REJECTED table=%s source=%s reason=%s record=%s",
            table, source, reason, json.dumps(_jsonable(rec), default=str),
        )
    logger.info(
        "INGEST table=%s source=%s received=%d inserted=%d rejected=%d",
        table, source, result.received, result.inserted, result.rejected,
    )
    return result


def _validation_context(conn, table: str) -> dict:
    if table != "hired_employees":
        return {}
    return {
        "department_ids": _ids(conn, "departments"),
        "job_ids": _ids(conn, "jobs"),
    }


def _ids(conn, table: str) -> set[int]:
    return {row[0] for row in conn.execute(f"SELECT id FROM {table}")}


def _existing_ids(conn, table: str, ids: list[int]) -> set[int]:
    rows = conn.execute(f"SELECT id FROM {table} WHERE id = ANY(%s)", (ids,))
    return {row[0] for row in rows}


def _jsonable(record: Any) -> Any:
    """Make an arbitrary record safe to store as JSONB / return in a response."""
    try:
        json.dumps(record)
        return record
    except (TypeError, ValueError):
        return repr(record)
