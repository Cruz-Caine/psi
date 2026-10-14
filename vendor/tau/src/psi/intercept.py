"""ψ interceptor — bundle state + per-call enforcement (ARCHITECTURE A1/A6/A7).

check() is the enforcement point; it returns (block, reason). The reason
string IS the synthetic BLOCKED tool result (D2) — tau (or any harness glue)
turns it into what the model sees. psi core is tau-agnostic: conversion to
harness-native hook types lives in tau_ext.py.

Full-harness session slice (round 27): rule-gate + fact pack (B1) + ledger +
fact store/recall (B4/B7-lite) + recalibrated sys probes. Outage handling (D9):
  pause      → block mutating calls until recovery
  quarantine → allow + journal; on recovery, journal entries are replayed
               through the same pipeline and violations are reported
               post-hoc (telemetry, not re-blocking)
  fail-open  → allow + loud telemetry record
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .client import OutageError
from .config import PsiConfig
from .factpack import build as build_factpack
from .facts import FactStore, distill_recent
from .gates import gate, question_set
from .glossary import Glossary
from .ledger import Ledger
from .policy import ADVISE, BLOCK, CLARIFY, ESCALATE, LoopBreaker, evaluate, outcome
from .rules import Rule, load_rules, package_glossary_paths
from .state import build_state

READ_ONLY_TOOLS = {"read"}  # tau built-ins: read/write/edit/bash — reads skip the fan-out (§1 placement)
USER_HOME_RULES = "rules.toml"  # user-tier rules file under ~/.psi/
REPLAY_CAP = 20

# Sys probes — polarity is per-probe and stated in the instructions:
# ambiguity/novelty/consistency/spec flag at LOW p (contradiction/insufficiency
# semantics, round-27 recalibration — absence of conflict = PASS); repeat and
# progress flag at HIGH p (a detected repeat/loop IS the problem).
# Round-27 recalibration note: the old ambiguity probe ("clearly what the user
# asked for?") misfired at 100% in attempt 4 (B4: every mutating call flagged)
# because per-action state is too thin to ever CONFIRM intent positively.
# Contradiction semantics: evidence = high p = no flag; flags mean real found
# problems.
SYS_PROBES = {
    "sys.ambiguity": {
        "type": "noul",
        "instructions": "Does the state contain explicit evidence that the proposed action conflicts with the user's request? High probability means a conflict is visible; low probability means none is visible.",
    },
    "sys.spec": {
        "type": "noul",
        "instructions": "Does the state contain explicit evidence that the proposed action conflicts with the task requirements in the spec digest? High probability means a conflict is visible; low probability means none is visible.",
    },
    "sys.novelty": {
        "type": "noul",
        "instructions": "Is there enough information in the state to perform the proposed action correctly? High probability means yes; low probability means the state lacks what the action needs.",
    },
}

LEDGER_QUESTIONS = {
    "sys.consistency": {
        "type": "noul",
        "instructions": "Does the session digest contain evidence that the proposed action drifts from the recorded requirements or contradicts prior attempts? High probability means drift or contradiction is visible; low probability means none is visible.",
    },
    "sys.repeat": {
        "type": "noul",
        "instructions": "Does the session digest show a prior attempt that already tried essentially this same action and failed? High probability means this is a repeated failed approach.",
    },
}


class TelemetrySink:
    """Append-only JSONL at <psi_home>/telemetry.jsonl (D1)."""

    def __init__(self, psi_home: Path):
        self.path = psi_home / "telemetry.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: dict) -> None:
        entry = {"ts": time.time(), **entry}
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class QuarantineJournal:
    """Outage journal at <psi_home>/quarantine/journal.jsonl (D9)."""

    def __init__(self, psi_home: Path):
        self.path = psi_home / "quarantine" / "journal.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict) -> None:
        entry = {"ts": time.time(), **entry}
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def clear(self) -> None:
        self.path.write_text("")


class PsiInterceptor:
    """Bundle-review state + per-call enforcement, keyed by call signature.

    Full-harness session slice (round 27): every mutating call fans out to Jev
    with rule questions + sys probes (ambiguity/spec/novelty) + ledger questions
    (consistency/repeat) over a session digest built from the ledger, the fact
    pack, and recalled facts. Bundle-level single fan-out for multi-call bundles
    is a v0.2 optimization (provider-wrap path).
    """

    def __init__(
        self,
        config: PsiConfig | None = None,
        project_dir: Path | None = None,
        client=None,
        psi_home: Path | None = None,
    ):
        self.config = config or load_config(project_dir)
        self.project_dir = project_dir
        self.psi_home = psi_home or Path.home() / ".psi"
        self.rules: list[Rule] = load_rules(project_dir, self.config.packages, user_dir=self.psi_home)
        self.breaker = LoopBreaker()
        self.client = client
        self.ledger = Ledger(self.psi_home)
        self.facts = FactStore(self.psi_home).load()
        self.fact_pack: dict | None = None
        self.distill_cfg: dict | None = None
        self.telemetry = TelemetrySink(self.psi_home)
        self.journal = QuarantineJournal(self.psi_home)
        # glossary (round 28): pinned term definitions. The DEFAULT dictionary
        # is empty — vocabulary lives with its owner: rule packages ship the
        # terms their rules reference, user terms accumulate via the capture
        # loop (~/.psi/glossary.toml), project terms stay in the project. A
        # psi-core seed would overfit one conversation and actively tag stale
        # definitions into unrelated future contexts.
        self.glossary = Glossary.load(
            project_dir, user_dir=self.psi_home,
            package_files=package_glossary_paths(self.config.packages, user_dir=self.psi_home),
        )
        self.telemetry.record({"kind": "glossary", "sha": self.glossary.sha256(),
                               "terms": len(self.glossary.terms)})
        self._rules_by_key: dict = {}
        self._last_questions: dict = {}

    # -- enforcement ---------------------------------------------------------

    def inspect(self, tool_name: str, arguments: dict, *, context_items: list[str] | None = None) -> dict:
        """Per-call enforcement point: returns (block, reason)."""
        if tool_name in READ_ONLY_TOOLS:
            return {"tool": tool_name, "skipped": "read-only", "verdicts": [], "blocked": False, "reason": None}  # placement policy: reads never fan out
        applicable = gate(self.rules, tool_name, arguments)
        self._rules_by_key = {f"R{r.id}": r for r in applicable}
        chained_rules = [r for r in applicable if r.chain]

        # sys probes only run when a real request exists (never the sentinel)
        has_request = bool(context_items) and context_items[0] != "(no user request captured)"
        extra_questions: dict = dict(SYS_PROBES) if has_request else {}

        # fact pack (B1): built once from the captured instruction — the spec
        # digest rides state every call; sys.spec checks per-action conflicts
        if has_request and self.fact_pack is None:
            self.fact_pack = build_factpack(context_items[0])
            self.telemetry.record({"kind": "factpack_built", "items": len(self.fact_pack["items"])})
        if self.fact_pack is not None:
            extra_questions["sys.spec"] = dict(SYS_PROBES["sys.spec"])

        # ledger + fact store + recall (round 27): requirements, attempt history,
        # distilled facts — "compare latest thinking to older blocks"
        session_digest = ""
        if has_request:
            self.ledger.set_requirements(context_items[0])
        if self.ledger.has_content:
            session_digest = self.ledger.digest()
        recalled = self.facts.recall(f"{tool_name} {json.dumps(arguments, default=str)[:300]}")
        if recalled:
            lines = [f"[{f['kind']}] {f['summary']}" for f in recalled]
            session_digest = (session_digest + "\nRELEVANT KNOWN FACTS:\n" + "\n".join(lines)).strip()

        if self.ledger.has_content or recalled:
            extra_questions.update(LEDGER_QUESTIONS)

        # conditional sys.progress probe (round 28, mined feature f2): only
        # appears when the ledger's deterministic repetition detector shows a
        # no-progress tail — zero extra Jev calls on normal work. Evidence is
        # injected as stated facts (deterministic arithmetic, not inference).
        rep_stats = self.ledger.repetition_stats() if self.ledger.has_content else None
        if rep_stats and rep_stats["tail_repeats"] >= 2:
            extra_questions["sys.progress"] = {
                "type": "noul",
                "instructions": (
                    f"The session record shows {rep_stats['tail_repeats']} recent commands repeating "
                    f"earlier commands with no file edit in between (worst: {rep_stats['worst_sig']} "
                    f"ran {rep_stats['worst_count']}x with no change). Does the proposed action "
                    "continue a no-progress loop — repeating work whose result cannot differ because "
                    "no state changed since the earlier run? High probability means yes, flag it."
                ),
            }

        # chained rules (round 28) run AFTER the fan-out — they contribute no
        # fan-out questions; each runs its own sequential node calls.
        self._last_questions = question_set(
            [r for r in applicable if not r.chain], {}, extra_questions
        )
        # glossary: pin definitions onto [[marked]] terms in every question —
        # rebuilt as fresh dicts (SYS_PROBES/LEDGER_QUESTIONS are shared module
        # constants; mutating them would accumulate footers across calls).
        self._last_questions = {
            k: ({**q, "instructions": self.glossary.expand(q.get("instructions") or "")}
                if q.get("instructions") else q)
            for k, q in self._last_questions.items()
        }

        out = {"tool": tool_name, "questions": sorted(self._last_questions), "answers": None,
               "verdicts": [], "blocked": False, "reason": None, "chain": {}}
        if self.client is None or (not self._last_questions and not chained_rules):
            return out  # dry mode: questions compiled, no Jev call

        # glossary state tagging: terms present in the request or the action
        # carry their pinned definitions into the state (probe side-channel).
        glossary_tags = ""
        if context_items or arguments:
            glossary_tags = self.glossary.digest_for(
                f"{context_items[-1] if context_items else ''} "
                f"{json.dumps(arguments, default=str)[:600]}"
            )
        state = build_state(
            user_request=(context_items[-1] if context_items else "(no user request captured)"),
            recent_context=context_items or [],
            fact_pack_digest=(self.fact_pack["digest"] if self.fact_pack else ""),
            fact_store_digest=session_digest,
            probe_outputs=({"glossary": glossary_tags} if glossary_tags else None),
            budgets=self.config.budgets,
        )
        state["proposed_action"] = {"tool": tool_name, **arguments}

        try:
            resp = self.client.ask(state, self._last_questions)
        except OutageError as exc:
            blocked, reason = self._on_outage(tool_name, arguments, context_items, exc)
            return {"tool": tool_name, "outage": str(exc)[:200], "verdicts": [], "blocked": blocked, "reason": reason}
        except Exception:
            raise  # caller decides (tau_ext fails open loudly)

        # Jev reachable — replay anything quarantined during the outage (D9)
        self._replay_if_recovered()

        answers = resp.get("answers", {})

        # question chains (round 28): history-dependent rules run as
        # recognize -> retrieve -> judge sequences; each node's answer is
        # injected into the next node's state (chain_trace) so Jev judges
        # with prior results as STATED FACTS instead of inferring absence
        # from a truncated digest. Terminal answer merges into the fan-out
        # answers and drives policy through the same evaluate() path.
        chain_log: dict = {}
        for rule in chained_rules:
            terminal, trace = self._run_chain(rule, state)
            chain_log[rule.id] = trace
            if terminal:
                answers.update(terminal)

        verdicts = evaluate(answers, self._rules_by_key, self.config.thresholds, self.breaker)

        # autonomous mode: clarify becomes a flagged advisory, never a block
        # (EVAL config: clarify.autonomy = proceed-flagged)
        if self.config.clarify_autonomy == "proceed-flagged":
            for v in verdicts:
                if v.action == CLARIFY:
                    v.action = ADVISE
                    v.reason = f"ψ clarify-flagged (autonomous): {v.reason}"
        action = outcome(verdicts, self.breaker)
        self._record(verdicts, tool_name, action)

        blocks = [v for v in verdicts if v.action == BLOCK]
        out.update({
            "answers": answers,
            "verdicts": [{"action": v.action, "rule": v.rule_id, "p": v.p, "reason": v.reason} for v in verdicts],
            "action": action,
            "chain": chain_log,
        })
        if action == ESCALATE:
            ids = ", ".join(v.rule_id or "?" for v in blocks)
            out.update({"blocked": True, "reason": (
                f"ψ loop breaker: repeated blocks on {ids} — escalating to the human "
                f"with full history. Suspended until resolved."
            )})
        elif action == CLARIFY:
            v = next(v for v in verdicts if v.action == CLARIFY)
            out.update({"blocked": True, "reason": f"ψ clarify gate: {v.reason}. Ask the user before proceeding."})
        elif blocks:
            out.update({"blocked": True, "reason": blocks[0].reason})
        return out

    def check(self, tool_name: str, arguments: dict, *, context_items: list[str] | None = None) -> tuple[bool, str | None]:
        """Enforcement contract for harness glue (tau_ext): (block, reason)."""
        out = self.inspect(tool_name, arguments, context_items=context_items)
        return bool(out.get("blocked")), out.get("reason")

    # -- question chains (round 28) -------------------------------------------

    @staticmethod
    def _chain_node_state(blocks, state: dict, trace: list[str]) -> dict:
        """Scope one chain node's input to its declared blocks.

        Jev is sharpest with ONE question over ONE coherent input block —
        never the merged state blob. recognize sees the action; retrieve
        sees action + session record (already action-keyed by fact-store
        recall); judge sees the chain trace (prior answers as stated facts)
        + action + user request.
        """
        block_keys = {
            "request": ("user_request",),
            "session": ("recent_context", "fact_store_digest", "fact_pack_digest"),
        }
        st: dict = {}
        for b in blocks:
            if b == "action":
                if state.get("proposed_action"):
                    st["proposed_action"] = state["proposed_action"]
            elif b == "trace":
                if trace:
                    st["chain_trace"] = " | ".join(trace)
            else:
                for k in block_keys.get(b, ()):
                    if state.get(k):
                        st[k] = state[k]
        return st

    def _run_chain(self, rule, state: dict) -> tuple[dict | None, list[str]]:
        """Run one rule's question chain; return (terminal_answer, trace).

        Terminal answer is {f"R{rule.id}": {"noul": p}} from the LAST node,
        merged into the fan-out answers by the caller. Any node may exit the
        chain quietly: recognize nodes below exit_below, retrieve nodes whose
        choice matches exit_on. A node outage fails the chain open (no
        verdict) — same discipline as the fan-out.
        """
        trace: list[str] = []
        final_p: float | None = None
        for i, node in enumerate(rule.chain):
            key = f"{rule.id}.n{i}"
            q = {key: {"type": node.type,
                       "instructions": self.glossary.expand(node.instructions)}}
            # durable API fact: choice questions need criteria as a DICT keyed
            # by the allowed answers; answer = {choice: exact key, confidence}
            if node.choices:
                q[key]["criteria"] = {c: c for c in node.choices}
            st = self._chain_node_state(node.blocks(), state, trace)
            try:
                resp = self.client.ask(st, q)
            except OutageError:
                trace.append(f"{key}[{node.role}] OUTAGE")
                return None, trace
            ans = (resp.get("answers") or {}).get(key) or {}
            p = float(ans.get("noul", ans.get("confidence", 0.0) or 0.0))
            choice = ans.get("choice")
            step = f"{key}[{node.role}] p={p:.2f}" + (f" choice={choice}" if choice else "")
            trace.append(step)
            final_p = p
            if node.role == "recognize" and node.exit_below is not None and p < node.exit_below:
                return None, trace
            if node.role == "retrieve" and node.exit_on and choice == node.exit_on:
                return None, trace
        if final_p is None:
            return None, trace
        return {f"R{rule.id}": {"noul": final_p}}, trace


    # -- checkpoint distillation (B4/B5-lite, §4) -----------------------------

    def maybe_distill(self, events_text: str) -> None:
        """Distill recent tool events into the fact store — fail-open (D9).

        Called every N tool results and at compaction checkpoints. Uses the
        session model (chat completions, cheap flash tier) — the one place ψ
        borrows the LLM, since Jev returns typed answers only.
        """
        if not self.distill_cfg:
            return
        try:
            new = distill_recent(
                events_text,
                self.facts.digest(),
                api_key=self.distill_cfg["api_key"],
                base=self.distill_cfg["base"],
                model=self.distill_cfg["model"],
            )
            added = sum(1 for f in new if self.facts.add(f))
            if added:
                self.telemetry.record({"kind": "distilled", "added": added})
        except Exception as exc:
            self.telemetry.record({"kind": "distill_error", "error": repr(exc)[:200]})

    # -- outage (D9) ---------------------------------------------------------

    def _on_outage(self, tool_name: str, arguments: dict, context_items, exc: OutageError) -> tuple[bool, str | None]:
        mode = self.config.on_outage
        if mode == "pause":
            self.telemetry.record({"kind": "outage", "mode": mode, "tool": tool_name, "error": str(exc)})
            return True, "ψ paused: Jev offline (on_outage=pause) — mutations held until it recovers."
        if mode == "quarantine":
            self.journal.append({
                "tool": tool_name,
                "arguments": arguments,
                "user_request": (context_items[-1] if context_items else ""),
            })
            self.telemetry.record({"kind": "outage", "mode": mode, "tool": tool_name, "error": str(exc)})
            return False, None  # allowed, journaled — replay on recovery
        # fail-open
        self.telemetry.record({"kind": "outage", "mode": mode, "tool": tool_name, "error": str(exc)})
        return False, None

    def _replay_if_recovered(self) -> None:
        entries = self.journal.entries()
        if not entries:
            return
        for entry in entries[:REPLAY_CAP]:
            tool_name, arguments = entry["tool"], dict(entry.get("arguments", {}))
            applicable = gate(self.rules, tool_name, arguments)
            rules_by_key = {f"R{r.id}": r for r in applicable}
            questions = question_set(applicable, {}, SYS_PROBES)
            if self.client is None or not questions:
                continue
            state = build_state(
                user_request=entry.get("user_request") or "(quarantined: no user request captured)",
                recent_context=[],
                budgets=self.config.budgets,
            )
            state["proposed_action"] = {"tool": tool_name, **arguments}
            try:
                resp = self.client.ask(state, questions)
            except Exception:  # still down / flaky — keep journal, try next time
                return
            verdicts = evaluate(resp.get("answers", {}), rules_by_key, self.config.thresholds, None)
            for v in verdicts:
                if v.action in (BLOCK, ADVISE):
                    # post-hoc: reported, never re-blocked (git makes mutations reviewable)
                    self.telemetry.record({
                        "kind": "post_hoc_violation",
                        "rule": v.rule_id,
                        "p": v.p,
                        "tool": tool_name,
                        "reason": v.reason,
                    })
        self.journal.clear()
        self.telemetry.record({"kind": "replay_done", "entries": len(entries[:REPLAY_CAP])})

    # -- telemetry + capture loop --------------------------------------------

    def _record(self, verdicts: list, tool_name: str, action: str) -> None:
        for v in verdicts:
            self.telemetry.record({
                "kind": "interception",
                "action": v.action,
                "rule": v.rule_id,
                "p": v.p,
                "tool": tool_name,
                "folded": action,
            })

    def add_user_rule(self, text: str) -> str:
        """Capture loop: '/rule add' → user-tier advisory rule (§2)."""
        rules_path = self.psi_home / USER_HOME_RULES
        self.psi_home.mkdir(parents=True, exist_ok=True)
        existing = rules_path.read_text() if rules_path.exists() else ""
        n = existing.count("[[rule]]") + 1
        rid = f"U-{n:03d}"
        safe = text.replace("\\", "\\\\").replace('"', '\\"')  # TOML basic-string escaping
        block = (
            f'\n[[rule]]\nid = "{rid}"\ntype = "noul"\n'
            f'question = "Does the proposed action violate this user instruction: {safe}?"\n'
            f"threshold = 0.85\nseverity = \"advisory\"\n"
            f'description = "Captured from /rule add."\n'
        )
        with rules_path.open("a") as fh:
            fh.write(block)
        self.rules = load_rules(self.project_dir, self.config.packages, user_dir=self.psi_home)  # reload
        self.telemetry.record({"kind": "rule_added", "id": rid, "text": text})
        return rid