"""Runtime configuration, read from environment variables."""
import json
import os
from functools import lru_cache
from urllib.parse import quote

MAX_BATCH_SIZE = 1000


class Settings:
    """Environment-driven settings (evaluated lazily so tests can override env)."""

    @property
    def database_url(self) -> str:
        url = os.getenv("DATABASE_URL")
        if url:
            return url
        host = os.environ["DB_HOST"]
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "globant")
        user, password = _db_credentials()
        return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{name}"

    @property
    def api_key(self) -> str | None:
        """Optional app-level API key (in AWS API Gateway enforces its own key)."""
        return os.getenv("API_KEY") or None

    @property
    def backup_dir(self) -> str:
        return os.getenv("BACKUP_DIR", "backups")

    @property
    def backup_bucket(self) -> str | None:
        return os.getenv("BACKUP_S3_BUCKET") or None


@lru_cache(maxsize=1)
def _db_credentials() -> tuple[str, str]:
    """User/password from Secrets Manager (AWS) or plain env vars (local)."""
    secret_arn = os.getenv("DB_SECRET_ARN")
    if secret_arn:
        import boto3

        secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
        data = json.loads(secret["SecretString"])
        return data["username"], data["password"]
    return os.environ["DB_USER"], os.environ["DB_PASSWORD"]


def get_settings() -> Settings:
    return Settings()
