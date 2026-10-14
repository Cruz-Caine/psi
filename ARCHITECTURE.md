# ψ (Psi) — Architecture

> Working name: **ψ (Psi)** — Ψ, the wavefunction: the state before collapse. ψ intercepts the
> agent's intent in the moment before it becomes action. τ (tau) is the body; ψ is the mind.
> Rationale and decision history live in VISION.md; this file is the implementation source of truth.
> Diagram style follows tau's documentation convention (text diagrams, arrows as data flow).
>
> This document is written **compile-ready**: every component lists its inputs and outputs.
> It doubles as the input spec for the clean-room self-test (§14): a second system reading
> only this file should be able to reimplement ψ.

---

## 1. System context

```
                            ┌───────────────────────────┐
          user ────────────▶│      tau TUI / CLI        │
                            └─────────────┬─────────────┘
                                          │ events (agent.*, jev.*)
                                          ▼
                            ┌───────────────────────────┐
                            │        tau_agent          │
                            │  the loop: response →     │
                            │  [ψ intercept] → tools    │
                            └──────┬─────────────┬──────┘
                                   │             │
                    ┌──────────────▼───────┐  ┌──▼──────────────────┐
                    │     ψ interceptor    │  │  model provider     │
                    │   (this document)    │  │  (System Two LLM)   │
                    └──┬───────┬───────┬───┘  └─────────────────────┘
                       │       │       │
        ┌──────────────▼─┐  ┌──▼──────────────▼──┐  ┌───────────────────┐
        │   artifacts    │  │  Jev API (S1)      │  │  telemetry        │
        │   .psi/,       │  │  POST /v1/         │  │  session JSONL    │
        │   ~/.psi/      │  │  systemone         │  │  ~/.tau/sessions/ │
        └────────────────┘  └────────────────────┘  └───────────────────┘
```

ψ is a layer inside tau's loop, not a separate process. Everything to the left of the loop
(artifacts, rules) lives on disk; Jev is the only remote dependency.

## 2. Subsystems and components

Every component: name, one-line job, IN, OUT.

### Subsystem A — Intercept (hot path, runs per model response)

| # | Component | Job | IN | OUT |
| - | - | - | - | - |
| A1 | `bundle-capture` | holds the response bundle | model response (stream) | `Bundle{thinking[], tool_calls[]}` |
| A2 | `state-builder` | assembles Jev state | session fields, artifact digests, probe outputs | redacted `StateObject` (budget-capped) |
| A3 | `gate-filter` | decides which rules apply | `Bundle`, resolved `RuleSet` | applicable rules + sys probes |
| A4 | `probe-runner` | gathers rule evidence | rules carrying `probe:`, repo | evidence blobs (timeout, size-capped) |
| A5 | `jev-client` | the only remote call | `StateObject`, `QuestionSet` | `Answers{}` (or outage signal) — backend-pluggable: `typesafe` \| `openrouter`, same `/v1/systemone` schema (`psi.toml [provider]`) |
| A6 | `policy-engine` | numbers → verdicts | `Answers`, `RuleSet` | `Verdicts[]` |
| A7 | `action-dispatcher` | verdicts → outcomes | `Verdicts`, `Bundle` | per-tool-call outcome + advisory flush + events |

### Subsystem B — Artifacts (cold path)

| # | Component | Job | IN | OUT |
| - | - | - | - | - |
| B1 | `fact-pack-manager` | task spec → checkable items | user request (+clarify round) | `.psi/factpack.yaml` (spec \| ledger mode) |
| B2 | `ledger-manager` | experiment state | runs, transcriptions | `.psi/ledger.yaml` (hypotheses/findings) |
| B3 | `verification-runner` | mechanical done-check | fact pack, repo | verification report |
| B4 | `fact-store` | project knowledge | distiller writes (versioned) | entries + digest → state |
| B5 | `distiller` | background distillation | event stream (checkpoint triggers) | fact-store updates, rule candidates |
| B6 | `session-indexer` | episodic finding aid | session JSONL (on close) | embedding index `~/.psi/index/` |
| B7 | `recall-gate` | Jev-filtered recall | `probe: recall <topic>` query | verified evidence chunks |

### Subsystem C — Rules & config

