#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPO_URL=${1:?usage: push.sh <repo-url> [seconds] [workers]}
DURATION=${2:-10}
WORKERS=${3:-1}
RUN_ID="push-$(date -u +%Y%m%dT%H%M%SZ)"

"$ROOT/setup/for-all-machines.py" -- bash -lc '
set -euo pipefail
rm -rf "/mnt/corigin-throughput/push/$1"
mkdir -p "/mnt/corigin-throughput/push/$1"
for ((worker = 0; worker < $2; worker++)); do
  git init --quiet "/mnt/corigin-throughput/push/$1/$worker"
  git -C "/mnt/corigin-throughput/push/$1/$worker" checkout --quiet -b main
  git -C "/mnt/corigin-throughput/push/$1/$worker" remote add origin "$3"
done
' _ "$RUN_ID" "$WORKERS" "$REPO_URL"

START_EPOCH=$(($(date -u +%s) + 25))

"$ROOT/setup/for-all-machines.py" -- \
  /opt/corigin-benchmarks/benchmark.py \
  --run-id "$RUN_ID" \
  --start-epoch "$START_EPOCH" \
  --seconds "$DURATION" \
  --workers "$WORKERS" \
  --env HOME=/root \
  --env GIT_TERMINAL_PROMPT=0 \
  --env "RUN_ID=$RUN_ID" \
  --env GIT_AUTHOR_NAME=corigin-benchmark \
  --env GIT_AUTHOR_EMAIL=benchmark@corigin.local \
  --env GIT_COMMITTER_NAME=corigin-benchmark \
  --env GIT_COMMITTER_EMAIL=benchmark@corigin.local \
  --env "CORIGIN_TOKEN=$(corigin repos token "${REPO_URL#corigin://}" --access write --ttl-seconds 21600)" \
  --env "REPO_URL=$REPO_URL" \
  -- bash -lc '
set -euo pipefail
repo="/mnt/corigin-throughput/push/$RUN_ID/$BENCHMARK_WORKER"
branch="refs/heads/bench/$RUN_ID/$(hostname)-worker-$BENCHMARK_WORKER"
prepare_commit() {
  count=$((RANDOM % 10 + 1))
  for ((i = 0; i < count; i++)); do
    dd if=/dev/urandom bs=2048 count=1 status=none > "$repo/file-$i.txt"
  done
  git -C "$repo" add .
  git -C "$repo" commit --quiet -m benchmark
}
if ! git -C "$repo" rev-parse --verify HEAD >/dev/null 2>&1; then
  prepare_commit
fi
git -C "$repo" push --quiet origin "HEAD:$branch"
prepare_commit
'

"$ROOT/setup/aggregate-results.py" "$RUN_ID"
