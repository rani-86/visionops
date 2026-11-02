"""End-to-end worker behaviour against mocked S3 + SQS (moto)."""

import io
import json
import random
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from visionops.config import Settings, load_model_config
from visionops.metrics import MetricsEmitter
from visionops.processor import JobProcessor, parse_s3_event
from visionops.synth import generate_text
from visionops.worker import Worker, backoff_seconds

BUCKET = "test-bucket"


def s3_event(key: str, size: int = 100) -> str:
    return json.dumps({"Records": [{
        "eventName": "ObjectCreated:Put",
        "eventTime": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "s3": {"bucket": {"name": BUCKET}, "object": {"key": key.replace(" ", "+"), "size": size}},
    }]})


@pytest.fixture
def env(model):
    with mock_aws():
        s3 = boto3.client("s3")
        sqs = boto3.client("sqs")
        s3.create_bucket(Bucket=BUCKET)
        q = sqs.create_queue(QueueName="jobs")["QueueUrl"]
        settings = Settings(bucket=BUCKET, queue_url=q, model_version="v1", wait_seconds=0)
        processor = JobProcessor(s3, settings, model, load_model_config("v1"))
        out = io.StringIO()
        worker = Worker(settings, sqs, processor, MetricsEmitter("test", out))
        yield {"s3": s3, "sqs": sqs, "q": q, "worker": worker, "metrics": out}


def send(env, key: str, body: bytes):
    env["s3"].put_object(Bucket=BUCKET, Key=key, Body=body)
    env["sqs"].send_message(QueueUrl=env["q"], MessageBody=s3_event(key, len(body)))


def queue_depth(env) -> int:
    attrs = env["sqs"].get_queue_attributes(QueueUrl=env["q"], AttributeNames=["All"])["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(attrs["ApproximateNumberOfMessagesNotVisible"])


def metrics(env) -> list[dict]:
    return [json.loads(line) for line in env["metrics"].getvalue().splitlines()]


def test_parse_s3_event_decodes_keys():
    recs = parse_s3_event(s3_event("incoming/run 1/a.png"))
    assert recs[0].key == "incoming/run 1/a.png" and recs[0].ext == ".png"
    assert parse_s3_event(json.dumps({"Event": "s3:TestEvent"})) is None


def test_successful_job_writes_result_and_deletes_message(env):
    send(env, "incoming/run1/doc-1.txt", generate_text("payslip", random.Random(5)).encode())
    assert env["worker"].poll_once() == 1

    result = json.loads(env["s3"].get_object(Bucket=BUCKET, Key="results/run1/doc-1.json")["Body"].read())
    assert result["document_type"] == "payslip"
    assert result["status"] in ("completed", "needs_review")
    assert result["model_version"] == "v1"
    assert result["timings_ms"]["end_to_end"] is not None
    assert queue_depth(env) == 0
    assert metrics(env)[-1]["JobsSucceeded"] == 1


def test_duplicate_delivery_is_idempotent(env):
    body = generate_text("invoice", random.Random(1)).encode()
    send(env, "incoming/run1/dup.txt", body)
    env["worker"].poll_once()
    env["sqs"].send_message(QueueUrl=env["q"], MessageBody=s3_event("incoming/run1/dup.txt", len(body)))
    env["worker"].poll_once()
    assert metrics(env)[-1]["DuplicateJobs"] == 1
    assert queue_depth(env) == 0


@pytest.mark.parametrize("key,body,reason", [
    ("incoming/run1/bad.docx", b"PK..", "unsupported_type"),
    ("incoming/run1/short.txt", b"hi", "no_text"),
    ("incoming/run1/corrupt.png", b"\x89PNG broken", "corrupt_image"),
])
def test_permanent_failure_is_recorded_and_not_retried(env, key, body, reason):
    send(env, key, body)
    env["worker"].poll_once()
    job_id = key[len("incoming/"):].rsplit(".", 1)[0]
    failed = json.loads(env["s3"].get_object(Bucket=BUCKET, Key=f"failed/{job_id}.json")["Body"].read())
    assert failed["reason"] == reason
    assert queue_depth(env) == 0
    assert metrics(env)[-1]["JobsFailed"] == 1


def test_oversized_file_is_rejected_before_download(env):
    env["sqs"].send_message(QueueUrl=env["q"], MessageBody=s3_event("incoming/run1/huge.png", 50 * 1024 * 1024))
    env["worker"].poll_once()
    failed = json.loads(env["s3"].get_object(Bucket=BUCKET, Key="failed/run1/huge.json")["Body"].read())
    assert failed["reason"] == "file_too_large"


def test_transient_error_leaves_message_for_retry(env, monkeypatch):
    send(env, "incoming/run1/flaky.txt", generate_text("receipt", random.Random(2)).encode())

    def boom(*_a, **_k):
        raise ConnectionError("S3 hiccup")

    monkeypatch.setattr(env["worker"].processor, "process", boom)
    env["worker"].poll_once()
    assert queue_depth(env) == 1  # still there: redelivered later, DLQ after maxReceiveCount
    assert metrics(env)[-1]["JobErrors"] == 1


def test_malformed_and_test_events_are_dropped(env):
    env["sqs"].send_message(QueueUrl=env["q"], MessageBody="not json")
    env["sqs"].send_message(QueueUrl=env["q"], MessageBody=json.dumps({"Event": "s3:TestEvent"}))
    env["worker"].poll_once()
    env["worker"].poll_once()
    assert queue_depth(env) == 0


def test_backoff_is_capped():
    assert [backoff_seconds(n) for n in (1, 2, 3)] == [30, 60, 120]
    assert backoff_seconds(10) == 600
