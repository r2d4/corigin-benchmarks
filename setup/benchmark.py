#!/usr/bin/env -S uv run --quiet --with boto3 python
import argparse
import json
import os
import shutil
import shlex
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import boto3

INFRA_ENV = Path("/opt/corigin-throughput/env")
DEFAULT_WORKDIR = Path("/mnt/corigin-throughput")
DEFAULT_RESULT = Path(f"/tmp/corigin-benchmark-{socket.gethostname()}.json")
OVERRUN_GRACE_SECONDS = 5.0
PROGRESS_INTERVAL_SECONDS = 5.0


@dataclass
class Counts:
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    overruns: int = 0
    process_seconds_samples: list[float] = field(default_factory=list)

    def record(self, result: str, elapsed: float) -> None:
        if result == "ok":
            self.successes += 1
            self.process_seconds_samples.append(elapsed)
        elif result == "fail":
            self.failures += 1
            self.process_seconds_samples.append(elapsed)
        elif result == "overrun":
            self.overruns += 1
        else:
            self.timeouts += 1

    @property
    def processes(self) -> int:
        return self.successes + self.failures

    def as_json(self) -> dict[str, object]:
        process_seconds = sum(self.process_seconds_samples)
        mean = process_seconds / self.processes if self.processes else None
        return {
            "successes": self.successes,
            "failures": self.failures,
            "timeouts": self.timeouts,
            "overruns": self.overruns,
            "processes": self.processes,
            "process_seconds": process_seconds,
            "process_seconds_samples": self.process_seconds_samples,
            "mean_process_seconds": mean,
        }


def main() -> int:
    load_infra_env()
    args = parse_args()

    end_epoch = args.start_epoch + args.seconds
    run_dir = DEFAULT_WORKDIR / f"run-{int(args.start_epoch * 1000)}-{os.getpid()}"
    wait_until(args.start_epoch)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        counts = run_workers(
            args.command,
            args.env,
            run_dir,
            args.workers,
            end_epoch,
            args.run_id,
        )
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    result = {
        "host": socket.gethostname(),
        "run_id": args.run_id,
        "start_epoch": args.start_epoch,
        "end_epoch": end_epoch,
        "finished_epoch": time.time(),
        "seconds": args.seconds,
        "workers": args.workers,
        "command": args.command,
        **counts.as_json(),
    }
    write_result(DEFAULT_RESULT, result)
    upload_result(DEFAULT_RESULT, args.run_id)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start-epoch", type=float, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    env = os.environ.copy()
    for value in args.env:
        name, separator, env_value = value.partition("=")
        if not separator or not name:
            parser.error("--env must be NAME=VALUE")
        env[name] = env_value
    args.env = env
    if args.seconds <= 0:
        parser.error("--seconds must be greater than zero")
    if args.workers <= 0:
        parser.error("--workers must be greater than zero")
    if not args.command:
        parser.error("missing command after --")
    return args


def load_infra_env(path: Path = INFRA_ENV) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        if parts[0] == "export":
            parts = parts[1:]
        for part in parts:
            name, separator, value = part.partition("=")
            if separator and name:
                os.environ.setdefault(name, value)


def wait_until(epoch: float) -> None:
    while True:
        remaining = epoch - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


def run_workers(
    command: list[str],
    env: dict[str, str],
    run_dir: Path,
    workers: int,
    end_epoch: float,
    run_id: str,
) -> Counts:
    total = Counts()
    lock = Lock()
    bucket = run_bucket()
    s3 = boto3.client("s3")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(
                run_worker,
                command,
                env,
                run_dir,
                worker_id,
                end_epoch,
                total,
                lock,
            )
            for worker_id in range(workers)
        }
        while pending:
            completed, pending = wait(pending, timeout=PROGRESS_INTERVAL_SECONDS)
            for future in completed:
                future.result()
            if pending:
                with lock:
                    progress = total.as_json()
                    del progress["process_seconds_samples"]
                upload_progress(s3, bucket, run_id, progress)
    return total


def run_worker(
    command: list[str],
    env: dict[str, str],
    run_dir: Path,
    worker_id: int,
    end_epoch: float,
    counts: Counts,
    lock,
) -> None:
    attempt = 0
    while time.time() < end_epoch:
        attempt_dir = run_dir / f"{worker_id}-{attempt}"
        result, elapsed = run_once(
            command,
            worker_env(env, worker_id, attempt, attempt_dir),
            attempt_dir,
            end_epoch,
        )
        with lock:
            counts.record(result, elapsed)
        shutil.rmtree(attempt_dir, ignore_errors=True)
        attempt += 1


def worker_env(base: dict[str, str], worker_id: int, attempt: int, attempt_dir: Path) -> dict[str, str]:
    env = base.copy()
    env["BENCHMARK_WORKER"] = str(worker_id)
    env["BENCHMARK_ATTEMPT"] = str(attempt)
    env["BENCHMARK_DIR"] = str(attempt_dir)
    return env


def run_once(
    command: list[str],
    env: dict[str, str],
    attempt_dir: Path,
    end_epoch: float,
) -> tuple[str, float]:
    remaining = end_epoch - time.time()
    if remaining <= 0:
        return ("timeout", 0.0)
    argv = [part.replace("{dir}", str(attempt_dir)) for part in command]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=remaining + OVERRUN_GRACE_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return ("timeout", 0.0)
    elapsed = time.perf_counter() - started
    if time.time() > end_epoch:
        return ("overrun", 0.0)
    return ("ok" if completed.returncode == 0 else "fail", elapsed)


def write_result(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def upload_result(path: Path, run_id: str) -> None:
    bucket = run_bucket()
    key = f"results/{run_id}/{socket.gethostname()}.json"
    boto3.client("s3").upload_file(str(path), bucket, key)


def upload_progress(s3, bucket: str, run_id: str, counts: dict[str, object]) -> None:
    body = {
        "host": socket.gethostname(),
        "run_id": run_id,
        "updated_epoch": time.time(),
        **counts,
    }
    s3.put_object(
        Bucket=bucket,
        Key=f"progress/{run_id}/{socket.gethostname()}.json",
        Body=json.dumps(body, sort_keys=True) + "\n",
        ContentType="application/json",
    )


def run_bucket() -> str:
    bucket = os.environ.get("RUN_BUCKET")
    if not bucket:
        raise SystemExit("RUN_BUCKET is not set")
    return bucket


if __name__ == "__main__":
    sys.exit(main())
