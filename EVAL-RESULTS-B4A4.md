# Minimal {A,B}×deep-swe eval — results (attempt 4, regraded)

Date: 2026-10-13. Arms: **A4-bare** (tau only) vs **B4-ψ** (tau + ψ, PSI_ENABLED=1).
10 tasks × 1 seed, n-concurrent 2, model openrouter/z-ai/glm-5.3.
Grading: offline regrade via `spike/regrade_trial.sh` (authoritative — the in-eval
verifier was broken by the keep-id patch: reward 0 on all 20 trials; reverted post-run).

## Paired results

| task | A4-bare | B4-ψ | Δ |
|---|---|---|---|
| boa-hierarchical-evaluation-cancellation | 1 (17/17 f2p) | 0 (0/17) | −1 |
| go-critic-doc-link-checker | 0 (2/3) | 0 (2/3) | 0 |
| httpx-streaming-json-iteration | 0 (107/108) | 0 (107/108) | 0 |
| igel-persist-feature-schema | 1 (24/24) | 1 (24/24) | 0 |
| kysely-window-grouping-helpers | 1 (254/254) | 0 (184/254) | −1 |
| prometheus-typed-label-sorting | 1 (17/17) | 1 (17/17) | 0 |
| query-persist-restored-query-state | 1 (8/8) | 1 (8/8) | 0 |
| skrub-duration-encoding | 1 (130/130) | 1 (130/130) | 0 |
| textual-kitty-key-phases | 1 (23/23) | 0 (22/23) | −1 |
| valibot-recursive-schema-composition | 1 (10/10) | 1 (10/10) | 0 |

**A4: 8/10. B4: 5/10. Δ = −0.3 (0 wins, 3 losses, 7 ties).**

## Verdict: NO-OP read

Arm B was a structural no-treatment arm — ψ never had a causal pathway into the agent:
- **Tilde leak**: `upload_file` got a literal `~/.psi/...` path (SDK calls don't shell-expand)
  → rules.toml landed at `/app/~/.psi/` → **ZERO clean-code rules loaded** into `/root/.psi`
  (evidence: a `~/.psi` hunk in 9/10 arm-B model.patches).
- **0 blocks** in any probed trial; ADVISE/clarify = telemetry-only (design decision D3).
- Ledger off (PSI_LEDGER=0).

⇒ arm B ≡ arm A behaviorally. Δ = sampling noise (sign-test p≈0.125 on the 3 decided
pairs) — not ψ harm, and not ψ evidence. Supporting signal: 7/10 tasks with IDENTICAL
f2p counts across arms; the boa pair produced architecturally identical
implementations (bare got the hidden-test semantics right, ψ-arm wrong).

## What the 20 rollouts bought

- End-to-end pipeline shakedown: 12+ ops bugs found and fixed (see psi-exec-log).
- Same-distribution confirmation across arms (7/10 identical f2p).
- Post-run fixes: keep-id reverted (in-eval verifier now grades), tilde leak fixed
  (rules actually load), ψ telemetry/ledger persisted host-side before teardown.

## Next

**B′ (PSI_LEDGER=1: rules + recall/ledger)** on the same 10-task subset — the first arm
with a real firing pathway (`sys.repeat` blocks repeated failed attempts; block path
fire-drill-proven). A4's 8/10 stands as control. ~1–1.5h, ~$3–8.