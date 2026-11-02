"""SQS long-polling worker. Run with ``python -m visionops.worker``.

Message lifecycle
  success            -> result written to results/, message deleted
  permanent error    -> error written to failed/, message deleted (retrying a corrupt file is pointless)
  transient error    -> message left on the queue with exponential backoff; after
                        maxReceiveCount receives SQS moves it to the dead-letter queue
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

import boto3

from .config import Settings, load_model_config
from .errors import PermanentJobError
from .metrics import MetricsEmitter
from .model_store import load_model
from .processor import JobProcessor, parse_s3_event

HEARTBEAT = Path("/tmp/visionops-heartbeat")
log = logging.getLogger("visionops.worker")


class JsonFormatter(logging.Formatter):
    _skip = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        out = {"level": record.levelname, "logger": record.name, "msg": record.getMessage(),
               "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S")}
        out.update({k: v for k, v in record.__dict__.items() if k not in self._skip})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("botocore").setLevel(logging.WARNING)


def backoff_seconds(receive_count: int) -> int:
    return min(30 * 2 ** (receive_count - 1), 600)


class Worker:
    def __init__(self, settings: Settings, sqs, processor: JobProcessor, metrics: MetricsEmitter):
        self.settings = settings
        self.sqs = sqs
        self.processor = processor
        self.metrics = metrics
        self.stop = threading.Event()

    def poll_once(self) -> int:
        resp = self.sqs.receive_message(
            QueueUrl=self.settings.queue_url,
            MaxNumberOfMessages=self.settings.max_messages,
            WaitTimeSeconds=self.settings.wait_seconds,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        messages = resp.get("Messages", [])
        for msg in messages:
            self.handle(msg)
        return len(messages)

    def handle(self, msg: dict) -> bool:
        """Process one SQS message. Returns True if the message was deleted."""
        receive_count = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        model_version = self.processor.model.version
        try:
            records = parse_s3_event(msg["Body"])
        except PermanentJobError as err:
            log.warning("dropping malformed message", extra={"reason": err.reason, "message_id": msg["MessageId"]})
            self.metrics.emit({"JobsFailed": 1}, reason=err.reason, model_version=model_version)
            self._delete(msg)
            return True
        if records is None:  # s3:TestEvent sent when the notification is configured
            self._delete(msg)
            return True

        ok = True
        for rec in records:
            try:
                result = self.processor.process(rec, receive_count)
                if result["status"] == "duplicate":
                    self.metrics.emit({"DuplicateJobs": 1}, job_id=result["job_id"], model_version=model_version)
                    continue
                t = result["timings_ms"]
                values = {
                    "ProcessingLatency": t["processing"], "OcrLatency": t["ocr"], "InferenceLatency": t["inference"],
                    "ModelConfidence": result["confidence"],
                    "JobsSucceeded": 1, "JobsNeedsReview": int(result["status"] == "needs_review"),
                }
                if t["end_to_end"] is not None:
                    values["EndToEndLatency"] = t["end_to_end"]
                    values["QueueWait"] = t["queue_wait"]
                self.metrics.emit(values, job_id=result["job_id"], status=result["status"],
                                  document_type=result["document_type"], model_version=model_version,
                                  receive_count=receive_count)
            except PermanentJobError as err:
                failed_key = self.processor.record_failure(rec, err, receive_count)
                log.warning("job failed permanently", extra={"key": rec.key, "reason": err.reason,
                                                             "detail": err.detail[:200], "failed_key": failed_key})
                self.metrics.emit({"JobsFailed": 1}, key=rec.key, reason=err.reason, model_version=model_version)
            except Exception:  # transient: leave the message for retry / DLQ
                log.exception("job error, will retry", extra={"key": rec.key, "receive_count": receive_count})
                self.metrics.emit({"JobErrors": 1}, key=rec.key, receive_count=receive_count,
                                  model_version=model_version,
                                  final_attempt=receive_count >= self.settings.max_receive_count)
                ok = False

        if ok:
            self._delete(msg)
            return True
        self._backoff(msg, receive_count)
        return False

    def _delete(self, msg: dict) -> None:
        self.sqs.delete_message(QueueUrl=self.settings.queue_url, ReceiptHandle=msg["ReceiptHandle"])

    def _backoff(self, msg: dict, receive_count: int) -> None:
        try:
            self.sqs.change_message_visibility(QueueUrl=self.settings.queue_url, ReceiptHandle=msg["ReceiptHandle"],
                                               VisibilityTimeout=backoff_seconds(receive_count))
        except Exception:
            log.warning("could not change visibility; message reappears after the queue default", exc_info=True)

    def run(self) -> None:
        log.info("worker started", extra={"queue_url": self.settings.queue_url,
                                          "model_version": self.processor.model.version})
        while not self.stop.is_set():
            HEARTBEAT.touch()
            try:
                self.poll_once()
            except Exception:
                log.exception("poll failed")
                self.stop.wait(5)
        log.info("worker stopped cleanly")


def main() -> None:
    setup_logging()
    settings = Settings.from_env()
    model_config = load_model_config(settings.model_version)
    s3 = boto3.client("s3")
    sqs = boto3.client("sqs")
    model = load_model(settings.model_version, s3=s3, bucket=settings.bucket, models_prefix=settings.models_prefix,
                       cache_dir=settings.model_cache_dir, local_dir=settings.local_model_dir)
    worker = Worker(settings, sqs, JobProcessor(s3, settings, model, model_config),
                    MetricsEmitter(settings.service_name))

    def _graceful(signum, _frame):
        log.info("shutdown signal received, finishing current batch", extra={"signal": signum})
        worker.stop.set()

    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)
    worker.run()


def healthcheck(max_age: float = 120.0) -> int:
    """Container health check: healthy if the poll loop touched the heartbeat recently."""
    try:
        return 0 if time.time() - HEARTBEAT.stat().st_mtime < max_age else 1
    except FileNotFoundError:
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "healthcheck":
        sys.exit(healthcheck())
    main()
