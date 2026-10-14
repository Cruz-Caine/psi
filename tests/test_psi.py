#!/usr/bin/env python3
"""ψ v0 skeleton tests — plain asserts, zero dependencies beyond stdlib.

Run: python3 tests/test_psi.py
Covers: config backends, rule loading + tiers, gate filter, redactor,
policy decision table, loop breaker, interceptor with a fake transport.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from psi.config import BACKENDS, PsiConfig, load_config  # noqa: E402
from psi.gates import gate, question_set  # noqa: E402
from psi.intercept import SYS_PROBES, PsiInterceptor  # noqa: E402
from psi.policy import (  # noqa: E402
    ADVISE,
    BLOCK,
    CLARIFY,
    PASS,
    ESCALATE,
    LoopBreaker,
    evaluate,
    outcome,
)
from psi.rules import Rule, load_rules  # noqa: E402
from psi.state import build_state, redact  # noqa: E402

FAILED = []


def check(name, cond):
    if cond:
        print(f"  ok  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}")


# --- config ---
check("backends registered", set(BACKENDS) == {"typesafe", "openrouter"})
cfg = PsiConfig(backend="openrouter", api_url=BACKENDS["openrouter"][0], api_key=None)
check("openrouter url", "openrouter.ai" in cfg.api_url)

# --- rules ---
r_dup = Rule(id="CC-014", tier="stdlib", type="noul", question="dup?",
             applies_to=["edit", "write"], paths=["src/**/*.py"], threshold=0.85,
             severity="blocking", remedy="reuse", description="no dup")
r_pin = Rule(id="CC-050", tier="user", type="noul", question="pinned?",
             threshold=0.85, severity="blocking", description="constraint")
r_style = Rule(id="CC-042", tier="stdlib", type="score", question="maintainable?",
               applies_to=["edit", "write"], threshold=1.5, severity="advisory")

# --- gates ---
# r_pin has no applies_to / paths → applies to every tool and path
g = gate([r_dup, r_pin, r_style], "write", {"path": "src/app.py"})
check("gate: matching path+tool", [r.id for r in g] == ["CC-014", "CC-050", "CC-042"])
g2 = gate([r_dup, r_pin, r_style], "write", {"path": "docs/readme.md"})
check("gate: path glob excludes src rule", [r.id for r in g2] == ["CC-050", "CC-042"])
r_bash = Rule(id="CC-001", tier="stdlib", type="noul", question="secrets?",
              applies_to=["bash", "edit", "write"], paths=["src/**"],
              threshold=0.90, severity="blocking", description="no secrets")
check("gate: bash bypasses path gate (touches anything)",
      [r.id for r in gate([r_bash], "bash", {"command": "cat .env"})] == ["CC-001"])
check("gate: path gate still filters non-bash",
      gate([r_bash], "write", {"path": "docs/x.py"}) == [])
qs = question_set(g, {"3": {"type": "noul", "instructions": "paginates?"}}, SYS_PROBES)
check("question keys namespaced",
      set(qs) == {"RCC-014", "RCC-050", "RCC-042", "F3", "sys.ambiguity", "sys.spec", "sys.novelty"})

# --- state / redaction ---
red = redact("key = sk-abcdefghijklmnopqrst1234 and AWS AKIAIOSFODNN7EXAMPLE")
check("redact: api keys stripped", "sk-abc" not in red and "AKIA" not in red and "[REDACTED]" in red)
st = build_state(user_request="build it in SDL2", recent_context=["ctx line"], budgets={"state_core_tok": 4000})
check("state has named core fields", st["user_request"] == "build it in SDL2" and st["recent_context"] == ["ctx line"])

# --- policy table ---
thr = {"blocking": 0.85, "advisory": 0.75, "gray_band": 0.10, "ambiguity": 0.60, "novelty": 0.60}
by_key = {"RCC-014": r_dup, "RCC-042": r_style, "RCC-050": r_pin}

v = evaluate({"RCC-014": {"noul": 0.93}}, by_key, thr)
check("block above threshold", v[0].action == BLOCK and "BLOCKED by rule CC-014" in v[0].reason and "Remedy" in v[0].reason)
v = evaluate({"RCC-014": {"noul": 0.80}}, by_key, thr)
check("gray band advises", v[0].action == ADVISE and "near-threshold" in v[0].reason)
v = evaluate({"RCC-014": {"noul": 0.40}}, by_key, thr)
check("pass below threshold", v == [])
v = evaluate({"RCC-042": {"score": 1.0, "confidence": 0.9}}, by_key, thr)
check("advisory rule advises", v[0].action == ADVISE)
v = evaluate({"sys.ambiguity": {"noul": 0.30}}, by_key, thr)
check("ambiguity gate clarifies", v[0].action == CLARIFY)

brk = LoopBreaker()
for _ in range(3):
    evaluate({"RCC-014": {"noul": 0.95}}, by_key, thr, brk)
check("loop breaker trips after 3", brk.is_suspended("CC-014"))
v = evaluate({"RCC-014": {"noul": 0.95}}, by_key, thr, brk)
check("suspended rule goes quiet", v == [])

# --- interceptor with fake client ---
def fake_client(url, key, state, questions):
    # matches JevClient.ask(state, questions) contract: noul p=0.95 for all —
    # rule questions "violated", sys probes "clearly asked / not novel" (high = clean)
    return {"answers": {k: {"noul": 0.95} for k in questions}}


import tempfile  # noqa: E402
from psi.client import JevClient, OutageError  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    ic = PsiInterceptor(
        config=PsiConfig(backend="typesafe", api_url=BACKENDS["typesafe"][0], api_key="test",
                         thresholds=thr, budgets={"state_core_tok": 4000},
                         on_outage="quarantine"),
        project_dir=None,
        client=None,
        psi_home=home,
    )
    ic.client = type("C", (), {"ask": staticmethod(
        lambda state, questions: {"answers": {k: {"noul": 0.95} for k in questions}}
    )})()
    ic.rules = [r_dup, r_pin]
    ic.begin_bundle = lambda **kw: None  # not part of the v0 check() path anymore
    blocked, reason = ic.check("write", {"path": "src/app.py", "content": "x = 1"})
    check("interceptor blocks on violation", blocked and "BLOCKED by rule" in (reason or ""))
    ok, _ = ic.check("read", {"path": "src/app.py"})
    check("interceptor passes clean call", not ok)

    # --- outage modes (D9) ---
    def down(state, questions):
        raise OutageError("simulated outage")

    ic.client = type("C", (), {"ask": staticmethod(down)})()
    blocked, reason = ic.check("write", {"path": "src/app.py"})
    check("quarantine allows + journals", not blocked and len(ic.journal.entries()) == 1)

    ic.config.on_outage = "pause"
    blocked, reason = ic.check("write", {"path": "src/app.py"})
    check("pause blocks with reason", blocked and "paused" in (reason or ""))

    ic.config.on_outage = "fail-open"
    ok, _ = ic.check("write", {"path": "src/app.py"})
    check("fail-open allows", not ok)
    check("fail-open writes no journal", len(ic.journal.entries()) == 1)

    # --- recovery replay (D9) ---
    ic.config.on_outage = "quarantine"
    ic.client = type("C", (), {"ask": staticmethod(
        lambda state, questions: {"answers": {k: {"noul": 0.95} for k in questions}}
    )})()
    ok, _ = ic.check("write", {"path": "src/app.py"})
    check("recovery replays journal", len(ic.journal.entries()) == 0)
    tel = (home / "telemetry.jsonl").read_text()
    check("replay reports post-hoc violations", "post_hoc_violation" in tel)

    # --- capture loop: /rule add ---
    rid = ic.add_user_rule('always explain in this format: "summary then diff"')
    check("rule add writes user rules", (home / "rules.toml").exists() and rid == "U-001")
    import tomllib as _tl  # noqa: E402
    parsed = _tl.loads((home / "rules.toml").read_text())
    check("captured rule parses + phrased", parsed["rule"][0]["id"] == "U-001"
          and "user instruction" in parsed["rule"][0]["question"])
    check("reloaded rules include capture", any(r.id == "U-001" for r in ic.rules))

    # --- context ring (tau_ext) ---
    from psi.tau_ext import _Ring  # noqa: E402
    ring = _Ring()
    for i in range(7):
        ring.add(f"input {i}")
    check("ring keeps last 5", ring.snapshot() == [f"input {i}" for i in range(2, 7)])

    # --- telemetry wrote interceptions ---
    check("telemetry recorded interceptions", '"kind": "interception"' in tel or '"kind":"interception"' in tel)

print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {FAILED}")
    sys.exit(1)
print("ALL TESTS PASSED")
# --- ledger-lite (round 25) ---
from psi.ledger import Ledger  # noqa: E402
from psi.config import load_config  # noqa: E402
import tempfile as _tf  # noqa: E402

check("ledger digest content", Ledger().digest() == "" and Ledger().has_content is False)
_l = Ledger()
_l.set_requirements("build it in SDL2")
_l.record("bash", "cargo build", "error")
_l.record("write", "renderer.py", "executed")
check("ledger digest has req+attempts", "SDL2" in _l.digest() and "cargo build" in _l.digest())

with _tf.TemporaryDirectory() as tmp2:
    ic2 = PsiInterceptor(
        config=PsiConfig(backend="typesafe", api_url=BACKENDS["typesafe"][0], api_key="test",
                         thresholds=thr, budgets={"state_core_tok": 4000},
                         clarify_autonomy="proceed-flagged"),
        project_dir=None, client=None, psi_home=Path(tmp2),
    )
    ic2.rules = [r_pin]
    ic2.client = type("C", (), {"ask": staticmethod(
        lambda state, questions: {"answers": {
            **{k: {"noul": 0.5} for k in questions if k.startswith("sys.") and k != "sys.repeat"},
            "sys.repeat": {"noul": 0.95},
        }}
    )})()
    ic2.begin_bundle = lambda **kw: None
    blocked, reason = ic2.check("write", {"path": "src/x.py"}, context_items=["build it in SDL2"])
    check("repeat gate blocks with SYS-REPEAT", blocked and "SYS-REPEAT" in (reason or "") and "repeat" in (reason or ""))
    ic2.check("write", {"path": "src/x.py"}, context_items=["build it in SDL2"])
    ic2.check("write", {"path": "src/x.py"}, context_items=["build it in SDL2"])
    check("SYS-REPEAT breaker trips after 3", ic2.breaker.is_suspended("SYS-REPEAT"))
    blocked4, _ = ic2.check("write", {"path": "src/x.py"}, context_items=["build it in SDL2"])
    check("suspended SYS-REPEAT goes quiet (agent unblocked)", not blocked4)
    qs = ic2._last_questions
    check("ledger questions in fanout", "sys.repeat" in qs and "sys.consistency" in qs)

# --- full-harness additions (round 27) ---
from psi.factpack import build as build_factpack  # noqa: E402
from psi.facts import FactStore  # noqa: E402

fp = build_factpack("Build the renderer using SDL2 as I specified.\n"
                    "- The renderer must support windowed and fullscreen modes\n"
                    "2. Input handling should use SDL events\n"
                    "Ship it.")
check("factpack: requirement lines extracted", len(fp["items"]) >= 2 and fp["items"][0]["id"] == "F01")
check("factpack: digest pinned", fp["digest"].startswith("Build the renderer"))

with _tf.TemporaryDirectory() as tmp3:
    fs = FactStore(Path(tmp3))
    fs.add({"files": ["src/render/sdl.go"], "summary": "render loop lives in src/render/sdl.go", "kind": "file"})
    fs.add({"files": [], "summary": "cargo test needs network for dev-deps", "kind": "constraint"})
    check("factstore: in-memory add dedupes+counts", len(fs.facts) == 2)
    fs2 = FactStore(Path(tmp3)).load()
    check("factstore: persists + reloads", len(fs2.facts) == 2)
    hits = fs.recall("edit src/render/sdl.go to fix the loop")
    check("factstore: recall hits relevant fact", len(hits) >= 1 and "sdl.go" in hits[0]["summary"])
    check("factstore: recall empty on no overlap", fs.recall("unrelated quantum widget") == [])

    # intercept with fact pack: sys.spec appears after first has_request check
    ic3 = PsiInterceptor(
        config=PsiConfig(backend="typesafe", api_url=BACKENDS["typesafe"][0], api_key="test",
                         thresholds=thr, budgets={"state_core_tok": 4000},
                         clarify_autonomy="proceed-flagged"),
        project_dir=None, client=None, psi_home=Path(tmp3),
    )
    ic3.client = type("C", (), {"ask": staticmethod(
        lambda state, questions: {"answers": {k: {"noul": 0.9} for k in questions}}
    )})()
    ic3.rules = [r_pin]
    ic3.check("write", {"path": "src/app.py"}, context_items=["build it in SDL2"])
    qs3 = ic3._last_questions
    check("intercept: sys.spec present with fact pack",
          "sys.spec" in qs3 and "sys.ambiguity" in qs3 and ic3.fact_pack is not None)
    v = evaluate({"sys.spec": {"noul": 0.2}}, {}, thr)
    check("policy: sys.spec conflict flags", v[0].action == CLARIFY)

# recalibrated probes: no-conflict = high p = PASS (the B4 noise-floor fix)
v = evaluate({"sys.ambiguity": {"noul": 0.95}}, {}, thr)
check("policy: recalibrated ambiguity passes on no-conflict", v == [])
v = evaluate({"sys.ambiguity": {"noul": 0.3}}, {}, thr)
check("policy: recalibrated ambiguity flags on conflict evidence", v[0].action == CLARIFY)

# --- replay engine (round 28) ---
import json as _json  # noqa: E402
from psi.replay import load_steps, run_replay  # noqa: E402

# synthetic tau session JSONL
sess = _tf.TemporaryDirectory()
sess_path = Path(sess.name) / "session.jsonl"
entries = [
    {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "build it in SDL2"}]}},
    {"type": "message", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "thinking..."},
        {"type": "tool_call", "name": "write", "arguments": {"path": "src/a.py", "content": "x=1"}},
        {"type": "tool_call", "name": "read", "arguments": {"path": "src/b.py"}},
        {"type": "tool_call", "name": "bash", "arguments": {"command": "cargo build"}},
    ]}},
]
sess_path.write_text(chr(10).join(_json.dumps(e) for e in entries))
steps = load_steps(str(sess_path))
check("replay: tau session parsed to 3 steps", len(steps) == 3 and steps[0]["tool"] == "write")
generic = Path(sess.name) / "generic.json"
generic.write_text(_json.dumps({"steps": [{"tool": "edit", "arguments": {"path": "src/c.py"}}]}))
check("replay: generic json parsed", len(load_steps(str(generic))) == 1)

# dry replay (no client): questions compiled, no Jev calls
ic4 = PsiInterceptor(
    config=PsiConfig(backend="typesafe", api_url=BACKENDS["typesafe"][0], api_key=None,
                     thresholds=thr, budgets={"state_core_tok": 4000},
                     clarify_autonomy="proceed-flagged"),
    project_dir=None, client=None, psi_home=Path(tmp3),
)
ic4.rules = [r_pin]
rep = run_replay([(str(generic), load_steps(str(generic)))], interceptor=ic4,
                 instruction="build it in SDL2")
res = rep["sources"][0]["results"][0]
check("replay dry: questions compiled", "sys.ambiguity" in (res.get("questions") or []) and "sys.spec" in (res.get("questions") or []))
check("replay dry: no answers without client", res.get("answers") is None and rep["summary"]["actions"].get("SKIPPED", 0) + rep["summary"]["actions"].get("PASS", 0) >= 1)

# live replay with fake client: blocked step lands in the report
ic5 = PsiInterceptor(
    config=PsiConfig(backend="typesafe", api_url=BACKENDS["typesafe"][0], api_key="test",
                     thresholds=thr, budgets={"state_core_tok": 4000},
                     clarify_autonomy="proceed-flagged"),
    project_dir=None, client=None, psi_home=Path(tmp3),
)
ic5.rules = [r_pin]
ic5.client = type("C", (), {"ask": staticmethod(
    lambda state, questions: {"answers": {k: {"noul": 0.95} for k in questions}}
)})()
rep5 = run_replay([(str(sess_path), steps)], interceptor=ic5, instruction="build it in SDL2")
blocked_steps = [r for s in rep5["sources"] for r in s["results"] if r.get("blocked")]
check("replay live: blocking step recorded", len(blocked_steps) >= 1 and any((r.get("verdicts") or [{}])[0].get("action") == "BLOCK" for r in blocked_steps))
sess.cleanup()

# --- CC-060 config-surgery QUESTION CHAIN: first rule mined from OUR OWN trace ---
# psi-eval attempt 5 lost 18/20 trials because the adapter text-rewrote tau's
# catalog (find + cp + sed) instead of tau's documented user-overlay mechanism
# (~/.tau/catalog.toml) — with the tau source cloned locally the whole time.
# An atomized question cannot carry the diligence clause (it would ask Jev to
# confirm an ABSENCE the state digest may not carry — round-23 noise-floor
# trap), so CC-060 is the first CHAIN rule: recognize -> retrieve -> judge,
# each node's answer injected into the next node's state as a stated fact.
from psi.rules import _load_file  # noqa: E402

_cc060 = next(r for r in _load_file(
    Path(__file__).resolve().parents[1] / "src/psi/packages/clean-code/0.0.1/rules.toml",
    "stdlib") if r.id == "CC-060")
check("CC-060 loads as a 3-node chain (recognize/retrieve/judge)",
      _cc060.type == "chain" and _cc060.chain is not None
      and len(_cc060.chain) == 3
      and [s.role for s in _cc060.chain] == ["recognize", "retrieve", "judge"])

surg = _tf.TemporaryDirectory()
surg_path = Path(surg.name) / "surgery.json"
surg_path.write_text(_json.dumps({"steps": [
    {"tool": "bash", "arguments": {"command":
        "F=$(find /opt/venv -name catalog.toml | head -1); cp \"$F\" ~/.tau/catalog.toml && "
        "sed -i 's|z-ai/glm-5.1|z-ai/glm-5.1, z-ai/glm-5.3-flash|' ~/.tau/catalog.toml"}},
    {"tool": "bash", "arguments": {"command": "cat /etc/hosts"}},
]}))

_chain_calls = []  # (keys, sorted state keys seen by the node)


def _chain_client():
    def ask(state, questions):
        keys = sorted(questions)
        _chain_calls.append((keys, sorted(state.keys())))
        out = {}
        for k in keys:
            if k.endswith(".n0"):
                out[k] = {"noul": 0.95 if "sed -i" in _json.dumps(state) else 0.05}
            elif k.endswith(".n1"):
                out[k] = {"choice": "no such evidence in the session", "confidence": 0.9}
            elif k.endswith(".n2"):
                out[k] = {"noul": 0.85}
            elif k == "sys.repeat":
                out[k] = {"noul": 0.05}  # inverted probe: high p = repeat = BLOCK
            elif k.startswith("sys."):
                out[k] = {"noul": 0.95}
        return {"answers": out}
    return type("C", (), {"ask": staticmethod(ask)})()


ic6 = PsiInterceptor(
    config=PsiConfig(backend="typesafe", api_url=BACKENDS["typesafe"][0], api_key="test",
                     thresholds=thr, budgets={"state_core_tok": 4000},
                     clarify_autonomy="proceed-flagged"),
    project_dir=None, client=None, psi_home=Path(tmp3),
)
ic6.rules = [_cc060]
ic6.client = _chain_client()
rep6 = run_replay([(str(surg_path), load_steps(str(surg_path)))],
                  interceptor=ic6,
                  instruction="add the flash model to the agent's model catalog")
res6 = rep6["sources"][0]["results"]
check("CC-060 chain: sed config-surgery flagged as ADVISE via judge node",
      res6[0].get("action") == "ADVISE"
      and any(v.get("rule") == "CC-060" for v in (res6[0].get("verdicts") or [])))
chain0 = (res6[0].get("chain") or {}).get("CC-060") or []
check("CC-060 chain: all 3 nodes ran with trace injected",
      len(chain0) == 3
      and all(c for k, c in _chain_calls if any(kk.endswith(".n1") or kk.endswith(".n2") for kk in k)))
# per-node input scoping (one question, one coherent block — Jev principle):
by_node: dict = {}
for keys, skeys in _chain_calls:
    for k in keys:
        if k.startswith("CC-060."):
            by_node.setdefault(k, set()).update(skeys)
check("chain scoping: recognize sees ONLY the action block",
      by_node.get("CC-060.n0") == {"proposed_action"})
check("chain scoping: retrieve sees action + session record, no trace",
      by_node.get("CC-060.n1") is not None
      and "chain_trace" not in by_node["CC-060.n1"]
      and "proposed_action" in by_node["CC-060.n1"]
      and bool(by_node["CC-060.n1"] & {"recent_context", "fact_store_digest", "fact_pack_digest"}))
check("chain scoping: judge sees the chain trace",
      "chain_trace" in by_node.get("CC-060.n2", set()))
check("CC-060 chain: benign step exits at recognize (1 node, clean pass)",
      res6[1].get("action") in (None, "PASS")
      and len(((res6[1].get("chain") or {}).get("CC-060") or [])) == 1
      and not any(v.get("rule") == "CC-060" for v in (res6[1].get("verdicts") or [])))
check("CC-060 chain: node ask counts (3 for surgery + 1 for benign)",
      sum(1 for k, _ in _chain_calls if any(kk.startswith("CC-060.") for kk in k)) == 4)
surg.cleanup()

# --- glossary (round 28): pinned term definitions --------------------------
# Terms referenced as [[id]] get a definitions footer appended for Jev; terms
# present in free text get tagged into the state. Unknown markers stay put
# (they are ambiguity-mining candidates). NO psi-core seed dictionary: the
# default dictionary is EMPTY — package vocabulary ships with the package
# that references it, user/project tiers capture the rest. A conversation-
# local seed would actively tag stale definitions into unrelated contexts.
from psi.glossary import Glossary, Term  # noqa: E402

_pkg_gloss = Path(__file__).resolve().parents[1] / "src/psi/packages/clean-code/0.0.1/glossary.toml"
check("glossary: DEFAULT dictionary is empty (no psi-core seed)",
      Glossary.load(None, user_dir=Path(tmp3)).terms == {})
g = Glossary.load(None, user_dir=Path(tmp3), package_files=[_pkg_gloss])
check("glossary: package vocabulary ships with its package",
      {"config-surgery", "mechanism-check"} <= set(g.terms) and "atomic" not in g.terms)
g2 = Glossary(terms={"atomic": Term(id="atomic", definition="single cognitive evaluation; not sentence count.")})
exp = g2.expand("Run the [[atomic]] task now.")
check("glossary: marker expands to a definitions footer",
      "[Definitions]" in exp and "single cognitive evaluation" in exp and "[[atomic]]" in exp)
check("glossary: unknown marker left unexpanded",
      g.expand("use [[wibble]] here") == "use [[wibble]] here")
tags = g2.tag_terms("this ATOMIC step")
check("glossary: tagging is case-insensitive", {t.id for t in tags} == {"atomic"})
check("glossary: fingerprint is a stable 16-char digest", len(g.sha256()) == 16)

# interceptor integration: marked rule question + state tagging
ic7 = PsiInterceptor(
    config=PsiConfig(backend="typesafe", api_url=BACKENDS["typesafe"][0], api_key="test",
                     thresholds=thr, budgets={"state_core_tok": 4000},
                     clarify_autonomy="proceed-flagged",
                     packages=["clean-code@0.0.1"]),
    project_dir=None, client=None, psi_home=Path(tmp3),
)
r_marked = Rule(id="CC-099", tier="stdlib", type="noul",
                question="Is the proposed change [[config-surgery]]?",
                applies_to=["bash"], threshold=0.85, severity="advisory")
ic7.rules = [r_marked]
_seen = {}


def _gloss_ask(state, questions):
    _seen["state"] = state
    _seen["questions"] = questions
    return {"answers": {k: {"noul": 0.05} for k in questions}}


ic7.client = type("C", (), {"ask": staticmethod(_gloss_ask)})()
ic7.inspect("bash", {"command": "echo config-surgery"}, context_items=["avoid [[config-surgery]]"])
_q = (_seen["questions"] or {}).get("RCC-099") or {}
check("glossary: rule question carries the definitions footer",
      "[Definitions]" in (_q.get("instructions") or "")
      and "text substitution" in (_q.get("instructions") or ""))
check("glossary: state tags definitions for terms present in request/action",
      bool((_seen["state"] or {}).get("probe.glossary")))
check("glossary: CC-060 recognize marker expands (package glossary wired in)",
      "[Definitions] config-surgery:" in g.expand(_cc060.chain[0].instructions))

# --- sys.progress: no-progress repetition probe (round 28, mined feature f2)
# Mined from the contrast sample: failing traces END in re-runs of already-seen
# commands (tail churn); passing traces end with fresh actions. Deterministic
# ledger arithmetic drives a CONDITIONAL probe — zero Jev calls on normal work.
from psi.ledger import Ledger  # noqa: E402

lg = Ledger(Path(tmp3))
for _ in range(10):
    lg.record("bash", "python runtests.py forms_tests -v 0", "executed")
st = lg.repetition_stats()
check("ledger: bare re-runs detected as no-progress repeats",
      st["tail_repeats"] >= 2 and st["worst_count"] >= 2)
lg.record("edit", "src/a.py", "executed")
for i in range(3):
    lg.record("bash", f"ls -la src/dir{i}", "executed")
check("ledger: after an edit, fresh work resets the repetition state",
      lg.repetition_stats()["tail_repeats"] == 0)

_cfg = lambda: PsiConfig(backend="typesafe", api_url=BACKENDS["typesafe"][0], api_key="test",
                         thresholds=thr, budgets={"state_core_tok": 4000},
                         clarify_autonomy="proceed-flagged")


def _progress_fake(state, questions):
    return {"answers": {k: {"noul": 0.9 if k == "sys.progress"
                            else (0.05 if k == "sys.repeat" else 0.95)}
                        for k in questions}}


ic8 = PsiInterceptor(config=_cfg(), project_dir=None, client=None, psi_home=Path(tmp3))
ic8.rules = []
ic8.ledger.set_requirements("fix the tests")
for _ in range(10):
    ic8.ledger.record("bash", "python runtests.py forms_tests -v 0", "executed")
ic8.client = type("C", (), {"ask": staticmethod(_progress_fake)})()
out8 = ic8.inspect("bash", {"command": "python runtests.py forms_tests -v 0"},
                   context_items=["fix the tests"])
check("sys.progress: conditional probe fires on a repeat tail",
      "sys.progress" in (out8.get("questions") or []))
check("sys.progress: high p flags as ADVISE (SYS-PROGRESS)",
      any(v.get("rule") == "SYS-PROGRESS" for v in (out8.get("verdicts") or [])))

ic9 = PsiInterceptor(config=_cfg(), project_dir=None, client=None, psi_home=Path(tmp3))
ic9.rules = []
ic9.ledger.set_requirements("fix the tests")
for _ in range(10):
    ic9.ledger.record("bash", "python runtests.py forms_tests -v 0", "executed")
ic9.ledger.record("edit", "src/a.py", "executed")
for i in range(3):
    ic9.ledger.record("bash", f"ls -la src/dir{i}", "executed")
ic9.client = type("C", (), {"ask": staticmethod(_progress_fake)})()
out9 = ic9.inspect("bash", {"command": "grep -rn TODO src/", },
                   context_items=["fix the tests"])
check("sys.progress: quiet when the tail is fresh work after an edit",
      "sys.progress" not in (out9.get("questions") or []))
# --- self-voice correction text (round 28, user design) ---------------------
# The model-visible text of an interception must read as the model OWN
# realization — no authority frame. The igel trace showed authority-voice
# blocks produce external attribution (the model said the test harness is
# blocking it) and compliance-by-escalation instead of reflection.
from psi.voice import continuation_for, result_reason_for, scan  # noqa: E402

cont = continuation_for(kind="repeat", evidence="python runtests.py forms_tests -v 0")
check("voice: repeat continuation is first-person self-talk",
      cont.startswith("Wait") and "I already" in cont
      and "What changed since my last attempt" in cont)
check("voice: no authority vocabulary in model-visible text",
      scan(cont) == [] and scan(result_reason_for(kind="repeat", evidence="cmd x")) == [])
rule_cont = continuation_for(kind="rule",
                             description="User-stated constraints outrank agent preferences.",
                             remedy="use the pinned stack")
check("voice: rule continuation embeds description + remedy",
      "outrank agent preferences" in rule_cont and "use the pinned stack" in rule_cont
      and scan(rule_cont) == [])
lg3 = Ledger(Path(tmp3))
lg3.record("bash", "python runtests.py -v 0", "executed")
check("voice: ledger evidence helper finds the prior matching attempt",
      lg3.prior_attempt_evidence("bash", "python runtests.py -v 0") is not None
      and lg3.prior_attempt_evidence("bash", "totally different cmd") is None)

# --- tau fork: edits are IN SOURCE (no patching) ----------------------------
_fork_rt = Path(__file__).resolve().parents[1] / "vendor/tau/src/tau_coding/extensions/runtime.py"
check("tau fork: continuation helper present in vendored source",
      "_psi_apply_continuation" in _fork_rt.read_text()
      and "psi_set_current_assistant" in _fork_rt.read_text())
_fork_api = Path(__file__).resolve().parents[1] / "vendor/tau/src/tau_coding/extensions/api.py"
check("tau fork: thought_continuation field present in vendored source",
      "thought_continuation" in _fork_api.read_text())