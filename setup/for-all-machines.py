#!/usr/bin/env -S uv run --quiet --with boto3 python
import argparse
import json
import shlex
import sys
import time

import boto3

TARGETS = [{"Key": "tag:CoriginBenchmark", "Values": ["throughput"]}]
TIMEOUT_SECONDS = 900


def main() -> int:
    args = parse_args()
    ssm = boto3.client("ssm")
    command_id = send_command(ssm, args)
    print(f"ssm_command_id={command_id}", file=sys.stderr, flush=True)
    wait_for_command(ssm, command_id)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing command after --")
    return args


def send_command(ssm, args: argparse.Namespace) -> str:
    response = ssm.send_command(
        DocumentName="AWS-RunShellScript",
        Comment="corigin-benchmark",
        Targets=TARGETS,
        MaxConcurrency="100%",
        Parameters={
            "commands": [remote_script(args)],
            "executionTimeout": [str(TIMEOUT_SECONDS)],
        },
    )
    return response["Command"]["CommandId"]


def remote_script(args: argparse.Namespace) -> str:
    lines = [
        "set -euo pipefail",
    ]
    lines.append(" ".join(shlex.quote(part) for part in args.command))
    return "\n".join(lines)


def wait_for_command(ssm, command_id: str) -> None:
    while True:
        response = ssm.list_commands(CommandId=command_id)
        if not response["Commands"]:
            time.sleep(2)
            continue
        status = response["Commands"][0]["Status"]
        if status == "Success":
            if invocation_count(ssm, command_id) == 0:
                raise SystemExit("no machines received the command")
            return
        if status in {"Failed", "Cancelled", "TimedOut", "Cancelling"}:
            print_failures(ssm, command_id)
            raise SystemExit(1)
        time.sleep(2)


def invocation_count(ssm, command_id: str) -> int:
    count = 0
    response = ssm.list_command_invocations(CommandId=command_id)
    while True:
        count += len(response["CommandInvocations"])
        next_token = response.get("NextToken")
        if next_token is None:
            return count
        response = ssm.list_command_invocations(CommandId=command_id, NextToken=next_token)


def print_failures(ssm, command_id: str) -> None:
    response = ssm.list_command_invocations(CommandId=command_id, Details=True)
    for invocation in response["CommandInvocations"]:
        sys.stderr.write(json.dumps(invocation, default=str, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
