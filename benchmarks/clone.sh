#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPO_URL=${1:?usage: clone.sh <repo-url> [seconds] [workers]}
DURATION=${2:-10}
WORKERS=${3:-1}
RUN_ID="clone-$(date -u +%Y%m%dT%H%M%SZ)"
START_EPOCH=$(($(date -u +%s) + 25))

"$ROOT/setup/for-all-machines.py" -- \
  /opt/corigin-benchmarks/benchmark.py \
  --run-id "$RUN_ID" \
  --start-epoch "$START_EPOCH" \
  --seconds "$DURATION" \
  --workers "$WORKERS" \
  --env HOME=/root \
  --env GIT_TERMINAL_PROMPT=0 \
  --env "CORIGIN_TOKEN=$(corigin repos token "${REPO_URL#corigin://}" --ttl-seconds 21600)" \
  -- git clone --quiet "$REPO_URL" "{dir}"

"$ROOT/setup/aggregate-results.py" "$RUN_ID"
