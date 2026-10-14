"""Rule loading and tier resolution.

Tiers: user > project > stdlib. Precedence is applied by sorting with a
tier rank; conflicts resolve later in policy (§5 of ARCHITECTURE.md).

Format: TOML ([[rule]] entries) — stdlib tomllib, zero-dependency (D13).
A YAML loader can be added later behind the same entry point.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TIER_RANK = {"user": 0, "project": 1, "stdlib": 2}


@dataclass
class ChainStep:
    """One node of a question chain (round 28: history-dependent rules).

    Jev is sharpest with ONE question over ONE coherent input block — so
    every node declares which blocks it sees (default per role):
      recognize — action only (classify the proposed action's shape)
      retrieve  — action + session record (the record is already action-keyed
                  by fact-store keyword recall)
      judge     — chain trace + action + user request (prior answers are
                  STATED FACTS here, never inferences from silence)
    recognize — exit_below short-circuits the whole chain when p < exit_below
    (benign actions cost one question). retrieve — exit_on passes the rule
    quietly when the named evidence IS on record. judge — terminal node; its
    answer drives policy via evaluate().
    """
    role: str  # recognize | retrieve | judge
    type: str  # noul | choice
    instructions: str
    choices: list = field(default_factory=list)  # choice-type nodes
    exit_below: float | None = None
    exit_on: str | None = None
    inputs: tuple = ()  # block names; empty = role default

    def blocks(self) -> tuple:
        if self.inputs:
            return tuple(self.inputs)
        return DEFAULT_INPUTS.get(self.role, ("action", "trace"))


# role -> which state blocks the node sees (one coherent job per node)
DEFAULT_INPUTS = {
    "recognize": ("action",),
    "retrieve": ("action", "session"),
    "judge": ("trace", "action", "request"),
}


@dataclass
class Rule:
    id: str
    tier: str
    type: str  # noul | score | choice | chain
    question: str = ""
    applies_to: list = field(default_factory=list)  # tool names, empty = all
    paths: list = field(default_factory=list)  # globs, empty = all
    probe: str | None = None
    threshold: float = 0.85
    severity: str = "advisory"  # blocking | advisory
    remedy: str | None = None
    description: str = ""
    criteria: list = field(default_factory=list)  # score rubric / choice options (API-required for non-noul)
    package: str | None = None
    chain: tuple | None = None  # tuple[ChainStep, ...] when type == "chain"

    @property
    def sort_key(self):
        return (TIER_RANK[self.tier], -0 if self.severity == "blocking" else 1, self.id)


def _parse_chain(steps) -> tuple:
    out = []
    for s in steps or []:
        out.append(ChainStep(
            role=s.get("role", "judge"),
            type=s.get("type", "noul"),
            instructions=s.get("instructions", ""),
            choices=list(s.get("choices", [])),
            exit_below=(float(s["exit_below"]) if s.get("exit_below") is not None else None),
            exit_on=s.get("exit_on"),
            inputs=tuple(s.get("inputs", [])),
        ))
    return tuple(out) if out else None


def _parse_rules(data: dict, tier: str, source: str) -> list[Rule]:
    rules = []
    for entry in data.get("rule", []):
        rules.append(
            Rule(
                id=str(entry["id"]),
                tier=tier,
                type=entry.get("type", "noul"),
                question=entry.get("question", ""),
                applies_to=list(entry.get("applies_to", [])),
                paths=list(entry.get("paths", [])),
                probe=entry.get("probe"),
                threshold=float(entry.get("threshold", 0.85)),
                severity=entry.get("severity", "advisory"),
                remedy=entry.get("remedy"),
                description=entry.get("description", ""),
                criteria=list(entry.get("criteria", [])),
                package=entry.get("package") if tier == "stdlib" else source,
                chain=_parse_chain(entry.get("chain")),
            )
        )
    return rules


def _load_file(path: Path, tier: str) -> list[Rule]:
    data = tomllib.loads(path.read_text())
    return _parse_rules(data, tier, str(path))


def resolve_rule_paths(project_dir: Path | None, packages: list[str], user_dir: Path | None = None):
    """Yield (path, tier) in load order. Project tier is trust-gated upstream."""
    home = Path.home()
    udir = user_dir or home / ".psi"
    for pkg in packages:
        pkg_path = udir / "packages" / pkg / "rules.toml"
        if pkg_path.exists():
            yield pkg_path, "stdlib"
    user_rules = udir / "rules.toml"
    if user_rules.exists():
        yield user_rules, "user"
    if project_dir is not None:
        rules_dir = project_dir / ".psi" / "rules"
        if rules_dir.exists():
            for p in sorted(rules_dir.glob("*.toml")):
                yield p, "project"


def package_glossary_paths(packages: list[str], user_dir: Path | None = None) -> list[Path]:
    """Glossary files shipped with rule packages — package vocabulary lives
    with the package that references it (round 28 lesson: a psi-core seed
    dictionary is a conversation-local overfit, and active tagging would
    inject stale definitions into unrelated future contexts). psi_home
    (staged) is checked first; the source tree next to this module is the
    dev fallback so tests and local runs work without staging."""
    udir = user_dir or Path.home() / ".psi"
    src_root = Path(__file__).resolve().parent / "packages"
    out: list[Path] = []
    for pkg in packages:
        candidates = [udir / "packages" / pkg / "glossary.toml",
                      src_root / pkg / "glossary.toml"]
        if "@" in pkg:
            name, _, ver = pkg.partition("@")
            # dev layout nests name/version; staging flattens to name@ver
            candidates.append(src_root / name / ver / "glossary.toml")
        for c in candidates:
            if c.exists():
                out.append(c)
                break
    return out


def load_rules(project_dir: Path | None, packages: list[str], user_dir: Path | None = None) -> list[Rule]:
    rules: list[Rule] = []
    for path, tier in resolve_rule_paths(project_dir, packages, user_dir):
        rules.extend(_load_file(path, tier))
    rules.sort(key=lambda r: r.sort_key)
    return rules