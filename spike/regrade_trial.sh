#!/bin/bash
# Re-grade one saved model.patch offline (keep-id bug recovery, 2026-10-13).
# Usage: regrade_trial.sh <task-dir> <trial-artifacts-dir>
#   task-dir:  eval/deep-swe/tasks/<task-name>   (has tests/Dockerfile)
#   trial-dir: jobs/<job>/<trial>/               (has artifacts/model.patch)
# Runs the task's own tests/test.sh + grader.py in the verifier image as
# container root (no keep-id), with the trial's verifier/ + artifacts/
# bind-mounted exactly as pier mounted them. Writes verifier/reward.json
# in place (backing up the old apply-failed one).
set -uo pipefail
TASK_DIR=$(eval ls -d "$1" 2>/dev/null | head -1)
TRIAL_DIR=$(eval ls -d "$2" 2>/dev/null | head -1)
[ -d "$TASK_DIR" ] || { echo "task dir not found: $1"; exit 1; }
[ -d "$TRIAL_DIR" ] || { echo "trial dir not found: $2"; exit 1; }
TASK_DIR=$(readlink -f "$TASK_DIR")
TRIAL_DIR=$(readlink -f "$TRIAL_DIR")
echo "regrading trial: $TRIAL_DIR (task: $TASK_DIR)"
TAG="psi-regrade-$(basename "$TASK_DIR")"
export DOCKER_BUILDKIT=0 COMPOSE_BAKE=false

[ -f "$TRIAL_DIR/artifacts/model.patch" ] || { echo "no model.patch in $TRIAL_DIR"; exit 1; }

# 1. build verifier image (cached after first use)
podman build -q -t "$TAG" -f "$TASK_DIR/tests/Dockerfile" "$TASK_DIR/tests/" >/dev/null || { echo "build failed"; exit 1; }

# 2. clean scratch dirs
mkdir -p "$TRIAL_DIR/verifier-regrade"
chmod 0777 "$TRIAL_DIR/verifier-regrade"

# 3. run test.sh as container root (NO keep-id) with artifacts mounted
podman run --rm \
  --network=none \
  -v "$TRIAL_DIR/artifacts:/logs/artifacts" \
  -v "$TRIAL_DIR/verifier-regrade:/logs/verifier" \
  "$TAG" \
  bash -c 'bash /tests/test.sh' >/dev/null 2>&1
RC=$?

# 4. read reward
if [ -f "$TRIAL_DIR/verifier-regrade/reward.json" ]; then
  REWARD=$(python3 -c "import json;d=json.load(open('$TRIAL_DIR/verifier-regrade/reward.json'));print(d.get('reward'),d.get('f2p_passed'),d.get('f2p_total'),d.get('p2p_passed'),d.get('p2p_total'))")
  echo "rc=$RC reward(f2p f2p_passed/total p2p_passed/total)=$REWARD  trial=$(basename "$TRIAL_DIR")"
  echo "$REWARD" > "$TRIAL_DIR/verifier-regrade/regrade-summary.txt"
else
  echo "rc=$RC NO reward.json produced — trial=$(basename "$TRIAL_DIR") (check $TRIAL_DIR/verifier-regrade/test-stdout.txt)"
fi
