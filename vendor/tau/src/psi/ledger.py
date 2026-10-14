"""ψ ledger-lite — in-container session state for consistency checks (round 25).

Not the full ledger/INDEX machinery (B2/B6 are post-eval): this is the
interceptor-local slice that makes "compare the latest thinking to older
blocks" possible in autonomous runs — requirements pinned at start, every
mutating action's outcome appended, digest fed to Jev state each fan-out.

Persisted to <psi_home>/ledger.jsonl for post-hoc analysis.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

_EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit"}


class Ledger:
    def __init__(self, psi_home: Path | None = None):
        self.requirements: str | None = None
        self.attempts: list[dict] = []  # {ts, tool, summary, outcome}
        self.psi_home = psi_home or Path.home() / ".psi"
        self.path = self.psi_home / "ledger.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def set_requirements(self, text: str) -> None:
        """Pin the task instruction as the checkable spec (first input wins)."""
        if self.requirements is None and text:
            self.requirements = text

    def record(self, tool: str, summary: str, outcome: str) -> None:
        entry = {"ts": time.time(), "tool": tool, "summary": summary[:200], "outcome": outcome[:120]}
        self.attempts.append(entry)
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def digest(self, max_attempts: int = 6, char_budget: int = 1500) -> str:
        """Requirements head + last N attempt lines — capped for state budgets."""
        parts: list[str] = []
        if self.requirements:
            parts.append("REQUIREMENTS: " + self.requirements[:600])
        for a in self.attempts[-max_attempts:]:
            parts.append(f"ATTEMPT[{a['tool']} -> {a['outcome']}]: {a['summary'][:150]}")
        text = "\n".join(parts)
        return text[:char_budget]

    @property
    def has_content(self) -> bool:
        return bool(self.requirements or self.attempts)

    # -- no-progress repetition detection (round 28) --------------------------

    @staticmethod
    def _sig(tool: str, summary: str) -> str:
        """Normalized action signature — masks hashes/long numbers so
        near-identical commands match (mirrors the replay normalization)."""
        s = re.sub(r"\s+", " ", (summary or "")).strip()
        s = re.sub(r"\b[0-9a-f]{7,40}\b", "H", s)
        s = re.sub(r"\b\d{2,}\b", "N", s)
        return f"{tool}:{s[:110]}"

    def prior_attempt_evidence(self, tool: str, summary: str) -> str | None:
        """Evidence for the repeat gate's self-correction: the most recent
        earlier attempt with the same normalized signature (the command as
        recorded). Returns None when no prior attempt exists."""
        sig = self._sig(tool, summary)
        for a in reversed([a for a in self.attempts if a.get("outcome") == "executed"]):
            if self._sig(a["tool"], a["summary"]) == sig:
                return a.get("summary", "")
        return None

    def repetition_stats(self, tail_frac: float = 0.2, min_actions: int = 5) -> dict:
        """No-progress repetition detector (mined feature f2, tail churn).

        An executed action is a TAIL-REPEAT when the same normalized signature
        ran earlier with NO intervening edit/write — edit → re-run is the dev
        loop and resets the repetition state; bare re-runs cannot produce new
        information. Only actually-executed actions count. Deterministic —
        no model call; the result feeds the conditional sys.progress probe.
        """
        acts = [a for a in self.attempts if a.get("outcome") == "executed"]
        n = len(acts)
        if n < min_actions:
            return {"tail_repeats": 0, "tail_len": 0, "worst_sig": "",
                    "worst_count": 0, "n_actions": n}
        edit_idx = {i for i, a in enumerate(acts) if a["tool"].lower() in _EDIT_TOOLS}
        last_edit_before = -1
        seen_at: dict[str, int] = {}
        repeats: list[tuple[int, str]] = []
        for i, a in enumerate(acts):
            sig = self._sig(a["tool"], a["summary"])
            j = seen_at.get(sig)
            if j is not None and last_edit_before <= j:
                repeats.append((i, sig))
            seen_at[sig] = i
            if i in edit_idx:
                last_edit_before = i
        tail_start = max(int(n * (1 - tail_frac)), 0)
        tail_reps = [x for x in repeats if x[0] >= tail_start]
        counts = Counter(sig for _, sig in tail_reps)
        worst_sig, worst_count = counts.most_common(1)[0] if counts else ("", 0)
        return {"tail_repeats": len(tail_reps), "tail_len": n - tail_start,
                "worst_sig": worst_sig[:80], "worst_count": worst_count,
                "n_actions": n}