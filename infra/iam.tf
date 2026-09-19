# One role per Lambda, each limited to what that function needs.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

locals {
  db_secret_arn = aws_db_instance.main.master_user_secret[0].secret_arn
}

# --- API: reads the DB secret, reads and writes AVRO backups ----------------

resource "aws_iam_role" "api" {
  name               = "${var.project}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "api_vpc" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "api" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.db_secret_arn]
  }

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.this["backups"].arn]
  }

  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.this["backups"].arn}/*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "access"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# --- Loader: reads the DB secret and the raw CSV files ----------------------

resource "aws_iam_role" "loader" {
  name               = "${var.project}-loader"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "loader_vpc" {
  role       = aws_iam_role.loader.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "loader" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.db_secret_arn]
  }

  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.this["raw"].arn}/*"]
  }
}

resource "aws_iam_role_policy" "loader" {
  name   = "access"
  role   = aws_iam_role.loader.id
  policy = data.aws_iam_policy_document.loader.json
}
