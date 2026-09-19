"""
EN: REST API (FastAPI). Runs locally with uvicorn and on AWS Lambda through Mangum.

    Design decisions
    - One endpoint per table instead of a generic one: each table has a different contract
      (only hired_employees has a date and foreign keys), so explicit routes give a precise
      OpenAPI document and simple clients. All the real logic is shared in ingestion.ingest.
    - Endpoints are plain `def`, not `async def`, on purpose. The database driver (psycopg)
      and boto3 are BLOCKING libraries. FastAPI runs a plain `def` endpoint in a thread pool,
      so it never freezes the event loop. An `async def` endpoint that calls a blocking
      library would freeze the whole server while it waits. `async def` only pays off with
      async drivers (psycopg async, aioboto3) AND many concurrent requests per process; a
      Lambda container serves one request at a time, so it would add complexity for no gain.

ES: API REST (FastAPI). Corre en local con uvicorn y en AWS Lambda mediante Mangum.

    Decisiones de diseño
    - Un endpoint por tabla en vez de uno genérico: cada tabla tiene un contrato distinto
      (solo hired_employees tiene fecha y llaves foráneas), así las rutas explícitas dan un
      documento OpenAPI preciso y clientes simples. Toda la lógica real se comparte en
      ingestion.ingest.
    - Los endpoints son `def` normales, no `async def`, a propósito. El driver de la base
      (psycopg) y boto3 son librerías BLOQUEANTES. FastAPI ejecuta un endpoint `def` en un
      pool de hilos, así nunca congela el bucle de eventos. Un endpoint `async def` que llama a
      una librería bloqueante congelaría todo el servidor mientras espera. `async def` solo
      rinde con drivers asíncronos (psycopg async, aioboto3) Y muchas peticiones simultáneas
      por proceso; un contenedor Lambda atiende una petición a la vez, así que solo añadiría
      complejidad sin beneficio.
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
    """
    EN: Application-level API key check, enabled only when the API_KEY variable is set. The
        comparison is constant-time (hmac.compare_digest) so response timing cannot be used to
        guess the key. On AWS, API Gateway already enforces its own key before the request
        reaches this code.
    ES: Verificación de API key a nivel de aplicación, activa solo cuando la variable API_KEY
        está definida. La comparación es de tiempo constante (hmac.compare_digest) para que el
        tiempo de respuesta no sirva para adivinar la llave. En AWS, API Gateway ya exige su
        propia llave antes de que la petición llegue a este código.
    Raises: HTTPException 401.
    """
    expected = get_settings().api_key
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


app = FastAPI(
    title="Globant Data Engineering PoC",
    version="1.0.0",
    dependencies=[Depends(require_api_key)],
)


class IngestResponse(BaseModel):
    """
    EN: Body returned by the ingestion endpoints: totals plus the reason for every rejected row.
    ES: Cuerpo que devuelven los endpoints de ingesta: totales más el motivo de cada fila rechazada.
    """

    table: str
    received: int
    inserted: int
    rejected: int
    errors: list[dict[str, Any]]


class RestoreRequest(BaseModel):
    """
    EN: Optional body of POST /restore/{table}: which backup file to use and how to apply it.
    ES: Cuerpo opcional de POST /restore/{table}: qué archivo de backup usar y cómo aplicarlo.
    """

    key: str | None = None  # EN: backup file key; latest when omitted | ES: llave del archivo; el más reciente si se omite
    mode: str = "replace"   # replace | merge


@app.get("/health", include_in_schema=False)
def health() -> dict:
    """
    EN: Liveness check. Does not touch the database.
    ES: Comprobación de vida. No toca la base de datos.
    """
    return {"status": "ok"}


def _ingest_endpoint(table: str, records: list[Any], response: Response) -> dict:
    """
    EN: Shared body of the three ingestion endpoints. Runs the ingestion and sets the HTTP
        status: 201 when every row was inserted, 207 (multi-status) when some were inserted and
        some rejected, 422 when nothing was inserted. A batch of 0 or more than 1000 rows is
        answered with 422 before anything is written.
    ES: Cuerpo compartido de los tres endpoints de ingesta. Ejecuta la ingesta y fija el
        estado HTTP: 201 si se insertaron todas las filas, 207 (multi-estado) si se insertaron
        unas y se rechazaron otras, 422 si no se insertó nada. Un lote de 0 filas o de más de
        1000 se responde con 422 antes de escribir nada.
    """
    try:
        result = ingest(table, records, "api")
    except BatchTooLargeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    response.status_code = 201 if result.rejected == 0 else 207 if result.inserted else 422
    return result.as_dict()


_BODY = Body(..., description=f"JSON array with 1 to {MAX_BATCH_SIZE} records")


@app.post("/departments", response_model=IngestResponse, tags=["ingestion"])
def post_departments(response: Response, records: list[Any] = _BODY):
    """
    EN: Ingest a batch (1 to 1000) of departments: [{"id": 1, "department": "Sales"}, ...].
    ES: Ingesta un lote (1 a 1000) de departamentos: [{"id": 1, "department": "Sales"}, ...].
    """
    return _ingest_endpoint("departments", records, response)


@app.post("/jobs", response_model=IngestResponse, tags=["ingestion"])
def post_jobs(response: Response, records: list[Any] = _BODY):
    """
    EN: Ingest a batch (1 to 1000) of jobs: [{"id": 1, "job": "Recruiter"}, ...].
    ES: Ingesta un lote (1 a 1000) de puestos: [{"id": 1, "job": "Recruiter"}, ...].
    """
    return _ingest_endpoint("jobs", records, response)


@app.post("/hired-employees", response_model=IngestResponse, tags=["ingestion"])
def post_hired_employees(response: Response, records: list[Any] = _BODY):
    """
    EN: Ingest a batch (1 to 1000) of hired employees. Every field is required, datetime must
        be ISO 8601, and department_id / job_id must already exist.
    ES: Ingesta un lote (1 a 1000) de empleados contratados. Todos los campos son obligatorios,
        datetime debe ser ISO 8601 y department_id / job_id deben existir.
    """
    return _ingest_endpoint("hired_employees", records, response)


@app.post("/backup/{table}", tags=["backup"])
def post_backup(table: str):
    """
    EN: Export the full table to a new AVRO file and return where it was saved.
    ES: Exporta la tabla completa a un nuevo archivo AVRO y devuelve dónde se guardó.
    Raises: HTTPException 404 for an unknown table. | 404 si la tabla no existe.
    """
    try:
        return backup.backup_table(table)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown table {table}") from None


@app.post("/restore/{table}", tags=["backup"])
def post_restore(table: str, request: RestoreRequest = RestoreRequest()):
    """
    EN: Restore a table from an AVRO backup. Error mapping: 404 unknown table or no backup,
        422 invalid mode, 409 when other tables still reference the rows being replaced, 400
        for an unreadable or corrupt file.
    ES: Restaura una tabla desde un backup AVRO. Mapeo de errores: 404 tabla desconocida o sin
        backup, 422 modo inválido, 409 si otras tablas aún referencian las filas que se
        reemplazan, 400 para un archivo ilegible o corrupto.
    """
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
    except Exception as exc:  # EN: corrupt AVRO file, storage errors | ES: AVRO corrupto, errores de almacenamiento
        logging.getLogger("backup").exception("restore failed")
        raise HTTPException(status_code=400, detail=f"restore failed: {exc}") from None


@app.get("/analytics/hires-by-quarter", tags=["analytics"])
def get_hires_by_quarter():
    """
    EN: Hires per department and job in 2021, by quarter (department, job, Q1..Q4).
    ES: Contrataciones por departamento y puesto en 2021, por trimestre (department, job, Q1..Q4).
    """
    return analytics.hires_by_quarter()


@app.get("/analytics/departments-above-average", tags=["analytics"])
def get_departments_above_average():
    """
    EN: Departments that hired more than the 2021 average (id, department, hired), highest first.
    ES: Departamentos que contrataron más que el promedio de 2021 (id, department, hired), de mayor a menor.
    """
    return analytics.departments_above_average()


@app.post("/admin/init-schema", include_in_schema=False)
def post_init_schema():
    """
    EN: Create the tables if they do not exist. Hidden from the OpenAPI document.
    ES: Crea las tablas si no existen. Oculto del documento OpenAPI.
    """
    init_schema()
    return {"status": "schema ready"}
