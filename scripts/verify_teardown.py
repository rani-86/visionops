"""After `terraform destroy`, list anything still tagged Project=<project> so nothing keeps billing.

    python scripts/verify_teardown.py --project visionops --region ap-south-1
"""

import argparse
import sys

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="visionops")
    ap.add_argument("--region", default="ap-south-1")
    args = ap.parse_args()

    tagging = boto3.client("resourcegroupstaggingapi", region_name=args.region)
    leftovers = []
    for page in tagging.get_paginator("get_resources").paginate(
            TagFilters=[{"Key": "Project", "Values": [args.project]}]):
        leftovers += [r["ResourceARN"] for r in page["ResourceTagMappingList"]]

    # Untagged things that are easy to forget.
    logs = boto3.client("logs", region_name=args.region)
    leftovers += [g["arn"] for g in logs.describe_log_groups(logGroupNamePrefix=f"/ecs/{args.project}")["logGroups"]]
    ecs = boto3.client("ecs", region_name=args.region)
    families = ecs.list_task_definitions(familyPrefix=f"{args.project}-worker", status="ACTIVE")["taskDefinitionArns"]

    if leftovers:
        print("Resources still present (may take a few minutes to disappear from the tagging API):")
        for arn in sorted(set(leftovers)):
            print("  ", arn)
    if families:
        print(f"{len(families)} ACTIVE task definition revisions (free, but you can deregister them):")
        for arn in families:
            print("  ", arn)
    if not leftovers:
        print("No billable VisionOps resources found.")
    sys.exit(1 if leftovers else 0)


if __name__ == "__main__":
    main()
