"""Create the LocalStack equivalent of the Terraform stack and publish trained models.

    docker compose up -d localstack
    python local/bootstrap_localstack.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = "ap-south-1"
BUCKET = "visionops-local"

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

s3 = boto3.client("s3", endpoint_url=ENDPOINT, region_name=REGION)
sqs = boto3.client("sqs", endpoint_url=ENDPOINT, region_name=REGION)

s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
dlq_url = sqs.create_queue(QueueName="visionops-jobs-dlq")["QueueUrl"]
dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
q_url = sqs.create_queue(QueueName="visionops-jobs", Attributes={
    "VisibilityTimeout": "300",
    "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"}),
})["QueueUrl"]
q_arn = sqs.get_queue_attributes(QueueUrl=q_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
s3.put_bucket_notification_configuration(Bucket=BUCKET, NotificationConfiguration={
    "QueueConfigurations": [{"QueueArn": q_arn, "Events": ["s3:ObjectCreated:*"],
                             "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "incoming/"}]}}}]
})
print(f"bucket={BUCKET}\nqueue={q_url}\ndlq={dlq_url}")

for version_dir in sorted((ROOT / "artifacts" / "models").glob("*")):
    env = {**os.environ, "AWS_ENDPOINT_URL": ENDPOINT, "AWS_DEFAULT_REGION": REGION}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_model.py"), "--version", version_dir.name,
                    "--bucket", BUCKET, "--force"], check=True, env=env)
