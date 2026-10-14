# This harness is not meant for real work it is just a tech demo/proof of concept. DO NOT TRUST THIS WITH YOUR WORK

# ψ (psi) — a rule-interception layer for coding agents

**ψ is a fork of [tau](https://github.com/huggingface/tau) (the minimalist
Pi-style coding-agent harness) with a rule-interception layer built in.**
It is not a plugin and not a wrapper: the agent runtime and the rule layer
ship as one package (`psi-ai`), under the `psi` command.

Before an agent executes a mutating action (edit, write, bash), ψ passes the
proposed action through [Jev](https://docs.typesafe.ai) (TypeSafe's System One
decision model — typed yes/no questions with calibrated probabilities, no text
generation) against a library of rules. A deterministic policy layer applies
thresholds:

- **confident violation** → the action is stopped, and the correction is
  delivered as the agent's own reasoning (first-person, no authority framing —
  see *self-attribution* in HOW-IT-WAS-TESTED.md);
- **uncertain** → flagged with a remedy;
- a **ledger** records every action and outcome so repetition and drift are
  caught (the repeat gate blocks re-running a failed approach); a **fact
  store** distills durable facts from the session and recalls them into
  later decisions.

## Why

Coding agents fail in patterned ways — re-running failed approaches,
swallowing errors, rewriting tools' config files in place instead of using
their documented mechanisms, abandoning tasks without acting. ψ encodes those
failure patterns as rules and catches them at execution time. The rule
library ships as a package (`clean-code@0.0.1`, 17 rules); users add their
own rules (`/rule add <text>` in a session) and definitions
(`[[term]]` markers expand from a tiered glossary: package < user < project).

## Results

One controlled paired run (10 deep-swe tasks, same model, same draw, one arm
with ψ and one without):

| metric | bare | **with ψ** |
|---|---|---|
| trials completed | 8 (3 crashed/timed out) | **10, zero exceptions** |
| tasks passed | 2 | **6** |
| fail-to-pass test rate | 53% | **89%** |
| pass-to-pass | 79% | **100%** |
| Jev infrastructure failures | n/a | **0 in 4,427 interception records** |

See HOW-IT-WAS-TESTED.md for the full methodology, per-task table, defect
history, and honest caveats (single replicate; testing was budget-limited).

## Install

Requirements: Python ≥ 3.12, a [Jev](https://docs.typesafe.ai) API key
(public; also reachable through OpenRouter), podman or docker for the
eval harness (optional).

```bash
git clone <this repo>
cd psi

# the agent runtime (psi's tau fork — includes the rule layer):
pip install vendor/dist/psi_ai-*.whl        # or: pip install ./vendor/tau
# (to rebuild the wheel from source: pip wheel --no-deps --no-build-isolation ./vendor/tau)

export OPENROUTER_API_KEY=sk-or-...         # or TYPESAFE_API_KEY

# a quick feel: the agent with the rule layer live
psi --provider openrouter --model z-ai/glm-5.3-flash -p "explain this repo"
```

Configuration lives in `psi.toml` (project dir or `~/.psi/psi.toml`):

```toml
[provider]
backend = "openrouter"

[rules]
packages = ["clean-code@0.0.1"]

[clarify]
autonomy = "proceed-flagged"   # autonomous runs: clarify becomes a flag
```

`PSI_ENABLED=0` disables the rule layer entirely (plain tau behavior).

## The benchmark harness

`psi_eval/` contains the eval adapter (pier/deep-swe via
[harbor](https://github.com/laude-institute/harbor)). To reproduce the paired
runs: `bash run-eval-psi-arm.sh` and `bash run-eval-bare-arm.sh` (see the
scripts for details). The deep-swe task pool itself is **not** redistributed
in this repo — fetch it separately and place it under `eval/deep-swe/tasks/`.

## Layout

```
vendor/tau/         the psi fork of tau (psi_coding/, psi_agent/, psi_ai/, psi/)
                    — the rule layer lives at src/psi, registered as a
                    built-in extension of the runtime
psi_eval/           benchmark adapter (pier/harbor) + launch scripts
spike/              fire drills, sweep tooling, regrade harness
tests/              78 tests (core, chains, glossary, voice, replay)
EVAL-RESULTS-*.md   paired-run verdicts, per task
HOW-IT-WAS-TESTED.md  methodology + limitations + model-board placement
```

## Status

Research preview. The rule layer and the fork are working code (78 tests;
two graded paired runs); the API surface will move. Issues and rule
contributions welcome — the highest-value contributions are new failure
patterns as rules.

## License

GPL-3.0 (see LICENSE). The vendored tau fork retains its MIT license
(vendor/tau/LICENSE) — ψ's changes to it are distributed under GPL-3.0 as
part of this combined work.
