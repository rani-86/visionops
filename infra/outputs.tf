output "bucket" {
  value = aws_s3_bucket.docs.bucket
}

output "queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service" {
  value = aws_ecs_service.worker.name
}

output "task_family" {
  value = aws_ecs_task_definition.worker.family
}

output "log_group" {
  value = aws_cloudwatch_log_group.worker.name
}

output "dashboard_url" {
  value = "https://${local.region}.console.aws.amazon.com/cloudwatch/home?region=${local.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "github_deploy_role_arn" {
  value = try(aws_iam_role.github_deploy[0].arn, null)
}

output "github_actions_variables" {
  description = "Paste these into GitHub -> Settings -> Secrets and variables -> Actions -> Variables"
  value = {
    AWS_REGION     = local.region
    AWS_ROLE_ARN   = try(aws_iam_role.github_deploy[0].arn, "(set github_repo to create)")
    ECR_REPOSITORY = aws_ecr_repository.worker.name
    ECS_CLUSTER    = aws_ecs_cluster.main.name
    ECS_SERVICE    = aws_ecs_service.worker.name
    TASK_FAMILY    = aws_ecs_task_definition.worker.family
    MODEL_BUCKET   = aws_s3_bucket.docs.bucket
    MODEL_VERSION  = var.model_version
  }
}
