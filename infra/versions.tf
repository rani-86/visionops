terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60, < 7.0"
    }
  }

  # Local state keeps the student setup simple. For a team, switch to an S3 backend:
  # backend "s3" { bucket = "<state-bucket>" key = "visionops/terraform.tfstate" region = "ap-south-1" use_lockfile = true }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name       = var.project
  account_id = data.aws_caller_identity.current.account_id
  region     = var.region
}
