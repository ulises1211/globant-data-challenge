locals {
  bucket_suffix = data.aws_caller_identity.current.account_id
  buckets = {
    raw     = "${var.project}-raw-${local.bucket_suffix}"
    backups = "${var.project}-backups-${local.bucket_suffix}"
  }
  csv_files = ["departments.csv", "jobs.csv", "hired_employees.csv"]
}

resource "aws_s3_bucket" "this" {
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = true # PoC: `terraform destroy` must also empty the buckets
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.this["backups"].id

  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "tls_only" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  policy   = data.aws_iam_policy_document.tls_only[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.this]
}

# The historical CSV files travel with the repository and land in the raw bucket.
resource "aws_s3_object" "csv" {
  for_each = toset(local.csv_files)
  bucket   = aws_s3_bucket.this["raw"].id
  key      = each.value
  source   = "${path.module}/../data/${each.value}"
  etag     = filemd5("${path.module}/../data/${each.value}")
}
