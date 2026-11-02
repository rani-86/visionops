# ---------------------------------------------------------------------------
# Least-privilege IAM
#
#   execution role  - used by the ECS agent: pull this one image repo, write to this one log group
#   task role       - used by the worker code: read incoming/ + models/, write results/ + failed/,
#                     consume the jobs queue. It cannot delete documents, read other buckets or
#                     touch the DLQ.
#   github role     - assumed by GitHub Actions through OIDC (no long-lived keys); can push to the
#                     ECR repo and roll this one ECS service.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:ecs:${local.region}:${local.account_id}:*"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# --- execution role --------------------------------------------------------

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"] # does not support resource-level permissions
    resources = ["*"]
  }
  statement {
    sid       = "PullWorkerImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = [aws_ecr_repository.worker.arn]
  }
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "least-privilege"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# --- task role -------------------------------------------------------------

resource "aws_iam_role" "task" {
  name               = "${local.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ReadInputsAndModels"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/incoming/*", "${aws_s3_bucket.docs.arn}/models/*"]
  }
  statement {
    sid       = "WriteOutputs"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.docs.arn}/results/*", "${aws_s3_bucket.docs.arn}/failed/*"]
  }
  statement {
    # HeadObject on results/ is the idempotency check. Without ListBucket a missing key returns 403
    # instead of 404, so allow listing, but only under results/.
    sid       = "CheckExistingResults"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.docs.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["results/*"]
    }
  }
  statement {
    sid       = "ReadResultsForIdempotency"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/results/*"]
  }
  statement {
    sid       = "ConsumeJobQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.jobs.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "least-privilege"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# --- GitHub Actions deploy role (OIDC) --------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count           = var.github_repo != "" && var.create_github_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"] # ignored by AWS for GitHub, but required by older providers
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.github_repo != "" && !var.create_github_oidc_provider ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  github_oidc_arn = var.github_repo == "" ? null : (
    var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
  )
}

data "aws_iam_policy_document" "github_assume" {
  count = var.github_repo != "" ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only workflows on the main branch of this repository can deploy.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count                = var.github_repo != "" ? 1 : 0
  name                 = "${local.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_assume[0].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PushWorkerImage"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages",
    ]
    resources = [aws_ecr_repository.worker.arn]
  }
  statement {
    sid       = "TaskDefinitions"
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"] # no resource-level support
    resources = ["*"]
  }
  statement {
    sid       = "RollWorkerService"
    actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = [aws_ecs_service.worker.id]
  }
  statement {
    sid       = "PassOnlyWorkerRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "VerifyModelPublished"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/models/*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = var.github_repo != "" ? 1 : 0
  name   = "deploy-worker"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy.json
}
