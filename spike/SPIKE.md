# SPIKE: tau graft-point verification (v0 item 1)

**Question (D1):** does tau's loop expose the full response bundle (thinking + tool calls)
before any tool executes, and can ψ intercept/block there without forking?

**Method:** shallow clone of huggingface/tau@main; read `src/tau_agent/loop.py`,
`messages.py`, `provider.py`, `harness.py`.

## Findings

| # | Finding | Location |
| - | - | - |
| 1 | The complete `AssistantMessage` (thinking + all tool_calls) is assembled **before any tool executes** | `loop.py:125–147` (assistant captured via `MessageEndEvent`), tool loop starts at `loop.py:153–156` |
| 2 | Thinking is a first-class content block: `ThinkingContent(type="thinking", thinking, thinking_signature)`, with `AssistantMessage.thinking_text()` helper | `messages.py:103–105, 200–202` |
| 3 | **Native pre-execution enforcement hook exists**: `before_tool_call: BeforeToolCall` → `await before_tool_call(call)` → `(blocked, block_reason)`; blocked calls never run and return an error result to the model | `loop.py:66, 296–303` |
| 4 | The loop is constructed with the hooks as parameters (`before_tool_call`, `after_tool_call`) — config-level wiring, not a fork | `harness.py:47–48` |
| 5 | Provider is a Protocol: `ModelProvider.stream_response(...) -> AsyncIterator[AssistantMessageEvent]` — wrappable for bundle-level capture | `provider.py:19–31` |
| 6 | Tool results are `ToolResultMessage(tool_call_id, tool_name, content, is_error)` — ψ's synthetic `BLOCKED` result maps onto the error-result path | `loop.py:331–340` |

## Verdict

**D1 CONFIRMED.** Both interception layers ψ needs exist natively:

- **Bundle capture (A1):** wrap `ModelProvider.stream_response` — on the terminal
  `AssistantMessageEvent`, the full bundle is available pre-execution. Zero loop patches.
- **Per-call enforcement (A7):** ψ implements `before_tool_call` — a blocking verdict
  becomes `blocked=True, block_reason="BLOCKED by rule <id> (p=<p>): <desc>. Remedy: <remedy>"`,
  which tau turns into the synthetic error tool result. D2's append-only mechanism maps 1:1.

Integration is a wiring change in `tau_coding`'s harness construction (where hooks are
passed), not a tau fork.

## Integration plan (v0)

1. ψ wraps the provider: captures `AssistantMessage`, runs state-builder → gates → probes
   → fan-out once per bundle; stores `Verdicts` keyed by `tool_call.id`.
2. ψ's `before_tool_call` consults the verdict map: BLOCK → blocked+reason; PASS → proceed.
   CLARIFY (v0): block with the batched question as the reason + emit `jev.clarify` — the
   model asks the user in its next turn. (True hold-and-wait needs a loop patch; deferred.)
3. Advisories: TUI/session-log only (D3); injected into context only when promoted.

## Open items (none blocking)

- Custom `jev.*` events: tau's `AgentEvent` set may need extension, or ψ logs via its own
  JSONL + session annotations. Check `events.py` during v0 wiring.
- Thinking replay: `thinking_signature` must be preserved if ψ ever strips/reorders
  content (Anthropic replay requirement) — v0 never modifies messages, so N/A.
- `after_tool_call` gives ψ post-execution observation for free (telemetry enrichment).

## Next

Smoke test (needs `TYPESAFE_API_KEY`) → v0 skeleton: rule-loader + gate-filter +
provider-wrap + before_tool_call policy, in `src/psi/`.