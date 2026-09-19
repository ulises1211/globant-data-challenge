"""Historical data migration: CSV (no header) -> PostgreSQL.

Usage (local):  python -m app.loader --dir data
On AWS the same code runs in a Lambda that reads the CSVs from S3
(see lambda_loader.py).
"""
import argparse
import csv
import io
import logging
from pathlib import Path
from typing import Iterable, Iterator

from .config import MAX_BATCH_SIZE
from .db import close_pool, init_schema
from .ingestion import IngestResult, ingest
from .validators import TABLES

logger = logging.getLogger("loader")

# Parents first so hired_employees can validate its foreign keys.
LOAD_ORDER = ("departments", "jobs", "hired_employees")


def csv_records(lines: Iterable[str], table: str) -> Iterator[dict]:
    """Map header-less CSV rows to dicts using the data dictionary column order.

    A row with the wrong number of columns is yielded as-is (a list) so the
    ingestion step rejects and logs it instead of silently dropping it.
    """
    columns = TABLES[table].columns
    for row in csv.reader(lines):
        if not row:
            continue
        yield dict(zip(columns, row)) if len(row) == len(columns) else {"raw_row": row}


def load_table(table: str, lines: Iterable[str]) -> IngestResult:
    total = IngestResult(table=table)
    batch: list[dict] = []
    for record in csv_records(lines, table):
        batch.append(record)
        if len(batch) == MAX_BATCH_SIZE:
            total.add(ingest(table, batch, "csv"))
            batch = []
    if batch:
        total.add(ingest(table, batch, "csv"))
    return total


def load_directory(directory: Path) -> list[dict]:
    init_schema()
    summary = []
    for table in LOAD_ORDER:
        with open(directory / f"{table}.csv", encoding="utf-8-sig", newline="") as handle:
            result = load_table(table, handle)
        summary.append({k: v for k, v in result.as_dict().items() if k != "errors"})
    return summary


def load_from_s3(bucket: str, prefix: str = "") -> list[dict]:
    import boto3

    s3 = boto3.client("s3")
    init_schema()
    summary = []
    for table in LOAD_ORDER:
        body = s3.get_object(Bucket=bucket, Key=f"{prefix}{table}.csv")["Body"].read()
        lines = io.StringIO(body.decode("utf-8-sig"), newline="")
        result = load_table(table, lines)
        summary.append({k: v for k, v in result.as_dict().items() if k != "errors"})
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Load the historical CSV files")
    parser.add_argument("--dir", default="data", type=Path, help="folder with the CSV files")
    args = parser.parse_args()
    try:
        for row in load_directory(args.dir):
            print(row)
    finally:
        close_pool()


if __name__ == "__main__":
    main()
