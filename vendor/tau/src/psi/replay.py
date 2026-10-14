"""ψ replay — offline regression suite for the rule gate (round 28).

Feeds recorded agent traces back through the interception pipeline as
proposed_actions — nothing executes, nothing bills an agent rollout:
  - each mutating step becomes one inspect() call (rules + sys probes +
    spec gate + ledger/fact-store questions over the session digest)
  - output: per-step verdicts (BLOCK/ADVISE/PASS + rule ids + probabilities)
    and a summary (flag rate, per-rule counts, question coverage)

Purpose (pre-registered): validate rulesets against annotated failure corpora
BEFORE spending live rollouts — should-flag on weak-model failure steps,
should-not-flag on strong-model clean steps (the B4 noise-floor lesson,
operationalized as a regression test).

Trace sources (v0.1):
  - tau session JSONL (trial agent/ dirs — the adapter persists them)
  - generic JSON: [{"tool":..., "arguments":{...}}, ...] or {"steps":[...]}
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .client import JevClient
from .config import load_config
from .intercept import PsiInterceptor


def steps_from_tau_session(path: str | Path) -> list[dict]:
    """Extract tool-call steps from a tau session JSONL.

    Session entries: {"type": "message", "message": {"role": "assistant",
    "content": [{"type": "tool_call", "name": ..., "arguments": {...}}, ...]}}
    """
    steps: list[dict] = []
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if not isinstance(e, dict) or e.get("type") != "message":
                continue
            msg = e.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_call":
                    steps.append({"tool": block.get("name"), "arguments": block.get("arguments") or {}})
    return steps


def steps_from_json(path: str | Path) -> list[dict]:
    """Generic trace: [{"tool":..., "arguments":{...}}] or {"steps":[...]}."""
    data = json.loads(Path(path).read_text(errors="replace"))
    if isinstance(data, dict) and "steps" in data:
        data = data["steps"]
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of steps")
    out: list[dict] = []
    for s in data:
        if isinstance(s, dict) and s.get("tool"):
            out.append({"tool": s["tool"], "arguments": s.get("arguments") or {}})
    return out


def load_steps(path: str | Path) -> list[dict]:
    p = Path(path)
    if p.suffix == ".jsonl":
        steps = steps_from_tau_session(p)
        if steps:
            return steps
    return steps_from_json(p)


def run_replay(
    sources: list[tuple[str, list[dict]]],
    *,
    interceptor: PsiInterceptor,
    instruction: str,
    label: str = "",
) -> dict:
    """Replay every step through the interceptor; returns the report dict."""
    report: dict = {"label": label, "sources": [], "summary": {}}
    for src, steps in sources:
        src_report: dict = {"source": src, "n_steps": len(steps), "results": []}
        for i, s in enumerate(steps):
            try:
                out = interceptor.inspect(s["tool"], s["arguments"], context_items=[instruction])
            except Exception as exc:
                src_report["results"].append({"i": i, "tool": s["tool"], "error": repr(exc)[:200]})
                continue
            src_report["results"].append({
                "i": i,
                "tool": out.get("tool"),
                "blocked": out.get("blocked"),
                "reason": (out.get("reason") or "")[:200] or None,
                "action": out.get("action"),
                "verdicts": out.get("verdicts"),
                "questions": out.get("questions"),
                "chain": out.get("chain"),
                "skipped": out.get("skipped"),
                "outage": out.get("outage"),
            })
        counts: dict = {}
        for r in src_report["results"]:
            a = r.get("action") or ("BLOCKED" if r.get("blocked") else ("SKIPPED" if r.get("skipped") else "PASS"))
            counts[a] = counts.get(a, 0) + 1
        src_report["summary"] = counts
        report["sources"].append(src_report)
    total: dict = {}
    for s in report["sources"]:
        for k, v in s["summary"].items():
            total[k] = total.get(k, 0) + v
    report["summary"] = {"label": label, "n_sources": len(sources), "actions": total}
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="psi.replay", description="ψ replay — offline rule-gate regression suite")
    ap.add_argument("traces", nargs="+", help="trace files (.jsonl tau sessions or .json step lists)")
    ap.add_argument("--instruction", default="", help="task instruction text (spec digest + ledger requirements)")
    ap.add_argument("--instruction-file", default=None, help="read the instruction from a file")
    ap.add_argument("--project-dir", default=".", help="dir containing psi.toml (ruleset/thresholds config)")
    ap.add_argument("--psi-home", default=None, help="ψ home for replay scratch state (default: temp dir)")
    ap.add_argument("--dry", action="store_true", help="compile questions only — no Jev calls")
    ap.add_argument("--out", default=None, help="write the full JSON report to a file")
    a = ap.parse_args(argv)

    instruction = a.instruction
    if a.instruction_file:
        instruction = Path(a.instruction_file).read_text(errors="replace")
    if not instruction:
        print("warning: no instruction given — sys probes and the spec gate will be skipped", file=sys.stderr)

    cfg = load_config(Path(a.project_dir) if a.project_dir != "." else None)
    client = None
    if not a.dry:
        if not cfg.api_key:
            print("error: no API key for backend", cfg.backend, "— export it or pass --dry", file=sys.stderr)
            return 2
        client = JevClient(url=cfg.api_url, api_key=cfg.api_key, retries=cfg.retries)

    # replay never touches the user's real ~/.psi — scratch state by default
    import tempfile
    psi_home = Path(a.psi_home) if a.psi_home else Path(tempfile.mkdtemp(prefix="psi-replay-"))
    ic = PsiInterceptor(
        config=cfg,
        project_dir=(Path(a.project_dir) if a.project_dir != "." else None),
        client=client,
        psi_home=psi_home,
    )

    sources: list[tuple[str, list[dict]]] = []
    for t in a.traces:
        try:
            sources.append((t, load_steps(t)))
        except Exception as exc:
            print(f"error loading {t}: {exc}", file=sys.stderr)
            return 2
    if not sources or all(not steps for _, steps in sources):
        print("error: no tool-call steps found in the given traces", file=sys.stderr)
        return 2

    report = run_replay(sources, interceptor=ic, instruction=instruction, label=",".join(t for t, _ in sources))

    print(json.dumps(report["summary"], indent=1))
    for s in report["sources"]:
        print(f"--- {s['source']}: {s['n_steps']} steps, {s['summary']}")
        for r in s["results"]:
            if r.get("blocked") or r.get("action") in ("BLOCK", "ADVISE"):
                print(f"  step {r['i']} [{r['tool']}] -> {r.get('action') or 'BLOCKED'}: {(r.get('reason') or '')[:140]}")
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print("full report:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())