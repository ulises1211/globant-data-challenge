# Both functions run the same container image with a different handler.

locals {
  image_uri = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  db_env = {
    DB_HOST       = aws_db_instance.main.address
    DB_PORT       = tostring(aws_db_instance.main.port)
    DB_NAME       = var.db_name
    DB_SECRET_ARN = local.db_secret_arn
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.project}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "loader" {
  name              = "/aws/lambda/${var.project}-loader"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "api" {
  function_name = "${var.project}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["x86_64"]
  memory_size   = 512
  timeout       = 28 # API Gateway gives up at 29 s

  image_config {
    command = ["app.lambda_handlers.api_handler"]
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = merge(local.db_env, {
      BACKUP_S3_BUCKET = aws_s3_bucket.this["backups"].id
    })
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy_attachment.api_vpc]
}

resource "aws_lambda_function" "loader" {
  function_name = "${var.project}-loader"
  role          = aws_iam_role.loader.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["x86_64"]
  memory_size   = 512
  timeout       = 300

  image_config {
    command = ["app.lambda_handlers.loader_handler"]
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = local.db_env
  }

  depends_on = [aws_cloudwatch_log_group.loader, aws_iam_role_policy_attachment.loader_vpc]
}
