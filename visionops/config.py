"""Runtime settings (environment variables) and versioned model configuration (YAML)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("MODEL_CONFIG_DIR", Path(__file__).resolve().parent.parent / "model_configs"))


@dataclass(frozen=True)
class Settings:
    bucket: str
    queue_url: str
    model_version: str
    service_name: str = "visionops-worker"
    incoming_prefix: str = "incoming/"
    results_prefix: str = "results/"
    failed_prefix: str = "failed/"
    models_prefix: str = "models/"
    max_messages: int = 5
    wait_seconds: int = 20
    max_receive_count: int = 3
    model_cache_dir: str = "/tmp/visionops-models"
    local_model_dir: str | None = None  # load model from disk instead of S3 (tests / offline dev)

    @classmethod
    def from_env(cls) -> Settings:
        def req(name: str) -> str:
            value = os.environ.get(name)
            if not value:
                raise RuntimeError(f"Missing required environment variable {name}")
            return value

        return cls(
            bucket=req("VISIONOPS_BUCKET"),
            queue_url=req("VISIONOPS_QUEUE_URL"),
            model_version=req("MODEL_VERSION"),
            service_name=os.environ.get("SERVICE_NAME", "visionops-worker"),
            max_messages=int(os.environ.get("MAX_MESSAGES", "5")),
            wait_seconds=int(os.environ.get("WAIT_SECONDS", "20")),
            max_receive_count=int(os.environ.get("MAX_RECEIVE_COUNT", "3")),
            model_cache_dir=os.environ.get("MODEL_CACHE_DIR", "/tmp/visionops-models"),
            local_model_dir=os.environ.get("LOCAL_MODEL_DIR") or None,
        )


@dataclass(frozen=True)
class ServingConfig:
    min_confidence: float = 0.6
    max_file_mb: float = 10.0
    min_text_chars: int = 20
    ocr_lang: str = "eng"
    ocr_psm: int = 6
    max_image_side: int = 3000
    allowed_extensions: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf", ".txt")


@dataclass(frozen=True)
class ModelConfig:
    version: str
    description: str
    training: dict = field(default_factory=dict)
    serving: ServingConfig = field(default_factory=ServingConfig)


def load_model_config(version: str, config_dir: Path | str = CONFIG_DIR) -> ModelConfig:
    path = Path(config_dir) / f"{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No model config for version '{version}' at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw.get("version") != version:
        raise ValueError(f"{path} declares version {raw.get('version')!r}, expected {version!r}")
    serving = dict(raw.get("serving") or {})
    if "allowed_extensions" in serving:
        serving["allowed_extensions"] = tuple(serving["allowed_extensions"])
    return ModelConfig(
        version=version,
        description=raw.get("description", ""),
        training=raw.get("training") or {},
        serving=ServingConfig(**serving),
    )
