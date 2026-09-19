"""REST API (FastAPI). Runs locally with uvicorn and on AWS Lambda via Mangum.

Design: one endpoint per table instead of a generic one. Each table has a
different contract (only hired_employees has a date and foreign keys), so
explicit routes give a precise OpenAPI doc and simple clients, while all the
real logic is shared in `ingestion.ingest`.
"""
import hmac
import logging
from typing import Any

import psycopg
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from . import analytics, backup
from .config import MAX_BATCH_SIZE, get_settings
from .db import init_schema
from .ingestion import BatchTooLargeError, ingest

logging.getLogger().setLevel(logging.INFO)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """App-level API key check (enabled when API_KEY is set)."""
    expected = get_settings().api_key
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


app = FastAPI(
    title="Globant Data Engineering PoC",
    version="1.0.0",
    dependencies=[Depends(require_api_key)],
)


class IngestResponse(BaseModel):
    table: str
    received: int
    inserted: int
    rejected: int
    errors: list[dict[str, Any]]


class RestoreRequest(BaseModel):
    key: str | None = None  # backup file key; latest backup when omitted
    mode: str = "replace"   # replace | merge


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


def _ingest_endpoint(table: str, records: list[Any], response: Response) -> dict:
    try:
        result = ingest(table, records, "api")
    except BatchTooLargeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # 201 all inserted | 207 partially inserted | 422 nothing inserted
    response.status_code = 201 if result.rejected == 0 else 207 if result.inserted else 422
    return result.as_dict()


_BODY = Body(..., description=f"JSON array with 1 to {MAX_BATCH_SIZE} records")


@app.post("/departments", response_model=IngestResponse, tags=["ingestion"])
def post_departments(response: Response, records: list[Any] = _BODY):
    return _ingest_endpoint("departments", records, response)


@app.post("/jobs", response_model=IngestResponse, tags=["ingestion"])
def post_jobs(response: Response, records: list[Any] = _BODY):
    return _ingest_endpoint("jobs", records, response)


@app.post("/hired-employees", response_model=IngestResponse, tags=["ingestion"])
def post_hired_employees(response: Response, records: list[Any] = _BODY):
    return _ingest_endpoint("hired_employees", records, response)


@app.post("/backup/{table}", tags=["backup"])
def post_backup(table: str):
    try:
        return backup.backup_table(table)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown table {table}") from None


@app.post("/restore/{table}", tags=["backup"])
def post_restore(table: str, request: RestoreRequest = RestoreRequest()):
    try:
        return backup.restore_table(table, request.key, request.mode)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown table {table}") from None
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(
            status_code=409,
            detail="rows in other tables still reference this table; use mode=merge "
                   "or restore the dependent table afterwards",
        ) from None
    except Exception as exc:  # unreadable / corrupt AVRO file, storage errors
        logging.getLogger("backup").exception("restore failed")
        raise HTTPException(status_code=400, detail=f"restore failed: {exc}") from None


@app.get("/analytics/hires-by-quarter", tags=["analytics"])
def get_hires_by_quarter():
    return analytics.hires_by_quarter()


@app.get("/analytics/departments-above-average", tags=["analytics"])
def get_departments_above_average():
    return analytics.departments_above_average()


@app.post("/admin/init-schema", include_in_schema=False)
def post_init_schema():
    init_schema()
    return {"status": "schema ready"}
