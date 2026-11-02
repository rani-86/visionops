"""Load test: upload N synthetic documents, wait for the pipeline, report latency/accuracy/failures.

    python scripts/load_test.py --bucket <bucket> --count 500 --concurrency 16

About 3% of the documents are bad on purpose (corrupt image, unsupported type, blank page) to
exercise the failure path. Reports go to reports/loadtest-<run_id>.{md,json}.
Works against LocalStack too: set AWS_ENDPOINT_URL=http://localhost:4566.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from visionops.synth import LABELS, generate_lines, render_png  # noqa: E402


def make_job(i: int, r: random.Random, bad_rate: float) -> dict:
    if r.random() < bad_rate:
        kind = r.choice(["corrupt_image", "unsupported_type", "no_text"])
        if kind == "corrupt_image":
            return {"name": f"doc-{i:05d}.png", "body": b"\x89PNG\r\n\x1a\n" + r.randbytes(200), "expected": "failed", "kind": kind}
        if kind == "unsupported_type":
            return {"name": f"doc-{i:05d}.docx", "body": b"PK\x03\x04" + r.randbytes(200), "expected": "failed", "kind": kind}
        return {"name": f"doc-{i:05d}.png", "body": render_png([""], r, degrade=False), "expected": "failed", "kind": kind}
    label = r.choice(LABELS)
    return {"name": f"doc-{i:05d}.png", "body": render_png(generate_lines(label, r), r), "expected": label, "kind": "valid"}


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def list_keys(s3, bucket: str, prefix: str) -> set[str]:
    keys = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.update(o["Key"] for o in page.get("Contents", []))
    return keys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--bad-rate", type=float, default=0.03)
    ap.add_argument("--timeout", type=int, default=1800, help="seconds to wait for all jobs")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--run-id", default=datetime.now().strftime("lt-%Y%m%d-%H%M%S"))
    args = ap.parse_args()

    s3 = boto3.client("s3", config=Config(max_pool_connections=args.concurrency * 2, retries={"mode": "adaptive"}))
    r = random.Random(args.seed)
    print(f"[{args.run_id}] generating {args.count} documents...")
    jobs = [make_job(i, r, args.bad_rate) for i in range(args.count)]

    def upload(job: dict) -> None:
        key = f"incoming/{args.run_id}/{job['name']}"
        s3.put_object(Bucket=args.bucket, Key=key, Body=job["body"])
        job["job_id"] = f"{args.run_id}/{job['name'].rsplit('.', 1)[0]}"
        job["uploaded_at"] = time.time()
        del job["body"]

    t_start = time.time()
    with ThreadPoolExecutor(args.concurrency) as pool:
        list(pool.map(upload, jobs))
    upload_s = time.time() - t_start
    print(f"uploaded {len(jobs)} documents in {upload_s:.1f}s ({len(jobs) / upload_s:.1f}/s)")

    expected_ids = {j["job_id"] for j in jobs}
    done: set[str] = set()
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        results = {k[len("results/"):-5] for k in list_keys(s3, args.bucket, f"results/{args.run_id}/")}
        failed = {k[len("failed/"):-5] for k in list_keys(s3, args.bucket, f"failed/{args.run_id}/")}
        done = (results | failed) & expected_ids
        print(f"  {len(done)}/{len(jobs)} finished ({len(results)} results, {len(failed)} failed)", flush=True)
        if len(done) == len(jobs):
            break
        time.sleep(10)
    wall_s = time.time() - t_start

    def fetch(job: dict) -> dict:
        for prefix in ("results/", "failed/"):
            try:
                body = s3.get_object(Bucket=args.bucket, Key=f"{prefix}{job['job_id']}.json")["Body"].read()
                return {**job, "result": json.loads(body)}
            except s3.exceptions.NoSuchKey:
                continue
        return {**job, "result": None}

    with ThreadPoolExecutor(args.concurrency) as pool:
        rows = list(pool.map(fetch, jobs))

    statuses = Counter((row["result"] or {}).get("status", "missing") for row in rows)
    valid = [row for row in rows if row["kind"] == "valid" and row["result"] and row["result"]["status"] != "failed"]
    correct = sum(row["result"]["document_type"] == row["expected"] for row in valid)
    bad = [row for row in rows if row["kind"] != "valid"]
    bad_caught = sum(1 for row in bad if row["result"] and row["result"]["status"] == "failed")
    false_failures = [row for row in rows if row["kind"] == "valid" and row["result"] and row["result"]["status"] == "failed"]
    proc = [row["result"]["timings_ms"]["processing"] for row in valid]
    ocr = [row["result"]["timings_ms"]["ocr"] for row in valid]
    e2e = [row["result"]["timings_ms"]["end_to_end"] for row in valid if row["result"]["timings_ms"]["end_to_end"] is not None]
    retried = sum(1 for row in rows if row["result"] and row["result"].get("receive_count", 1) > 1)
    per_type = Counter((row["expected"], row["result"]["document_type"] == row["expected"]) for row in valid)

    summary = {
        "run_id": args.run_id,
        "finished_at": datetime.now(UTC).isoformat(),
        "documents": len(jobs),
        "bad_documents_injected": len(bad),
        "status_counts": dict(statuses),
        "missing_jobs": statuses.get("missing", 0),
        "classification_accuracy": round(correct / len(valid), 4) if valid else None,
        "bad_documents_flagged": f"{bad_caught}/{len(bad)}",
        "valid_documents_failed": len(false_failures),
        "jobs_retried": retried,
        "upload_seconds": round(upload_s, 1),
        "wall_clock_seconds": round(wall_s, 1),
        "throughput_docs_per_min": round(len(done) / wall_s * 60, 1),
        "processing_ms": {"p50": pct(proc, 50), "p90": pct(proc, 90), "p99": pct(proc, 99),
                          "mean": round(statistics.mean(proc), 1) if proc else None},
        "ocr_ms": {"p50": pct(ocr, 50), "p90": pct(ocr, 90)},
        "end_to_end_ms": {"p50": pct(e2e, 50), "p90": pct(e2e, 90), "p99": pct(e2e, 99)},
        "accuracy_by_type": {lbl: round(per_type[(lbl, True)] / max(per_type[(lbl, True)] + per_type[(lbl, False)], 1), 4)
                             for lbl in LABELS},
        "model_versions": dict(Counter(row["result"].get("model_version") for row in rows if row["result"])),
    }

    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"loadtest-{args.run_id}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md = [f"# Load test {args.run_id}", "", "| Metric | Value |", "|---|---|"]
    md += [f"| {k} | {json.dumps(v) if isinstance(v, dict) else v} |" for k, v in summary.items()]
    (out / f"loadtest-{args.run_id}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if summary["missing_jobs"]:
        print(f"\n{summary['missing_jobs']} jobs never finished: check the DLQ (scripts/dlq.py peek) and worker logs.")


if __name__ == "__main__":
    main()
