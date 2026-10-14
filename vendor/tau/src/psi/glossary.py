"""ψ glossary — pinned term definitions (round 28).

Problem: rules and instructions use terms whose meaning varies by reader
("atomic task" = a sentence? <=300 chars? no multi-step evaluation?). Every
consumer — Jev across calls, the user, the harness — should resolve the term
the SAME way, and the resolution should live in ONE auditable place.

Mechanics:
  - Glossaries are TOML ([[term]] entries) loaded user > project > stdlib;
    later loads override earlier ones per term id.
  - Rule questions / chain-node instructions may reference a term with the
    marker [[term-id]]; expand() appends a "[Definitions]" footer so Jev sees
    the pinned meaning alongside the question. Unknown markers are left as-is
    (they are ambiguity candidates for the mining path).
  - tag_terms(text) finds defined terms present in free text (word-boundary
    match) — the interceptor tags those definitions into the state, so any
    term the model or user actually uses carries its pinned meaning.
  - Accountability: verdict reproducibility = the glossary version; sha256()
    fingerprints the active dictionary for telemetry.

Fail-open discipline: a broken glossary file logs and loads nothing; a
missing term never blocks anything (D9).
"""
from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

MARKER_RE = re.compile(r"\[\[([a-zA-Z0-9_-]+)\]\]")
TAG_BUDGET = 1200  # total chars of definitions tagged into state


@dataclass
class Term:
    id: str
    definition: str
    source: str = ""


class Glossary:
    def __init__(self, terms: dict[str, Term] | None = None):
        self.terms: dict[str, Term] = terms or {}

    # -- loading ---------------------------------------------------------------

    @classmethod
    def load(cls, project_dir: Path | None, user_dir: Path | None = None,
             package_files: list[Path] | None = None) -> "Glossary":
        """stdlib package glossaries, then user (~/.psi/glossary.toml), then
        project (.psi/glossary/*.toml) — later wins per term id."""
        g = cls()
        paths: list[Path] = []
        home = Path.home()
        udir = user_dir or home / ".psi"
        if package_files:
            paths.extend(package_files)
        paths.append(udir / "glossary.toml")
        if project_dir is not None:
            gdir = project_dir / ".psi" / "glossary"
            if gdir.exists():
                paths.extend(sorted(gdir.glob("*.toml")))
        for p in paths:
            try:
                if p.exists():
                    g._merge_file(p)
            except Exception:
                continue  # fail-open: a broken glossary never blocks work
        return g

    def _merge_file(self, path: Path) -> None:
        data = tomllib.loads(path.read_text())
        for entry in data.get("term", []):
            tid = str(entry.get("id", "")).strip()
            definition = str(entry.get("definition", "")).strip()
            if tid and definition:
                self.terms[tid] = Term(id=tid, definition=definition,
                                       source=str(entry.get("source", "")))

    # -- queries ---------------------------------------------------------------

    def sha256(self) -> str:
        blob = "\n".join(f"{t.id}={t.definition}" for t in sorted(self.terms.values(), key=lambda t: t.id))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def expand(self, text: str) -> str:
        """Append a definitions footer for every [[marker]] present in text.

        Markers are left in place (they read as deliberate pointers); the
        footer carries the pinned meanings. Unknown markers stay unexpanded —
        they are candidates for the ambiguity-mining path.
        """
        if not text:
            return text
        ids: list[str] = []
        for m in MARKER_RE.finditer(text):
            tid = m.group(1).lower()
            if tid in self.terms and tid not in ids:
                ids.append(tid)
        if not ids:
            return text
        lines = [f"{tid}: {self.terms[tid].definition}" for tid in ids]
        return text.rstrip() + "\n\n[Definitions] " + " | ".join(lines)

    def tag_terms(self, text: str, budget: int = TAG_BUDGET) -> list[Term]:
        """Defined terms actually present in free text (word-boundary match),
        longest-id first so multi-word ids win over their prefixes."""
        if not text:
            return []
        out: list[Term] = []
        used = 0
        for tid in sorted(self.terms, key=len, reverse=True):
            if re.search(rf"\b{re.escape(tid)}\b", text, flags=re.IGNORECASE):
                t = self.terms[tid]
                if used + len(t.definition) > budget:
                    continue
                out.append(t)
                used += len(t.definition)
        return out

    def digest_for(self, text: str, budget: int = TAG_BUDGET) -> str:
        """Definitions footer for terms present in text (state tagging)."""
        terms = self.tag_terms(text, budget=budget)
        if not terms:
            return ""
        return "; ".join(f"{t.id}: {t.definition}" for t in terms)
