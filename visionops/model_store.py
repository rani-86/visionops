"""Download a versioned model from S3, verify its checksum and load it."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import joblib

from .errors import ModelIntegrityError

log = logging.getLogger(__name__)


@dataclass
class LoadedModel:
    version: str
    pipeline: object
    labels: list[str]
    metadata: dict

    def predict(self, text: str) -> tuple[str, float, dict[str, float]]:
        probs = self.pipeline.predict_proba([text])[0]
        classes = list(self.pipeline.classes_)
        best = int(probs.argmax())
        return classes[best], float(probs[best]), {c: round(float(p), 4) for c, p in zip(classes, probs, strict=True)}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_model(version: str, *, s3=None, bucket: str | None = None, models_prefix: str = "models/",
               cache_dir: str = "/tmp/visionops-models", local_dir: str | None = None) -> LoadedModel:
    """Load ``<models_prefix><version>/model.joblib`` (+ metadata.json) from S3 or a local directory."""
    started = time.perf_counter()
    if local_dir:
        model_dir = Path(local_dir)
    else:
        model_dir = Path(cache_dir) / version
        model_dir.mkdir(parents=True, exist_ok=True)
        for name in ("metadata.json", "model.joblib"):
            key = f"{models_prefix}{version}/{name}"
            log.info("downloading model artifact", extra={"key": key})
            s3.download_file(bucket, key, str(model_dir / name))

    metadata = json.loads((model_dir / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("version") != version:
        raise ModelIntegrityError(f"metadata version {metadata.get('version')!r} != requested {version!r}")
    actual = sha256_file(model_dir / "model.joblib")
    if actual != metadata["sha256"]:
        raise ModelIntegrityError(f"sha256 mismatch for {version}: expected {metadata['sha256']}, got {actual}")

    pipeline = joblib.load(model_dir / "model.joblib")
    log.info("model loaded", extra={"model_version": version, "load_ms": round((time.perf_counter() - started) * 1000)})
    return LoadedModel(version=version, pipeline=pipeline, labels=metadata["labels"], metadata=metadata)
