# VisionOps: AWS ML Deployment & Monitoring Platform

**Stack:** AWS S3 · SQS · ECS Fargate · ECR · IAM · CloudWatch · Docker · MLflow · Terraform · GitHub Actions · Python (scikit-learn, Tesseract)

An asynchronous document-processing pipeline. A document uploaded to S3 triggers an SQS message. An autoscaled ECS worker OCRs it, classifies it (invoice / receipt / bank statement / payslip / ID card) and writes a JSON result back to S3. Everything is monitored in CloudWatch.

```
upload ─► S3 incoming/ ──event──► SQS jobs ──► ECS Fargate worker (0..N, Spot) ─► S3 results/  or  failed/
                                    │ 3 failed receives                  │
                                    ▼                                    ▼ EMF logs
                                   DLQ ──► alarm                 CloudWatch metrics, dashboard, alarms
model_configs/vN.yaml ─► training/train.py (MLflow) ─► S3 models/vN/ (sha256-verified at load)
git push ─► GitHub Actions ─► ECR (tag = git SHA) ─► ECS rolling deploy with circuit-breaker rollback
```

## Layout

| Path | Purpose |
|---|---|
| `visionops/` | worker: SQS loop, OCR, inference, idempotency, EMF metrics |
| `training/train.py` | trains from `model_configs/<version>.yaml`, logs to MLflow, writes `artifacts/models/<v>/` |
| `model_configs/` | versioned training and serving config (v1 baseline, v2 char n-grams) |
| `infra/` | Terraform: S3, SQS + DLQ, ECR, ECS, autoscaling, IAM, CloudWatch |
| `.github/workflows/` | `ci.yml` (lint, tests, terraform validate, image smoke test), `deploy.yml` (OIDC → ECR → ECS) |
| `scripts/` | `publish_model.py`, `load_test.py`, `dlq.py`, `verify_teardown.py`, `push_image.sh` |
| `docs/` | `RUNBOOK.md` (failure recovery), `SECURITY.md` (IAM / access controls), `TEARDOWN.md` |

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Linux/Mac: .venv/bin/pip
pytest -q                                   # 26 tests, S3/SQS mocked with moto
python training/train.py --version v1       # MLflow: `mlflow ui --backend-store-uri sqlite:///mlflow.db`
python training/train.py --version v2
```

Locally without AWS cost: `docker compose up -d localstack`, `python local/bootstrap_localstack.py`, `docker compose up --build worker`, then run the load test with `AWS_ENDPOINT_URL=http://localhost:4566`.

### Deploy to AWS (needs an AWS account)

```bash
cd infra && cp terraform.tfvars.example terraform.tfvars   # edit
terraform init && terraform apply
python ../scripts/publish_model.py --version v1 --bucket $(terraform output -raw bucket)
../scripts/push_image.sh $(terraform output -raw ecr_repository_url) v1-bootstrap
terraform apply -var image_tag=v1-bootstrap
terraform output github_actions_variables    # paste into GitHub repo variables; later pushes deploy via CI
python ../scripts/load_test.py --bucket $(terraform output -raw bucket) --count 500
```

Then tear down with `docs/TEARDOWN.md`.

## Design choices

- **At-least-once safe:** result existence check makes redeliveries no-ops. Bad inputs fail fast to `failed/`, transient errors retry with backoff, then go to the DLQ.
- **Metrics via EMF** log lines: no `PutMetricData` permission, and the same line is queryable in Logs Insights.
- **Cost:** Fargate Spot, scale-to-zero, no NAT gateway, public IP with no inbound ports (trade-off in `docs/SECURITY.md`).
- **Model integrity:** versions are immutable in S3 and verified by SHA-256 before `joblib.load`.

## Honest scope notes

- The classifier is trained on **synthetic** documents, so the ~100% accuracy on the held-out synthetic set says little about real documents. The project's point is the MLOps platform, not the model.
- Load-test numbers (500+ jobs) must come from your own AWS run: `scripts/load_test.py` writes them to `reports/`. Put real measured numbers on your resume, not estimates.
