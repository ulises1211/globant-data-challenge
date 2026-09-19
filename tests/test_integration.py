"""
EN: End-to-end tests against a real PostgreSQL (enabled by setting TEST_DATABASE_URL).

        docker compose up -d db
        TEST_DATABASE_URL=postgresql://globant:globant@localhost:54329/globant pytest

    They load the real CSV files and call the real API, so they prove that validation, the
    database, the API, the AVRO backup and the analytics work TOGETHER.
ES: Pruebas de extremo a extremo contra un PostgreSQL real (se activan definiendo
    TEST_DATABASE_URL).

        docker compose up -d db
        TEST_DATABASE_URL=postgresql://globant:globant@localhost:54329/globant pytest

    Cargan los CSV reales y llaman a la API real, así demuestran que la validación, la base de
    datos, la API, el backup AVRO y el análisis funcionan JUNTOS.
"""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.loader import load_directory
from app.main import app

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
# EN: Skip this whole file when no test database is configured. | ES: Omite todo el archivo si no hay base de pruebas.
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    EN: Give each test a clean world: point the app at the test database, send backups to a
        temporary folder, disable the API key, and empty every table. Returns an HTTP test
        client for the API.
    ES: Da a cada prueba un mundo limpio: apunta la app a la base de pruebas, envía los backups
        a una carpeta temporal, desactiva la API key y vacía todas las tablas. Devuelve un
        cliente HTTP de pruebas para la API.
    """
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("BACKUP_S3_BUCKET", raising=False)
    db.close_pool()
    db.init_schema()
    with db.get_connection() as conn:
        conn.execute("TRUNCATE hired_employees, jobs, departments, rejected_records")
    yield TestClient(app)
    db.close_pool()


def load_csvs():
    """
    EN: Run the historical migration on the real CSV files and return the summary by table.
    ES: Ejecuta la migración histórica con los CSV reales y devuelve el resumen por tabla.
    """
    return {row["table"]: row for row in load_directory(DATA_DIR)}


def test_historical_load_rejects_invalid_rows(client):
    """
    EN: Loads the real CSVs and checks the numbers add up: every received row is either
        inserted or rejected, every rejection is logged, and no stored employee points to a
        department that does not exist.
    ES: Carga los CSV reales y comprueba que las cuentas cuadran: toda fila recibida se
        inserta o se rechaza, todo rechazo queda registrado, y ningún empleado guardado apunta
        a un departamento inexistente.
    """
    summary = load_csvs()
    assert summary["departments"]["inserted"] == 12
    emp = summary["hired_employees"]
    assert emp["received"] == 1999
    assert emp["inserted"] + emp["rejected"] == 1999
    assert emp["rejected"] > 0

    with db.get_connection() as conn:
        stored = conn.execute("SELECT count(*) FROM hired_employees").fetchone()[0]
        logged = conn.execute(
            "SELECT count(*) FROM rejected_records WHERE table_name='hired_employees'"
        ).fetchone()[0]
        # EN: No row in the table may violate the rules. | ES: Ninguna fila de la tabla puede violar las reglas.
        orphans = conn.execute(
            "SELECT count(*) FROM hired_employees e LEFT JOIN departments d ON d.id=e.department_id "
            "WHERE d.id IS NULL"
        ).fetchone()[0]
    assert stored == emp["inserted"] and logged == emp["rejected"] and orphans == 0


def test_reset_reload_removes_test_rows(client):
    """
    EN: Loading with reset=True wipes rows added by hand (like a test employee) and the old
        rejection log, and leaves exactly what a first load leaves.
    ES: Cargar con reset=True borra las filas agregadas a mano (como un empleado de prueba) y
        el log de rechazos anterior, y deja exactamente lo que deja una primera carga.
    """
    first = load_csvs()["hired_employees"]
    test_row = {"id": 99999, "name": "Test", "datetime": "2021-02-03T04:05:06Z",
                "department_id": 1, "job_id": 1}
    assert client.post("/hired-employees", json=[test_row]).status_code == 201

    again = {r["table"]: r for r in load_directory(DATA_DIR, reset=True)}["hired_employees"]
    assert (again["inserted"], again["rejected"]) == (first["inserted"], first["rejected"])

    with db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM hired_employees WHERE id = 99999").fetchone()[0] == 0
        # EN: The rejection log restarts too. | ES: El log de rechazos también se reinicia.
        assert conn.execute("SELECT count(*) FROM rejected_records").fetchone()[0] == first["rejected"]


def test_api_batch_validation_and_limits(client):
    """
    EN: API contract: 201 when all rows are inserted, 207 when some are rejected, 422 when none
        is inserted or the id is repeated, 422 for an empty batch and for 1001 rows, and 201
        for exactly 1000 rows (the limit is inclusive).
    ES: Contrato de la API: 201 si se insertan todas las filas, 207 si algunas se rechazan, 422
        si no se inserta ninguna o el id se repite, 422 para un lote vacío y para 1001 filas,
        y 201 para exactamente 1000 filas (el límite es inclusivo).
    """
    load_csvs()
    ok = {"id": 90001, "name": "Test", "datetime": "2021-02-03T04:05:06Z", "department_id": 1, "job_id": 1}
    bad = {"id": 90002, "name": "Bad", "datetime": "yesterday", "department_id": 1, "job_id": 1}

    assert client.post("/hired-employees", json=[ok]).status_code == 201
    partial = client.post("/hired-employees", json=[{**ok, "id": 90003}, bad])
    assert partial.status_code == 207 and partial.json()["rejected"] == 1
    assert client.post("/hired-employees", json=[bad]).status_code == 422
    assert client.post("/hired-employees", json=[ok]).status_code == 422  # EN: duplicate id | ES: id duplicado

    assert client.post("/hired-employees", json=[]).status_code == 422
    too_many = [{**ok, "id": 100000 + i} for i in range(1001)]
    assert client.post("/hired-employees", json=too_many).status_code == 422
    exactly = [{**ok, "id": 100000 + i} for i in range(1000)]
    assert client.post("/hired-employees", json=exactly).status_code == 201


def test_analytics(client):
    """
    EN: The two analytics endpoints: the quarter report has the expected columns and is sorted
        by department then job, and the above-average list is sorted by hires (highest first)
        with every department really above the average.
    ES: Los dos endpoints de análisis: el reporte por trimestre tiene las columnas esperadas y
        está ordenado por departamento y luego puesto, y la lista sobre el promedio está
        ordenada por contrataciones (mayor primero) con cada departamento realmente sobre el
        promedio.
    """
    load_csvs()
    quarters = client.get("/analytics/hires-by-quarter").json()
    assert quarters == sorted(quarters, key=lambda r: (r["department"], r["job"]))
    assert set(quarters[0]) == {"department", "job", "Q1", "Q2", "Q3", "Q4"}

    above = client.get("/analytics/departments-above-average").json()
    hired = [r["hired"] for r in above]
    assert hired == sorted(hired, reverse=True)
    total_2021 = sum(sum(r[q] for q in ("Q1", "Q2", "Q3", "Q4")) for r in quarters)
    assert all(h > total_2021 / 12 for h in hired)


def test_backup_and_restore_roundtrip(client):
    """
    EN: Full AVRO cycle: back up all three tables, delete the employees, restore them and check
        the table is identical to before. Also checks that replacing a table that is still
        referenced fails cleanly (409), merge works, and an unknown table is a 404.
    ES: Ciclo AVRO completo: respalda las tres tablas, borra los empleados, los restaura y
        comprueba que la tabla queda idéntica a antes. También comprueba que reemplazar una
        tabla que aún está referenciada falla de forma limpia (409), que merge funciona y que
        una tabla desconocida da 404.
    """
    load_csvs()

    def snapshot(table):
        """
        EN: Return every row of `table` ordered by id, to compare the table before and after.
        ES: Devuelve todas las filas de `table` ordenadas por id, para comparar antes y después.
        """
        with db.get_connection() as conn:
            return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    before = {t: snapshot(t) for t in ("departments", "jobs", "hired_employees")}
    for table in before:
        assert client.post(f"/backup/{table}").json()["rows"] == len(before[table])

    with db.get_connection() as conn:
        conn.execute("DELETE FROM hired_employees")
    assert snapshot("hired_employees") == []

    restored = client.post("/restore/hired_employees", json={})
    assert restored.status_code == 200 and restored.json()["inserted"] == len(before["hired_employees"])
    assert snapshot("hired_employees") == before["hired_employees"]

    # EN: Replacing a parent table that is still referenced must fail cleanly.
    # ES: Reemplazar una tabla padre que aún está referenciada debe fallar de forma limpia.
    assert client.post("/restore/departments", json={}).status_code == 409
    assert client.post("/restore/departments", json={"mode": "merge"}).status_code == 200
    assert client.post("/backup/unknown").status_code == 404


def test_api_key_enforced_when_configured(client, monkeypatch):
    """
    EN: When API_KEY is set, a request without the key gets 401 and one with the right key gets 200.
    ES: Cuando API_KEY está definida, una petición sin la llave recibe 401 y una con la llave correcta recibe 200.
    """
    monkeypatch.setenv("API_KEY", "secret")
    assert client.get("/analytics/hires-by-quarter").status_code == 401
    assert client.get("/analytics/hires-by-quarter", headers={"x-api-key": "secret"}).status_code == 200
