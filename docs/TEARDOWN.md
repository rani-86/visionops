# Infrastructure teardown

Run this when you finish a demo so nothing keeps billing.

## What costs money while it is up

| Resource | Idle cost | Under load |
|---|---|---|
| ECS Fargate Spot (0.5 vCPU / 1 GB) | **$0** (scales to zero) | ≈ $0.006 per task-hour |
| S3 | a few cents per GB-month | requests: ≈ $0.005 per 1,000 PUTs |
| SQS | free tier: 1M requests/month | — |
| CloudWatch | dashboard: 3 free per account; alarms ≈ $0.10 each per month (7 alarms ≈ $0.70) | custom metrics ≈ $0.30 per metric-month |
| ECR | ≈ $0.10/GB-month (image ≈ 250 MB) | — |
| Public IPv4 on running tasks | $0.005 per hour per task | — |

A 500-job load test plus a few days of keeping the stack up costs well under $2.

## Steps

1. **Stop the workers**, so nothing writes while you delete:
   ```bash
   cd infra
   aws ecs update-service --cluster "$(terraform output -raw ecs_cluster)" \
     --service "$(terraform output -raw ecs_service)" --desired-count 0
   ```
2. **Optional: keep what you need.** Copy anything you want to keep, such as load-test results or model artifacts:
   ```bash
   aws s3 sync "s3://$(terraform output -raw bucket)/models" ./backup/models
   ```
3. **Destroy everything Terraform created.** `force_destroy = true` empties the versioned bucket and the ECR repo:
   ```bash
   terraform destroy
   ```
4. **Check nothing is left:**
   ```bash
   python ../scripts/verify_teardown.py --project visionops --region ap-south-1
   ```
   The tagging API can lag by a few minutes. Run it again if it lists items that you already deleted.
5. **Clean up by hand**, since Terraform does not track these:
   - ECS task definition revisions registered by GitHub Actions. They are free; deregister them if you want a clean account:
     ```bash
     for td in $(aws ecs list-task-definitions --family-prefix visionops-worker --query 'taskDefinitionArns[]' --output text); do
       aws ecs deregister-task-definition --task-definition "$td" > /dev/null; done
     ```
   - The GitHub OIDC provider, **only if** Terraform did not create it (`create_github_oidc_provider = false`).
   - Local files: `infra/terraform.tfstate*`, `mlruns/`, `mlflow.db`, `artifacts/`.
6. **Disable the GitHub deploy workflow** (Actions → Deploy worker → Disable), or delete the repo variables, so a push to `main` does not try to deploy into a stack that no longer exists.

## Re-creating the stack

`terraform apply`, then `scripts/publish_model.py` for each model version, then the deploy workflow (or `scripts/push_image.sh` + `terraform apply -var image_tag=<tag>`). The S3 bucket name is deterministic (`visionops-docs-<account>-<region>`), so scripts keep working.
