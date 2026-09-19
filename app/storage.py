"""
EN: Backup storage. Uses the local filesystem by default and S3 when BACKUP_S3_BUCKET is set,
    so the backup code does not care where the files live.
ES: Almacenamiento de backups. Usa el sistema de archivos local por defecto y S3 cuando
    BACKUP_S3_BUCKET está definido, así el código de backup no se preocupa de dónde viven los
    archivos.
"""
from pathlib import Path

import boto3

from .config import get_settings


def save(key: str, data: bytes) -> str:
    """
    EN: Store `data` under `key` (a path-like name such as "jobs/jobs_2026....avro") in S3 or
        on the local disk, and return a readable location.
    ES: Guarda `data` bajo `key` (un nombre tipo ruta como "jobs/jobs_2026....avro") en S3 o
        en el disco local, y devuelve una ubicación legible.
    Returns: "s3://bucket/key" or the local file path. | "s3://bucket/key" o la ruta local.
    """
    bucket = get_settings().backup_bucket
    if bucket:
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=data)
        return f"s3://{bucket}/{key}"
    path = Path(get_settings().backup_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def load(key: str) -> bytes:
    """
    EN: Read the bytes stored under `key` from S3 or from the local disk.
    ES: Lee los bytes guardados bajo `key` desde S3 o desde el disco local.
    Raises: FileNotFoundError / botocore errors if it does not exist. | si no existe.
    """
    bucket = get_settings().backup_bucket
    if bucket:
        return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return _local_path(key).read_bytes()


def latest_key(prefix: str) -> str | None:
    """
    EN: Return the most recent backup key under `prefix`, or None when there is none. File
        names contain a UTC timestamp, so the greatest name is the newest.
    ES: Devuelve la llave del backup más reciente bajo `prefix`, o None si no hay ninguno. Los
        nombres llevan una marca de tiempo UTC, así que el nombre mayor es el más nuevo.
    """
    bucket = get_settings().backup_bucket
    if bucket:
        paginator = boto3.client("s3").get_paginator("list_objects_v2")
        keys = [
            obj["Key"]
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
            for obj in page.get("Contents", [])
        ]
    else:
        base = Path(get_settings().backup_dir)
        keys = [p.relative_to(base).as_posix() for p in (base / prefix).rglob("*.avro")]
    return max(keys) if keys else None


def _local_path(key: str) -> Path:
    """
    EN: Resolve a key to a file inside the backup folder and refuse anything that escapes it
        (path traversal such as "../../secret").
    ES: Convierte una llave en un archivo dentro de la carpeta de backups y rechaza cualquier
        cosa que se salga de ella (path traversal como "../../secreto").
    Raises: ValueError.
    """
    base = Path(get_settings().backup_dir).resolve()
    path = (base / key).resolve()
    if base not in path.parents:  # EN: block path traversal | ES: bloquear path traversal
        raise ValueError("invalid backup key")
    return path
