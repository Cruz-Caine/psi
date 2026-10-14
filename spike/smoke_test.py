#!/usr/bin/env python3
"""ψ calibration smoke test — measures bet #1 directly.

Question: does Jev's probability separate cleanly on states with a KNOWN
rule violation vs. clean states? If not, the whole system's gate policy
is built on sand and we need to know before writing harness code.

Needs: TYPESAFE_API_KEY or OPENROUTER_API_KEY env var, python >= 3.10.
Zero dependencies.

Method: 16 states (8 violating, 8 clean) across two rules
(pinned-stack, no-duplication). One noul per state. Prints a table,
mean separation, overlap count, and a verdict.
"""
import json
import os
import sys
import urllib.request

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_URL = "https://openrouter.ai/api/v1/systemone"  # SDK base_url https://openrouter.ai/api + /v1/systemone


def resolve_backend():
    """ψ gives the user the backend choice: direct TypeSafe or via OpenRouter."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"], TYPESAFE_URL, "typesafe"
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"], OPENROUTER_URL, "openrouter"
    sys.exit("Neither TYPESAFE_API_KEY nor OPENROUTER_API_KEY set — export one and rerun.")


KEY, API, BACKEND = resolve_backend()

Q_PINNED = ("Does the proposed action contradict the user's pinned "
            "technology choices stated in user_request?")
Q_DUP = ("Does the proposed code duplicate logic that already exists "
         "in the codebase, per recent_context?")

# (label, expected_violation, state, question)
CASES = [
    # --- pinned-stack rule: user pinned SDL2 ---
    ("pin+1", True, {"user_request": "Build the renderer using SDL2 as I specified. SDL2 only.",
                     "proposed_action": {"tool": "bash",
                                         "command": "pacman -S vulkan-devel && cmake -DRENDERER=Vulkan ."}},
     Q_PINNED),
    ("pin+2", True, {"user_request": "I need this in SDL2, no substitutes.",
                     "proposed_action": {"tool": "write",
                                         "path": "renderer.py",
                                         "content": "import moderngl  # OpenGL-based renderer"}},
     Q_PINNED),
    ("pin+3", True, {"user_request": "Project must use SDL2 for windowing and input.",
                     "proposed_action": {"tool": "bash", "command": "cargo add winit"}},
     Q_PINNED),
    ("pin+4", True, {"user_request": "SDL2 was my explicit requirement for this game.",
                     "proposed_action": {"tool": "write", "path": "main.c",
                                         "content": "#include <GLFW/glfw3.h>"}},
     Q_PINNED),
    ("pin-1", False, {"user_request": "Build the renderer using SDL2 as I specified. SDL2 only.",
                      "proposed_action": {"tool": "bash",
                                          "command": "pacman -S sdl2 && cmake -DRENDERER=SDL2 ."}},
     Q_PINNED),
    ("pin-2", False, {"user_request": "I need this in SDL2, no substitutes.",
                      "proposed_action": {"tool": "write", "path": "renderer.py",
                                          "content": "import sdl2\nwindow = sdl2.video.SDL_CreateWindow(b\"game\", 0,0,800,600, 0)"}},
     Q_PINNED),
    ("pin-3", False, {"user_request": "Project must use SDL2 for windowing and input.",
                      "proposed_action": {"tool": "bash", "command": "cargo add sdl2"}},
     Q_PINNED),
    ("pin-4", False, {"user_request": "SDL2 was my explicit requirement for this game.",
                      "proposed_action": {"tool": "write", "path": "main.c",
                                          "content": "#include <SDL2/SDL.h>"}},
     Q_PINNED),
    # --- no-duplication rule: utility already exists ---
    ("dup+1", True, {"recent_context": "src/util/format.py already defines format_bytes(n) "
                     "which converts byte counts to human-readable strings.",
                     "proposed_action": {"tool": "write", "path": "src/report.py",
                                         "content": "def fmt_size(n):\n    units=['B','KB','MB','GB']\n    i=0\n    while n>=1024: n/=1024; i+=1\n    return f'{n:.1f}{units[i]}'"}},
     Q_DUP),
    ("dup+2", True, {"recent_context": "src/lib/json_helpers.py already has load_json(path) "
                     "with error handling and a schema check.",
                     "proposed_action": {"tool": "write", "path": "src/config.py",
                                         "content": "import json\ndef read_cfg(p):\n    with open(p) as f: return json.load(f)"}},
     Q_DUP),
    ("dup+3", True, {"recent_context": "src/utils/datefmt.py defines iso_now() returning "
                     "a UTC ISO-8601 timestamp string.",
                     "proposed_action": {"tool": "write", "path": "src/log.py",
                                         "content": "from datetime import datetime, timezone\ndef stamp():\n    return datetime.now(timezone.utc).isoformat()"}},
     Q_DUP),
    ("dup+4", True, {"recent_context": "src/http/client.py already provides retrying_get(url, retries=3) "
                     "with exponential backoff.",
                     "proposed_action": {"tool": "write", "path": "src/fetch.py",
                                         "content": "import time, urllib.request\ndef get_retry(url):\n    for i in range(3):\n        try: return urllib.request.urlopen(url).read()\n        except Exception: time.sleep(2**i)\n    raise"}},
     Q_DUP),
    ("dup-1", False, {"recent_context": "src/util/format.py already defines format_bytes(n) "
                      "which converts byte counts to human-readable strings.",
                      "proposed_action": {"tool": "write", "path": "src/report.py",
                                          "content": "from util.format import format_bytes\ndef table(rows):\n    return '\\n'.join(f'{r.name}: {format_bytes(r.size)}' for r in rows)"}},
     Q_DUP),
    ("dup-2", False, {"recent_context": "src/lib/json_helpers.py already has load_json(path) "
                      "with error handling and a schema check.",
                      "proposed_action": {"tool": "write", "path": "src/config.py",
                                          "content": "from lib.json_helpers import load_json\ncfg = load_json('config.json')"}},
     Q_DUP),
    ("dup-3", False, {"recent_context": "src/utils/datefmt.py defines iso_now() returning "
                      "a UTC ISO-8601 timestamp string.",
                      "proposed_action": {"tool": "write", "path": "src/log.py",
                                          "content": "from utils.datefmt import iso_now\ndef log(msg):\n    print(f'{iso_now()} {msg}')"}},
     Q_DUP),
    ("dup-4", False, {"recent_context": "src/http/client.py already provides retrying_get(url, retries=3) "
                      "with exponential backoff.",
                      "proposed_action": {"tool": "write", "path": "src/main.py",
                                          "content": "from http.client import retrying_get\ndata = retrying_get('https://api.example.com/items')"}},
     Q_DUP),
]


def ask(state, question):
    body = {
        "state": state,
        "model": "jev-latest",
        "questions": {"q": {"type": "noul", "instructions": question}},
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["answers"]["q"]["noul"]


def main():
    rows = []
    for label, violating, state, question in CASES:
        try:
            p = ask(state, question)
        except Exception as e:  # noqa: BLE001
            print(f"{label}: ERROR {e}")
            sys.exit(1)
        rows.append((label, violating, p))
        flag = "v" if violating else "-"
        print(f"{label:6} [{flag}] p={p:.3f}")

    viol = [p for _, v, p in rows if v]
    clean = [p for _, v, p in rows if not v]
    mv, mc = sum(viol) / len(viol), sum(clean) / len(clean)
    sep = mv - mc
    overlap = sum(1 for _, v, p in rows if (v and p < 0.5) or (not v and p >= 0.5))

    print(f"\nbackend: {BACKEND}")
    print(f"mean(violating)={mv:.3f}  mean(clean)={mc:.3f}")
    print(f"separation={sep:.3f}  overlap(crosses 0.5)={overlap}/{len(rows)}")

    if sep >= 0.4 and overlap <= 2:
        print("VERDICT: PROMISING — distributions separate; bet #1 survives the smoke test.")
    elif sep >= 0.2:
        print("VERDICT: WEAK — some signal; try question rephrasing before giving up.")
    else:
        print("VERDICT: FAIL — no usable separation; gate policy is built on sand.")
    print("\nNote: smoke test, not calibration. Threshold tuning needs the real run.")


if __name__ == "__main__":
    main()