"""ψ voice — first-person self-correction text (round 28, user design).

The model-visible text of an interception must read as the model's OWN
realization — "I noticed X, therefore I should Y" — never an external
authority. Evidence (igel trace, 2026-10-13): authority-voice blocks
("Tool call blocked: psi repeat gate (SYS-REPEAT)...") produced compliance
through an external frame — the model said "the test harness is blocking
me", retried twice against evidence-carrying blocks, and only the loop
breaker's escalation threat changed behavior. A model cannot self-correct
inside a text that casts it as being corrected by something else; and it
has nothing to dodge when the correction is its own thought.

Accountability is unaffected: telemetry records the rule id, probability,
and original arguments; the transcript shows self-correction, the ledger
shows the truth. The toolResult text carries no tool/authority framing —
tau's "Tool call blocked: " prefix is stripped by the tau patch when a
thought continuation is present (psi_eval/tau_patch.py).

Banned vocabulary in model-visible text: psi, rule ids, blocked, harness,
violation, policy, gate, injected, intercept. scan() enforces it for tests
and telemetry warnings; runtime never fails a run over vocabulary (D9).
"""
from __future__ import annotations

import re

BANNED_PATTERNS = (
    r"\bpsy?\b", r"\brules?\b", r"\bblocked?\b", r"\bharness\b",
    r"\bviolation\b", r"\bpolicy\b", r"\bgate\b", r"\binject\w*\b",
    r"\bintercept\w*\b", r"\bdenied\b", r"\brefused\b",
)

_REPEAT_REMEDY = (
    "look at why the earlier attempt failed, fix that cause, "
    "or take a genuinely different approach"
)


class VoiceError(ValueError):
    """Model-visible text contains banned authority vocabulary."""


def scan(text: str) -> list[str]:
    """Return banned-vocabulary matches in text (empty = clean)."""
    hits: list[str] = []
    for pattern in BANNED_PATTERNS:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            hits.append(m.group(0))
    return hits


def _short(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text[:n] + ("…" if len(text) > n else "")


def continuation_for(
    *,
    kind: str,
    description: str = "",
    remedy: str = "",
    evidence: str | None = None,
) -> str:
    """Self-voice continuation appended to the model's own thinking block.

    kind: "repeat" (the approach already failed this session) or "rule"
    (the action conflicts with a standing constraint). The text must read
    as the model noticing, not being told.
    """
    if kind == "repeat":
        ev = f' I already ran {_short(evidence)} earlier and it failed.' if evidence else (
            " I already tried this exact approach earlier in this session and it failed.")
        text = (
            f"Wait —{ev} Nothing has changed since that attempt, so running it "
            "again will just reproduce the same failure. What changed since my "
            f"last attempt: nothing yet. The right move: {remedy or _REPEAT_REMEDY} "
            "before running anything else."
        )
    elif kind == "rule":
        text = (
            f"Hold on — {description.strip().rstrip('.')}. Doing this anyway would "
            f"be the wrong call. What I should do instead: {remedy}."
        )
    else:
        raise ValueError(f"unknown voice kind: {kind}")
    return text


def result_reason_for(*, kind: str, evidence: str | None = None, description: str = "",
                      remedy: str = "") -> str:
    """Short self-voice line for the toolResult channel (no tool framing)."""
    if kind == "repeat":
        ev = f" ({_short(evidence, 60)})" if evidence else ""
        return (
            f"I already ran this approach and it failed{ev}; nothing changed since — "
            f"I need to {remedy or _REPEAT_REMEDY} instead of re-running."
        )
    if kind == "rule":
        return f"{description.strip().rstrip('.')}. Instead: {remedy}"
    raise ValueError(f"unknown voice kind: {kind}")