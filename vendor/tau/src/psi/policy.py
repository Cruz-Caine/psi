"""Policy engine — numbers into behavior (ARCHITECTURE A6/D5/D8).

Decision table:
  blocking rule, p >= threshold            -> BLOCK
  blocking rule, p >= threshold - gray_band -> ADVISE (near-threshold flag)
  advisory rule, p >= threshold            -> ADVISE
  sys.ambiguity < ambiguity threshold      -> CLARIFY
  sys.novelty  < novelty threshold         -> CLARIFY (autonomy gate)
  else                                     -> PASS

Conflicts resolve by tier rank then severity (rule loading sorts already).
Loop breaker: >=3 blocks sharing a rule within the task -> escalate+suspend.
"""
from __future__ import annotations

from dataclasses import dataclass, field

BLOCK = "BLOCK"
ADVISE = "ADVISE"
CLARIFY = "CLARIFY"
PASS = "PASS"

ESCALATE = "ESCALATE"
LOOP_BREAKER_LIMIT = 3


@dataclass
class Verdict:
    action: str
    key: str
    rule_id: str | None
    p: float
    reason: str

    def as_block_reason(self) -> str:
        r = self.rule_id or "psi"
        return f"{self.reason}"


@dataclass
class LoopBreaker:
    """Counts blocks per rule across the task window (D8)."""
    counts: dict = field(default_factory=dict)
    suspended: set = field(default_factory=set)
    limit: int = LOOP_BREAKER_LIMIT

    def record_block(self, rule_id: str) -> str | None:
        """Returns ESCALATE when the breaker trips, else None."""
        if rule_id in self.suspended:
            return None
        self.counts[rule_id] = self.counts.get(rule_id, 0) + 1
        if self.counts[rule_id] >= self.limit:
            self.suspended.add(rule_id)
            return ESCALATE
        return None

    def is_suspended(self, rule_id: str) -> bool:
        return rule_id in self.suspended


def _near_threshold(threshold: float, gray_band: float) -> float:
    return threshold - gray_band


def evaluate(
    answers: dict,
    rules_by_key: dict,
    thresholds: dict,
    breaker: LoopBreaker | None = None,
) -> list[Verdict]:
    """answers: {key: {'noul': p} | {'choice':…, 'confidence': c} | {'score':…, 'confidence': c}}
    rules_by_key: {'R<id>': Rule} for rule questions; sys probes keyed directly.
    """
    blocking = float(thresholds.get("blocking", 0.85))
    advisory = float(thresholds.get("advisory", 0.75))
    gray = float(thresholds.get("gray_band", 0.10))
    amb_thr = float(thresholds.get("ambiguity", 0.60))
    nov_thr = float(thresholds.get("novelty", 0.60))
    rep_thr = float(thresholds.get("repeat", 0.90))

    verdicts: list[Verdict] = []
    for key, ans in answers.items():
        p = float(ans.get("noul", ans.get("confidence", ans.get("score", 0.0))))
        rule = rules_by_key.get(key)

        if key == "sys.ambiguity" and p < amb_thr:
            verdicts.append(Verdict(CLARIFY, key, None, p, "psi ambiguity gate: request underspecified"))
            continue
        if key == "sys.novelty" and p < nov_thr:
            verdicts.append(Verdict(CLARIFY, key, None, p, "psi novelty gate: situation is off-distribution"))
            continue
        if key == "sys.consistency" and p < amb_thr:
            verdicts.append(Verdict(CLARIFY, key, None, p, "psi consistency gate: action drifts from recorded requirements/attempts"))
            continue
        if key == "sys.spec" and p < amb_thr:
            verdicts.append(Verdict(CLARIFY, key, None, p, "psi spec gate: action conflicts with the task requirements digest"))
            continue
        if key == "sys.repeat" and p >= rep_thr:
            # anti-brute-force: block with ledger evidence; breaker-counted via
            # the SYS-REPEAT pseudo-rule so loops escalate instead of grinding
            if breaker is not None and breaker.is_suspended("SYS-REPEAT"):
                continue
            verdicts.append(Verdict(BLOCK, key, "SYS-REPEAT", p, "psi repeat gate (SYS-REPEAT): the ledger shows this approach already failed."))
            if breaker is not None:
                breaker.record_block("SYS-REPEAT")
            continue
        if key == "sys.progress" and p >= rep_thr:
            # no-progress loop (round 28, mined feature f2): repetition WITHOUT
            # state change cannot produce new information. Advisory — re-running
            # after an edit is the dev loop; the trigger only fires on bare
            # re-runs (the ledger's repetition_stats resets on edits).
            verdicts.append(Verdict(ADVISE, key, "SYS-PROGRESS", p, "psi progress gate: repetition without state change — re-running this cannot produce a new result."))
            continue
        if rule is None:
            continue

        if breaker is not None and breaker.is_suspended(rule.id):
            continue  # suspended rules are quiet until escalation resolves

        thr = blocking if rule.severity == "blocking" else advisory
        if rule.severity == "blocking" and p >= _near_threshold(thr, gray) and p < thr:
            verdicts.append(Verdict(ADVISE, key, rule.id, p, f"rule {rule.id} near-threshold (p={p:.2f}): {rule.description}"))
        elif p >= thr:
            if rule.severity == "blocking":
                remedy = f" Remedy: {rule.remedy}" if rule.remedy else ""
                reason = f"BLOCKED by rule {rule.id} (p={p:.2f}): {rule.description}.{remedy}"
                verdicts.append(Verdict(BLOCK, key, rule.id, p, reason))
                if breaker is not None:
                    breaker.record_block(rule.id)
            else:
                verdicts.append(Verdict(ADVISE, key, rule.id, p, f"rule {rule.id} (p={p:.2f}): {rule.description}"))
        # else: PASS (no verdict emitted)

    return verdicts


def outcome(verdicts: list[Verdict], breaker: LoopBreaker | None = None) -> str:
    """Fold verdicts to a single action: CLARIFY > BLOCK > ADVISE > PASS.
    A tripped breaker upgrades BLOCK to escalate (handled by caller)."""
    actions = {v.action for v in verdicts}
    if breaker is not None and breaker.counts:
        tripped = any(v.action == BLOCK and breaker.is_suspended(v.rule_id) for v in verdicts)
        if tripped:
            return ESCALATE
    for a in (CLARIFY, BLOCK, ADVISE):
        if a in actions:
            return a
    return PASS