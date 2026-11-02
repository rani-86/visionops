import random

import boto3
import pytest
from moto import mock_aws

from visionops.config import ServingConfig
from visionops.errors import ModelIntegrityError, PermanentJobError
from visionops.extract import extract_text
from visionops.model_store import load_model
from visionops.synth import LABELS, add_ocr_noise, generate_text


def test_model_classifies_noisy_text(model):
    r = random.Random(99)
    correct = 0
    for label in LABELS:
        for _ in range(5):
            pred, conf, probs = model.predict(add_ocr_noise(generate_text(label, r), r, 0.05))
            correct += pred == label
            assert 0 <= conf <= 1 and abs(sum(probs.values()) - 1) < 1e-3
    assert correct / (5 * len(LABELS)) >= 0.9


@mock_aws
def test_load_model_from_s3(model_dir, tmp_path):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="model-bucket")
    for name in ("model.joblib", "metadata.json"):
        s3.upload_file(str(model_dir / name), "model-bucket", f"models/v1/{name}")
    loaded = load_model("v1", s3=s3, bucket="model-bucket", cache_dir=str(tmp_path))
    assert loaded.version == "v1"
    assert (tmp_path / "v1" / "model.joblib").exists()


def test_tampered_model_is_rejected(model_dir, tmp_path):
    bad = tmp_path / "v1"
    bad.mkdir()
    (bad / "metadata.json").write_text((model_dir / "metadata.json").read_text())
    (bad / "model.joblib").write_bytes((model_dir / "model.joblib").read_bytes() + b"x")
    with pytest.raises(ModelIntegrityError):
        load_model("v1", local_dir=str(bad))


def test_extract_txt():
    assert extract_text(b"hello invoice", ".txt", ServingConfig()) == "hello invoice"


@pytest.mark.parametrize("data,ext,reason", [
    (b"abc", ".docx", "unsupported_type"),
    (b"", ".png", "empty_file"),
    (b"\x89PNG not really", ".png", "corrupt_image"),
    (b"%PDF-1.4 garbage", ".pdf", "corrupt_pdf"),
    (b"\xff\xfe\xfa", ".txt", "bad_encoding"),
])
def test_extract_permanent_errors(data, ext, reason):
    with pytest.raises(PermanentJobError) as exc:
        extract_text(data, ext, ServingConfig())
    assert exc.value.reason == reason
