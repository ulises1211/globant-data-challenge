"""
EN: Historical data migration: CSV files (no header row) -> PostgreSQL. Locally it reads a
    folder (python -m app.loader --dir data); on AWS the same code runs in a Lambda that reads
    the CSV files from S3 (see lambda_handlers.py).
ES: Migración de datos históricos: archivos CSV (sin fila de encabezado) -> PostgreSQL. En
    local lee una carpeta (python -m app.loader --dir data); en AWS el mismo código corre en
    una Lambda que lee los CSV desde S3 (ver lambda_handlers.py).
"""
import argparse
import csv
import io
import logging
from pathlib import Path
from typing import Iterable, Iterator

import boto3

from .config import MAX_BATCH_SIZE
from .db import close_pool, get_connection, init_schema
from .ingestion import IngestResult, ingest
from .validators import TABLES

logger = logging.getLogger("loader")

# EN: Parents first so hired_employees can check its foreign keys against loaded data.
# ES: Primero las tablas padre, para que hired_employees valide sus llaves foráneas.
LOAD_ORDER = ("departments", "jobs", "hired_employees")


def csv_records(lines: Iterable[str], table: str) -> Iterator[dict]:
    """
    EN: Turn header-less CSV lines into dictionaries, naming the columns in the order given by
        the data dictionary. A row with the wrong number of columns is yielded as
        {"raw_row": [...]} so the ingestion step rejects and logs it instead of silently
        dropping it. Blank lines are skipped.
    ES: Convierte líneas de CSV sin encabezado en diccionarios, nombrando las columnas en el
        orden del diccionario de datos. Una fila con un número de columnas incorrecto se
        entrega como {"raw_row": [...]} para que la ingesta la rechace y la registre en lugar
        de descartarla en silencio. Las líneas en blanco se omiten.
    """
    columns = TABLES[table].columns
    for row in csv.reader(lines):
        if not row:
            continue
        yield dict(zip(columns, row)) if len(row) == len(columns) else {"raw_row": row}


def load_table(table: str, lines: Iterable[str]) -> IngestResult:
    """
    EN: Load one table from CSV lines, sending the rows to the ingestion step in batches of
        MAX_BATCH_SIZE (1000). Returns the totals for the whole table.
    ES: Carga una tabla desde líneas de CSV, enviando las filas a la ingesta en lotes de
        MAX_BATCH_SIZE (1000). Devuelve los totales de toda la tabla.
    """
    total = IngestResult(table=table)
    batch: list[dict] = []
    for record in csv_records(lines, table):
        batch.append(record)
        if len(batch) == MAX_BATCH_SIZE:
            total.add(ingest(table, batch, "csv"))
            batch = []
    if batch:  # EN: last, smaller batch | ES: último lote, más pequeño
        total.add(ingest(table, batch, "csv"))
    return total


def reset_tables() -> None:
    """
    EN: Empty every table, including the rejected-record log, before a fresh load. Destructive:
        it is only called when the caller explicitly asks for a reset.
    ES: Vacía todas las tablas, incluido el log de rechazos, antes de una carga nueva.
        Destructivo: solo se llama cuando quien invoca pide un reset de forma explícita.
    """
    with get_connection() as conn:
        conn.execute(
            "TRUNCATE hired_employees, jobs, departments, rejected_records RESTART IDENTITY"
        )
    logger.warning("RESET all tables were emptied before loading")


def load_directory(directory: Path, reset: bool = False) -> list[dict]:
    """
    EN: Load the three CSV files from a local folder, in dependency order. Creates the schema
        first and, when `reset` is true, empties the tables.
    ES: Carga los tres CSV de una carpeta local, en orden de dependencia. Crea primero el
        esquema y, si `reset` es verdadero, vacía las tablas.
    Returns: one summary dict per table (received / inserted / rejected).
             un diccionario resumen por tabla (recibidas / insertadas / rechazadas).
    """
    init_schema()
    if reset:
        reset_tables()
    summary = []
    for table in LOAD_ORDER:
        # EN: utf-8-sig removes the byte-order mark some tools add at the start of a file.
        # ES: utf-8-sig quita la marca BOM que algunas herramientas agregan al inicio.
        with open(directory / f"{table}.csv", encoding="utf-8-sig", newline="") as handle:
            result = load_table(table, handle)
        summary.append({k: v for k, v in result.as_dict().items() if k != "errors"})
    return summary


def load_from_s3(bucket: str, prefix: str = "", reset: bool = False) -> list[dict]:
    """
    EN: Same as load_directory, but reads "<prefix><table>.csv" objects from an S3 bucket.
        This is what the loader Lambda runs on AWS.
    ES: Igual que load_directory, pero lee los objetos "<prefix><tabla>.csv" de un bucket S3.
        Es lo que ejecuta la Lambda loader en AWS.
    Returns: one summary dict per table. | un diccionario resumen por tabla.
    """
    s3 = boto3.client("s3")
    init_schema()
    if reset:
        reset_tables()
    summary = []
    for table in LOAD_ORDER:
        body = s3.get_object(Bucket=bucket, Key=f"{prefix}{table}.csv")["Body"].read()
        lines = io.StringIO(body.decode("utf-8-sig"), newline="")
        result = load_table(table, lines)
        summary.append({k: v for k, v in result.as_dict().items() if k != "errors"})
    return summary


def main() -> None:
    """
    EN: Command-line entry point: `python -m app.loader --dir data [--reset]`. Prints one
        summary line per table and always closes the connection pool.
    ES: Punto de entrada por línea de comandos: `python -m app.loader --dir data [--reset]`.
        Imprime una línea de resumen por tabla y siempre cierra el pool de conexiones.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Load the historical CSV files")
    parser.add_argument("--dir", default="data", type=Path, help="folder with the CSV files")
    parser.add_argument("--reset", action="store_true", help="empty all tables before loading")
    args = parser.parse_args()
    try:
        for row in load_directory(args.dir, reset=args.reset):
            print(row)
    finally:
        close_pool()


if __name__ == "__main__":
    main()
