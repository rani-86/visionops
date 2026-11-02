# Access controls

## Principals and what they can do

| Principal | Used by | Allowed | Explicitly not allowed |
|---|---|---|---|
| `visionops-ecs-execution` | ECS agent (starting tasks) | pull from the `visionops-worker` ECR repo; write to the `/ecs/visionops-worker` log group | other repos, other log groups, any S3/SQS |
| `visionops-worker-task` | worker code | `GetObject` on `incoming/*`, `models/*`, `results/*`; `PutObject` on `results/*`, `failed/*`; `ListBucket` only under `results/`; receive/delete/change-visibility on the jobs queue | deleting objects, writing `models/` or `incoming/`, reading other buckets, touching the DLQ, any IAM/ECS action |
| `visionops-github-deploy` | GitHub Actions (OIDC) | push to the worker ECR repo; register task definitions; update/describe **only** the worker service; `PassRole` **only** on the two roles above, **only** to `ecs-tasks.amazonaws.com`; read `models/*` to check a version is published | Terraform-level changes, IAM changes, reading documents/results |
| You (admin) | Terraform, load tests, model publishing | everything | n/a |

How the policies are scoped:

- **Resource ARNs, not `*`.** The only `"*"` resources are `ecr:GetAuthorizationToken`, `ecs:RegisterTaskDefinition` and `ecs:DescribeTaskDefinition`, which AWS does not support scoping.
- **Confused-deputy protection.** The ECS trust policy requires `aws:SourceAccount` and `aws:SourceArn`. The SQS queue policy only accepts `SendMessage` from S3 when `aws:SourceArn` is this bucket.
- **No long-lived CI keys.** GitHub gets short-lived credentials through OIDC. The trust policy pins `aud = sts.amazonaws.com` and `sub = repo:<owner>/<repo>:ref:refs/heads/main`, so forks, pull requests and other branches cannot assume the role.
- **`iam:PassRole` is conditioned** on `iam:PassedToService = ecs-tasks.amazonaws.com`. That stops CI from registering a task that runs under some more powerful role.

## Data protection

- S3: Block Public Access (all four settings), `BucketOwnerEnforced` (ACLs disabled), SSE-S3 encryption, versioning, and a bucket policy that **denies non-TLS requests**.
- SQS: both queues use SSE-SQS.
- Lifecycle rules: `incoming/` expires after 30 days and `results/` + `failed/` after 90 days, which limits how long uploaded personal data stays around.
- Logs keep only a 300-character text preview, and they expire after 14 days.

## Network

- Worker tasks have **no inbound ports**. The security group allows egress on 443 only.
- Trade-off: tasks use public subnets with a public IP to avoid NAT gateway cost. They are still unreachable from the internet (no inbound rules, no listener). For production, move them to private subnets with VPC endpoints for S3 (gateway, free), SQS, ECR (api + dkr), and CloudWatch Logs.

## Supply chain

- ECR has **immutable tags** (each image tag is a git SHA) and **scan on push**.
- The container runs as non-root UID 10001.
- The model artifact is verified against the SHA-256 in `metadata.json` before loading. Loading pickles is only safe from a trusted source, which is why the worker cannot write `models/`.

## Verifying least privilege

Lint the policy with IAM Access Analyzer:

```bash
aws accessanalyzer validate-policy --policy-type IDENTITY_POLICY \
  --policy-document "$(aws iam get-role-policy --role-name visionops-worker-task --policy-name least-privilege --query PolicyDocument)"
```

With the IAM policy simulator you can check that the task role is **denied** `s3:DeleteObject` and `s3:PutObject` on `models/*`, and allowed `s3:GetObject`:

```bash
ROLE_ARN=$(aws iam get-role --role-name visionops-worker-task --query Role.Arn --output text)
aws iam simulate-principal-policy --policy-source-arn "$ROLE_ARN" \
  --action-names s3:DeleteObject s3:PutObject s3:GetObject \
  --resource-arns "arn:aws:s3:::$BUCKET/models/v1/model.joblib" \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
```
