output "api_url" {
  description = "Base URL of the REST API (send the x-api-key header)."
  value       = aws_api_gateway_stage.v1.invoke_url
}

output "api_key_id" {
  description = "ID of the API key. Reveal its value in the console: API Gateway > API keys."
  value       = aws_api_gateway_api_key.client.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "loader_function_name" {
  value = aws_lambda_function.loader.function_name
}

output "raw_bucket" {
  value = aws_s3_bucket.this["raw"].id
}

output "backups_bucket" {
  value = aws_s3_bucket.this["backups"].id
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}
