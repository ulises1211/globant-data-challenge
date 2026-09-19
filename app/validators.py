"""Row-level validation rules from the data dictionary.

Pure functions (no DB access): reference data is passed in as sets so the
same rules serve the CSV loader, the REST API and the AVRO restore.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

# ISO 8601, e.g. 2021-07-27T16:02:08Z (optional fraction / numeric offset).
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"
)


class ValidationError(ValueError):
    """A single record failed validation; the message is the rejection reason."""


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    text_columns: tuple[str, ...]


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
    value = record.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{field} is required")
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip())
    raise ValidationError(f"{field} must be an integer, got {value!r}")


def parse_text(record: dict, field: str) -> str:
    value = record.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{field} is required")
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string, got {value!r}")
    return value.strip()


def parse_iso_datetime(record: dict, field: str) -> datetime:
    """Return a timezone-aware UTC datetime or raise ValidationError."""
    value = parse_text(record, field)
    if not _ISO_RE.match(value):
        raise ValidationError(f"{field} is not ISO 8601 (e.g. 2021-07-27T16:02:08Z): {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{field} is not a valid date/time: {value!r}") from None
    return parsed.astimezone(timezone.utc)


def validate_department(record: dict, **_: Any) -> dict:
    return {"id": _positive_id(record, "id"), "department": parse_text(record, "department")}


def validate_job(record: dict, **_: Any) -> dict:
    return {"id": _positive_id(record, "id"), "job": parse_text(record, "job")}


def validate_hired_employee(
    record: dict, *, department_ids: set[int], job_ids: set[int]
) -> dict:
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


def _positive_id(record: dict, field: str) -> int:
    value = parse_int(record, field)
    if value <= 0:
        raise ValidationError(f"{field} must be a positive integer")
    return value


VALIDATORS: dict[str, Callable[..., dict]] = {
    "departments": validate_department,
    "jobs": validate_job,
    "hired_employees": validate_hired_employee,
}
