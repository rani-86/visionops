"""CloudWatch metrics via the Embedded Metric Format (EMF).

The worker prints one JSON line per job to stdout. The ECS ``awslogs`` driver ships it to
CloudWatch Logs, which extracts the metrics automatically. This needs no
``cloudwatch:PutMetricData`` permission and no extra API calls, and the same line stays
queryable in Logs Insights (job id, model version, document type).
"""

from __future__ import annotations

import json
import sys
import time
from typing import TextIO

NAMESPACE = "VisionOps"

UNITS = {
    "ProcessingLatency": "Milliseconds",
    "EndToEndLatency": "Milliseconds",
    "QueueWait": "Milliseconds",
    "OcrLatency": "Milliseconds",
    "InferenceLatency": "Milliseconds",
    "ModelConfidence": "None",
    "JobsSucceeded": "Count",
    "JobsNeedsReview": "Count",
    "JobsFailed": "Count",
    "JobErrors": "Count",
    "DuplicateJobs": "Count",
}


class MetricsEmitter:
    def __init__(self, service: str, stream: TextIO | None = None):
        self.service = service
        self.stream = stream or sys.stdout

    def emit(self, metrics: dict[str, float], **properties) -> dict:
        unknown = set(metrics) - set(UNITS)
        if unknown:
            raise ValueError(f"Unknown metric names: {sorted(unknown)}")
        record = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": NAMESPACE,
                    "Dimensions": [["Service"]],
                    "Metrics": [{"Name": k, "Unit": UNITS[k]} for k in metrics],
                }],
            },
            "Service": self.service,
            "event": "job_metrics",
            **properties,
            **metrics,
        }
        self.stream.write(json.dumps(record, default=str) + "\n")
        self.stream.flush()
        return record
