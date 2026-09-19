"""AVRO backup and restore of whole tables."""
import io
import logging
from datetime import datetime, timezone

import fastavro

from . import storage
from .db import get_connection
from .ingestion import IngestResult, ingest
from .validators import TABLES

logger = logging.getLogger("backup")

RESTORE_BATCH = 1000

AVRO_SCHEMAS = {
    "departments": {
        "type": "record", "name": "departments",
        "fields": [{"name": "id", "type": "int"}, {"name": "department", "type": "string"}],
    },
    "jobs": {
        "type": "record", "name": "jobs",
        "fields": [{"name": "id", "type": "int"}, {"name": "job", "type": "string"}],
    },
    "hired_employees": {
        "type": "record", "name": "hired_employees",
        "fields": [
            {"name": "id", "type": "int"},
            {"name": "name", "type": "string"},
            {"name": "datetime", "type": "string"},  # ISO 8601, as in the data model
            {"name": "department_id", "type": "int"},
            {"name": "job_id", "type": "int"},
        ],
    },
}

_SELECT = {
    "departments": "SELECT id, department FROM departments ORDER BY id",
    "jobs": "SELECT id, job FROM jobs ORDER BY id",
    "hired_employees": (
        "SELECT id, name, to_char(datetime AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
        "AS datetime, department_id, job_id FROM hired_employees ORDER BY id"
    ),
}


def backup_table(table: str) -> dict:
    """Export the full table to an AVRO file and return where it was saved."""
    _check_table(table)
    schema = fastavro.parse_schema(AVRO_SCHEMAS[table])
    buffer = io.BytesIO()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SELECT[table])
            columns = [c.name for c in cur.description]
            rows = (dict(zip(columns, row)) for row in cur)
            # Stream in blocks so memory does not grow with row count on the DB side.
            fastavro.writer(buffer, schema, rows, codec="deflate")
            count = cur.rowcount
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"{table}/{table}_{stamp}.avro"
    location = storage.save(key, buffer.getvalue())
    logger.info("BACKUP table=%s rows=%d location=%s", table, count, location)
    return {"table": table, "rows": count, "key": key, "location": location}


def restore_table(table: str, key: str | None = None, mode: str = "replace") -> dict:
    """Restore `table` from an AVRO backup (latest one when `key` is omitted).

    mode="replace": delete current rows, then load the backup (one transaction).
    mode="merge":   upsert backup rows, keep rows that are not in the backup.
    Rows are re-validated with the same rules as any other ingestion.
    """
    _check_table(table)
    if mode not in ("replace", "merge"):
        raise ValueError("mode must be 'replace' or 'merge'")
    key = key or storage.latest_key(f"{table}/")
    if key is None:
        raise FileNotFoundError(f"no backup found for table {table}")

    data = storage.load(key)
    records = list(fastavro.reader(io.BytesIO(data)))
    result = IngestResult(table=table)

    with get_connection() as conn:  # single transaction: all or nothing
        if mode == "replace":
            conn.execute(f"DELETE FROM {table}")
        for start in range(0, len(records), RESTORE_BATCH):
            result.add(
                ingest(table, records[start:start + RESTORE_BATCH], "restore",
                       upsert=(mode == "merge"), conn=conn)
            )
    logger.info("RESTORE table=%s key=%s mode=%s inserted=%d rejected=%d",
                table, key, mode, result.inserted, result.rejected)
    return {"key": key, "mode": mode, **result.as_dict()}


def _check_table(table: str) -> None:
    if table not in TABLES:
        raise KeyError(table)
