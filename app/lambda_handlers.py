"""AWS Lambda entry points (same container image, different CMD)."""
import logging

from mangum import Mangum

from .loader import load_from_s3
from .main import app

logging.getLogger().setLevel(logging.INFO)

# API Gateway (REST API, stage path is stripped by the "/{stage}" base path).
api_handler = Mangum(app, lifespan="off", api_gateway_base_path="/v1")


def loader_handler(event, context):
    """Invoke with {"bucket": "...", "prefix": "optional/"} to run the CSV migration."""
    return {"summary": load_from_s3(event["bucket"], event.get("prefix", ""))}
