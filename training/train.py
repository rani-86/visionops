"""Train a document-type classifier from a versioned config and track it in MLflow.

    python training/train.py --version v2

Outputs
  * an MLflow run (params, metrics, confusion matrix, model) in ./mlflow.db + ./mlruns,
    with the model registered as ``visionops-doc-classifier``,
  * artifacts/models/<version>/model.joblib + metadata.json (sha256, metrics, run id),
    ready for ``scripts/publish_model.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from visionops.config import load_model_config  # noqa: E402
from visionops.synth import LABELS, make_dataset  # noqa: E402

REGISTERED_NAME = "visionops-doc-classifier"


def build_pipeline(t: dict) -> Pipeline:
    features = [("word", TfidfVectorizer(lowercase=True, ngram_range=tuple(t["word_ngrams"]),
                                         max_features=t["max_features"], sublinear_tf=True))]
    if t.get("char_ngrams"):
        features.append(("char", TfidfVectorizer(lowercase=True, analyzer="char_wb",
                                                 ngram_range=tuple(t["char_ngrams"]),
                                                 max_features=t["max_features"], sublinear_tf=True)))
    return Pipeline([
        ("features", FeatureUnion(features)),
        ("clf", LogisticRegression(C=t["C"], max_iter=2000)),
    ])


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="model config version, e.g. v1")
    ap.add_argument("--tracking-uri", default=f"sqlite:///{(ROOT / 'mlflow.db').as_posix()}")
    ap.add_argument("--no-mlflow", action="store_true", help="skip MLflow (used by unit tests)")
    ap.add_argument("--out-dir", default=str(ROOT / "artifacts" / "models"))
    args = ap.parse_args()

    cfg = load_model_config(args.version)
    t = cfg.training
    texts, labels = make_dataset(t["n_per_class"], seed=t["seed"], noise_rate=t["noise_rate"])
    x_train, x_test, y_train, y_test = train_test_split(texts, labels, test_size=t["test_size"],
                                                        stratify=labels, random_state=t["seed"])
    # Robustness set: unseen seed and double the OCR noise of training.
    x_hard, y_hard = make_dataset(60, seed=t["seed"] + 1000, noise_rate=t["noise_rate"] * 2)

    pipe = build_pipeline(t)
    pipe.fit(x_train, y_train)

    pred = pipe.predict(x_test)
    pred_hard = pipe.predict(x_hard)
    metrics = {
        "test_accuracy": accuracy_score(y_test, pred),
        "test_macro_f1": f1_score(y_test, pred, average="macro"),
        "hard_accuracy": accuracy_score(y_hard, pred_hard),
        "hard_macro_f1": f1_score(y_hard, pred_hard, average="macro"),
        "hard_mean_confidence": float(pipe.predict_proba(x_hard).max(axis=1).mean()),
    }
    report = classification_report(y_hard, pred_hard, digits=4)
    cm = confusion_matrix(y_hard, pred_hard, labels=list(LABELS)).tolist()

    out = Path(args.out_dir) / cfg.version
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / "model.joblib"
    joblib.dump(pipe, model_path)

    run_id = None
    if not args.no_mlflow:
        import mlflow
        import mlflow.sklearn

        mlflow.set_tracking_uri(args.tracking_uri)
        mlflow.set_experiment("visionops-doc-classifier")
        with mlflow.start_run(run_name=f"{cfg.version}-{datetime.now():%Y%m%d-%H%M%S}") as run:
            run_id = run.info.run_id
            mlflow.set_tags({"model_version": cfg.version, "git_sha": git_sha(), "description": cfg.description})
            mlflow.log_params({k: str(v) for k, v in t.items()})
            mlflow.log_params({f"serving.{k}": str(v) for k, v in cfg.serving.__dict__.items()})
            mlflow.log_metrics(metrics)
            mlflow.log_dict({"labels": list(LABELS), "matrix": cm}, "confusion_matrix.json")
            mlflow.log_text(report, "classification_report.txt")
            mlflow.log_artifact(str(ROOT / "model_configs" / f"{cfg.version}.yaml"))
            mlflow.sklearn.log_model(pipe, name="model", registered_model_name=REGISTERED_NAME)

    metadata = {
        "version": cfg.version,
        "description": cfg.description,
        "sha256": sha256(model_path),
        "labels": list(LABELS),
        "metrics": {k: round(v, 4) for k, v in metrics.items()},
        "mlflow_run_id": run_id,
        "git_sha": git_sha(),
        "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "trained_at": datetime.now(UTC).isoformat(),
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(report)
    print(json.dumps(metadata, indent=2))
    print(f"\nArtifacts written to {out}")


if __name__ == "__main__":
    main()
