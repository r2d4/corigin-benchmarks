#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BENCHMARK_B64=$(base64 <"$ROOT/setup/benchmark.py" | tr -d "\n")
RUN_ID="setup-$(date -u +%Y%m%dT%H%M%SZ)"
START_EPOCH=$(($(date -u +%s) + 10))

"$ROOT/setup/for-all-machines.py" -- bash -lc "$(cat <<SH
set -euo pipefail

dnf install -y git chrony nodejs npm python3
curl -LsSf https://astral.sh/uv/install.sh | sh
ln -sf /root/.local/bin/uv /usr/local/bin/uv
npm install -g @corigin/cli@0.1.18
mkdir -p /root/.config/corigin
cat >/root/.config/corigin/config.toml <<'CONFIG'
api_url = "https://api.corigin.dev"
git_data_api_url = "https://git-bench.corigin.dev"
CONFIG
systemctl enable --now chronyd

mkdir -p /opt/corigin-benchmarks /mnt/corigin-throughput
base64 -d >/opt/corigin-benchmarks/benchmark.py <<'BENCHMARK'
$BENCHMARK_B64
BENCHMARK
chmod +x /opt/corigin-benchmarks/benchmark.py

mountpoint -q /mnt/corigin-throughput || mount -t tmpfs -o size=80% -o mode=1777 tmpfs /mnt/corigin-throughput

chronyc waitsync 60 1 100 1
chronyc tracking

/opt/corigin-benchmarks/benchmark.py \\
  --run-id "$RUN_ID" \\
  --start-epoch "$START_EPOCH" \\
  --seconds 1 \\
  --workers 1 \\
  -- true "{dir}"
SH
)"

"$ROOT/setup/aggregate-results.py" "$RUN_ID"
