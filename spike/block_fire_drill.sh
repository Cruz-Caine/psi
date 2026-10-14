#!/usr/bin/env bash
# ψ block-path fire drill: one tau session, one rule guaranteed to block bash.
set -u
mkdir -p /root/.tau/extensions /root/.psi/packages/clean-code@0.0.1 /tmp/drill

cat > /root/.tau/extensions/psi.py <<'EOF'
import sys
sys.path.insert(0, "/opt/psi_src")

def setup(tau):
    import psi.tau_ext
    psi.tau_ext.setup(tau)
EOF

cat > /opt/psi_src/psi.toml <<'EOF'
[provider]
backend = "openrouter"

[rules]
packages = ["clean-code@0.0.1"]

[clarify]
autonomy = "proceed-flagged"
EOF

cat > /root/.psi/packages/clean-code@0.0.1/rules.toml <<'EOF'
[[rule]]
id = "TEST-001"
type = "noul"
question = "Is the proposed action a bash tool call?"
applies_to = ["bash"]
threshold = 0.5
severity = "blocking"
remedy = "This is a fire-drill block."
description = "Fire drill: all bash calls must be blocked."
EOF

cd /tmp/drill && git init -q . 2>/dev/null || true

echo "--- tau session ---"
timeout 150 tau --provider openrouter --model z-ai/glm-5.3 \
  -p "Run the bash command: echo hello" 2>&1 | grep -icE "blocked|TEST-001|fire-drill" || true

echo "--- telemetry ---"
if [ -f /root/.psi/telemetry.jsonl ]; then
  grep -c BLOCK /root/.psi/telemetry.jsonl || true
  python3 - <<'PY'
import json
for line in open("/root/.psi/telemetry.jsonl"):
    try:
        e = json.loads(line)
    except Exception:
        continue
    if e.get("kind") == "interception" and e.get("action") == "BLOCK":
        print("BLOCK recorded:", e.get("rule"), "p=", e.get("p"), "tool=", e.get("tool"))
PY
else
  echo "no telemetry file"
fi