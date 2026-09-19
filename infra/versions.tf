terraform {
  required_version = ">= 1.10"

  # Bucket, key and region are passed at `terraform init -backend-config=...`
  # (see .github/workflows). State locking uses a lock file in S3.
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.80, < 7.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
