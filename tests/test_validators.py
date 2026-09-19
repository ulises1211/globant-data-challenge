"""
EN: Unit tests for the validation rules. They need no database: the validators are pure
    functions, so these tests are fast and pinpoint exactly which rule broke.
ES: Pruebas unitarias de las reglas de validación. No necesitan base de datos: los
    validadores son funciones puras, por eso estas pruebas son rápidas e indican con
    precisión qué regla se rompió.
"""
import pytest

from app.validators import (
    ValidationError,
    validate_department,
    validate_hired_employee,
    validate_job,
)

# EN: Reference data: which departments and jobs "exist". | ES: Datos de referencia: qué departamentos y puestos "existen".
CTX = {"department_ids": {1, 2}, "job_ids": {10, 20}}


def employee(**overrides):
    """
    EN: Build a valid employee record (values as strings, like a CSV row) and let each test
        override only the field it wants to break.
    ES: Construye un registro de empleado válido (valores como cadenas, como una fila de CSV)
        y deja que cada prueba cambie solo el campo que quiere romper.
    """
    row = {"id": "1", "name": "Ana", "datetime": "2021-07-27T16:02:08Z",
           "department_id": "2", "job_id": "10"}
    row.update(overrides)
    return row


def test_valid_employee_from_csv_strings():
    """
    EN: A record whose values are strings (as read from a CSV) is accepted and converted to the
        right types: integer ids and a UTC datetime.
    ES: Un registro con valores de tipo cadena (como sale de un CSV) se acepta y se convierte a
        los tipos correctos: ids enteros y un datetime en UTC.
    """
    row = validate_hired_employee(employee(), **CTX)
    assert row["id"] == 1 and row["department_id"] == 2 and row["job_id"] == 10
    assert row["datetime"].isoformat() == "2021-07-27T16:02:08+00:00"


def test_valid_employee_from_json_types():
    """
    EN: A record with real JSON numbers (as sent to the API) is accepted too.
    ES: Un registro con números JSON reales (como los recibe la API) también se acepta.
    """
    row = validate_hired_employee(employee(id=5, department_id=1, job_id=20), **CTX)
    assert row["id"] == 5


@pytest.mark.parametrize("field", ["id", "name", "datetime", "department_id", "job_id"])
@pytest.mark.parametrize("empty", ["", "  ", None])
def test_every_field_is_required(field, empty):
    """
    EN: Rule "all fields are required": each of the 5 fields is rejected when it is an empty
        string, only spaces or null (5 fields x 3 kinds of empty = 15 cases).
    ES: Regla "todos los campos son obligatorios": cada uno de los 5 campos se rechaza cuando
        es una cadena vacía, solo espacios o nulo (5 campos x 3 tipos de vacío = 15 casos).
    """
    with pytest.raises(ValidationError, match="required"):
        validate_hired_employee(employee(**{field: empty}), **CTX)


def test_missing_key_is_required():
    """
    EN: A record that does not even contain the key (not just an empty value) is rejected.
    ES: Un registro que ni siquiera contiene la clave (no solo un valor vacío) se rechaza.
    """
    record = employee()
    del record["job_id"]
    with pytest.raises(ValidationError, match="job_id is required"):
        validate_hired_employee(record, **CTX)


@pytest.mark.parametrize("value", [
    "2021-07-27", "27/07/2021", "2021-07-27 16:02:08", "2021-13-40T00:00:00Z",
    "2021-07-27T25:00:00Z", "not a date", "2021-07-27T16:02:08",
])
def test_datetime_must_be_iso(value):
    """
    EN: Rule "datetime must be ISO 8601": date only, other formats, a space instead of T, an
        impossible month or hour, plain text and a missing time zone are all rejected.
    ES: Regla "datetime debe ser ISO 8601": se rechazan solo fecha, otros formatos, espacio en
        vez de T, mes u hora imposibles, texto cualquiera y falta de zona horaria.
    """
    with pytest.raises(ValidationError, match="datetime"):
        validate_hired_employee(employee(datetime=value), **CTX)


def test_datetime_with_offset_is_normalised_to_utc():
    """
    EN: A valid date with an offset (+02:00) is accepted and stored as the equivalent UTC time.
    ES: Una fecha válida con desfase (+02:00) se acepta y se guarda como la hora UTC equivalente.
    """
    row = validate_hired_employee(employee(datetime="2021-07-27T18:02:08+02:00"), **CTX)
    assert row["datetime"].isoformat() == "2021-07-27T16:02:08+00:00"


def test_unknown_department_and_job_are_rejected():
    """
    EN: Rules "department_id must exist" and "job_id must exist": ids missing from the
        reference sets are rejected with a message naming the offending value.
    ES: Reglas "department_id debe existir" y "job_id debe existir": los ids que no están en
        los conjuntos de referencia se rechazan con un mensaje que nombra el valor.
    """
    with pytest.raises(ValidationError, match="department_id 99"):
        validate_hired_employee(employee(department_id="99"), **CTX)
    with pytest.raises(ValidationError, match="job_id 99"):
        validate_hired_employee(employee(job_id="99"), **CTX)


@pytest.mark.parametrize("value", ["abc", "1.5", 1.5, True, "0", "-3"])
def test_ids_must_be_positive_integers(value):
    """
    EN: Ids must be positive integers: text, decimals, booleans, zero and negatives are rejected.
    ES: Los ids deben ser enteros positivos: se rechazan texto, decimales, booleanos, cero y negativos.
    """
    with pytest.raises(ValidationError):
        validate_hired_employee(employee(id=value), **CTX)


def test_department_and_job_rows():
    """
    EN: The simple tables: valid rows are cleaned (spaces trimmed) and empty values are rejected.
    ES: Las tablas simples: las filas válidas se limpian (se recortan espacios) y los vacíos se rechazan.
    """
    assert validate_department({"id": "3", "department": " Sales "}) == {"id": 3, "department": "Sales"}
    assert validate_job({"id": 4, "job": "Analyst"}) == {"id": 4, "job": "Analyst"}
    with pytest.raises(ValidationError):
        validate_department({"id": "3", "department": ""})
    with pytest.raises(ValidationError):
        validate_job({"id": "", "job": "x"})
