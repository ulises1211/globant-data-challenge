"""
EN: AVRO backup and restore of whole tables. A backup reads the full table and writes one
    .avro file; a restore reads that file, validates every row again and loads it.
ES: Backup y restore en AVRO de tablas completas. Un backup lee la tabla entera y escribe un
    archivo .avro; un restore lee ese archivo, valida de nuevo cada fila y la carga.
"""
import io
import logging
from datetime import datetime, timezone

import fastavro

from . import storage
from .db import get_connection
from .ingestion import IngestResult, ingest
from .validators import TABLES

logger = logging.getLogger("backup")

# EN: Rows per batch when restoring (matches the API limit). | ES: Filas por lote al restaurar (igual al límite de la API).
RESTORE_BATCH = 1000

# EN: AVRO schema of each table. The AVRO file stores this schema in its header, so a backup
#     can be read without any other information.
# ES: Esquema AVRO de cada tabla. El archivo AVRO guarda este esquema en su cabecera, así un
#     backup se puede leer sin ninguna otra información.
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
            {"name": "datetime", "type": "string"},  # ISO 8601, as in the data model | como en el modelo de datos
            {"name": "department_id", "type": "int"},
            {"name": "job_id", "type": "int"},
        ],
    },
}

# EN: Query used to read each table. The date is formatted as ISO 8601 text in UTC.
# ES: Consulta para leer cada tabla. La fecha se formatea como texto ISO 8601 en UTC.
_SELECT = {
    "departments": "SELECT id, department FROM departments ORDER BY id",
    "jobs": "SELECT id, job FROM jobs ORDER BY id",
    "hired_employees": (
        "SELECT id, name, to_char(datetime AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
        "AS datetime, department_id, job_id FROM hired_employees ORDER BY id"
    ),
}


def backup_table(table: str) -> dict:
    """
    EN: Export the full content of `table` to an AVRO file and save it (S3 on AWS, local disk
        otherwise). The file name is "<table>/<table>_<UTC timestamp>.avro", so each backup
        is a new file and none is overwritten.
    ES: Exporta el contenido completo de `table` a un archivo AVRO y lo guarda (S3 en AWS,
        disco local en otro caso). El nombre es "<tabla>/<tabla>_<marca UTC>.avro", así cada
        backup es un archivo nuevo y ninguno se sobrescribe.
    Returns: dict with table, rows, key and location. | dict con table, rows, key y location.
    Raises: KeyError (unknown table). | tabla desconocida.
    """
    _check_table(table)
    schema = fastavro.parse_schema(AVRO_SCHEMAS[table])
    buffer = io.BytesIO()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SELECT[table])
            columns = [c.name for c in cur.description]
            rows = (dict(zip(columns, row)) for row in cur)
            # EN: fastavro consumes the rows one by one and compresses them (deflate).
            # ES: fastavro consume las filas una a una y las comprime (deflate).
            fastavro.writer(buffer, schema, rows, codec="deflate")
            count = cur.rowcount
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"{table}/{table}_{stamp}.avro"
    location = storage.save(key, buffer.getvalue())
    logger.info("BACKUP table=%s rows=%d location=%s", table, count, location)
    return {"table": table, "rows": count, "key": key, "location": location}


def restore_table(table: str, key: str | None = None, mode: str = "replace") -> dict:
    """
    EN: Restore `table` from an AVRO backup (the latest one when `key` is omitted). Rows are
        validated again with the same rules as any other ingestion. Everything runs in ONE
        transaction: if anything fails, the table is left untouched.
        mode="replace": delete the current rows, then load the backup.
        mode="merge":   insert or update the backup rows and keep the rest.
    ES: Restaura `table` desde un backup AVRO (el más reciente si se omite `key`). Las filas
        se validan de nuevo con las mismas reglas que cualquier otra ingesta. Todo corre en
        UNA transacción: si algo falla, la tabla queda intacta.
        mode="replace": borra las filas actuales y luego carga el backup.
        mode="merge":   inserta o actualiza las filas del backup y conserva el resto.
    Raises: KeyError (unknown table), ValueError (bad mode), FileNotFoundError (no backup),
            psycopg ForeignKeyViolation (other tables still reference the rows).
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

    with get_connection() as conn:  # EN: single transaction: all or nothing | ES: una transacción: todo o nada
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
    """
    EN: Fail fast if `table` is not one of the three known tables (also protects the SQL that
        embeds the table name).
    ES: Falla de inmediato si `table` no es una de las tres tablas conocidas (también protege
        el SQL que incluye el nombre de la tabla).
    Raises: KeyError.
    """
    if table not in TABLES:
        raise KeyError(table)
