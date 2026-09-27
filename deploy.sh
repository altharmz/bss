#!/usr/bin/env bash
# Deploys the committed state of main. Refuses to deploy uncommitted work.
set -euo pipefail
APP="${1:-demo}"

if [ -n "$(git status --porcelain)" ]; then
  echo "FAIL: working tree is dirty. Commit your changes before deploying." >&2
  git status --short >&2
  exit 1
fi

git push origin main
ssh vps-test "cd ~/src/vps-lab && git fetch --quiet origin && git reset --hard origin/main"
ssh vps-test "cd ~/src/vps-lab/app/${APP} && docker compose up -d"
echo "deployed: ${APP} @ $(git rev-parse --short HEAD)"