| # | Component | Job | IN | OUT |
| - | - | - | - | - |
| C1 | `rule-loader` | load + resolve tiers | `~/.psi/packages/`, `~/.psi/rules.yaml`, `.psi/rules/` | resolved `RuleSet` (trust-gated, stdlib sigchecked) |
| C2 | `config-loader` | read ψ config | `psi.toml` | thresholds, `on_outage`, budgets, extensions, quiet mode |
| C3 | `extension-registry` | register extensions | `psi.toml [extensions.*]` | generators / state sources / probes (separate processes) |

### Subsystem D — Infrastructure

| # | Component | Job | IN | OUT |
| - | - | - | - | - |
| D1 | `telemetry-sink` | record everything | interception records | session JSONL + override/compliance reports |
| D2 | `replay-journal` | outage quarantine | bundles during outage | replay queue → post-recovery re-check |
| D3 | `event-emitter` | TUI visibility | verdicts, policy outcomes | `jev.blocked` / `jev.advisory` / `jev.clarify` / `jev.degraded` |

## 3. The main loop

```
 model response (thinking + tool calls)
        │
        ▼
 ┌──────────────┐ Bundle{thinking[], tool_calls[]}
 │  A1 capture  │ ─ graft point: after response assembly,
 └──────┬───────┘   before tool dispatch (tau surface: spike §14)
        ▼
 ┌──────────────┐ IN: digests + recent_context + probe outputs
 │  A2 state    │ PROC: redaction filter, budget caps
 │   builder    │ OUT: StateObject{user_request, plan_excerpt,
 └──────┬───────┘      fact_pack_digest, fact_store_digest,
        │              recent_context[], probe_outputs{}}
        ▼
 ┌──────────────┐ IN: Bundle + RuleSet
 │  A3 gates    │ OUT: applicable rules (applies_to + paths +
 │              │      language tags — deterministic, free)
 └──────┬───────┘
        ▼
 ┌──────────────┐ IN: rules carrying probe:
 │  A4 probes   │ OUT: evidence blobs → injected into state
 └──────┬───────┘      (rules needing evidence w/o probe: → advisory)
        ▼
 ┌──────────────┐ one POST /v1/systemone
 │  A5 jev      │ questions = applicable rules + fact-pack items
 │   client     │           + sys.ambiguity + sys.novelty
 └──────┬───────┘ OUT: Answers{}  (noul p IS the answer; choice/score carry confidence)
        ▼
 ┌──────────────┐ decision table (§5) + conflict resolution
 │  A6 policy   │ + loop breaker (≥3 related blocks → escalate)
 └──────┬───────┘
        ▼
 ┌──────────────┐ BLOCK ──▶ tool call never runs; synthetic result:
 │  A7 dispatch │           "BLOCKED by rule <id> (p=<p>): <desc>.
 │              │            Remedy: <remedy>"  (append-only)
 │              │ ADVISE ─▶ advisory buffer → TUI + telemetry (v0:
 │              │           no model-context injection unless promoted)
 │              │ CLARIFY ─▶ hold mutating call, batch questions,
 │              │            jev.clarify event → user (or park if autonomous)
 │              │ PASS ───▶ execute normally
 └──────┬───────┘
        ▼
 telemetry record {rule, question, p, action, outcome} → D1
```

## 4. Rule pipeline

```
 ~/.psi/packages/<pkg>@<ver>/rules.yaml   stdlib (signed, pinned)
 ~/.psi/rules.yaml                        user tier
 <repo>/.psi/rules/*.yaml                 project tier (trust-gated)
        │
        ▼  C1 rule-loader: merge + tier resolution
        │     precedence: user > project > stdlib, then severity
        ▼
 RuleSet[id, tier, type(noul|score|choice), question,
         applies_to[], paths[], probe?, threshold,
         severity(blocking|advisory), remedy?, description]
        │
        ▼  per bundle: A3 gate-filter (non-applicable = free)
        ▼
 QuestionSet{ "R<id>": <rule question>,   … resolved per call
              "F<nn>": <fact-pack item>,
              "sys.ambiguity": "…clearly what the user asked?",
              "sys.novelty":   "…off-distribution for evidence?" }
```

Rule schema (YAML) — complete:

```yaml
id: CC-014
package: clean-code@1.2.0
tier: stdlib
type: noul
question: "Does the proposed code duplicate logic that already exists in the codebase?"
applies_to: [edit, write]
paths: ["src/**/*.py"]
probe: similar-code --top 3
threshold: 0.85
severity: blocking
remedy: "Reuse the existing utility — see probe results."
description: "Prefer reusing existing utilities over reinventing them."
```

## 5. Policy decision table (A6)

