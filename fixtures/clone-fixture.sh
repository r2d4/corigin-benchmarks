#!/bin/bash
set -euo pipefail

app=$(mktemp -d /tmp/corigin-next-starter.XXXXXX)/next-starter

npx --yes create-next-app@16.2.10 "$app" --yes --skip-install --use-npm >&2

repo_url=$(corigin repos create "${1:-bench-next-starter-$(date -u +%Y%m%dT%H%M%SZ)}")
git -C "$app" push --quiet "$repo_url" HEAD:main

printf "export REPO_URL=%q\n" "$repo_url"
