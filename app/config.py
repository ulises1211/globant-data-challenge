"""
EN: Runtime configuration, read from environment variables. Nothing is hard-coded, so the
same code runs locally (docker compose) and on AWS Lambda (Terraform sets the variables).
ES: Configuración en tiempo de ejecución, leída de variables de entorno. Nada está fijo en
el código, así que el mismo código corre en local (docker compose) y en AWS Lambda
(Terraform define las variables).
"""
import json
import os
from functools import lru_cache
from urllib.parse import quote

import boto3

# EN: Maximum rows accepted per request/batch (challenge requirement: 1 to 1000).
# ES: Máximo de filas por request/lote (requisito del reto: de 1 a 1000).
MAX_BATCH_SIZE = 1000


class Settings:
    """
    EN: Read-only view of the environment. Properties are evaluated on every access (not at
        import time) so tests can change environment variables between runs.
    ES: Vista de solo lectura del entorno. Las propiedades se evalúan en cada acceso (no al
        importar) para que las pruebas puedan cambiar variables de entorno entre ejecuciones.
    """

    @property
    def database_url(self) -> str:
        """
        EN: PostgreSQL connection URL. Uses DATABASE_URL when present (local/tests); otherwise
            builds it from DB_HOST, DB_PORT, DB_NAME and the credentials (AWS).
        ES: URL de conexión a PostgreSQL. Usa DATABASE_URL si existe (local/pruebas); si no,
            la arma con DB_HOST, DB_PORT, DB_NAME y las credenciales (AWS).
        """
        url = os.getenv("DATABASE_URL")
        if url:
            return url
        host = os.environ["DB_HOST"]
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "globant")
        user, password = _db_credentials()
        # EN: Quote user/password so special characters cannot break the URL.
        # ES: Se escapan usuario y contraseña para que caracteres especiales no rompan la URL.
        return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{name}"

    @property
    def api_key(self) -> str | None:
        """
        EN: Optional application-level API key. On AWS, API Gateway enforces its own key, so
            this stays unset there.
        ES: API key opcional a nivel de aplicación. En AWS, API Gateway aplica su propia
            llave, por lo que aquí queda sin definir.
        """
        return os.getenv("API_KEY") or None

    @property
    def backup_dir(self) -> str:
        """
        EN: Local folder for AVRO backups (used when no S3 bucket is configured).
        ES: Carpeta local para los backups AVRO (se usa cuando no hay bucket S3 configurado).
        """
        return os.getenv("BACKUP_DIR", "backups")

    @property
    def backup_bucket(self) -> str | None:
        """
        EN: S3 bucket for AVRO backups. When set, backups go to S3 instead of the local disk.
        ES: Bucket S3 para los backups AVRO. Si está definido, los backups van a S3 en lugar
            del disco local.
        """
        return os.getenv("BACKUP_S3_BUCKET") or None


@lru_cache(maxsize=1)
def _db_credentials() -> tuple[str, str]:
    """
    EN: Return (user, password) for the database. On AWS it reads the secret that RDS
        generated and stored in Secrets Manager (DB_SECRET_ARN); locally it reads DB_USER and
        DB_PASSWORD. The result is cached so Secrets Manager is called once per container.
    ES: Devuelve (usuario, contraseña) de la base. En AWS lee el secreto que RDS generó y
        guardó en Secrets Manager (DB_SECRET_ARN); en local lee DB_USER y DB_PASSWORD. El
        resultado se guarda en caché para llamar a Secrets Manager una vez por contenedor.
    """
    secret_arn = os.getenv("DB_SECRET_ARN")
    if secret_arn:
        secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
        data = json.loads(secret["SecretString"])
        return data["username"], data["password"]
    return os.environ["DB_USER"], os.environ["DB_PASSWORD"]


def get_settings() -> Settings:
    """
    EN: Return a Settings object. A new instance per call is cheap and always reflects the
        current environment.
    ES: Devuelve un objeto Settings. Crear uno nuevo por llamada es barato y siempre refleja
        el entorno actual.
    """
    return Settings()
