"""
EN: Row-level validation rules from the data dictionary. These are pure functions (no database
    access): reference data (existing department/job ids) is passed in as sets, so the same
    rules serve the CSV loader, the REST API and the AVRO restore.
ES: Reglas de validación por fila según el diccionario de datos. Son funciones puras (sin
    acceso a la base): los datos de referencia (ids de departamentos y puestos existentes) se
    reciben como conjuntos, así las mismas reglas sirven al loader de CSV, a la API REST y al
    restore de AVRO.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

# EN: ISO 8601, e.g. 2021-07-27T16:02:08Z (optional fraction and numeric offset).
# ES: ISO 8601, p. ej. 2021-07-27T16:02:08Z (fracción de segundo y desfase numérico opcionales).
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"
)


class ValidationError(ValueError):
    """
    EN: Raised when ONE record breaks a rule. Its message is the rejection reason that is
        stored in `rejected_records`, logged and returned by the API.
    ES: Se lanza cuando UN registro incumple una regla. Su mensaje es el motivo de rechazo que
        se guarda en `rejected_records`, se registra en el log y devuelve la API.
    """


@dataclass(frozen=True)
class TableSpec:
    """
    EN: Static description of a table: its name, its columns in CSV order (the CSV files have
        no header) and which ones are free text.
    ES: Descripción estática de una tabla: su nombre, sus columnas en el orden del CSV (los
        archivos no traen encabezado) y cuáles son texto libre.
    """

    name: str
    columns: tuple[str, ...]
    text_columns: tuple[str, ...]


# EN: The three tables of the challenge. | ES: Las tres tablas del reto.
TABLES: dict[str, TableSpec] = {
    "departments": TableSpec("departments", ("id", "department"), ("department",)),
    "jobs": TableSpec("jobs", ("id", "job"), ("job",)),
    "hired_employees": TableSpec(
        "hired_employees",
        ("id", "name", "datetime", "department_id", "job_id"),
        ("name",),
    ),
}


def parse_int(record: dict, field: str) -> int:
    """
    EN: Read `field` from the record as an integer. Accepts real ints and numeric strings
        (CSV values are always strings). Rejects missing/empty values, booleans, decimals and
        any other text.
    ES: Lee `field` del registro como entero. Acepta enteros y cadenas numéricas (los valores
        del CSV siempre son cadenas). Rechaza valores ausentes o vacíos, booleanos, decimales
        y cualquier otro texto.
    Raises: ValidationError.
    """
    value = record.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{field} is required")
    # EN: bool is a subclass of int in Python, so it must be rejected explicitly.
    # ES: bool es subclase de int en Python, por eso se rechaza de forma explícita.
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip())
    raise ValidationError(f"{field} must be an integer, got {value!r}")


def parse_text(record: dict, field: str) -> str:
    """
    EN: Read `field` as a non-empty string (surrounding spaces are removed). Rejects missing,
        empty, blank or non-string values.
    ES: Lee `field` como cadena no vacía (se quitan los espacios de los extremos). Rechaza
        valores ausentes, vacíos, en blanco o que no sean texto.
    Raises: ValidationError.
    """
    value = record.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{field} is required")
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string, got {value!r}")
    return value.strip()


def parse_iso_datetime(record: dict, field: str) -> datetime:
    """
    EN: Read `field` as an ISO 8601 date-time and return it as a timezone-aware UTC datetime.
        Two checks: the text must match the ISO pattern, and it must be a real moment in time
        (so month 13 or hour 25 are rejected). Offsets such as +02:00 are converted to UTC.
    ES: Lee `field` como fecha-hora ISO 8601 y la devuelve como datetime en UTC con zona
        horaria. Dos controles: el texto debe cumplir el patrón ISO y debe ser un instante
        real (mes 13 u hora 25 se rechazan). Los desfases como +02:00 se convierten a UTC.
    Raises: ValidationError.
    """
    value = parse_text(record, field)
    if not _ISO_RE.match(value):
        raise ValidationError(f"{field} is not ISO 8601 (e.g. 2021-07-27T16:02:08Z): {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{field} is not a valid date/time: {value!r}") from None
    return parsed.astimezone(timezone.utc)


def _positive_id(record: dict, field: str) -> int:
    """
    EN: Read `field` as an integer id and require it to be greater than zero.
    ES: Lee `field` como un id entero y exige que sea mayor que cero.
    Raises: ValidationError.
    """
    value = parse_int(record, field)
    if value <= 0:
        raise ValidationError(f"{field} must be a positive integer")
    return value


def validate_department(record: dict, **_: Any) -> dict:
    """
    EN: Validate a `departments` record (id, department) and return the clean row. Extra
        keyword arguments are ignored so all validators share one calling convention.
    ES: Valida un registro de `departments` (id, department) y devuelve la fila limpia. Los
        argumentos con nombre adicionales se ignoran para que todos los validadores se llamen
        igual.
    Raises: ValidationError.
    """
    return {"id": _positive_id(record, "id"), "department": parse_text(record, "department")}


def validate_job(record: dict, **_: Any) -> dict:
    """
    EN: Validate a `jobs` record (id, job) and return the clean row.
    ES: Valida un registro de `jobs` (id, job) y devuelve la fila limpia.
    Raises: ValidationError.
    """
    return {"id": _positive_id(record, "id"), "job": parse_text(record, "job")}


def validate_hired_employee(
    record: dict, *, department_ids: set[int], job_ids: set[int]
) -> dict:
    """
    EN: Validate a `hired_employees` record. Rules: all five fields are required, `datetime`
        is ISO 8601, `department_id` exists in `department_ids` and `job_id` exists in
        `job_ids`. Returns the clean row (datetime as UTC datetime, ids as integers).
    ES: Valida un registro de `hired_employees`. Reglas: los cinco campos son obligatorios,
        `datetime` es ISO 8601, `department_id` existe en `department_ids` y `job_id` existe
        en `job_ids`. Devuelve la fila limpia (datetime en UTC, ids como enteros).
    Args:
        department_ids / job_ids: ids that exist today in the database.
                                  ids que existen hoy en la base de datos.
    Raises: ValidationError.
    """
    row = {
        "id": _positive_id(record, "id"),
        "name": parse_text(record, "name"),
        "datetime": parse_iso_datetime(record, "datetime"),
        "department_id": parse_int(record, "department_id"),
        "job_id": parse_int(record, "job_id"),
    }
    if row["department_id"] not in department_ids:
        raise ValidationError(f"department_id {row['department_id']} does not exist in departments")
    if row["job_id"] not in job_ids:
        raise ValidationError(f"job_id {row['job_id']} does not exist in jobs")
    return row


# EN: Validator for each table, looked up by table name. | ES: Validador de cada tabla, buscado por nombre.
VALIDATORS: dict[str, Callable[..., dict]] = {
    "departments": validate_department,
    "jobs": validate_job,
    "hired_employees": validate_hired_employee,
}
