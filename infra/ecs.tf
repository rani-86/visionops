# ---------------------------------------------------------------------------
# ECS Fargate worker
#
# Networking: tasks run in the default VPC's public subnets with a public IP and an
# egress-only security group (no inbound ports at all). This avoids a NAT gateway
# (~$35/month). Production alternative: private subnets + VPC endpoints for S3, SQS,
# ECR and CloudWatch Logs.
# ---------------------------------------------------------------------------

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "worker" {
  name        = "${local.name}-worker"
  description = "VisionOps worker: no inbound, HTTPS outbound to AWS APIs"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "HTTPS to AWS APIs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled" # enable for per-task metrics; it is billed per metric
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT" # ~70% cheaper; interruptions are safe because SQS redelivers
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name}-worker"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${aws_ecr_repository.worker.repository_url}:${var.image_tag}"
    essential = true

    environment = [
      { name = "VISIONOPS_BUCKET", value = aws_s3_bucket.docs.bucket },
      { name = "VISIONOPS_QUEUE_URL", value = aws_sqs_queue.jobs.url },
      { name = "MODEL_VERSION", value = var.model_version },
      { name = "MAX_RECEIVE_COUNT", value = tostring(var.max_receive_count) },
      { name = "SERVICE_NAME", value = "${local.name}-worker" },
      { name = "AWS_DEFAULT_REGION", value = local.region },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = local.region
        "awslogs-stream-prefix" = "worker"
      }
    }

    healthCheck = {
      command     = ["CMD", "python", "-m", "visionops.worker", "healthcheck"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 120
    }

    stopTimeout            = 120 # let the worker finish its current batch on scale-in / deploy
    readonlyRootFilesystem = false
    user                   = "10001"
  }])
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.min_workers

  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # A bad image or model that crashes on start is rolled back automatically.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    # CI registers new task definition revisions and autoscaling owns desired_count.
    ignore_changes = [task_definition, desired_count]
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main]
}

# ---------------------------------------------------------------------------
# Queue-driven autoscaling: backlog = visible + in-flight messages
# ---------------------------------------------------------------------------

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.min_workers
  max_capacity       = var.max_workers
}

resource "aws_appautoscaling_policy" "scale_out" {
  name               = "${local.name}-scale-out"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  policy_type        = "StepScaling"

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"

    # backlog (alarm threshold 1):  1-49 -> 1 task, 50-199 -> 2, 200-499 -> 3, 500+ -> max
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 49
      scaling_adjustment          = max(1, var.min_workers)
    }
    step_adjustment {
      metric_interval_lower_bound = 49
      metric_interval_upper_bound = 199
      scaling_adjustment          = min(2, var.max_workers)
    }
    step_adjustment {
      metric_interval_lower_bound = 199
      metric_interval_upper_bound = 499
      scaling_adjustment          = min(3, var.max_workers)
    }
    step_adjustment {
      metric_interval_lower_bound = 499
      scaling_adjustment          = var.max_workers
    }
  }
}

resource "aws_appautoscaling_policy" "scale_in" {
  name               = "${local.name}-scale-in"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  policy_type        = "StepScaling"

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = var.min_workers
    }
  }
}

locals {
  backlog_metric_queries = [
    {
      id          = "backlog"
      expression  = "FILL(visible, 0) + FILL(inflight, 0)"
      label       = "Backlog (visible + in flight)"
      return_data = true
      metric      = null
    },
    {
      id          = "visible"
      expression  = null
      label       = null
      return_data = false
      metric      = { name = "ApproximateNumberOfMessagesVisible" }
    },
    {
      id          = "inflight"
      expression  = null
      label       = null
      return_data = false
      metric      = { name = "ApproximateNumberOfMessagesNotVisible" }
    },
  ]
}

resource "aws_cloudwatch_metric_alarm" "backlog_high" {
  alarm_name          = "${local.name}-backlog-scale-out"
  alarm_description   = "Scale workers out when jobs are waiting"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.scale_out.arn]

  dynamic "metric_query" {
    for_each = local.backlog_metric_queries
    content {
      id          = metric_query.value.id
      expression  = metric_query.value.expression
      label       = metric_query.value.label
      return_data = metric_query.value.return_data
      dynamic "metric" {
        for_each = metric_query.value.metric == null ? [] : [metric_query.value.metric]
        content {
          namespace   = "AWS/SQS"
          metric_name = metric.value.name
          period      = 60
          stat        = "Maximum"
          dimensions  = { QueueName = aws_sqs_queue.jobs.name }
        }
      }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "backlog_empty" {
  alarm_name          = "${local.name}-backlog-scale-in"
  alarm_description   = "Scale workers in after the queue has been empty for 10 minutes"
  comparison_operator = "LessThanOrEqualToThreshold"
  threshold           = 0
  evaluation_periods  = 10
  treat_missing_data  = "breaching" # SQS stops publishing metrics for idle queues
  alarm_actions       = [aws_appautoscaling_policy.scale_in.arn]

  dynamic "metric_query" {
    for_each = local.backlog_metric_queries
    content {
      id          = metric_query.value.id
      expression  = metric_query.value.expression
      label       = metric_query.value.label
      return_data = metric_query.value.return_data
      dynamic "metric" {
        for_each = metric_query.value.metric == null ? [] : [metric_query.value.metric]
        content {
          namespace   = "AWS/SQS"
          metric_name = metric.value.name
          period      = 60
          stat        = "Maximum"
          dimensions  = { QueueName = aws_sqs_queue.jobs.name }
        }
      }
    }
  }
}
