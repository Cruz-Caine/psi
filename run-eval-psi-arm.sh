#!/usr/bin/env bash
# Run the WITH-psi arm (the treatment group) — 10 deep-swe trials, flash model.
# Usage:  bash run-eval-psi-arm.sh          (from anywhere)
# Watch:  ls jobs/psi-eval-B7-psi-full/*/verifier/reward.json 2>/dev/null | wc -l
# Grades: jobs/psi-eval-B7-psi-full/<task>/verifier/reward.json   (reward 0 or 1)
set -euo pipefail
cd "$(dirname "$0")"

# podman socket must be up (docker-compose talks to it; no systemd here):
ss -lx 2>/dev/null | grep -q podman.sock || {
  echo "podman socket not running — starting it detached:"
  setsid nohup podman system service unix:/run/user/1000/podman/podman.sock --time=0 < /dev/null > /tmp/podman-service.log 2>&1 &
  sleep 3
}

set -a; . ./.env; set +a
export PSI_ENABLED=1
export PSI_SRC="$PWD/src"
export DOCKER_BUILDKIT=0 COMPOSE_BAKE=false

pier run -c eval/psi-eval-B6-psi-full.json --job-name psi-eval-B7-psi-full
echo "=== PSI ARM COMPLETE ==="
ls jobs/psi-eval-B7-psi-full/*/verifier/reward.json 2>/dev/null | wc -l