```
for each answer (key, value):
  noul p IS the answer; choice/score use their confidence for bands

  blocking rule, p ≥ threshold               → BLOCK
  blocking rule, p ∈ [threshold−0.1, threshold) → ADVISE (near-threshold flag)
  advisory rule, p ≥ threshold               → ADVISE
  sys.ambiguity < ambiguity_threshold        → CLARIFY (batch, don't re-ask
                                                what fact store knows)
  sys.novelty  < novelty_threshold           → CLARIFY (autonomy gate)
  else                                       → PASS

conflicts: user > project > stdlib, then blocking > advisory
loop breaker: ≥3 blocks sharing a rule within a task window
              → suspend rule (telemetry flag) + escalate with history
```

Band widths and thresholds are tunable in `psi.toml`; defaults are empirical (v0 calibrates).

## 6. Outage and degradation (A5/D2)

```
 Jev call fails → retry ×3 (exponential backoff)
   still failing → on_outage (psi.toml):
     pause      → hold all mutating calls; jev.degraded banner; wait
     quarantine → execute + journal bundle (D2) → replay queue
     fail-open  → execute + loud log + banner
 recovery → replay queue re-runs A2–A6 per bundle → violations reported
            (git makes mutations reviewable/undoable post-hoc)
 completion gate: verification (B3) requires Jev reachable — "done" is
 always trustworthy, in every mode.
 deterministic fast path (gates, literal checks) runs in all modes.
```

## 7. Artifacts

```
 user request ──▶ clarify round ──▶ ┌─────────────────────────┐
                                    │ fact pack (.psi/…)       │
                                    │ spec mode:   requirements│
                                    │              → check items│
                                    │ ledger mode: hypotheses,  │
                                    │              findings     │
                                    └────────────┬────────────┘
                                                 │ plan maps items
                                                 ▼
                                    implementation ──▶ B3 verification
                                                 │    report:
   "compile errors" = unmet/uncheckable items ◀─┘    item → check type
                                                     → result
 check types: deterministic (run tests/greps)
              | jev (fan-out at task end)
              | human (listed for the user)
 done = fact pack verifies.  overhead scales with task size
 (small tasks: rules-only mode, no fact pack).

 The fact pack is a LIVING artifact (round 18): mid-task requirement
 statements ("it must do X") append as items — the user confirms drafts
 (capture loop; no silent extraction) — and are checked at response
 review from that point on.
```

## 8. Memory (fact store + recall)

```
 HOT  (every call):   fact_store_digest → StateObject (≤500 tok)
 WARM (on write):     distiller → fact store entries
                      audit per entry (Jev): supported-by-evidence,
                      dedup/novelty, staleness on re-encounter
 COLD (archive):      raw session JSONL — tau ships it, ground truth
 INDEX (finding aid): session-indexer B6 — chunk turn/tool-result
                      units → embedding candidates (not the book,
                      the index)
 READ GATE (recall):  probe: recall <topic>
                      candidates → Jev: relevance? contradiction?
                      staleness? → winners enter state
                      batch contradiction sweeps at checkpoints
 LIVE SESSION:        the current conversation is just another session —
                      B6 indexes the active session incrementally as turns
                      complete (round 18). Recall triggers:
                      explicit ("remember requirement" → targeted recall)
                      implicit (a gate check comes back thin/contradictory
                      → gate-driven targeted recall → verbatim injection)
                      Injected segments are VERBATIM (provenance beats
                      paraphrase), append-only; a recalled requirement is
                      drafted as a fact-pack item, confirmed by the user
                      (capture loop), then enforced at response review.
```

## 9. Background pipeline (B5/B6)

```
 event stream ──▶ triggers: compaction | task end | every N turns
                       │ (compaction checkpoint = guaranteed pass)
                       ▼
                distiller subagent (separate process)
                       │            │
                       ▼            ▼
                 fact store    rule candidates
                 (versioned)   ("corrected twice on X → draft rule")
                       │
                       ▼
                 session-indexer (on session close) → ~/.psi/index/
```

## 10. Extension wiring (C3)

```
 psi.toml:
   [extensions.file-descriptions]
   kind = "generator"          # generator | state_source | probe
   command = "psi-ext-filedesc"
   tier = "user"

 loader ──▶ registry ──▶ orchestrator spawns as SEPARATE process
                          (harness orchestrates, never embeds)
 generators     → artifacts        (built-in: distiller, indexer)
 state sources  → named digests    (unioned with core fields, budgeted)
 probes         → evidence cmds    (injectable per-rule)
 NOT extensible in v0: policy layer, placements (trust core — PR, not config)
```

