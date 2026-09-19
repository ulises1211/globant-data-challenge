variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Prefix for resource names."
  type        = string
  default     = "globant-poc"
}

variable "image_tag" {
  description = "Tag of the container image in ECR (the workflow passes the git commit SHA)."
  type        = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_name" {
  type    = string
  default = "globant"
}

variable "db_username" {
  type    = string
  default = "globant"
}

variable "log_retention_days" {
  type    = number
  default = 14
}
