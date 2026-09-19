import pytest

from app.validators import (
    ValidationError,
    validate_department,
    validate_hired_employee,
    validate_job,
)

CTX = {"department_ids": {1, 2}, "job_ids": {10, 20}}


def employee(**overrides):
    row = {"id": "1", "name": "Ana", "datetime": "2021-07-27T16:02:08Z",
           "department_id": "2", "job_id": "10"}
    row.update(overrides)
    return row


def test_valid_employee_from_csv_strings():
    row = validate_hired_employee(employee(), **CTX)
    assert row["id"] == 1 and row["department_id"] == 2 and row["job_id"] == 10
    assert row["datetime"].isoformat() == "2021-07-27T16:02:08+00:00"


def test_valid_employee_from_json_types():
    row = validate_hired_employee(employee(id=5, department_id=1, job_id=20), **CTX)
    assert row["id"] == 5


@pytest.mark.parametrize("field", ["id", "name", "datetime", "department_id", "job_id"])
@pytest.mark.parametrize("empty", ["", "  ", None])
def test_every_field_is_required(field, empty):
    with pytest.raises(ValidationError, match="required"):
        validate_hired_employee(employee(**{field: empty}), **CTX)


def test_missing_key_is_required():
    record = employee()
    del record["job_id"]
    with pytest.raises(ValidationError, match="job_id is required"):
        validate_hired_employee(record, **CTX)


@pytest.mark.parametrize("value", [
    "2021-07-27", "27/07/2021", "2021-07-27 16:02:08", "2021-13-40T00:00:00Z",
    "2021-07-27T25:00:00Z", "not a date", "2021-07-27T16:02:08",
])
def test_datetime_must_be_iso(value):
    with pytest.raises(ValidationError, match="datetime"):
        validate_hired_employee(employee(datetime=value), **CTX)


def test_datetime_with_offset_is_normalised_to_utc():
    row = validate_hired_employee(employee(datetime="2021-07-27T18:02:08+02:00"), **CTX)
    assert row["datetime"].isoformat() == "2021-07-27T16:02:08+00:00"


def test_unknown_department_and_job_are_rejected():
    with pytest.raises(ValidationError, match="department_id 99"):
        validate_hired_employee(employee(department_id="99"), **CTX)
    with pytest.raises(ValidationError, match="job_id 99"):
        validate_hired_employee(employee(job_id="99"), **CTX)


@pytest.mark.parametrize("value", ["abc", "1.5", 1.5, True, "0", "-3"])
def test_ids_must_be_positive_integers(value):
    with pytest.raises(ValidationError):
        validate_hired_employee(employee(id=value), **CTX)


def test_department_and_job_rows():
    assert validate_department({"id": "3", "department": " Sales "}) == {"id": 3, "department": "Sales"}
    assert validate_job({"id": 4, "job": "Analyst"}) == {"id": 4, "job": "Analyst"}
    with pytest.raises(ValidationError):
        validate_department({"id": "3", "department": ""})
    with pytest.raises(ValidationError):
        validate_job({"id": "", "job": "x"})
