"""Runs inside the worker image: real Tesseract OCR + model on rendered synthetic documents."""

import os
import random

from visionops.config import load_model_config
from visionops.extract import extract_text
from visionops.model_store import load_model
from visionops.synth import LABELS, generate_lines, render_png

version = os.environ["MODEL_VERSION"]
cfg = load_model_config(version)
model = load_model(version, local_dir=os.environ["LOCAL_MODEL_DIR"])
r = random.Random(123)
correct = 0
for label in LABELS:
    text = extract_text(render_png(generate_lines(label, r), r), ".png", cfg.serving)
    pred, conf, _ = model.predict(text)
    print(f"{label:15s} -> {pred:15s} conf={conf:.2f} chars={len(text)}")
    correct += pred == label
assert correct >= len(LABELS) - 1, f"only {correct}/{len(LABELS)} correct"
print(f"smoke test passed: {correct}/{len(LABELS)}")
