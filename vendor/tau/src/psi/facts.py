"""ψ fact store + distiller + recall (ARCHITECTURE §4 — v0.1 in-trial slice).

- FactStore: durable session facts (files touched + purpose, approaches tried +
  outcomes, constraints discovered). Persisted to <psi_home>/facts.jsonl.
- distill_recent(): checkpoint call to the session model (openrouter chat
  completions — the cheap flash model) extracting NEW durable facts from recent
  tool events. Fail-open: any error is logged and skipped (D9).
- recall(): keyword-overlap retrieval of facts relevant to a proposed action
  (v0.1 has no embeddings — the digest rides the Jev state instead).

Jev can't generate text (System One = typed answers only), so distillation runs
through the session model's chat endpoint — the one place ψ borrows the LLM.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

DISTILL_SYSTEM = (
    "You are a session fact distiller for a coding agent. Given recent tool "
    "events and already-known facts, extract ONLY new durable facts worth "
    "remembering for the rest of the session: files touched and their purpose, "
    "approaches tried and their outcomes (especially failures), and constraints "
    "discovered (test commands, build quirks, environment limits). Output one "
    "JSON object per line, max 8 lines, format: "
    '{"files": ["path", ...], "summary": "one sentence", '
    '"kind": "file|approach|constraint"}. No prose. Never repeat a known fact.'
)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_./-]{3,}")


def _tokens(s: str) -> set:
    return {t.lower().lstrip("./") for t in _TOKEN_RE.findall((s or "").lower()) if len(t) >= 3}


class FactStore:
    def __init__(self, psi_home: Path | None = None):
        self.psi_home = psi_home or Path.home() / ".psi"
        self.path = self.psi_home / "facts.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.facts: list[dict] = []

    def load(self) -> "FactStore":
        if self.path.exists():
            for line in self.path.read_text(errors="replace").splitlines():
                try:
                    self.facts.append(json.loads(line))
                except Exception:
                    pass
        return self

    def add(self, fact: dict) -> bool:
        summary = str(fact.get("summary", ""))[:200].strip()
        if not summary:
            return False
        if any(f["summary"].lower() == summary.lower() for f in self.facts):
            return False
        entry = {
            "ts": time.time(),
            "files": [str(x) for x in (fact.get("files") or [])][:8],
            "summary": summary,
            "kind": str(fact.get("kind", "file"))[:20],
        }
        self.facts.append(entry)
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True

    def digest(self, cap: int = 900) -> str:
        lines = [
            f"[{f['kind']}] {f['summary']} ({', '.join(f['files'][:3])})"
            for f in self.facts
        ]
        return "\n".join(lines)[:cap]

    def recall(self, action_str: str, k: int = 5) -> list[dict]:
        """Keyword-overlap retrieval: facts sharing tokens with the proposed
        action (paths, tool names, module words). Good enough for v0.1 — the
        Jev questions judge semantics; this only picks what lands in state."""
        if not self.facts:
            return []
        toks = _tokens(action_str)
        scored = []
        for f in self.facts:
            ftoks = _tokens(f["summary"] + " " + " ".join(f.get("files", [])))
            score = len(toks & ftoks)
            if score:
                scored.append((score, f))
        scored.sort(key=lambda x: -x[0])
        return [f for _, f in scored[:k]]


def distill_recent(
    events_text: str,
    known_digest: str,
    *,
    api_key: str,
    base: str,
    model: str,
    timeout: float = 45.0,
) -> list[dict]:
    """One chat-completions call to the session model; returns parsed fact dicts.

    `base` is the Jev API URL (…/v1/systemone); the chat endpoint is derived by
    stripping the systemone suffix.
    """
    url = base.rstrip("/")
    if url.endswith("/systemone"):
        url = url[: -len("/systemone")]
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": DISTILL_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"KNOWN FACTS:\n{known_digest or '(none)'}\n\n"
                    f"RECENT TOOL EVENTS:\n{events_text[:6000]}"
                ),
            },
        ],
    }
    req = urllib.request.Request(
        url + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = json.load(resp)["choices"][0]["message"]["content"]
    out: list[dict] = []
    for line in (content or "").splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if isinstance(d, dict) and d.get("summary"):
            out.append(d)
    return out[:8]