## 11. Config surface (`psi.toml`)

```
 [provider]    backend = "typesafe"              # typesafe | openrouter
               # typesafe:  api.typesafe.ai, TYPESAFE_API_KEY
               # openrouter: openrouter.ai/api, OPENROUTER_API_KEY
               # (same SDK + /v1/systemone schema; post-v0: ordered list
               #  = backend failover, softens on_outage)
 [thresholds]  blocking = 0.85  advisory = 0.75  gray_band = 0.10
               ambiguity = 0.60  novelty = 0.60
 [outage]      on_outage = "quarantine"   # pause | quarantine | fail-open
 [budgets]     state_core_tok = 4000  digest_tok = 500  probe_tok = 1000
 [rules]       packages = ["clean-code@1.2.0", …]
               untrusted_project_tier = false   # default off
 [extensions]  <see §10>
 [quiet]       advisory_batching = true
```

## 12. Events & telemetry (D3/D1)

```
 jev.blocked   {rule, p, remedy, tool, summary}
 jev.advisory  {rule, p, batch}
 jev.clarify   {questions[], context}
 jev.degraded  {mode, since}
 every interception → session JSONL record:
   {ts, rule, question, p, action, outcome: complied|worked_around|overrode}
 reports: per-rule override rate (→ rescope/retire),
          per-rule compliance rate (response rules)
```

## 13. Decision log (resolved by this document)

| # | Decision |
| - | - |
| D1 | Graft point: after response assembly, before tool dispatch. **Spike-confirmed** (SPIKE.md): full `AssistantMessage` available pre-execution (`loop.py:146–156`); native `before_tool_call` hook blocks calls (`loop.py:296–303`); bundle capture via provider wrap (`provider.py:19–31`). Wiring, not a fork. |
| D2 | Block mechanics: call never executes; synthetic `BLOCKED` tool result carries rule id, p, remedy. Append-only — no context rewrite, ever. |
| D3 | Advisories: TUI + telemetry in v0; model-context injection only for promoted rules (habituation control). |
| D4 | Clarify: hold mutating call, batch questions, one `jev.clarify` event; autonomous mode parks and logs (`clarify.autonomy = park \| proceed-flagged`). |
| D5 | noul p is the answer (no separate confidence field — per API docs); gray band `[thr−0.1, thr)` → ADVISE. |
| D6 | State budgets: core ≤4k tok, digests ≤500 each, probes ≤1k each. Tunable. |
| D7 | Redactor: gitleaks-class detectors (regex + entropy) in `state-builder`; `.env` contents stripped; hashed markers where identity matters. |
| D8 | Loop breaker: ≥3 related blocks per task window → suspend + escalate. |
| D9 | Replay journal under `quarantine`; completion gate requires Jev reachable. |
| D10 | Naming: `psi.toml`; project `.psi/` (`rules/`, fact pack, ledger); user `~/.psi/` (`packages/`, `index/`). |
| D11 | Recall rides probes (`probe: recall`); hot digest always in state. |
| D12 | Extensions: separate processes, `psi.toml`-declared, trust-tiered; untrusted origins don't auto-enable. |
| D13 | Rule format: TOML (`[[rule]]` via stdlib tomllib) — zero-dependency; YAML loader optional later behind the same entry point. |
| D14 | **Delivery: ψ ships as a tau extension (addon, not fork).** Entry `~/.tau/extensions/psi.py` (dev shim → `psi.tau_ext.setup(tau)`); hooks via `tau.on("tool_call" \| "input" \| "tool_result" \| "compaction_end")`; handlers return the real `ToolCallHookResult` (runtime isinstance-validates — conversion lives in tau_ext; psi core stays tau-agnostic). `compaction_end` = distiller checkpoint trigger. `/rule` via `register_command`. tau fails safe (hook exception = blocked call), so ψ catches its own errors and fails open loudly. Outage implemented: journal `~/.psi/quarantine/journal.jsonl`, replay reports post-hoc violations to `~/.psi/telemetry.jsonl` (cap 20/recovery), never re-blocks. |

## 14. Self-reimplementation test

This file is the input spec for the clean-room self-test (dry-run suite #1, applied to ψ
itself): a second system reads **only** this document and reimplements the harness. The
reference implementation is the one built here. Behavioral divergence between the two —
same inputs, same traces — is the precision metric. If this document can't compile into
ψ, it wasn't architecture; it was prose.