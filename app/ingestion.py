"""
EN: Validate a batch of records and persist the valid ones. Invalid records are never
    inserted: they are written to `rejected_records` and logged. Valid rows and the rejection
    log are written in the SAME transaction.
ES: Valida un lote de registros y guarda los válidos. Los registros inválidos nunca se
    insertan: se escriben en `rejected_records` y se registran en el log. Las filas válidas y
    el log de rechazos se escriben en la MISMA transacción.
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

# EN: INSERT statement per table (named parameters). | ES: Sentencia INSERT por tabla (parámetros con nombre).
_INSERT_SQL = {
    "departments": "INSERT INTO departments (id, department) VALUES (%(id)s, %(department)s)",
    "jobs": "INSERT INTO jobs (id, job) VALUES (%(id)s, %(job)s)",
    "hired_employees": (
        "INSERT INTO hired_employees (id, name, datetime, department_id, job_id) "
        "VALUES (%(id)s, %(name)s, %(datetime)s, %(department_id)s, %(job_id)s)"
    ),
}
# EN: Appended to the INSERT in "merge" mode so existing rows are overwritten (upsert).
# ES: Se añade al INSERT en modo "merge" para sobrescribir filas existentes (upsert).
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
    """
    EN: Outcome of ingesting one or more batches: how many rows were received, inserted and
        rejected, plus the detail (position, original record, reason) of every rejection.
    ES: Resultado de ingerir uno o más lotes: cuántas filas se recibieron, insertaron y
        rechazaron, más el detalle (posición, registro original, motivo) de cada rechazo.
    """

    table: str
    received: int = 0
    inserted: int = 0
    rejected: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add(self, other: "IngestResult") -> None:
        """
        EN: Accumulate another result into this one (used to total several batches).
        ES: Acumula otro resultado en este (sirve para totalizar varios lotes).
        """
        self.received += other.received
        self.inserted += other.inserted
        self.rejected += other.rejected
        self.errors.extend(other.errors)

    def as_dict(self) -> dict:
        """
        EN: Plain dictionary form, ready to be returned as JSON by the API.
        ES: Forma de diccionario simple, lista para devolverse como JSON en la API.
        """
        return {
            "table": self.table,
            "received": self.received,
            "inserted": self.inserted,
            "rejected": self.rejected,
            "errors": self.errors,
        }


class BatchTooLargeError(ValueError):
    """
    EN: Raised when a batch has 0 rows or more than MAX_BATCH_SIZE (1000) rows.
    ES: Se lanza cuando un lote tiene 0 filas o más de MAX_BATCH_SIZE (1000) filas.
    """


def ingest(
    table: str,
    records: Iterable[Any],
    source: str,
    *,
    upsert: bool = False,
    conn=None,
) -> IngestResult:
    """
    EN: Validate `records` for `table` and insert the valid ones; log and store the rest.
        Opens its own transaction unless an existing connection is passed (the restore does
        that so that deleting and reloading is one atomic step).
    ES: Valida `records` para `table` e inserta los válidos; registra y guarda el resto. Abre
        su propia transacción salvo que se pase una conexión existente (el restore lo hace
        para que borrar y recargar sea un único paso atómico).
    Args:
        source: where the data came from: "csv", "api" or "restore".
                de dónde vienen los datos: "csv", "api" o "restore".
        upsert: overwrite rows whose id already exists instead of rejecting them.
                sobrescribe filas cuyo id ya existe en lugar de rechazarlas.
        conn:   optional existing connection/transaction. | conexión/transacción existente opcional.
    Raises: KeyError (unknown table), BatchTooLargeError (batch size outside 1..1000).
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
    """
    EN: Do the actual work on an open connection, in three steps: (1) validate every record
        and detect duplicates inside the batch, (2) reject ids that already exist in the
        table (one query for the whole batch), (3) insert the valid rows and write the
        rejected ones to `rejected_records` and to the log.
    ES: Hace el trabajo real sobre una conexión abierta, en tres pasos: (1) valida cada
        registro y detecta duplicados dentro del lote, (2) rechaza los ids que ya existen en
        la tabla (una sola consulta para todo el lote), (3) inserta las filas válidas y
        escribe las rechazadas en `rejected_records` y en el log.
    """
    result = IngestResult(table=table, received=len(records))
    valid_rows: list[dict] = []
    rejects: list[tuple[Any, str]] = []
    context = _validation_context(conn, table)
    seen_ids: set[int] = set()
    row_index: dict[int, int] = {}  # EN: row id -> position in the batch | ES: id de la fila -> posición en el lote

    # --- Step 1 / Paso 1: per-record validation / validación por registro ---
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

    # --- Step 2 / Paso 2: ids that already exist in the table / ids que ya existen en la tabla ---
    if valid_rows and not upsert:
        # EN: Only look up the ids of this batch, not the whole table.
        # ES: Solo se consultan los ids de este lote, no toda la tabla.
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

    # --- Step 3 / Paso 3: write valid rows and the rejection log / escribir válidas y rechazos ---
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
    """
    EN: Reference data the validator needs. Only `hired_employees` has foreign keys, so only
        it receives the sets of existing department and job ids.
    ES: Datos de referencia que necesita el validador. Solo `hired_employees` tiene llaves
        foráneas, por eso solo ella recibe los conjuntos de ids de departamentos y puestos.
    """
    if table != "hired_employees":
        return {}
    return {
        "department_ids": _ids(conn, "departments"),
        "job_ids": _ids(conn, "jobs"),
    }


def _ids(conn, table: str) -> set[int]:
    """
    EN: Every id currently stored in `table` (the table name comes from a fixed allow-list,
        never from user input).
    ES: Todos los ids guardados hoy en `table` (el nombre de la tabla viene de una lista fija,
        nunca de datos del usuario).
    """
    return {row[0] for row in conn.execute(f"SELECT id FROM {table}")}


def _existing_ids(conn, table: str, ids: list[int]) -> set[int]:
    """
    EN: Which of the given ids already exist in `table` (a single query with ANY()).
    ES: Cuáles de los ids dados ya existen en `table` (una sola consulta con ANY()).
    """
    rows = conn.execute(f"SELECT id FROM {table} WHERE id = ANY(%s)", (ids,))
    return {row[0] for row in rows}


def _jsonable(record: Any) -> Any:
    """
    EN: Make an arbitrary record safe to store as JSONB and to return in a response. Values
        that cannot be serialised are replaced by their repr() text.
    ES: Hace que un registro cualquiera sea seguro de guardar como JSONB y de devolver en una
        respuesta. Los valores que no se pueden serializar se reemplazan por su texto repr().
    """
    try:
        json.dumps(record)
        return record
    except (TypeError, ValueError):
        return repr(record)
