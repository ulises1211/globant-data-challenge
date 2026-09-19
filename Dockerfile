# One image, three entry points (choose with the Lambda CMD / compose command):
#   app.lambda_handlers.api_handler     -> REST API behind API Gateway
#   app.lambda_handlers.loader_handler  -> historical CSV migration
FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements.txt

COPY app ${LAMBDA_TASK_ROOT}/app
COPY sql ${LAMBDA_TASK_ROOT}/sql

CMD ["app.lambda_handlers.api_handler"]
