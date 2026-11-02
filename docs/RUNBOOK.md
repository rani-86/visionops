# VisionOps runbook: failure recovery

Each alarm links to a section here. Shell snippets assume you ran `cd infra` and have these set:

```bash
BUCKET=$(terraform output -raw bucket)
QUEUE_URL=$(terraform output -raw queue_url)
DLQ_URL=$(terraform output -raw dlq_url)
DLQ_ARN=$(terraform output -raw dlq_arn)
CLUSTER=$(terraform output -raw ecs_cluster)
SERVICE=$(terraform output -raw ecs_service)
LOG_GROUP=$(terraform output -raw log_group)
```

## How failures are handled

| Failure | Example | What the worker does | Where it shows |
|---|---|---|---|
| Permanent (bad input) | corrupt PNG, `.docx`, blank page, >10 MB | writes `failed/<job>.json` with a `reason`, deletes the message | `JobsFailed` metric, `failed/` prefix |
| Transient | S3 throttling, network error, Tesseract crash | leaves the message, backs off 30s → 60s → 120s | `JobErrors` metric, WARNING/ERROR logs |
| Retries exhausted | transient error 3 times in a row | SQS moves the message to `visionops-jobs-dlq` | `dlq-not-empty` alarm |
| Duplicate delivery | SQS at-least-once redelivery | sees `results/<job>.json` exists and skips | `DuplicateJobs` metric |
| Worker crash / Spot interruption | OOM, task replaced | the message reappears after the visibility timeout (300s) and another task processes it | ECS events, `JobErrors` |
| Bad deploy | image or model crashes on start | ECS deployment circuit breaker rolls back to the last working task definition | GitHub Actions deploy fails |

---

## <a id="dlq"></a>Alarm: `visionops-dlq-not-empty`

1. **See what failed:**
   ```bash
   python scripts/dlq.py peek --dlq-url "$DLQ_URL"
   ```
2. **Find the cause in the logs** (CloudWatch → Logs Insights → saved query `visionops/failures`, or):
   ```bash
   aws logs tail "$LOG_GROUP" --since 1h --filter-pattern '"job error"'
   ```
   Common causes are `AccessDenied` (an IAM policy changed), a model or OCR crash on one kind of file, and S3 throttling.
3. **Fix the cause**, and deploy if needed (see "Roll back a deploy" below).
4. **Redrive** the messages to the main queue. Workers pick them up again, and the idempotency check prevents duplicate results:
   ```bash
   python scripts/dlq.py redrive --dlq-arn "$DLQ_ARN"
   python scripts/dlq.py status  --dlq-arn "$DLQ_ARN"
   ```
5. If a message is poison and should be dropped, delete it in the SQS console, then upload a fixed document instead.

## <a id="backlog"></a>Alarm: `visionops-queue-backlog-stale`

The oldest job has waited more than 15 minutes.

1. Are any workers running?
   ```bash
   aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
     --query 'services[0].{desired:desiredCount,running:runningCount,events:events[:5].message}'
   ```
2. **`desired = 0`:** autoscaling did not fire. Check the `visionops-backlog-scale-out` alarm state and scale by hand:
   ```bash
   aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 2
   ```
3. **`running < desired`:** tasks fail to start. Check the stopped-task reason:
   ```bash
   aws ecs list-tasks --cluster "$CLUSTER" --desired-status STOPPED --query 'taskArns[:3]' --output text \
     | xargs aws ecs describe-tasks --cluster "$CLUSTER" --query 'tasks[].stoppedReason' --tasks
   ```
   - `CannotPullContainerError`: the image tag does not exist, or the execution role lacks ECR access.
   - `ResourceInitializationError`: the task has no route to the internet. It needs `assign_public_ip=true` (or a NAT gateway / VPC endpoints).
   - `Essential container exited` plus a `ModelIntegrityError` in the logs: the model artifact is missing or was tampered with. Publish it again with `scripts/publish_model.py`.
   - Fargate Spot capacity unavailable: switch the service to the `FARGATE` capacity provider for now.
4. **Running but slow:** look at the latency widgets. Raise `max_workers` or `worker_cpu` in `terraform.tfvars` and apply.

## <a id="failed-jobs"></a>Alarm: `visionops-failed-jobs`

More than 5% of jobs failed within 5 minutes.

1. Group the failures by reason (dashboard table "Failures by reason", or):
   ```bash
   aws s3 ls "s3://$BUCKET/failed/" --recursive | tail
   aws s3 cp "s3://$BUCKET/failed/<job>.json" -
   ```
2. If `reason` is `corrupt_image`, `unsupported_type` or `no_text`, the client is sending bad files. Tell the uploader; the platform is working as intended.
3. If `JobErrors` dominate, the problem is on the platform side. Follow the DLQ steps above.

## Alarm: `visionops-processing-latency-p90`

- Compare `OcrLatency` with `InferenceLatency` on the dashboard. OCR usually dominates. Large scans are downscaled to `max_image_side`; lower it in the model config.
- CPU near 100% means workers are undersized: raise `worker_cpu` to 1024.
- If it started right after a deploy, roll back (below).

## Roll back a deploy

The circuit breaker covers crashes on start. To roll back a model that runs but performs badly:

```bash
# list recent revisions
aws ecs list-task-definitions --family-prefix visionops-worker --sort DESC --max-items 5
# point the service at the previous one
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --task-definition visionops-worker:<previous-revision>
```

Or run the **Deploy worker** workflow with `model_version` set to the previous version. Model versions are immutable in S3, so the old version is still there.

## Recover a deleted or overwritten object

The bucket is versioned, and non-current versions are kept for 7 days.

```bash
aws s3api list-object-versions --bucket "$BUCKET" --prefix models/v2/
aws s3api copy-object --bucket "$BUCKET" --key models/v2/model.joblib \
  --copy-source "$BUCKET/models/v2/model.joblib?versionId=<id>"
```

## Reprocess a document

Upload it again under a new key. Re-uploading to the same key is skipped because the result already exists. To force a rerun, delete `results/<job>.json` first.
