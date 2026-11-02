import hashlib
import json
import sys
from pathlib import Path

import joblib
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from training.train import build_pipeline  # noqa: E402
from visionops.config import load_model_config  # noqa: E402
from visionops.model_store import load_model  # noqa: E402
from visionops.synth import LABELS, make_dataset  # noqa: E402


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory) -> Path:
    """A small real model trained with the v1 config, saved like training/train.py does."""
    cfg = load_model_config("v1")
    texts, labels = make_dataset(60, seed=1, noise_rate=0.03)
    pipe = build_pipeline(cfg.training).fit(texts, labels)
    out = tmp_path_factory.mktemp("model") / "v1"
    out.mkdir()
    joblib.dump(pipe, out / "model.joblib")
    meta = {"version": "v1", "sha256": hashlib.sha256((out / "model.joblib").read_bytes()).hexdigest(),
            "labels": list(LABELS), "metrics": {}}
    (out / "metadata.json").write_text(json.dumps(meta))
    return out


@pytest.fixture(scope="session")
def model(model_dir):
    return load_model("v1", local_dir=str(model_dir))
