"""ψ fact pack — the task spec as a checkable artifact (ARCHITECTURE §3, B1-lite).

Generated at session start from the task instruction:
- the full instruction is the pinned requirement (spec digest → Jev state every call)
- requirement-ish lines become itemized spec entries (F-ids) for the end-of-task
  report; per-action checking rides the sys.spec probe (conflict semantics).

Mid-task appends (user corrections mid-run) are a post-eval feature — benchmarks
have no mid-run user.
"""
from __future__ import annotations

import re

_DIGEST_CAP = 1200
_MAX_ITEMS = 20

_REQ_VERB_RE = re.compile(
    r"\b(must|should|shall|need to|needs? to|requires?|expected|ensure|"
    r"make sure|implement|add|support|fix|handle|prevent|reject|expose)\b",
    re.I,
)
_BULLET_RE = re.compile(r"^(?:\d+[.)]|[-*•])\s+")


def extract_requirement_lines(text: str) -> list[str]:
    """Pull requirement-like lines out of an instruction (bullets, numbered
    items, verb-bearing lines). Naive by design — Jev judges semantics; this
    only decides what's worth itemizing."""
    out: list[str] = []
    seen: set = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _BULLET_RE.match(line):
            line = _BULLET_RE.sub("", line).strip()
        if len(line) < 8:
            continue
        is_numbered = bool(re.match(r"^\d", raw.strip()))
        if not (is_numbered or _REQ_VERB_RE.search(line)):
            continue
        line = line[:240]
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= _MAX_ITEMS:
            break
    return out


def build(instruction: str) -> dict:
    """Build the fact pack from a task instruction.

    Returns {"digest": str, "items": [{"id", "text", "check"}], "raw_len": int}.
    The digest is the pinned spec (goes into Jev state every call); items are
    the itemized requirements for the end-of-task report.
    """
    text = (instruction or "").strip()
    items = extract_requirement_lines(text)
    entries = [{"id": f"F{i + 1:02d}", "text": t, "check": "jev"} for i, t in enumerate(items)]
    return {"digest": text[:_DIGEST_CAP], "items": entries, "raw_len": len(text)}