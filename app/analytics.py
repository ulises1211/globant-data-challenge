"""
EN: Analytics queries for Challenge #2. The SQL itself lives in sql/*.sql so it can be read
    and reviewed as plain SQL; this module only runs it.
ES: Consultas de análisis del Reto #2. El SQL vive en sql/*.sql para poder leerlo y revisarlo
    como SQL puro; este módulo solo lo ejecuta.
"""
from pathlib import Path

from psycopg.rows import dict_row

from .db import get_connection

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> list[dict]:
    """
    EN: Read a query from sql/<filename> and return its rows as a list of dictionaries
        (column name -> value), ready to be serialised as JSON.
    ES: Lee una consulta de sql/<filename> y devuelve sus filas como lista de diccionarios
        (nombre de columna -> valor), lista para serializarse como JSON.
    """
    query = (_SQL_DIR / filename).read_text(encoding="utf-8")
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query)
            return cur.fetchall()


def hires_by_quarter() -> list[dict]:
    """
    EN: Employees hired in 2021 per department and job, split by quarter (Q1 to Q4), ordered
        alphabetically by department and job. Only valid rows exist in the table, so only
        valid rows are counted. Columns: department, job, Q1, Q2, Q3, Q4.
    ES: Empleados contratados en 2021 por departamento y puesto, separados por trimestre (Q1
        a Q4), ordenados alfabéticamente por departamento y puesto. En la tabla solo hay
        filas válidas, por lo que solo se cuentan filas válidas. Columnas: department, job,
        Q1, Q2, Q3, Q4.
    """
    return _run("hires_by_quarter.sql")


def departments_above_average() -> list[dict]:
    """
    EN: Departments whose 2021 hires exceed the average across ALL departments (departments
        with no hires count as 0). Ordered by hires, highest first. Columns: id, department,
        hired.
    ES: Departamentos cuyas contrataciones de 2021 superan el promedio de TODOS los
        departamentos (los que no contrataron cuentan como 0). Ordenados de mayor a menor.
        Columnas: id, department, hired.
    """
    return _run("departments_above_average.sql")
