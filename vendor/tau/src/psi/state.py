"""State builder — redaction filter + budget caps (ARCHITECTURE A2, D6/D7).

The state is the only thing Jev sees. Secrets that ride into context must
not ride into state. Redaction is gitleaks-class-lite: known token shapes
plus .env-style assignments with secret-ish names, plus an entropy heuristic.
"""
from __future__ import annotations

import math
import re

SECRET_SHAPES = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # openai-style
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),  # anthropic
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),  # github PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # aws access key id
    re.compile(r"xox[bpars]-[A-Za-z0-9\-]{10,}"),  # slack
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|token|passwd|password|pwd)\s*[=:]\s*\S+"),
]

_ENTROPY_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    probs = {c: s.count(c) / len(s) for c in set(s)}
    return -sum(p * math.log2(p) for p in probs.values())


def redact(text: str) -> str:
    out = text
    for pattern in SECRET_SHAPES:
        out = pattern.sub("[REDACTED]", out)
    out = _ENTROPY_RE.sub(lambda m: "[REDACTED]" if _entropy(m.group(0)) > 4.0 else m.group(0), out)
    return out


def approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def cap(text: str, token_budget: int) -> str:
    limit = token_budget * 4
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_state(
    *,
    user_request: str,
    recent_context: list[str],
    plan_excerpt: str = "",
    fact_pack_digest: str = "",
    fact_store_digest: str = "",
    probe_outputs: dict | None = None,
    budgets: dict | None = None,
) -> dict:
    b = budgets or {}
    core_cap = int(b.get("state_core_tok", 4000))
    digest_cap = int(b.get("digest_tok", 500))
    probe_cap = int(b.get("probe_tok", 1000))

    core = cap(redact(user_request), core_cap // 2)
    context = [cap(redact(c), core_cap // max(1, len(recent_context) or 1) // 4) for c in recent_context[-5:]]

    state: dict = {
        "user_request": core,
        "recent_context": context,
        "proposed_action": {},  # filled by the interceptor per bundle
    }
    if plan_excerpt:
        state["plan_excerpt"] = cap(redact(plan_excerpt), core_cap // 4)
    if fact_pack_digest:
        state["fact_pack_digest"] = cap(redact(fact_pack_digest), digest_cap)
    if fact_store_digest:
        state["fact_store_digest"] = cap(redact(fact_store_digest), digest_cap)
    for name, output in (probe_outputs or {}).items():
        state[f"probe.{name}"] = cap(redact(str(output)), probe_cap)
    return state