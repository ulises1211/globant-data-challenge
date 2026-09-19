"""Backup storage: local filesystem by default, S3 when BACKUP_S3_BUCKET is set."""
from pathlib import Path

from .config import get_settings


def save(key: str, data: bytes) -> str:
    """Store `data` under `key`; return a human-readable location."""
    bucket = get_settings().backup_bucket
    if bucket:
        import boto3

        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=data)
        return f"s3://{bucket}/{key}"
    path = Path(get_settings().backup_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def load(key: str) -> bytes:
    bucket = get_settings().backup_bucket
    if bucket:
        import boto3

        return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return _local_path(key).read_bytes()


def latest_key(prefix: str) -> str | None:
    """Most recent backup under `prefix` (timestamps in names sort lexically)."""
    bucket = get_settings().backup_bucket
    if bucket:
        import boto3

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
    base = Path(get_settings().backup_dir).resolve()
    path = (base / key).resolve()
    if base not in path.parents:  # block path traversal
        raise ValueError("invalid backup key")
    return path
