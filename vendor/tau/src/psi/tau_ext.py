"""τ glue — ψ as a tau extension (addon, not fork).

Evidence (SPIKE.md / psi_coding/extensions/api.py):
  - `tool_call` hook fires pre-execution; `ToolCallHookResult(block=True, reason=…)`
    prevents execution and the reason becomes what the model sees (D2 verbatim).
  - The runtime *isinstance-validates* hook results — handlers must return the real
    dataclass from `psi_coding.extensions.api`, not dicts. Conversion lives HERE,
    so the psi core stays tau-agnostic.
  - `input` lifecycle event (InputEvent.text) feeds ψ's recent-context ring.
  - `compaction_end` event is the distiller checkpoint trigger (ARCHITECTURE §9).
  - `register_command` gives the `/rule` capture loop a native slash command.
  - NOTE: tau fails safe — an exception inside a hook BLOCKS the tool. ψ therefore
    catches its own errors and fails open loudly (D9 philosophy), never silently.

setup(tau) is the entry point; tau discovers this file via the loader
(`~/.psi/extensions/psi.py` shim in dev, `psi-ai` package entry later).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .client import JevClient
from .config import load_config
from .intercept import PsiInterceptor
from .rules import load_rules
from .state import build_state  # noqa: F401  (re-exported for the shim)

USER_HOME_RULES = "rules.toml"


class _Ring:
    """Recent user inputs — feeds StateObject.recent_context (§1)."""

    def __init__(self, n: int = 5):
        self.items: list[str] = []
        self.n = n

    def add(self, text: str) -> None:
        self.items.append(text)
        del self.items[:-self.n]

    def snapshot(self) -> list[str]:
        return list(self.items)


def setup(tau):
    """ψ extension entry point (sync, per tau's loader contract).

    psi ships INSIDE this tau fork as a built-in extension; the PSI_ENABLED
    env var gates the layer (unset/0 = bare tau behavior, hooks skipped).
    """
    import os

    if os.environ.get("PSI_ENABLED", "0").lower() not in ("1", "true", "yes"):
        return None
    from psi_coding.extensions.api import ToolCallHookResult

    cwd = Path.cwd()
    cfg_root = cwd
    # psi.toml resolution: cwd first, then the psi home (~/.psi/psi.toml —
    # the adapter stages it there), then the legacy staged dir
    home = Path.home() / ".psi"
    if not (cwd / "psi.toml").exists():
        if (home / "psi.toml").exists():
            cfg_root = home
        elif (Path("/opt/psi_src") / "psi.toml").exists():
            cfg_root = Path("/opt/psi_src")
    cfg = load_config(cfg_root)
    interceptor = PsiInterceptor(
        config=cfg,
        project_dir=Path.cwd(),
        client=JevClient(url=cfg.api_url, api_key=cfg.api_key, retries=cfg.retries),
    )
    # checkpoint distiller: same key/base, cheap session model (PSI_MODEL is
    # set by the adapter = the eval model). Fail-open (D9) — distill errors
    # never block; they only cost one skipped fact-store update.
    interceptor.distill_cfg = (
        {"api_key": cfg.api_key, "base": cfg.api_url,
         "model": os.environ.get("PSI_MODEL", "z-ai/glm-5.3-flash")}
        if cfg.api_key else None
    )
    ring = _Ring()
    results_seen = 0

    def on_input(event, ctx):
        if getattr(event, "source", "interactive") == "interactive" and getattr(event, "text", ""):
            ring.add(event.text)

    async def on_tool_call(event, ctx):
        try:
            out = interceptor.inspect(
                event.tool_name, dict(event.arguments), context_items=ring.snapshot()
            )
            blocked = bool(out.get("blocked"))
            verdicts = out.get("verdicts") or []
            if not blocked and interceptor.ledger is not None:
                # record the executed attempt so repeat/consistency checks have history
                interceptor.ledger.record(event.tool_name, str(dict(event.arguments))[:150], "executed")
        except Exception as exc:  # noqa: BLE001 — ψ fails open loudly, never bricky
            ctx.ui.notify(f"ψ internal error ({exc!r}) — failing open, loudly", "warning")
            interceptor.telemetry.record({"kind": "internal_error", "error": repr(exc)})
            return ToolCallHookResult()
        if blocked:
            # self-voice correction (round 28, user design): the model-visible
            # text reads as the model's OWN realization — no authority frame.
            # Accountability (rule id, p, args) stays in telemetry/verdicts.
            from .voice import continuation_for, result_reason_for, scan
            v = next((v for v in verdicts if v.get("action") == "BLOCK"), None) or (
                verdicts[0] if verdicts else {})
            rule_id = v.get("rule") or ""
            args_txt = str(dict(event.arguments))[:150]
            if rule_id == "SYS-REPEAT":
                evidence = interceptor.ledger.prior_attempt_evidence(
                    event.tool_name, str(dict(event.arguments))[:150]) or args_txt
                continuation = continuation_for(kind="repeat", evidence=evidence)
                reason = result_reason_for(kind="repeat", evidence=evidence)
            else:
                rule = next((r for r in interceptor.rules if r.id == rule_id), None)
                continuation = continuation_for(
                    kind="rule", description=rule.description if rule else "this action conflicts with what I know I should do",
                    remedy=(rule.remedy or "take a different approach") if rule else "take a different approach")
                reason = result_reason_for(
                    kind="rule", description=rule.description if rule else "",
                    remedy=(rule.remedy or "take a different approach") if rule else "")
            for hit in scan(continuation + " " + reason):
                interceptor.telemetry.record({"kind": "voice_warning", "term": hit})
            ctx.ui.notify(f"ψ correction (self-voice): {rule_id or 'sys'} — {reason[:120]}", "warning")
            # thought_continuation requires the tau patch (psi_eval/tau_patch.py);
            # guard the extended field so an unpatched tau still blocks cleanly.
            try:
                return ToolCallHookResult(block=True, reason=reason,
                                          thought_continuation=continuation)
            except TypeError:
                interceptor.telemetry.record({"kind": "voice_fallback", "rule": rule_id})
                return ToolCallHookResult(block=True, reason=reason)
        return ToolCallHookResult()

    async def on_tool_result(event, ctx):
        nonlocal results_seen
        results_seen += 1
        outcome = "error" if getattr(getattr(event, "result", None), "is_error", False) else "ok"
        interceptor.ledger.record(event.tool_name, str(getattr(getattr(event, "result", None), "content", "") or "")[:150], outcome)
        interceptor.telemetry.record({"kind": "tool_result", "tool": event.tool_name})
        # checkpoint distillation every 20 tool results (fail-open, D9)
        if interceptor.facts is not None and results_seen % 20 == 0:
            events = chr(10).join(
                f"{a['tool']} -> {a['outcome']}: {a['summary']}"
                for a in interceptor.ledger.attempts[-20:]
            )
            interceptor.maybe_distill(events)

    async def on_compaction(event, ctx):
        interceptor.telemetry.record({"kind": "compaction_checkpoint"})
        # v0.1: distiller checkpoint trigger lands here (ARCHITECTURE §9)

    tau.on("input", on_input)
    tau.on("tool_call", on_tool_call)
    tau.on("tool_result", on_tool_result)
    tau.on("compaction_end", on_compaction)

    def cmd_rule(args: str, ctx) -> str | None:
        text = args.strip()
        if text.lower().startswith("add "):
            text = text[4:].strip()
        if not text:
            return "usage: /rule add <instruction the agent must follow>"
        rid = interceptor.add_user_rule(text)
        return f"rule {rid} added (user tier, advisory) — active on the next check"

    tau.register_command(
        "rule",
        cmd_rule,
        description="ψ: add a user rule (capture loop)",
        usage="/rule add <text>",
    )
    return interceptor  # exported for scripted pre-seeding (drills/tests)