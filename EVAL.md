# EVAL — does ψ improve the base model's thinking?

**Motivation.** Leaderboard context (Terminal-Bench, Aug 2026): GLM-5.3-Flash on Claude Code
scores **35.8% ± 3.5%**; `glm-5.3-flash[max]` (thinking=max) scores **63% ± 4%** at ~$0.24/task
(~73k tok avg). Question: can **harness-level thinking** — ψ's constraint adherence, persistent
artifacts, anti-thrash, recall — close that gap on the *base* model, without paying for
thinking tokens?

**Hypothesis (falsifiable):** ψ improves pass@1 on long-horizon tasks by
(a) keeping user/task constraints enforced (fact pack, checked every response),
(b) externalizing state that survives compaction (ledger, fact store),
(c) preventing thrash (loop breaker, declared deviations),
(d) resurfacing stated requirements via recall.
Predicted concentration: gains land on **long-horizon / debugging-heavy** tasks; short tasks
with crisp specs have no drift to prevent. Falsified if arm B ≤ arm A within paired noise.

## Design — 2×2 factorial, paired

**Minimal eval first (round 20d, user call): {A, B} × deep-swe only** — control vs ψ on the
long-horizon, original-task dataset (also sidesteps public-benchmark contamination). ~10 tasks
× 2 arms × 1 seed ≈ 20 rollouts, est. $5–15 caching-aware. Arms C/D (thinking=max) deferred
until this read is in; if the ψ effect exists here, the full 2×2 earns its budget.

Runner: **harbor** (installed). Custom agents supported natively (`--agent module.path:ClassName`);
built-in `pi` adapter is the reference config surface.
Datasets (full factorial later):
- **deep-swe** (datacurve, in harbor's registry) — primary for the minimal run: original,
  long-horizon engineering tasks; the regime where ψ's prediction says gains concentrate.
- **terminal-bench@2.0** — breadth + leaderboard-comparability anchor (35.8% / 63% rows);
  joins the full factorial when it's earned.

| | thinking default | thinking max |
| - | - | - |
| **ψ off** | **A** — tau bare (baseline, ≈ leaderboard 35.8% territory) | **C** — tau bare, thinking=max (raw-scaling control, ≈ 63% territory) |
| **ψ on** | **B** — tau + ψ | **D** — tau + ψ, thinking=max (the two-system hero config) |

Model in all arms: `openrouter/z-ai/glm-5.3-flash`. Jev backend: openrouter (same key).
2 seeds per task, `--n-concurrent 4`, local Docker.

**Analysis:** 2×2 factorial — main effect of ψ (B−A and D−C), main effect of thinking
(C−A and D−B), and the **interaction** (D−C−B+A): does ψ compose with max thinking, or
does interception noise interfere with a model that already thinks hard? That interaction
is an open empirical question the naive A/B would have hidden.
**Ambition framing:** arm D tests whether the two-system approach (S2 LLM + S1 gate +
artifacts) lets a cheap model approach what more expensive models score.

**Metrics:** pass@1 per arm (paired delta, Wilson CIs) · factorial effects · process
telemetry from ψ (blocks/advisories/recalls/loop-breaker escalations per task — the
*mechanism* evidence) · overhead (tokens, $, wall-clock; Jev latency p95).

## Config deltas for eval arms

- `clarify.autonomy = proceed-flagged` — benchmarks are autonomous; parking on ambiguity
  would fail tasks. ψ must flag, not stall.
- Ruleset: lean hygiene set — CC-001 (secrets) and CC-050 (user constraints) stay blocking;
  clean-code style rules ride advisory-only (benchmarks don't judge style).
- Fact pack: auto-generated from the task instruction (spec mode; deterministic check items
  where the task states them explicitly).
- `on_outage = quarantine` — benchmark resilience: Jev blips must not pause a 30-minute task.

## L0 — in-chat micro-eval (before L1, no infra)

### L0a result (done — via OpenRouter backend)

PROMISING. 16 cases (8 violating / 8 clean, pinned-stack + no-duplication rules):
mean(violating) **0.914**, mean(clean) **0.122**, separation **0.791**, overlap **0/16**.
Weakest violating case (dup+2, 0.79) lands in the gray band → advise, not block — band
design validated. Strongest clean case (dup-4, 0.30) involves a misimport — Jev's mild
suspicion was correct. Bet #1 survives; the gate architecture has calibration footing.
Full calibration (threshold tuning) still comes from the real run's telemetry.

Scripted multi-turn transcript: requirement stated early → drift mid-way. Two conditions on
identical transcripts: ψ-on (fact-pack item enforced at response review; recall gate fires)
vs ψ-off. Measures the **cross-reference mechanism** directly — does the system surface the
user's original words and correct course? Cost: ~$1. This is dry-run case #7 operationalized.

## L1 — harbor A/B (the real measurement)

Adapter: `psi_eval/harbor_agent.py` implementing harbor's custom-agent interface
(executor = tau print mode with the ψ extension loaded, inside the trial container).
Build next session against harbor's agent source; `harbor agent schema pi` is the config
reference. Then: `harbor run -d terminal-bench@2.0 -m openrouter/z-ai/glm-5.3-flash -a psi_eval.harbor_agent:TauPsiAgent`.

## Cost estimate (caching-aware, minimal-first)

**Minimal run: {A, B} × deep-swe × ~10 tasks ≈ 20 rollouts, est. $5–15** (long-horizon tasks
have more input *and* output even cached). Agent loops are the prompt-caching workload —
append-only prefix, ~90% of later-turn input tokens are cache reads at ~0.1× price; output
tokens are never cached. Full 2×2 (both datasets, 4 arms) deferred: TB leg ≈ $25–40,
deep-swe leg gated on observed minimal-run costs. L0 doubles as the cost-calibration probe —
OpenRouter usage exposes cache metrics. Budget gate: minimal run only, then reassess.

## What this eval does NOT measure

Adoption, rule-library quality at scale, stdlib packaging — product questions, later.
Telemetry interpretation (which mechanism moved the number) is manual for this round;
automated attribution is v0.1.