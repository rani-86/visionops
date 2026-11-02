# ---------------------------------------------------------------------------
# CloudWatch: alerts, dashboard, saved Logs Insights queries
# Custom metrics (namespace VisionOps) come from EMF log lines the worker writes.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  svc_dim      = { Service = "${local.name}-worker" }
  alarm_target = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.name}-dlq-not-empty"
  alarm_description   = "Jobs exhausted their retries and are in the DLQ. See docs/RUNBOOK.md#dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_target
  ok_actions          = local.alarm_target
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  alarm_name          = "${local.name}-queue-backlog-stale"
  alarm_description   = "Oldest job has waited too long; workers are down or cannot keep up. See docs/RUNBOOK.md#backlog"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.jobs.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = var.queue_age_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_target
  ok_actions          = local.alarm_target
}

resource "aws_cloudwatch_metric_alarm" "failed_jobs" {
  alarm_name          = "${local.name}-failed-jobs"
  alarm_description   = "More than 5% of jobs failed in 5 minutes. See docs/RUNBOOK.md#failed-jobs"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_target
  ok_actions          = local.alarm_target

  metric_query {
    id          = "failure_rate"
    expression  = "IF((ok + failed + errors) >= 10, 100 * (failed + errors) / (ok + failed + errors), 0)"
    label       = "Failure rate (%)"
    return_data = true
  }
  metric_query {
    id = "ok"
    metric {
      namespace   = "VisionOps"
      metric_name = "JobsSucceeded"
      dimensions  = local.svc_dim
      period      = 300
      stat        = "Sum"
    }
  }
  metric_query {
    id = "failed"
    metric {
      namespace   = "VisionOps"
      metric_name = "JobsFailed"
      dimensions  = local.svc_dim
      period      = 300
      stat        = "Sum"
    }
  }
  metric_query {
    id = "errors"
    metric {
      namespace   = "VisionOps"
      metric_name = "JobErrors"
      dimensions  = local.svc_dim
      period      = 300
      stat        = "Sum"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "latency_p90" {
  alarm_name          = "${local.name}-processing-latency-p90"
  alarm_description   = "p90 processing latency above threshold for 3 of 5 minutes"
  namespace           = "VisionOps"
  metric_name         = "ProcessingLatency"
  dimensions          = local.svc_dim
  extended_statistic  = "p90"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  threshold           = var.latency_p90_threshold_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_target
  ok_actions          = local.alarm_target
}

resource "aws_cloudwatch_metric_alarm" "worker_crash_loop" {
  alarm_name          = "${local.name}-worker-errors-in-logs"
  alarm_description   = "Worker logged unhandled poll errors (credentials, network, permissions)"
  namespace           = "VisionOps"
  metric_name         = "WorkerPollErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_target
}

resource "aws_cloudwatch_log_metric_filter" "poll_errors" {
  name           = "${local.name}-poll-errors"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "{ $.msg = \"poll failed\" }"

  metric_transformation {
    name      = "WorkerPollErrors"
    namespace = "VisionOps"
    value     = "1"
  }
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name}-pipeline"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "text", x = 0, y = 0, width = 24, height = 2
        properties = { markdown = "## VisionOps document pipeline\nS3 `incoming/` -> SQS `${aws_sqs_queue.jobs.name}` -> ECS `${aws_ecs_service.worker.name}` -> S3 `results/`. Runbook: `docs/RUNBOOK.md`" }
      },
      {
        type = "metric", x = 0, y = 2, width = 8, height = 6
        properties = {
          title = "Jobs per minute", region = local.region, stat = "Sum", period = 60, view = "timeSeries", stacked = true
          metrics = [
            ["VisionOps", "JobsSucceeded", "Service", local.svc_dim.Service, { label = "Succeeded", color = "#2ca02c" }],
            [".", "JobsNeedsReview", ".", ".", { label = "Needs review", color = "#ff7f0e" }],
            [".", "JobsFailed", ".", ".", { label = "Failed (permanent)", color = "#d62728" }],
            [".", "JobErrors", ".", ".", { label = "Errors (retried)", color = "#9467bd" }],
          ]
        }
      },
      {
        type = "metric", x = 8, y = 2, width = 8, height = 6
        properties = {
          title = "Processing latency (ms)", region = local.region, period = 60, view = "timeSeries"
          metrics = [
            ["VisionOps", "ProcessingLatency", "Service", local.svc_dim.Service, { stat = "p50", label = "p50" }],
            ["...", { stat = "p90", label = "p90" }],
            ["...", { stat = "p99", label = "p99" }],
          ]
          annotations = { horizontal = [{ label = "p90 alarm", value = var.latency_p90_threshold_ms }] }
        }
      },
      {
        type = "metric", x = 16, y = 2, width = 8, height = 6
        properties = {
          title = "End-to-end latency: upload -> result (ms)", region = local.region, period = 60, view = "timeSeries"
          metrics = [
            ["VisionOps", "EndToEndLatency", "Service", local.svc_dim.Service, { stat = "p50", label = "e2e p50" }],
            ["...", { stat = "p90", label = "e2e p90" }],
            [".", "QueueWait", ".", ".", { stat = "p90", label = "queue wait p90" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 8, width = 8, height = 6
        properties = {
          title = "Queue backlog", region = local.region, stat = "Maximum", period = 60, view = "timeSeries"
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.jobs.name, { label = "Waiting" }],
            [".", "ApproximateNumberOfMessagesNotVisible", ".", ".", { label = "In flight" }],
            [".", "ApproximateNumberOfMessagesVisible", ".", aws_sqs_queue.dlq.name, { label = "DLQ", color = "#d62728" }],
          ]
        }
      },
      {
        type = "metric", x = 8, y = 8, width = 8, height = 6
        properties = {
          title = "Oldest message age (s)", region = local.region, stat = "Maximum", period = 60, view = "timeSeries"
          metrics = [["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.jobs.name]]
          annotations = { horizontal = [{ label = "alarm", value = var.queue_age_threshold_seconds }] }
        }
      },
      {
        type = "metric", x = 16, y = 8, width = 8, height = 6
        properties = {
          title = "Workers: CPU / memory (%)", region = local.region, stat = "Average", period = 60, view = "timeSeries"
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.worker.name],
            [".", "MemoryUtilization", ".", ".", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 14, width = 8, height = 6
        properties = {
          title = "OCR vs inference latency p90 (ms)", region = local.region, stat = "p90", period = 60, view = "timeSeries"
          metrics = [
            ["VisionOps", "OcrLatency", "Service", local.svc_dim.Service],
            [".", "InferenceLatency", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 8, y = 14, width = 8, height = 6
        properties = {
          title = "Model confidence (avg)", region = local.region, stat = "Average", period = 300, view = "timeSeries"
          metrics = [["VisionOps", "ModelConfidence", "Service", local.svc_dim.Service]]
          yAxis   = { left = { min = 0, max = 1 } }
        }
      },
      {
        type = "alarm", x = 16, y = 14, width = 8, height = 6
        properties = {
          title = "Alarms"
          alarms = [
            aws_cloudwatch_metric_alarm.dlq_not_empty.arn,
            aws_cloudwatch_metric_alarm.queue_age.arn,
            aws_cloudwatch_metric_alarm.failed_jobs.arn,
            aws_cloudwatch_metric_alarm.latency_p90.arn,
            aws_cloudwatch_metric_alarm.worker_crash_loop.arn,
          ]
        }
      },
      {
        type = "log", x = 0, y = 20, width = 24, height = 6
        properties = {
          title  = "Failures by reason (last period)", region = local.region, view = "table"
          query  = "SOURCE '${aws_cloudwatch_log_group.worker.name}' | filter event = 'job_metrics' and (JobsFailed = 1 or JobErrors = 1) | stats count(*) as jobs by reason, model_version | sort jobs desc"
        }
      },
    ]
  })
}

resource "aws_cloudwatch_query_definition" "slowest_jobs" {
  name            = "${local.name}/slowest-jobs"
  log_group_names = [aws_cloudwatch_log_group.worker.name]
  query_string    = <<-EOT
    filter event = "job_metrics" and JobsSucceeded = 1
    | fields @timestamp, job_id, document_type, ProcessingLatency, OcrLatency, EndToEndLatency, model_version
    | sort ProcessingLatency desc
    | limit 25
  EOT
}

resource "aws_cloudwatch_query_definition" "failures" {
  name            = "${local.name}/failures"
  log_group_names = [aws_cloudwatch_log_group.worker.name]
  query_string    = <<-EOT
    filter level = "WARNING" or level = "ERROR"
    | fields @timestamp, level, msg, key, reason, detail, receive_count
    | sort @timestamp desc
    | limit 100
  EOT
}

resource "aws_cloudwatch_query_definition" "accuracy_by_type" {
  name            = "${local.name}/predictions-by-type"
  log_group_names = [aws_cloudwatch_log_group.worker.name]
  query_string    = <<-EOT
    filter event = "job_metrics" and JobsSucceeded = 1
    | stats count(*) as jobs, avg(ModelConfidence) as avg_conf, pct(ProcessingLatency, 90) as p90_ms by document_type, model_version
  EOT
}
