# How ψ was tested

Honest methodology, what the numbers mean, where testing stopped for budget
reasons, and a rough placement against the public model board.

## The paired design

Every eval run executes the **same 10 deep-swe tasks twice** — once bare and
once with ψ — same model, same task draw (`sample_seed 0`), same concurrency.
Trials are fully independent (fresh container, fresh repo copy, own hidden
tests per trial; the only shared things are the API key and this machine).
The two arms are then paired task-by-task: task difficulty cancels out and
whatever remains is the ψ effect. One trial is luck; ten paired trials are
the system.

## What a trial looks like

The agent gets a real repository (a bug or feature request from the
deep-swe set) and works autonomously — reading, editing, running tests —
until it reports done or a wall-clock limit. Then the task's hidden tests
grade the produced patch: `reward` (1 = all fail-to-pass tests now pass and
nothing broke), plus `f2p` (fail-to-pass: the tests that must newly pass)
and `p2p` (pass-to-pass: the tests that must keep passing).

## The verdict so far (B7/A7, glm-5.3-flash)

| metric | bare | **with ψ** |
|---|---|---|
| trials completed | 8 (3 crashed/timed out) | **10, zero exceptions** |
| tasks passed | 2 | **6** |
| fail-to-pass test rate | 53% | **89%** |
| pass-to-pass | 79% | **100%** |
| wall-clock | 4h 37m | **2h 24m** |

Paired per task: ψ wins 3 (three tasks where bare passed **zero** tests and
ψ passed *all* of them), loses 1, ties 4 — plus 2 wins where the bare agent
crashed outright. Statistical honesty: the sign test alone on 8 graded pairs
is p≈0.125 (not significant by itself); the strength is in the uniformity
(F2P higher on 8 of 10 tasks, P2P perfect, zero crashes) — and one replicate
cannot rule out run luck. **A repeat of the pair is the minimum for a
defensible claim; we stopped at one for budget reasons.**

Earlier attempts (5 invalidated runs across two days) were each killed by a
different plumbing bug — environment defaults, quota storms, a path leak
that silently loaded zero rules, a model-id the runtime rejected, a
file-ownership error in grading, one malformed rule that 400-rejected ~13%
of every interception call. Every bug is fixed and the final run had zero
infrastructure failures across 4,427 interceptions. That ladder is documented
in EVAL-RESULTS-B7A7.md — the debugging history is itself evidence of how
many ways an agent-harness integration can silently fail.

## Mechanism-level evidence (between score runs)

- **Interception fires live**: every mutating action in every ψ trial is
  judged; blocks carry ledger evidence; a loop breaker escalates instead of
  grinding. In the final run: repeat-gate blocks at p=0.90 in winning
  trials, a constraint rule hard-blocking twice in prometheus (bare 0/17 →
  ψ 17/17).
- **The self-attribution drill**: with `--thinking high`, a blocked
  correction delivered as first-person self-talk is adopted by the model as
  *its own reasoning* ("Nothing changed since the failure, so I should
  figure out why the command failed before doing anything else") — vs the
  same model under authority-voice framing ("the test harness is blocking
  me"), which complied without engaging. The mechanism is inert without
  thinking enabled (nothing to amend) — it is scored in the next arm
  (thinking=max), not in this run.

## Budget limits (why testing stopped where it did)

- **n=1 paired replicate** (the minimum informative read). A repeat costs
  ~$5–10 at flash rates; three replicates ≈ $15–30 would make the claim
  solid.
- **One task set** (10 of the 117-task deep-swe pool). Widening the sample
  or moving to a public benchmark costs proportionally.
- **One model** (glm-5.3-flash, ~$0.24-class). The interesting questions —
  does ψ's advantage grow or shrink with model capability, does it compose
  with extended thinking — need a second model tier and a thinking-on run.
- **No multi-seed averaging**, no pass@k.

## Rough board placement

Testing was on deep-swe (our task pool), so this does **not** map 1:1 onto
the public board's benchmark — treat it as an order-of-magnitude placement,
not a measured row:

- On our paired sample, bare glm-5.3-flash passed 2/10 tasks (20%);
  ψ-equipped glm-5.3-flash passed 6/10 (60%), with 89% of fail-to-pass tests
  passing and zero crashes.
- On the public board, glm-5.3-flash[max] sits at ~63% ($0.24/task) and the
  mid-tier GLM tier (glm-5.3[max], 69%, $3.99; grok-4.6, 67%, $3.45) marks
  the next band up.
- **If the delta transfers, ψ at flash pricing lands a $0.24-class model in
  the 67–69% band** — models costing 10–15× more per task. The caveat stack:
  different benchmark, single replicate, bare-side crashes inflating the
  gap, and our sample's difficulty is not their difficulty. The cheap
  confirm: run ψ against the public board's harness on its task list — that
  is the next-spend item after a replicate pair.
