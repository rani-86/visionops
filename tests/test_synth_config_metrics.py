import io
import json
import random

import pytest

from visionops.config import load_model_config
from visionops.metrics import MetricsEmitter
from visionops.synth import LABELS, add_ocr_noise, generate_text, make_dataset


def test_dataset_is_deterministic_and_balanced():
    a = make_dataset(5, seed=3)
    b = make_dataset(5, seed=3)
    assert a == b
    assert sorted(set(a[1])) == sorted(LABELS)
    assert len(a[0]) == 5 * len(LABELS)


def test_ocr_noise_changes_text_but_keeps_length_close():
    text = generate_text("invoice", random.Random(0))
    noisy = add_ocr_noise(text, random.Random(1), rate=0.2)
    assert noisy != text
    assert 0.7 * len(text) < len(noisy) <= len(text)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_model_configs_load(version):
    cfg = load_model_config(version)
    assert cfg.version == version
    assert 0 < cfg.serving.min_confidence < 1
    assert ".png" in cfg.serving.allowed_extensions


def test_model_config_version_mismatch(tmp_path):
    (tmp_path / "v9.yaml").write_text("version: v8\n")
    with pytest.raises(ValueError):
        load_model_config("v9", tmp_path)


def test_emf_record_shape():
    buf = io.StringIO()
    MetricsEmitter("svc", buf).emit({"ProcessingLatency": 12.5, "JobsSucceeded": 1}, job_id="j1")
    rec = json.loads(buf.getvalue())
    directive = rec["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "VisionOps"
    assert directive["Dimensions"] == [["Service"]]
    assert {m["Name"] for m in directive["Metrics"]} == {"ProcessingLatency", "JobsSucceeded"}
    assert rec["Service"] == "svc" and rec["ProcessingLatency"] == 12.5 and rec["job_id"] == "j1"


def test_emf_rejects_unknown_metric():
    with pytest.raises(ValueError):
        MetricsEmitter("svc", io.StringIO()).emit({"Typo": 1})
