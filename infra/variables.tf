variable "project" {
  description = "Name prefix for every resource"
  type        = string
  default     = "visionops"
}

variable "owner" {
  description = "Owner tag (your name or email), used to find resources during teardown"
  type        = string
  default     = "student"
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "github_repo" {
  description = "GitHub repository allowed to deploy, as owner/name. Leave empty to skip the CI role."
  type        = string
  default     = ""
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if your account already has one."
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "Worker image tag for the Terraform-managed task definition. CI deploys later revisions."
  type        = string
  default     = "bootstrap"
}

variable "model_version" {
  description = "Model config version the worker loads (model_configs/<version>.yaml)"
  type        = string
  default     = "v1"
}

variable "worker_cpu" {
  type    = number
  default = 512
}

variable "worker_memory" {
  type    = number
  default = 1024
}

variable "min_workers" {
  description = "0 means scale to zero when the queue is empty (cheapest)"
  type        = number
  default     = 0
}

variable "max_workers" {
  type    = number
  default = 4
}

variable "max_receive_count" {
  description = "Deliveries before a message is moved to the dead-letter queue"
  type        = number
  default     = 3
}

variable "visibility_timeout_seconds" {
  type    = number
  default = 300
}

variable "alert_email" {
  description = "Email address for CloudWatch alarm notifications (confirm the SNS email). Empty to skip."
  type        = string
  default     = ""
}

variable "latency_p90_threshold_ms" {
  type    = number
  default = 15000
}

variable "queue_age_threshold_seconds" {
  type    = number
  default = 900
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "force_destroy" {
  description = "Let terraform destroy delete a non-empty bucket and ECR repo (needed for one-command teardown)"
  type        = bool
  default     = true
}
