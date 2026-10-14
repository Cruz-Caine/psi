> **Status:** rationale and decision history. Implementation source of truth: **ARCHITECTURE.md**.

# Jev Interceptor — Harness Vision (v0)

> A coding agent whose System-2 reasoning is paired with a cheap, calibrated System-1 checker.
> The agent implements; the harness compiles the spec — and keeps the experiment honest.

Base: [tau](https://github.com/huggingface/tau) (HF's readable Python coding agent, MIT).
Checker: [TypeSafe Jev](https://docs.typesafe.ai) (`jev-latest`, System One model — typed answers + calibrated probabilities, no text generation).

## 0. Principle

The main agent LLM is System Two: slow, deliberate, generative. Jev is System One: fast, narrow, calibrated. Jev never writes anything — it answers typed questions about the agent's intent. A deterministic policy layer turns numbers into behavior: block, advise, ask, or pass.

**Autonomy comes from calibrated escalation, not bravado.** On novel tasks, models regress to the mean — they confidently produce the median answer. The harness's answer is not a smarter model but a model that *knows when it should ask*: low confidence on ambiguity/novelty probes triggers the clarify-first gate, so human attention lands exactly where the evidence is thin. Human-in-the-loop shrinks from "watch everything" to "answer what got escalated." (Whether noul probes reliably detect off-distribution novelty is an empirical question — v0 tests it.)

## 1. The loop

Every model response arrives as a bundle: thinking block(s) + tool calls. The harness reviews the bundle *before executing anything*:

```
model response bundle (thinking + tool calls)
        │
        ▼
fast deterministic checks ── tags/tool matching, cheap regex/AST (zero cost)
        │
        ▼
Jev fan-out (one call per bundle)
  state = {
    user_request, plan_excerpt, fact_pack_digest,
    fact_store_digest, recent_context, proposed_action
  }
  questions = applicable rules (user > project > stdlib)
            + fact-pack items
            + experiment probes (ledger mode, §4)
            + ambiguity probe ("is this clearly what the user asked for?")
            + novelty probe (autonomous mode: "is this task off-distribution for the available evidence?")
        │
        ▼
policy layer (deterministic)
  ├─ p ≥ threshold & severity=blocking → deny tool call, inject templated rewrite:
  │     "I was considering X, but it breaks rule #N: <desc> (p=0.93)"
  ├─ mid confidence / advisory severity → one-line batched advisory (never blocks)
  ├─ low confidence on ambiguity probe → clarify-first gate: ask the user, don't guess
  └─ else → execute
        │
        ▼
telemetry: {rule, question, p, action, outcome} → session JSONL
```

Placement policy: mutating tools (write/edit/bash) always reviewed; read-only tools skip the fan-out; the plan artifact is reviewed once at plan time; the fact pack is verified once at task end; **response-tier rules run on the outgoing response before it ships** (format, tone — see §2).

**Thought vs action.** The thinking block is reviewed for *drift detection* — "I'll build this in Vulkan" is catchable before any offending tool call exists. Enforcement always lands on the tool call: a thought can't be un-said or rewritten in context; corrections are appended, never retro-edited. Non-thinking models fall back to tool-call-only review.

## 2. Rules (standing constraints)

Tiers and precedence — **user > project > stdlib**:

| Tier | Source | Who maintains |
| - | - | - |
| stdlib | curated packages, versioned + pinned like dependencies (`clean-code@1.2`) | upstream — trusted, install-and-ignore, like a language stdlib |
| project | `.tau/rules/` in the repo | the project |
| user | personal rules, machine-global | the user |

Rule schema (YAML):

```yaml
id: CC-014
package: clean-code@1.2.0
tier: stdlib
type: noul                # noul | score | choice
question: "Does the proposed code duplicate logic that already exists in the codebase?"
applies_to: [edit, write] # tool-gating
paths: ["src/**/*.py"]    # when-gate: where the rule applies
probe: similar-code --top 3  # harness-run evidence injected into state (optional)
threshold: 0.85
severity: blocking        # blocking | advisory
remedy: "Reuse the existing utility — see probe results."  # shown in the rewrite
description: "Prefer reusing existing utilities over reinventing them."
```

- Noul = pass/fail checks. Score = rubrics ("how maintainable is this change, 0–3"). Choice = classification (which layer owns this).
- Rules never enter the main model's context. Only violations do — as one-paragraph rewrites. The library can grow arbitrarily without diluting System Two.
- **Evidence probes.** Some rules need repo/runtime evidence a prose digest can't carry (duplication, test status, API coverage). A rule may declare a `probe` — harness-run retrieval whose results are injected into state before the question fires. Evidence-hungry rules without a probe auto-downgrade to advisory: an unevidenced answer to "does this duplicate existing logic?" is confident garbage.
- **Rules are gates.** Applicability is decided *deterministically, pre-fan-out*: `applies_to` (tools) + `paths` (globs) + language tags. A clean-code rule doesn't fire on a README edit — non-applicable rules cost nothing and never consume questions. (Prior art: ESLint `overrides`, pre-commit `files:` filters, CI path triggers.) Gating is also what keeps a 1000-rule library affordable per call.
- **Deviations in legacy repos are declared, never silent.** In an existing repo with shoddy human work, the agent may legitimately need to deviate — to match surrounding reality. That happens via a *scoped suppression*: file/hunk-limited, reason required (`# noqa` / `eslint-disable` / Rust `#[allow]` prior art) — logged in telemetry, surfaced in the end-of-task report, and promotable to a permanent per-project ignore through the capture loop. Whether to *fix* the legacy part instead is a scope decision that belongs in the fact pack (the user's call), not the rule layer. Standards in legacy code are aspirations with recorded exceptions — not absolute gates, and never silent ones.
- Rule conflicts resolve by precedence, then severity, like lint configs. Per-project ignores and severity overrides handle stdlib-vs-legacy-code friction.
- **Standards default to advisory.** Stdlib packages install at `advisory` severity — they suggest, they don't enforce; score-type rules render as one-line guidance, never interruptions. Promoting a rule to `blocking` is an explicit user/project decision. Standards guide; requirements enforce.
- **Knowledge packages (post-cutoff use case).** Rulesets double as distilled domain knowledge: `rust-idioms@2027.3`, `golang@1.24-conventions`, `company-api@2.0` — authored by people who know the thing, installed by people whose model's cutoff predates it. The agent writes code as usual; Jev checks idioms, deprecated patterns, and wrong-API usage against the package. Honest limit: packages guard and correct — they don't teach the model syntax that postdates its cutoff; docs still live in context. They raise the floor and catch cutoff-blind spots.
- **Personality and format are user-tier rules.** "Always explain in this format" isn't a system-prompt hope (passive instructions get ignored as context grows) — it's a rule with `applies_to: [response]`, checked with calibrated confidence on every outgoing response. Violation → injected correction or regeneration, advisory by default, promotable to blocking. The capture loop makes it one command: "always do X" → `/rule add`. And the telemetry shows per-rule compliance rate — if the model only follows the format 60% of the time, you can see it and fix the rule instead of wondering why it's "broken".
- Rules customize *policy*. Customizing *behavior* — new artifacts, state contributions, evidence commands — lives in the extension surface (§10).

## 3. The fact pack (spec as a checkable artifact)

**The fact pack is a ruleset scoped to the task.** Same schema, different provenance: generated at task start from the user request (plus a clarify round), mapped by the plan, verified at task end.

```
fact pack (spec) ── plan maps items ──▶ plan (IR) ──▶ implementation ──▶ verification
      ▲                                                                  │
      └──────────── "compile errors" = unmet / uncheckable items ◀──────┘
```

- Every item declares a **check type**:
  - `deterministic` — a test, a grep-able property, an interface signature
  - `jev` — a noul/score question against the implementation
  - `human` — acceptance, deferred to the user
- **The ambiguity gate is the compile error for vague specs.** An underspecified item makes the intent-match noul return low confidence → "spec item R7 is uncheckable — clarify before build." A real compiler refuses ambiguous programs; this one refuses ambiguous specs.
- **Done is mechanical:** the task is complete when the fact pack verifies — not when the agent feels done. The verification report (item → check type → result) is the build artifact.
- Traceability guards the lossy part: the agent will quietly reinterpret the spec while implementing, so the plan maps edits to fact-pack items and the verification pass flags items nothing served.
- Small tasks skip the fact pack (rules-only mode); overhead scales with task size.

Difference from existing spec-driven flows: the checker is a calibrated decision model inspecting intent *before and during* implementation — not the same model grading its own output at the end.

## 4. Experimental mode (the ledger)

Experimental workflows — reverse engineering, decomposition, "figure out how this was generated and replicate it" — are where agents fail hardest today. The failure is structural: hypotheses and negative results live only in context, context rots under process noise, compaction amnesia erases what was learned, and the agent drifts into brute-force search (retrying refuted approaches, chasing tangents). A system worth anything must handle these — and the same artifact engine does, with a second fact-pack mode:

**Spec mode vs model mode:**

| | Spec mode (implementation) | Model mode (experimental) |
| - | - | - |
| Items | requirements | hypotheses, findings, refuted attempts, open questions |
| Lifecycle | written at start, verified at end | refined continuously as evidence arrives |
| Done | all items pass | replication/decomposition verifies against the artifact under study |

The ledger **is** the experiment's state, externalized. It survives compaction — the agent re-reads it and resumes with full state. Context loss becomes survivable instead of fatal.

**Experiment probes (same fan-out, experiment-specific questions):**
- *"Is this attempt substantially the same as a refuted approach?"* — the failed-attempt log sits in state; a repeat fires as a blocking rule **with its evidence** ("attempt #3 tried X, result was Y"). This is the anti-brute-force mechanism: the system refuses to pay for the same experiment twice.
- *"How well does the cited evidence support the leading hypothesis?"* — calibrated evidence scoring (noul/score), turning hypothesis evaluation into a gated step instead of vibes.
- *"Does this action follow from the current hypothesis, or are we drifting?"* — the drift check, same shape as the pinned-stack rule.
- Ambiguity gate covers underspecified experiment goals ("replicate it" → clarify what counts as replication).

**Done-checks by task type (mechanical where the task allows):**
- Replication: run the replica against the target, diff outputs — deterministic when the output is reproducible.
- Decomposition: each identified component's behavior pinned by a test; the ledger documents why each part is what it is.

**From experience to fact (the ingestion pipeline).** The agent runs `exp_1.py`, or spends an hour inside an unfamiliar repo — either way the raw material is a stream of tool results and turns. Three tiers turn it into facts:

1. **Opportunistic extraction (freebies only).** When a result is *already* structured — exit codes, benchmark lines, test reports — the loader ingests mechanically, no model in the loop. Nothing more: experiments are conducted for the human's understanding, not the harness's convenience, and we never shape an experiment around machine ingestion.
2. **Background distillation (the main path — component shape still open).** The commitment is the *pipeline*, not a fixed component: the main agent drops one-line inline findings for immediacy; bulk distillation runs off the main loop and lands in the **fact store** — what a file does, how modules relate, what an experiment showed, which environment quirk cost an afternoon. Not rigid JSON — structured prose entries with light metadata (topic, files touched, confidence, provenance), expressive enough for real understanding, filterable enough to digest into Jev state; also dedupes and proposes rule candidates ("user corrected the agent about this convention twice → draft a project rule"). *Who* distills is a tunable choice: a persistent event-stream consumer (fact-agent) vs a subagent triggered at checkpoints (compaction, task end). v0 defaults to checkpoint-triggered — the simplest correct thing; upgrade to a persistent consumer if fact freshness proves to matter. The non-negotiables: facts survive compaction (guaranteed distillation pass at the checkpoint) and get audited (item 3).
3. **Jev audit (the check).** The fact-agent checks itself with Jev, three questions per entry: *"is this fact supported by the transcript evidence?"* (anti-hallucination — a polluted fact store poisons everything downstream), *"is this already known?"* (dedup/novelty), and on re-encounter *"is the stored fact still consistent with current evidence?"* (staleness — the fact-rot counterpart to rule rot, closing the outdated-info loop).

**The fact store** is the repo-learning answer: open an unfamiliar repo → the agent explores → the fact-agent builds the map (file→purpose, module relationships, gotchas) → every later session starts warm instead of re-discovering the codebase. After compaction, the agent re-reads the fact store and resumes with state intact — the same anti-rot mechanism as the ledger, applied to codebase knowledge. The store feeds Jev state (as digests) and sits alongside rules and the ledger as a first-class artifact.

**Memory, the Jev way.** "Agents don't remember" is an artifact problem, not a context problem — and the same calibrated-gate pattern applies on *both* sides of memory. Write side: facts are audited before commit (supported-by-evidence, dedup, staleness — above). Read side: **relevance-gated recall** — candidate retrieval (embedding/grep) proposes a candidate set, Jev filters it (*"is this fact relevant to the current action?"*), winners enter state. Retrieval proposes, Jev disposes — recall rides the existing probes mechanism (`probe: recall <topic>`). Hot/cold tiering keeps it affordable: the fact-store digest is *hot* (in state every call); cold facts arrive on demand through probes. Episodic memory — what happened in past *tasks* — is **raw-session-first** (round 14): tau already persists every session as durable JSONL, so the ground-truth archive is free infrastructure. No pre-distillation of task history — distillation fixes interpretation at write time, but future questions are unknown; interpretation defers to read time. Recall = three steps: a **session-index generator** (§10 class — chunks raw sessions into turn/tool-result units, builds embedding candidates; a *finding aid*, like a book index, not the book) → retrieval proposes candidates → the **Jev read gate** disposes: *relevance* ("is this on-topic for the current action?"), *contradiction* ("does this past statement conflict with current evidence or the fact store?"), *staleness* ("true then — still true now?"). Contradiction sweeps also run batch at checkpoints (audit the fact store against new session evidence). This mirrors how memory-bearing agents already work (hot in-context digest, cold searchable store, background consolidation) — same pattern, deliberately.

Division of labor stays clean: the loader ingests freebies, the fact-agent distills, the main agent transcribes one-liners, Jev audits everything. **Jev never authors and never self-improves** — the *rule library* learns (capture loop), the *ledger* and *fact store* learn (findings); Jev stays a fixed judge. That fixity is what keeps its calibration comparable across time. Every "how does X reach Jev" question reduces to state construction — the fact-agent is the piece that does that construction.

**Honest scope:** this *bounds* context rot, it doesn't abolish it. The transcript still degrades — but experiment state lives in artifacts, not context, and interception stops the retry storms that generate the noise in the first place. Artifacts make context loss survivable; they don't make the context immortal.

Experimental tasks are also the strongest possible demo: they're the hardest case. A harness that holds there handles implementation tasks by comparison.

## 5. Telemetry and rule lifecycle

- Every interception logged to the session JSONL: rule id, probability, action, outcome (complied / worked around / overrode).
- Per-rule **override rate** drives the lifecycle: a rule the agent keeps tripping and working around gets rescope or retirement. Thresholds are empirical — calibration is statistical (across groups), not per-answer truth.
- Capture loop: `/rule add "don't do X"` turns a correction into a typed rule with provenance; at task end, the harness can propose rules from repeated corrections ("you corrected me twice about X — add a rule?").

## 6. Base: tau

- **Graft point:** `tau_agent`'s loop, at response-bundle handling before tool execution.
- **Events:** emit `jev.blocked`, `jev.advisory`, `jev.clarify` events — the TUI shows "rule CC-014 blocked X (p=0.91)". Visibility is trust.
- **Background distiller:** v0 = subagent triggered at compaction/task-end checkpoints; upgrade path = persistent event-stream consumer (tau already supports multiple event consumers) — see §4.
- **Sessions:** tau's JSONL sessions extended with interception records (telemetry rides on existing storage).
- **Config:** `jev.toml` (thresholds, installed packages, placement policy, quiet mode, extension registry per §10) + `.tau/rules/` project rules.
- **SDK:** `typesafe-sdk` (Python ≥3.10) — compatible with tau's Python 3.12 requirement.

Example check (real API shape):

```python
state = {
    "user_request": "Build the render loop in SDL2 as I specified",
    "proposed_action": {"tool": "bash", "command": "pacman -S vulkan-devel && cmake -DRENDERER=Vulkan"},
    "recent_context": "...",
}
resp = client.system_one(state=state, questions={
    "R-001_pinned_stack": Noul(instructions="Does the proposed action contradict the user's pinned technology choices?"),
    "CC-014_duplication": Noul(instructions="Does the proposed code duplicate existing logic in the codebase?"),
    "ambiguity": Noul(instructions="Is the proposed action clearly what the user asked for?"),
})
if resp.answers["R-001_pinned_stack"].noul >= 0.85:  # block + templated rewrite
```

## 7. V0 scope

1. **Spike:** read tau's loop — confirm the response bundle (thinking + tool calls) is visible pre-execution; find event emission points. *(Validates the core assumption everything else hangs on.)*
2. Rule schema + loader (local dirs, no package manager yet) with tier resolution.
3. Jev fan-out on mutating tool calls; block-with-rewrite + batched advisories + clarify-first gate.
4. Fact pack v0 (spec mode): generated from the request, items with check types, end-of-task verification report.
5. Telemetry: JSONL logging + per-rule override-rate report.
6. First stdlib package: ~20 clean-code rules as typed questions.

Post-v0: experiment ledger mode (§4) — same artifact engine, second instantiation; spec mode first so the engine is proven.

## 8. Open questions

- Jev practicals: latency/price at 20–100 questions per call; payload limits; noul threshold defaults per severity (empirical — tune in v0).
- tau spikes: thinking-block representation in the message model; injection point for rewrites/clarify prompts.
- Advisory batching UX in the TUI (signal-to-noise discipline decides whether habituation happens).
- Ledger fidelity: how much experiment state fits in a fan-out state payload (digest design matters more in model mode than spec mode).

## 9. Known blindspots (self-audit, round 7)

All interaction-loop failures, not capability failures. Ranked by how much they'd hurt if ignored:

1. **Evidence-hungry rules.** The core promise — calibrated checks — silently degrades to confident garbage for any rule whose answer requires content Jev never sees. **Fix: `probe` field** (§2) — per-rule harness-run retrieval injected into state; probeless evidence rules auto-downgrade to advisory.
2. **Block livelock.** Blocked → rewrite → variant → blocked again: the agent grinding through blind variant search against a wall with no door. **Fixes:** rules carry a `remedy` field so the rewrite offers a path, not just a wall; a loop breaker — N related blocks in a sequence auto-escalates to the human with the full history, or suspends the rule with a telemetry flag. An escape hatch that's never designed is an escape hatch that doesn't exist.
3. **Jev outage — undefined behavior becomes a deployment choice.** Fail-open silently voids every guarantee; fail-closed bricks the agent. **Fix: `on_outage` policy in `jev.toml`:**
   - `pause` — *professional default*. Quality-gate semantics (SonarQube gates, required CI checks): no unvetted mutation; the agent waits with backoff. Professionals install Jev *because* it's the trust layer — for them the gate being present is the product.
   - `quarantine` — *default*. Mutations are journaled and replayed through the fan-out on recovery (eventual checking — git makes mutations reviewable/undoable post-hoc); the completion gate still requires Jev, so "done" stays trustworthy even mid-outage.
   - `fail-open` — loud logs, visible banner.
   Degraded mode still runs the deterministic fast path — outage ≠ zero coverage. Never silently fail-open.
4. **State hygiene.** The state builder reads context — if the agent just catted `.env`, the secret rides to a third-party API. **Fix: redaction filter in the state builder** (secret-shaped strings, env contents, credentials — stripped; hashed markers where identity matters). Plus **untrusted-repo policy:** project-tier rules from a freshly cloned repo are attacker-controlled input — disabled by default on untrusted origins; stdlib packages are signed.
5. **Clarify fatigue.** The ambiguity gate interrogating the user is the mirror of Jev nagging the agent — habituation on the human side. **Fix:** batch clarify questions per turn; never re-ask what the fact store already knows; "stop asking" records the preference as a fact, not a dismissal.
6. **Background actors.** Fact-agent/distiller writes are mutations on artifacts — exempt from user-code rules *as an explicit decision, stated here so the exemption can't happen by accident*; still Jev-audited for content. Fact store writes are versioned to survive concurrent sessions.

**Prior art — every fix above is an adaptation of a named pattern, not an invention:**

| # | Blindspot | Pattern | Borrowed from |
| - | - | - | - |
| 1 | Evidence-hungry rules | Rules-as-queries + hooks that fetch evidence | Semgrep/CodeQL: the rule compiles to a query, evidence = query result. Agent-harness PreToolUse hooks already run commands and return structured output — a probe executor is a hook executor pointed at retrieval |
| 2 | Block livelock | Circuit breaker + actionable diagnostics | resilience4j/Hystrix: N failures → open circuit → escalate. rustc's `help:` suggestions and ESLint `--fix`: rules carry the fix, not just the violation |
| 3 | Jev outage | Severity-tiered failure + fallback + kill switch | Resilience-library fallback policies; feature-flag kill switches; status-banner visible degradation. The fail-open-vs-fail-closed debate (classic in authorization systems) resolves by tier: blocking rules fail closed, advisory fail open with loud logs |
| 4 | State hygiene / untrusted rules | Secret scanning + workspace trust + signed packages | gitleaks/truffleHog detectors relocated into the state builder; VS Code **Workspace Trust** (untrusted workspace → extensions/tasks disabled) for project tier + extensions; sigstore/cosign signing for stdlib packages |
| 5 | Clarify fatigue | Front-load + batch + persist | Setup wizards (requirements gathered at task start — already the clarify round); iOS/Android permission batching; the "don't ask again" checkbox that writes to settings = a fact store entry |
| 6 | Background actors | Scoped capabilities + versioned stores | GitHub Actions `permissions:` model — actors declare write scope, everything else denied (the distiller gets `fact-store: write, code: none`); git for versioned concurrent artifact stores |

Build implication: only the probe executor and the state redactor lack a direct off-the-shelf equivalent, and both have close cousins (hook executors, secret scanners). v0's engineering is adaptation.

## 10. Extension surface ("people can try shit")

Rules customize what's *enforced*. The extension surface customizes what the harness *does* — so users can bolt on capabilities without forking. Motivating example: a generator that writes plain-English descriptions of every file in the repo, registered as a state source so Jev can consult them for file relevance when the user asks something — Jev-as-router, the intent-routing pattern from TypeSafe's docs. Note that guardrails are not Jev's only job; routing is a legitimate second use.

Three extension classes, config-declared in `jev.toml`, all running as **separate processes** — the harness orchestrates, never embeds (same trust model as rules):

| Class | What it does | Ships with |
| - | - | - |
| **Generators** | Background jobs that read the repo / event stream and write artifacts | fact-distillation (the built-in one) |
| **State sources** | Named digests contributed to Jev state, unioned with the core fields | the five core fields (§1) |
| **Probes** | Evidence commands injectable per-rule (`probe: my-linter --file $f`) | a small default set |

The example decomposes cleanly: `file-descriptions` = a generator artifact → registered as a state source → routing behavior = generator + state source + a routing question (Choice over file paths). Whether per-file LLM prose beats paths + symbols + embedding search on a given repo is exactly the kind of thing users should *try* — the surface exists so the experiment is config, not a fork.

Deliberately **not** extensible in v0: the policy layer and placements — the trust core. A new placement is a harness PR, not config.

Guardrails on the surface:
- **State budget.** Every question sees the same state, so bloat muddies every judgment — state sources contribute *digests* under a size budget, core fields first.
- **Trust tiering carries over.** Extensions follow the rule tiers (user > project > stdlib); untrusted-repo origins don't auto-enable extensions (§9.4).
- **Staleness declarations.** Generated artifacts declare how they revalidate (mtime-triggered re-sweep, at-access check) — a stale artifact feeding state is fact rot by another name.

## 11. Validation: the dry-run suite

Mental dry runs test design sufficiency — each case names what it stresses and what a *predicted* failure looks like. Dry runs catch design gaps, not empirical numbers (those belong to the tau spike and real evals). Several graduate into the scripted eval plan later.

| # | Case | Stresses | Predicted failure if design insufficient |
| - | - | - | - |
| 1 | **Clean-room decomp** (A studies binary → fact pack; B reimplements from it; corpus diff = done-check) | Full integration: ledger mode → spec mode → compiler pipeline → verification; spec-vs-standards seam; probes on both sides | Fact pack shape insufficient for behavioral specs — needs corpus-first spec, not prose requirements. Headline metric: behavioral divergence rate |
| 2 | Pinned-stack conflict (SDL2→Vulkan) | Plan review placement, pinned-stack rule, ambiguity gate | Plan review too weak to catch cross-call drift |
| 3 | Legacy repo hotfix under standards | Gates (`paths` scoping), declared deviations (suppression w/ reason), scope question in fact pack | Deviation mechanism too heavy (friction) or too loose (silent rot) |
| 4 | Jev outage mid-task (quarantine mode) | `on_outage` mechanics: journal, replay-on-recovery, completion gate | Replay journal too vague to re-check mutations |
| 5 | Adversarial repo (hostile `.tau/rules/`, secret-bearing file agent will cat) | Untrusted-tier default-off, state redaction, signed packages | Trust model hole — repo-supplied config leaks through |
| 6 | Rule-capture lifecycle (dumb thing → `/rule add` → gates next session → overrides accumulate → rescope proposal) | Capture loop, precedence, telemetry → lifecycle | Rule conflicts unresolvable; override telemetry too noisy to act on |
| 7 | Repo onboarding, day 1 → day 2 | Fact store build, digest quality, compaction survival, staleness on re-encounter | Digest too lossy — day-2 agent re-discovers the codebase anyway |
| 8 | Post-cutoff language task | Knowledge packages, docs-in-context burden, `probe: lang-lint` | Packages insufficient — agent flounders without training knowledge |
| 9 | Autonomous overnight task (no human present) | Novelty probe, clarify-first in autonomy, escalation policy | Escalation incoherent — parks forever or bulldozes ahead |
| 10 | Livelock pressure test (task that requires violating a blocking rule) | Remedy field, N-block loop breaker, escalation UX | Grind: blind variant search against a wall |
| 11 | Format enforcement ("always explain in this format") | Response review placement, compliance telemetry | Habituation — nag volume unbounded |
| 12 | 1000-rule cost probe | Fan-out cost/latency with realistic gating rates | *(Empirical, not dry-run — belongs to the spike/eval phase; listed for completeness)* |