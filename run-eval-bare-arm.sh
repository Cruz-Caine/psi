#!/usr/bin/env bash
# Run the BARE arm (the control group — same 10 tasks, same model, NO psi).
# Usage:  bash run-eval-bare-arm.sh         (from anywhere)
# Watch:  ls jobs/psi-eval-A7-bare-flash/*/verifier/reward.json 2>/dev/null | wc -l
# Grades: jobs/psi-eval-A7-bare-flash/<task>/verifier/reward.json
set -euo pipefail
cd "$(dirname "$0")"

ss -lx 2>/dev/null | grep -q podman.sock || {
  echo "podman socket not running — starting it detached:"
  setsid nohup podman system service unix:/run/user/1000/podman/podman.sock --time=0 < /dev/null > /tmp/podman-service.log 2>&1 &
  sleep 3
}

set -a; . ./.env; set +a
export PSI_ENABLED=0
export PSI_SRC="$PWD/src"
export DOCKER_BUILDKIT=0 COMPOSE_BAKE=false

pier run -c eval/psi-eval-A6-bare-flash.json --job-name psi-eval-A7-bare-flash
echo "=== BARE ARM COMPLETE ==="
ls jobs/psi-eval-A7-bare-flash/*/verifier/reward.json 2>/dev/null | wc -l