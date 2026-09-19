"""End-to-end tests against a real PostgreSQL (set TEST_DATABASE_URL to enable).

    docker compose up -d db
    TEST_DATABASE_URL=postgresql://globant:globant@localhost:5432/globant pytest
"""
import os
from pathlib import Path

import pytest

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import db
    from app.main import app

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
    from app.loader import load_directory

    return {row["table"]: row for row in load_directory(DATA_DIR)}


def test_historical_load_rejects_invalid_rows(client):
    summary = load_csvs()
    assert summary["departments"]["inserted"] == 12
    emp = summary["hired_employees"]
    assert emp["received"] == 1999
    assert emp["inserted"] + emp["rejected"] == 1999
    assert emp["rejected"] > 0

    from app.db import get_connection

    with get_connection() as conn:
        stored = conn.execute("SELECT count(*) FROM hired_employees").fetchone()[0]
        logged = conn.execute(
            "SELECT count(*) FROM rejected_records WHERE table_name='hired_employees'"
        ).fetchone()[0]
        # No row in the table may violate the rules.
        orphans = conn.execute(
            "SELECT count(*) FROM hired_employees e LEFT JOIN departments d ON d.id=e.department_id "
            "WHERE d.id IS NULL"
        ).fetchone()[0]
    assert stored == emp["inserted"] and logged == emp["rejected"] and orphans == 0


def test_reset_reload_removes_test_rows(client):
    from app.db import get_connection
    from app.loader import load_directory

    first = load_csvs()["hired_employees"]
    test_row = {"id": 99999, "name": "Test", "datetime": "2021-02-03T04:05:06Z",
                "department_id": 1, "job_id": 1}
    assert client.post("/hired-employees", json=[test_row]).status_code == 201

    again = {r["table"]: r for r in load_directory(DATA_DIR, reset=True)}["hired_employees"]
    assert (again["inserted"], again["rejected"]) == (first["inserted"], first["rejected"])

    with get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM hired_employees WHERE id = 99999").fetchone()[0] == 0
        # The rejection log restarts too: only the rejects of this load remain.
        assert conn.execute("SELECT count(*) FROM rejected_records").fetchone()[0] == first["rejected"]


def test_api_batch_validation_and_limits(client):
    load_csvs()
    ok = {"id": 90001, "name": "Test", "datetime": "2021-02-03T04:05:06Z", "department_id": 1, "job_id": 1}
    bad = {"id": 90002, "name": "Bad", "datetime": "yesterday", "department_id": 1, "job_id": 1}

    assert client.post("/hired-employees", json=[ok]).status_code == 201
    partial = client.post("/hired-employees", json=[{**ok, "id": 90003}, bad])
    assert partial.status_code == 207 and partial.json()["rejected"] == 1
    assert client.post("/hired-employees", json=[bad]).status_code == 422
    assert client.post("/hired-employees", json=[ok]).status_code == 422  # duplicate id

    assert client.post("/hired-employees", json=[]).status_code == 422
    too_many = [{**ok, "id": 100000 + i} for i in range(1001)]
    assert client.post("/hired-employees", json=too_many).status_code == 422
    exactly = [{**ok, "id": 100000 + i} for i in range(1000)]
    assert client.post("/hired-employees", json=exactly).status_code == 201


def test_analytics(client):
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
    load_csvs()
    from app.db import get_connection

    def snapshot(table):
        with get_connection() as conn:
            return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    before = {t: snapshot(t) for t in ("departments", "jobs", "hired_employees")}
    for table in before:
        assert client.post(f"/backup/{table}").json()["rows"] == len(before[table])

    with get_connection() as conn:
        conn.execute("DELETE FROM hired_employees")
    assert snapshot("hired_employees") == []

    restored = client.post("/restore/hired_employees", json={})
    assert restored.status_code == 200 and restored.json()["inserted"] == len(before["hired_employees"])
    assert snapshot("hired_employees") == before["hired_employees"]

    # Replacing a parent table that is still referenced must fail cleanly.
    assert client.post("/restore/departments", json={}).status_code == 409
    assert client.post("/restore/departments", json={"mode": "merge"}).status_code == 200
    assert client.post("/backup/unknown").status_code == 404


def test_api_key_enforced_when_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    assert client.get("/analytics/hires-by-quarter").status_code == 401
    assert client.get("/analytics/hires-by-quarter", headers={"x-api-key": "secret"}).status_code == 200
