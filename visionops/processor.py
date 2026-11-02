"""Process one S3 object: download -> extract text -> classify -> write result JSON."""

from __future__ import annotations

import json
import posixpath
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote_plus

from botocore.exceptions import ClientError

from .config import ModelConfig, Settings
from .errors import PermanentJobError
from .extract import extract_text
from .model_store import LoadedModel


@dataclass
class S3Record:
    bucket: str
    key: str
    size: int | None
    event_time: datetime | None

    @property
    def ext(self) -> str:
        return posixpath.splitext(self.key)[1].lower()


def parse_s3_event(body: str) -> list[S3Record] | None:
    """Return the S3 records in an SQS message body, or None for S3's one-off ``s3:TestEvent``."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PermanentJobError("malformed_message", str(exc)) from exc
    if payload.get("Event") == "s3:TestEvent":
        return None
    records = []
    for rec in payload.get("Records", []):
        if not str(rec.get("eventName", "")).startswith("ObjectCreated"):
            continue
        s3 = rec["s3"]
        event_time = None
        if rec.get("eventTime"):
            event_time = datetime.fromisoformat(rec["eventTime"].replace("Z", "+00:00"))
        records.append(S3Record(
            bucket=s3["bucket"]["name"],
            key=unquote_plus(s3["object"]["key"]),  # S3 URL-encodes keys in notifications
            size=s3["object"].get("size"),
            event_time=event_time,
        ))
    if not records:
        raise PermanentJobError("malformed_message", "no ObjectCreated records in message")
    return records


class JobProcessor:
    def __init__(self, s3, settings: Settings, model: LoadedModel, model_config: ModelConfig):
        self.s3 = s3
        self.settings = settings
        self.model = model
        self.cfg = model_config

    def job_id(self, key: str) -> str:
        if not key.startswith(self.settings.incoming_prefix):
            raise PermanentJobError("outside_prefix", key)
        return posixpath.splitext(key[len(self.settings.incoming_prefix):])[0]

    def result_key(self, job_id: str) -> str:
        return f"{self.settings.results_prefix}{job_id}.json"

    def failed_key(self, job_id: str) -> str:
        return f"{self.settings.failed_prefix}{job_id}.json"

    def _exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.settings.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def process(self, record: S3Record, receive_count: int = 1) -> dict:
        """Process one document. Raises PermanentJobError for bad input; other errors are transient."""
        started = time.perf_counter()
        job_id = self.job_id(record.key)
        result_key = self.result_key(job_id)

        # SQS is at-least-once: a redelivered message must not redo (or double count) finished work.
        if self._exists(result_key):
            return {"job_id": job_id, "status": "duplicate", "result_key": result_key}

        max_bytes = int(self.cfg.serving.max_file_mb * 1024 * 1024)
        if record.size is not None and record.size > max_bytes:
            raise PermanentJobError("file_too_large", f"{record.size} bytes > {max_bytes}")
        if record.ext not in self.cfg.serving.allowed_extensions:
            raise PermanentJobError("unsupported_type", f"extension {record.ext or '(none)'} is not allowed")

        try:
            obj = self.s3.get_object(Bucket=record.bucket, Key=record.key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise PermanentJobError("object_deleted", record.key) from exc
            raise
        data = obj["Body"].read()

        t0 = time.perf_counter()
        text = extract_text(data, record.ext, self.cfg.serving)
        ocr_ms = (time.perf_counter() - t0) * 1000
        if len(text.strip()) < self.cfg.serving.min_text_chars:
            raise PermanentJobError("no_text", f"only {len(text.strip())} characters extracted")

        t1 = time.perf_counter()
        label, confidence, probs = self.model.predict(text)
        inference_ms = (time.perf_counter() - t1) * 1000

        finished_at = datetime.now(UTC)
        processing_ms = (time.perf_counter() - started) * 1000
        end_to_end_ms = None
        queue_wait_ms = None
        if record.event_time:
            end_to_end_ms = (finished_at - record.event_time).total_seconds() * 1000
            queue_wait_ms = max(end_to_end_ms - processing_ms, 0.0)

        status = "completed" if confidence >= self.cfg.serving.min_confidence else "needs_review"
        result = {
            "job_id": job_id,
            "status": status,
            "document_type": label,
            "confidence": round(confidence, 4),
            "probabilities": probs,
            "text_chars": len(text),
            "text_preview": text.strip()[:300],
            "source": {"bucket": record.bucket, "key": record.key, "size": record.size},
            "model_version": self.model.version,
            "receive_count": receive_count,
            "timings_ms": {
                "ocr": round(ocr_ms, 1),
                "inference": round(inference_ms, 1),
                "processing": round(processing_ms, 1),
                "queue_wait": round(queue_wait_ms, 1) if queue_wait_ms is not None else None,
                "end_to_end": round(end_to_end_ms, 1) if end_to_end_ms is not None else None,
            },
            "uploaded_at": record.event_time.isoformat() if record.event_time else None,
            "completed_at": finished_at.isoformat(),
        }
        self.s3.put_object(Bucket=self.settings.bucket, Key=result_key,
                           Body=json.dumps(result, indent=2).encode(), ContentType="application/json")
        result["result_key"] = result_key
        return result

    def record_failure(self, record: S3Record, err: PermanentJobError, receive_count: int) -> str | None:
        try:
            job_id = self.job_id(record.key)
        except PermanentJobError:
            return None
        key = self.failed_key(job_id)
        body = {
            "job_id": job_id,
            "status": "failed",
            "reason": err.reason,
            "detail": err.detail[:500],
            "source": {"bucket": record.bucket, "key": record.key, "size": record.size},
            "model_version": self.model.version,
            "receive_count": receive_count,
            "failed_at": datetime.now(UTC).isoformat(),
        }
        self.s3.put_object(Bucket=self.settings.bucket, Key=key,
                           Body=json.dumps(body, indent=2).encode(), ContentType="application/json")
        return key
