# ---------------------------------------------------------------------------
# S3: one private bucket, split by prefix
#   incoming/  uploaded documents (each upload emits an event to SQS)
#   results/   classification results (JSON)
#   failed/    permanent failures (JSON with reason)
#   models/    versioned model artifacts (model.joblib + metadata.json)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "docs" {
  bucket        = "${local.name}-docs-${local.account_id}-${local.region}"
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning lets you recover a model artifact or result that was overwritten or deleted by mistake.
resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id

  rule {
    id     = "expire-incoming"
    status = "Enabled"
    filter {
      prefix = "incoming/"
    }
    expiration {
      days = 30
    }
  }

  rule {
    id     = "expire-outputs"
    status = "Enabled"
    filter {
      prefix = "results/"
    }
    expiration {
      days = 90
    }
  }

  rule {
    id     = "expire-failed"
    status = "Enabled"
    filter {
      prefix = "failed/"
    }
    expiration {
      days = 90
    }
  }

  rule {
    id     = "cleanup-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.docs]
}

data "aws_iam_policy_document" "docs_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.docs.arn, "${aws_s3_bucket.docs.arn}/*"]
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

resource "aws_s3_bucket_policy" "docs" {
  bucket     = aws_s3_bucket.docs.id
  policy     = data.aws_iam_policy_document.docs_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.docs]
}

resource "aws_s3_bucket_notification" "incoming" {
  bucket = aws_s3_bucket.docs.id

  queue {
    queue_arn     = aws_sqs_queue.jobs.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "incoming/"
  }

  depends_on = [aws_sqs_queue_policy.jobs]
}

# ---------------------------------------------------------------------------
# SQS: job queue + dead-letter queue
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.name}-jobs-dlq"
  message_retention_seconds = 1209600 # 14 days to investigate and redrive
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "jobs" {
  name                       = "${local.name}-jobs"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  receive_wait_time_seconds  = 20 # long polling: fewer empty receives, lower cost
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Only allow the DLQ to receive messages from the jobs queue (needed for console/API redrive).
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.jobs.arn]
  })
}

data "aws_iam_policy_document" "jobs_queue" {
  statement {
    sid       = "AllowS3BucketNotifications"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs.arn]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.docs.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "jobs" {
  queue_url = aws_sqs_queue.jobs.id
  policy    = data.aws_iam_policy_document.jobs_queue.json
}

# ---------------------------------------------------------------------------
# ECR: worker images (immutable tags = every deploy is traceable to a git SHA)
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "worker" {
  name                 = "${local.name}-worker"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_destroy

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 15 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 15
      }
      action = { type = "expire" }
    }]
  })
}
