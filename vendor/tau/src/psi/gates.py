"""Gate filter — deterministic, pre-fan-out applicability (ARCHITECTURE §4).

A rule that doesn't apply costs nothing and never becomes a Jev question.
Path gating is heuristic in v0: structured path args gate normally; bash
calls bypass path gates (a command can touch anything).
"""
from __future__ import annotations

import fnmatch

PATH_ARG_KEYS = {"path", "file_path", "paths", "filename", "target"}
ALL_TOOLS = "*"


def _action_paths(call_args: dict) -> list[str] | None:
    """Extract path-ish args. Returns None for tools that bypass path gates."""
    paths: list[str] = []
    for key in PATH_ARG_KEYS:
        val = call_args.get(key)
        if isinstance(val, str):
            paths.append(val)
        elif isinstance(val, list):
            paths.extend(v for v in val if isinstance(v, str))
    return paths or None


def rule_applies(rule, tool_name: str, action_paths: list[str] | None) -> bool:
    if rule.applies_to and tool_name not in rule.applies_to and ALL_TOOLS not in rule.applies_to:
        return False
    if rule.paths and action_paths is not None:
        # fnmatch's * already crosses '/' — normalize glob ** to fnmatch *:
        # "src/**/*.py" -> "src/*.py"; "a/**/b" -> "a*b" (zero-or-more dirs)
        pats = [pat.replace("**/", "*").replace("**", "*") for pat in rule.paths]
        if not any(
            fnmatch.fnmatch(p, pat) for p in action_paths for pat in pats
        ):
            return False
    return True


def gate(rules, tool_name: str, call_args: dict) -> list:
    """Return the rules that apply to this proposed action."""
    action_paths = _action_paths(call_args)
    return [r for r in rules if rule_applies(r, tool_name, action_paths)]


def question_set(rules, fact_pack_items: dict, sys_probes: dict) -> dict:
    """Compile the fan-out QuestionSet: R<id> rules + F<nn> items + sys probes.

    Durable API contract (the 118×400 lesson): non-noul types carry extra
    required fields — score questions need a `criteria` array, choice
    questions need a `criteria` dict keyed by allowed answers. A payload
    missing them is rejected WHOLE by the backend, killing every question
    in that call, not just the malformed one.
    """
    questions: dict = {}
    for r in rules:
        q: dict = {"type": r.type, "instructions": r.question}
        if r.type == "score" and getattr(r, "criteria", None):
            q["criteria"] = list(r.criteria)
        questions[f"R{r.id}"] = q
    for key, item in fact_pack_items.items():
        questions[f"F{key}"] = item
    questions.update(sys_probes)
    return questions