"""Inspect and redrive the dead-letter queue.

    python scripts/dlq.py peek    --dlq-url <url>            # show up to 10 messages (they stay in the DLQ)
    python scripts/dlq.py redrive --dlq-arn <arn>            # move every message back to the jobs queue
    python scripts/dlq.py status  --task-handle <handle>     # progress of a redrive
"""

from __future__ import annotations

import argparse
import json

import boto3


def peek(sqs, url: str) -> None:
    resp = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=2, VisibilityTimeout=0,
                               MessageSystemAttributeNames=["ApproximateReceiveCount", "SentTimestamp"])
    msgs = resp.get("Messages", [])
    if not msgs:
        print("DLQ is empty")
        return
    for m in msgs:
        body = json.loads(m["Body"])
        keys = [rec["s3"]["object"]["key"] for rec in body.get("Records", [])]
        print(f"{m['MessageId']}  receives={m['Attributes']['ApproximateReceiveCount']}  keys={keys}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("peek")
    p.add_argument("--dlq-url", required=True)
    r = sub.add_parser("redrive")
    r.add_argument("--dlq-arn", required=True)
    r.add_argument("--rate", type=int, default=50, help="max messages per second")
    s = sub.add_parser("status")
    s.add_argument("--dlq-arn", required=True)
    args = ap.parse_args()

    sqs = boto3.client("sqs")
    if args.cmd == "peek":
        peek(sqs, args.dlq_url)
    elif args.cmd == "redrive":
        # No DestinationArn = back to the original source queue.
        resp = sqs.start_message_move_task(SourceArn=args.dlq_arn, MaxNumberOfMessagesPerSecond=args.rate)
        print(f"redrive started, task handle: {resp['TaskHandle']}")
    else:
        for task in sqs.list_message_move_tasks(SourceArn=args.dlq_arn, MaxResults=5)["Results"]:
            print(json.dumps(task, default=str))


if __name__ == "__main__":
    main()
