"""
EN: AWS Lambda entry points. The same container image serves both functions; Terraform picks
    the handler with the image CMD (api_handler or loader_handler).
ES: Puntos de entrada de AWS Lambda. La misma imagen de contenedor sirve a ambas funciones;
    Terraform elige el handler con el CMD de la imagen (api_handler o loader_handler).
"""
import logging

from mangum import Mangum

from .loader import load_from_s3
from .main import app

logging.getLogger().setLevel(logging.INFO)

# EN: Adapter that turns API Gateway events into ASGI requests for FastAPI. The stage is named
#     "v1", which is also the base path stripped from incoming URLs.
# ES: Adaptador que convierte eventos de API Gateway en peticiones ASGI para FastAPI. El stage
#     se llama "v1", que también es la ruta base que se quita de las URLs entrantes.
api_handler = Mangum(app, lifespan="off", api_gateway_base_path="/v1")


def loader_handler(event, context):
    """
    EN: Lambda that runs the historical CSV migration. Invoke it with
        {"bucket": "...", "prefix": "optional/", "reset": true}. "reset" (default false)
        empties all tables first, so the load starts from scratch.
    ES: Lambda que ejecuta la migración histórica de los CSV. Se invoca con
        {"bucket": "...", "prefix": "opcional/", "reset": true}. "reset" (por defecto false)
        vacía todas las tablas antes, para que la carga empiece desde cero.
    Returns: {"summary": [...]} with received / inserted / rejected per table.
             {"summary": [...]} con recibidas / insertadas / rechazadas por tabla.
    """
    reset = bool(event.get("reset", False))
    return {"summary": load_from_s3(event["bucket"], event.get("prefix", ""), reset=reset)}
