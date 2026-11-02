"""Upload a trained model version to S3 (immutable: refuses to overwrite an existing version).

    python scripts/publish_model.py --version v2 --bucket <bucket>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--prefix", default="models/")
    ap.add_argument("--force", action="store_true", help="overwrite an existing version (avoid in prod)")
    args = ap.parse_args()

    src = ROOT / "artifacts" / "models" / args.version
    model, meta = src / "model.joblib", src / "metadata.json"
    if not model.exists() or not meta.exists():
        sys.exit(f"{src} is missing model.joblib/metadata.json; run training/train.py --version {args.version}")
    metadata = json.loads(meta.read_text(encoding="utf-8"))
    if hashlib.sha256(model.read_bytes()).hexdigest() != metadata["sha256"]:
        sys.exit("local model.joblib does not match metadata.json sha256; retrain")

    s3 = boto3.client("s3")
    key = f"{args.prefix}{args.version}/model.joblib"
    try:
        s3.head_object(Bucket=args.bucket, Key=key)
        if not args.force:
            sys.exit(f"s3://{args.bucket}/{key} already exists. Model versions are immutable; "
                     "create a new config version instead (or pass --force).")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
            raise

    for path in (model, meta):
        dest = f"{args.prefix}{args.version}/{path.name}"
        s3.upload_file(str(path), args.bucket, dest)
        print(f"uploaded s3://{args.bucket}/{dest}")
    print(f"metrics: {metadata['metrics']}")


if __name__ == "__main__":
    main()
