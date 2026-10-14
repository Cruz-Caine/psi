"""ψ configuration — stdlib tomllib only, no dependencies.

Resolution order for psi.toml: explicit root > cwd. Missing file = defaults.
Backends are pluggable (typesafe | openrouter) — same /v1/systemone schema.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS: dict = {
    "provider": {"backend": "typesafe"},
    "thresholds": {
        "blocking": 0.85,
        "advisory": 0.75,
        "gray_band": 0.10,
        "ambiguity": 0.60,
        "novelty": 0.60,
        "repeat": 0.90,  # was 0.70 (policy default) — drill v5.3 blocked DIFFERENT
        # commands post-failure at p~0.73-0.85; 0.90 keeps genuine repeats while
        # letting unrelated commands through until calibration says otherwise.
    },
    "outage": {"on_outage": "quarantine", "retries": 3},
    "clarify": {"autonomy": "ask"},  # ask | proceed-flagged (autonomous runs)
    "budgets": {"state_core_tok": 4000, "digest_tok": 500, "probe_tok": 1000},
    "rules": {"packages": [], "untrusted_project_tier": False},
}

BACKENDS: dict[str, tuple[str, str]] = {
    "typesafe": ("https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/systemone", "OPENROUTER_API_KEY"),
}


@dataclass
class PsiConfig:
    backend: str
    api_url: str
    api_key: str | None
    thresholds: dict = field(default_factory=dict)
    on_outage: str = "quarantine"
    clarify_autonomy: str = "ask"
    retries: int = 3
    budgets: dict = field(default_factory=dict)
    packages: list = field(default_factory=list)
    untrusted_project_tier: bool = False
    raw: dict = field(default_factory=dict)


def load_config(root: Path | None = None) -> PsiConfig:
    path = (root or Path.cwd()) / "psi.toml"
    file_data: dict = {}
    if path.exists():
        file_data = tomllib.loads(path.read_text())

    merged = {k: {**v, **file_data.get(k, {})} for k, v in DEFAULTS.items()}
    backend = merged["provider"].get("backend", "typesafe")
    if backend not in BACKENDS:
        raise ValueError(f"unknown provider backend {backend!r}; expected one of {sorted(BACKENDS)}")
    api_url, env_key = BACKENDS[backend]

    return PsiConfig(
        backend=backend,
        api_url=api_url,
        api_key=os.environ.get(env_key),
        thresholds=merged["thresholds"],
        on_outage=merged["outage"]["on_outage"],
        clarify_autonomy=merged["clarify"]["autonomy"],
        retries=int(merged["outage"]["retries"]),
        budgets=merged["budgets"],
        packages=list(merged["rules"]["packages"]),
        untrusted_project_tier=bool(merged["rules"]["untrusted_project_tier"]),
        raw=file_data,
    )