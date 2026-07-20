#!/usr/bin/env -S uv run --quiet --with boto3 python
import argparse
import json
from pathlib import Path

import boto3

STACK_NAME = "CoriginThroughputInfraStack"
LOCAL_RESULTS = Path(__file__).resolve().parents[1] / ".results"


def main() -> int:
    args = parse_args()
    directory = LOCAL_RESULTS / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    download_results(boto3.Session(), args.run_id, directory)
    print(json.dumps(aggregate_results(args.run_id, directory), sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    return parser.parse_args()


def download_results(session: boto3.Session, run_id: str, directory: Path) -> None:
    bucket = run_bucket(session)
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"results/{run_id}/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith(".json"):
                destination = directory / Path(key).name
                if not destination.exists():
                    s3.download_file(bucket, key, str(destination))


def run_bucket(session: boto3.Session) -> str:
    cloudformation = session.client("cloudformation")
    paginator = cloudformation.get_paginator("list_stack_resources")
    for page in paginator.paginate(StackName=STACK_NAME):
        for resource in page["StackResourceSummaries"]:
            if (
                resource["ResourceType"] == "AWS::S3::Bucket"
                and resource["LogicalResourceId"].startswith("RunBucket")
            ):
                return resource["PhysicalResourceId"]
    raise SystemExit("run bucket not found")


def aggregate_results(run_id: str, directory: Path) -> dict[str, object]:
    successes = 0
    failures = 0
    timeouts = 0
    overruns = 0
    process_seconds_samples: list[float] = []
    files = sorted(directory.glob("*.json"))
    if not files:
        raise SystemExit("no benchmark results found")
    for path in files:
        result = json.loads(path.read_text(encoding="utf-8"))
        successes += int(result["successes"])
        failures += int(result["failures"])
        timeouts += int(result["timeouts"])
        overruns += int(result.get("overruns", 0))
        process_seconds_samples.extend(
            float(value) for value in result["process_seconds_samples"]
        )
    processes = successes + failures
    if len(process_seconds_samples) != processes:
        raise SystemExit("process sample count does not match completed process count")
    process_seconds_samples.sort()
    process_seconds = sum(process_seconds_samples)
    mean = process_seconds / processes if processes else None
    return {
        "run_id": run_id,
        "clients": len(files),
        "successes": successes,
        "failures": failures,
        "timeouts": timeouts,
        "overruns": overruns,
        "processes": processes,
        "process_seconds": process_seconds,
        "mean_process_seconds": mean,
        "p50_process_seconds": percentile(process_seconds_samples, 0.50),
        "p90_process_seconds": percentile(process_seconds_samples, 0.90),
        "p95_process_seconds": percentile(process_seconds_samples, 0.95),
        "p99_process_seconds": percentile(process_seconds_samples, 0.99),
    }


def percentile(sorted_samples: list[float], fraction: float) -> float | None:
    if not sorted_samples:
        return None
    position = (len(sorted_samples) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_samples) - 1)
    remainder = position - lower
    return (
        sorted_samples[lower]
        + (sorted_samples[upper] - sorted_samples[lower]) * remainder
    )


if __name__ == "__main__":
    raise SystemExit(main())
