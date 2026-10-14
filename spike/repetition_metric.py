"""Deterministic repetition metric across the clean contrast corpus.

Offline mirror of the ledger's sys.progress detector (round 28): for each
trace, count tail actions whose normalized signature ran earlier with no
intervening edit. Reports the F/P separation per task (replicate means) —
the overfit check for any loop-shaped rule.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from psi.ledger import Ledger  # noqa: E402
sys.path.insert(0, "/tmp/mining")
from parse import parse_cc  # noqa: E402

TRACES = Path("/tmp/hfsb_traces2")

rows = []
for p in sorted(TRACES.glob("*.jsonl")):
    task, model, rep, res = p.stem.rsplit("__", 3)
    steps, _ = parse_cc(str(p))
    lg = Ledger(None)
    for s in steps:
        lg.record(s["tool"], json.dumps(s["args"], default=str), "executed")
    st = lg.repetition_stats()
    rows.append({"task": task, "model": model, "rep": rep, "resolved": res == "P",
                 "n_actions": st["n_actions"], "tail_repeats": st["tail_repeats"],
                 "tail_len": st["tail_len"],
                 "churn": round(st["tail_repeats"] / max(st["tail_len"], 1), 2)})

print(f"{'task':40} {'model':6} {'rep':3} {'res':3} {'acts':5} {'tailrep':7} {'churn':5}")
for r in rows:
    print(f"{r['task']:40} {r['model']:6} {r['rep']:3} {'P' if r['resolved'] else 'F':3} "
          f"{r['n_actions']:<5} {r['tail_repeats']:<7} {r['churn']:<5}")

by_task = defaultdict(lambda: {"F": [], "P": []})
for r in rows:
    by_task[r["task"]]["P" if r["resolved"] else "F"].append(r["churn"])
wins = ties = losses = 0
for task, m in by_task.items():
    if not m["F"] or not m["P"]:
        continue
    f, p = sum(m["F"]) / len(m["F"]), sum(m["P"]) / len(m["P"])
    verdict = "F>P" if f > p else ("tie" if f == p else "P>F")
    if f > p:
        wins += 1
    elif f == p:
        ties += 1
    else:
        losses += 1
    print(f"{task[:50]:50} F_churn={f:.2f} P_churn={p:.2f} -> {verdict}")
print(f"\nseparation: F>P on {wins} tasks, tie {ties}, P>F on {losses}")

# --- ended-without-acting (trace-level, deterministic; mined 2026-10-13) ----
# The second uncovered family: traces that end in reading/searching with zero
# (or near-zero) mutations on a task that requires a code change — the agent
# burned its budget exploring. Live-psi equivalent: a ledger check ("no
# mutating action in the last K calls").
print("\nended-without-acting (0 edits, or last 8 actions all read-only):")
ewa = 0
for pth in sorted(TRACES.glob("*.jsonl")):
    task, model, rep, res = pth.stem.rsplit("__", 3)
    steps, _ = parse_cc(str(pth))
    mutating = [s for s in steps if s["tool"] in ("Edit", "Write", "MultiEdit", "NotebookEdit")]
    tail = steps[-8:]
    tail_readonly = bool(tail) and all(s["tool"] in ("Read", "Grep", "Glob") for s in tail)
    flag = (len(mutating) == 0 and len(steps) >= 5) or (tail_readonly and len(steps) >= 5)
    never_started = len(steps) < 5 and len(mutating) == 0  # 11630-r3: 0 steps on a code task
    if flag or never_started:
        ewa += 1
        print(f"  {pth.stem}  steps={len(steps)} edits={len(mutating)}")
print(f"flagged {ewa} traces")
