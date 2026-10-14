"""ψ trace sweep — Jev-as-classifier over recorded agent traces (round 28).

The user's design: don't pay an LLM subagent to read traces; draft one cheap
noul question per error class and run Jev over EVERY mutating step (pennies),
recording raw probabilities. Analysis then picks thresholds from the F/P
separation and replicate consistency — and uncovered failure points get
sampled to draft the next class. Iterate until coverage.

Usage:
  OPENROUTER_API_KEY=... python3 spike/sweep_traces.py [glob-pattern]
  (default: one largest F trace, for cost calibration; pass "all" for the
   full 36-trace sweep)

State per step: sentinel request (kills sys probes/fact pack — classification
only) + a rolling window of the prior steps rendered with ok/ERR markers.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, "/tmp/mining")

from psi.client import JevClient, post_systemone  # noqa: E402
from psi.config import PsiConfig  # noqa: E402
from psi.intercept import PsiInterceptor  # noqa: E402
from psi.rules import _load_file  # noqa: E402
from parse import parse_cc  # noqa: E402  (miner's parser)


def choice_criteria_transport(url, api_key, state, questions):
    """Live-discovered systemone contract (probe_choice2.py, 2026-10-13):
    questions of type "choice" require a `criteria` RECORD whose KEYS are the
    allowed answers; the answer comes back as {'choice': <exact key>,
    'probabilities': {...}, 'confidence': p}. Sending `choices` alone 400s
    (fail-fast), which the interceptor's _run_chain does today, so chains with
    retrieve nodes outage deterministically. psi core is untouchable from this
    spike; the adapter lives at the sweep's transport boundary. Flag for the
    psi-core fix later: _run_chain should send criteria: {c: c for c in
    node.choices} for choice nodes."""
    qs = dict(questions)
    for k, q in qs.items():
        if isinstance(q, dict) and q.get("type") == "choice" and q.get("choices") and "criteria" not in q:
            qs[k] = {**q, "criteria": {c: c for c in q["choices"]}}
    return post_systemone(url, api_key, state, qs)

TRACES = Path("/tmp/hfsb_traces2")
OUT = Path("/tmp/mining/sweep")
SENTINEL = "(no user request captured)"
WINDOW = 6
MUTATING = {"Bash", "Edit", "Write", "MultiEdit", "NotebookEdit"}

THRESHOLDS = {"blocking": 0.99, "advisory": 0.99, "gray_band": 0.0,
              "ambiguity": 0.0, "novelty": 0.0, "repeat": 1.01}


def render_step(i, s):
    head = str(s["args"].get("command") or s["args"].get("file_path")
               or s["args"].get("path") or json.dumps(s["args"], default=str))[:85]
    line = f"step {i} [{s['tool']}]{' ERR' if s['is_error'] else ' ok'}: {head}"
    if s["is_error"]:
        # state.py caps recent_context items at ~core_tok//len//4 chars each;
        # keep the whole line within that so the reported failure stays visible
        # to questions that depend on it (SW-001/SW-005).
        errtxt = " ".join(str(s.get("result") or "").split())[:95]
        line += f" || reported: {errtxt}"
    return line


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "all":
        files = sorted(TRACES.glob("*.jsonl"))
    else:
        files = sorted(TRACES.glob(arg or "django__django-12308__haiku__r*.jsonl"))
    if not files:
        print("no trace files matched")
        return 2

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set")
        return 2
    cfg = PsiConfig(backend="openrouter",
                    api_url="https://openrouter.ai/api/v1/systemone",
                    api_key=key, thresholds=THRESHOLDS,
                    budgets={"state_core_tok": 4000})
    rules = _load_file(Path(__file__).resolve().parents[1] / "src/psi/packages/sweep/0.0.1/rules.toml", "stdlib")
    client = JevClient(url=cfg.api_url, api_key=key, retries=2,
                       transport=choice_criteria_transport)
    ic = PsiInterceptor(config=cfg, project_dir=None, client=client,
                        psi_home=Path(tempfile.mkdtemp(prefix="psi-sweep-")))
    ic.rules = rules

    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    for f in files:
        steps, _think = parse_cc(str(f))
        recs = []
        for s in steps:
            if s["tool"] not in MUTATING:
                continue
            i = s["idx"]
            prior = [render_step(j, steps[j]) for j in range(max(0, i - WINDOW), i)]
            ctx = [SENTINEL] + prior
            try:
                out = ic.inspect(s["tool"].lower(), s["args"], context_items=ctx)
                outage = out.get("outage") or ""
                if outage and ("402" in outage or "in-flight" in outage):
                    # shared key with a concurrent eval — back off once and retry
                    time.sleep(30)
                    out = ic.inspect(s["tool"].lower(), s["args"], context_items=ctx)
                    outage = out.get("outage") or ""
            except Exception as exc:
                recs.append({"i": i, "tool": s["tool"].lower(), "error": repr(exc)[:200]})
                continue
            answers = out.get("answers") or {}
            recs.append({
                "i": i, "tool": s["tool"].lower(),
                "is_error": s["is_error"],
                "cmd": str(s["args"].get("command") or s["args"].get("file_path") or "")[:120],
                "p": {k.lstrip("R"): a.get("noul", a.get("confidence")) for k, a in answers.items()},
                "outage": bool(outage),
                "chain": out.get("chain") or {},
            })
            time.sleep(0.15)
        report.append({"trace": f.name, "resolved": f.stem.endswith("__P"),
                       "n_steps": len(steps), "n_swept": len(recs), "steps": recs})
        print(f"{f.name}: {len(steps)} steps, {len(recs)} swept", flush=True)

    (OUT / ("sweep_" + ("-".join(f.stem.split("__")[:2]) if len(files) == 1 else "full")
            + ".json")).write_text(json.dumps(report, indent=1))
    # quick per-class separation read
    import collections
    agg = collections.defaultdict(lambda: [0.0, 0])
    for tr in report:
        for r in tr["steps"]:
            for k, p in (r.get("p") or {}).items():
                if p is None:
                    continue
                a = agg[(tr["resolved"], k)]
                a[0] += float(p)
                a[1] += 1
    print("\nclass mean-p by outcome (F=failed traces, P=passed traces):")
    for (res, k), (tot, n) in sorted(agg.items()):
        print(f"  {k} {res}: mean p={tot / max(n, 1):.3f} n={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
