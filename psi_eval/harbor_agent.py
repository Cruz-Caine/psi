"""ψ/τ as a harbor agent — the eval adapter (EVAL.md L1a).

Executor: tau print mode (`tau -p`) inside the trial container, with the ψ
extension shim installed when `PSI_ENABLED` is set in the ORCHESTRATOR's env
(arm B); absent/zero = arm A (bare tau). `PSI_SRC` points at the psi package
on the host (dev default below). Model: whatever harbor passes via
`-m openrouter/z-ai/glm-5.3-flash` + OPENROUTER_API_KEY in the container env.

Usage:
  PSI_ENABLED=1 PSI_SRC=/path/to/thinker/src \
  harbor run -d deep-swe@<ver> -m openrouter/z-ai/glm-5.3-flash \
    -a psi_eval.harbor_agent:TauPsiAgent

Reference: harbor.agents.nop (minimal), harbor agent schema pi (options).
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

DEFAULT_PSI_SRC = (
    "/home/cruzc/Files/30-39 Engineering & Development/"
    "31 Software Projects/thinker/src"
)

SHIM_TEMPLATE = '''"""ψ shim inside the trial container."""
import sys
sys.path.insert(0, "/opt/psi_src")

def setup(tau):
    import psi.tau_ext
    return psi.tau_ext.setup(tau)
'''

# User-level tau catalog overlay (tau's SUPPORTED mechanism: ~/.psi/catalog.toml,
# merged over the built-in catalog — see tau_coding/catalog_loader.py). Required
# _CatalogProvider fields (display_name, kind, base_url, api_key_env,
# default_model, docs_url) are mirrored from the built-in openrouter block;
# raw merge is {**builtin, **overlay} so omitted keys (context_windows,
# thinking_*) survive, and `models` UNION-merges — this single entry adds the
# model without disturbing anything else.
CATALOG_OVERRIDE_TEMPLATE = '''schema_version = 1

[[providers]]
name = "openrouter"
display_name = "OpenRouter"
kind = "openai-compatible"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
credential_name = "openrouter"
models = ["{model_id}"]
default_model = "{model_id}"
docs_url = "https://openrouter.ai/docs"
api = "openai-completions"
'''


def _psi_enabled() -> bool:
    return os.environ.get("PSI_ENABLED", "0").lower() in ("1", "true", "yes")


def _psi_src() -> Path:
    return Path(os.environ.get("PSI_SRC", DEFAULT_PSI_SRC)).expanduser()


class TauPsiAgent(BaseAgent):
    capabilities = AgentCapabilities()

    @staticmethod
    def name() -> str:
        return "tau-psi"

    def version(self) -> str | None:
        return os.environ.get("TAU_PSI_VERSION", "0.0.1")

    def to_agent_info(self):
        """pier validates TrialResult.agent_info with ITS AgentInfo model —
        harbor's inherited version returns a foreign class instance and pydantic
        rejects it. Return pier's model when available; harbor's otherwise."""
        try:
            from pier.models.trial.result import AgentInfo as PierAgentInfo
            from pier.models.trial.result import ModelInfo as PierModelInfo
        except ImportError:
            return super().to_agent_info()
        provider = self._parsed_model_provider
        return PierAgentInfo(
            name=self.name(),
            version=self.version() or "unknown",
            model_info=(
                PierModelInfo(name=self._parsed_model_name, provider=provider)
                if self._parsed_model_name
                else None
            ),
        )

    def install_spec(self):
        """Build-time tau install (pier honors install_spec on ALL agents,
        including import-path agents — trial/execution.py:201). Lazy-imports
        pier models so the class still imports under plain harbor."""
        try:
            from pier.models.agent.install import AgentInstallSpec, InstallStep
        except ImportError:
            return None  # not running under pier (e.g. harbor TB leg)
        return AgentInstallSpec(
            agent_name=self.name(),
            version=self.version(),
            steps=[InstallStep(run="pip install --quiet tau-ai", user="agent")],
            verification_command="tau --version",
        )

    def network_allowlist(self):
        """pier runtime network policy: model API + Jev backend (both openrouter.ai);
        pypi domains kept listed in case installs fall back to runtime setup()."""
        try:
            from pier.models.agent.network import NetworkAllowlist
        except ImportError:
            return None
        return NetworkAllowlist(
            domains=["openrouter.ai", "pypi.org", "files.pythonhosted.org"]
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        # 0. catalog override — tau's openrouter catalog (static built-in +
        #    models.dev overlay) lacks z-ai/glm-5.3-flash, so tau exits at
        #    startup ("model not configured", exit 2). Stage tau's SUPPORTED
        #    user-level overlay (~/.psi/catalog.toml) UNCONDITIONALLY — no
        #    in-container discovery. (B5 bug: the old find /opt/venv + cp + sed
        #    only resolved in some task images — igel ran flash, the other 9
        #    images per arm died before doing any work.)
        provider_id, _, model_id = (self.model_name or "").partition("/")
        if provider_id == "openrouter" and model_id:
            toml_body = CATALOG_OVERRIDE_TEMPLATE.format(model_id=model_id)
            result = await environment.exec(
                "mkdir -p ~/.psi && cat > ~/.psi/catalog.toml <<'PSI_EOF'\n"
                f"{toml_body}PSI_EOF\n",
                timeout_sec=60,
            )
            if result.return_code != 0:
                raise RuntimeError(f"catalog override failed: {result.stdout} {result.stderr}")
        else:
            self.logger.info("model not openrouter/* — skipping catalog override")
        # 0.5 psi tau-fork — psi ships INSIDE its own tau fork (renamed:
        # psi_coding/psi_agent/psi_ai, ~/.psi home, `psi` CLI). The wheel is
        # prebuilt host-side (vendor/dist); install it over the pip tau-ai
        # that install_spec() laid down at build time. Both arms (parity).
        wheel = sorted(Path(__file__).parent.parent.glob("vendor/dist/psi_ai-*.whl"))[-1]
        wheel_name = wheel.name
        await environment.upload_file(str(wheel), f"/opt/{wheel_name}")
        result = await environment.exec(
            f"pip install --quiet --force-reinstall --no-deps /opt/{wheel_name} && psi --version",
            timeout_sec=300,
        )
        if result.return_code != 0:
            raise RuntimeError(f"psi tau fork install failed: {result.stdout} {result.stderr}")
        self.logger.info("psi tau fork installed")
        # tau installs at BUILD time via install_spec() — setup() stages the
        # psi config + installs psi's tau fork (psi ships INSIDE the fork).
        if not _psi_enabled():
            self.logger.info("PSI_ENABLED unset — arm A (bare tau), no psi layer")
            return
        # pre-create the host artifacts dir: container users map to foreign
        # host uids (rootless podman subuid ranges) — a container-created dir
        # becomes unwritable for pier (rootless uid 100999, 0755). A 0777 dir
        # we own lets docker-cp and the verifier's artifact copy work.
        artifacts = Path(self.logs_dir).parent / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        artifacts.chmod(0o777)

        # 2. ψ package into the container
        await environment.upload_dir(str(_psi_src()), "/opt/psi_src")

        # 3. psi.toml into the psi home (openrouter backend — key arrives
        # per-exec from the orchestrator env) + the rule package where psi's
        # loader looks for it (~/.psi/packages/<pkg>/). No shim, no separate
        # code upload: psi ships inside the installed fork, loaded natively.
        psi_toml = '[provider]\nbackend = "openrouter"\n\n[rules]\npackages = ["clean-code@0.0.1"]\n\n[clarify]\nautonomy = "proceed-flagged"\n'
        if os.environ.get("PSI_LEDGER", "0").lower() in ("1", "true", "yes"):
            psi_toml += "[ledger]\nenabled = true\n"
        pkg_src = Path(__file__).parent.parent / "vendor/tau/src/psi/packages/clean-code/0.0.1/rules.toml"
        cfg_cmd = (
            "mkdir -p /root/.psi/packages/clean-code@0.0.1 && "
            f"cat > /root/.psi/psi.toml <<'PSI_EOF'\n{psi_toml}PSI_EOF\n"
        )
        result = await environment.exec(cfg_cmd, timeout_sec=30)
        if result.return_code != 0:
            raise RuntimeError(f"psi.toml staging failed: {result.stdout} {result.stderr}")
        if pkg_src.exists():
            # upload_file is an SDK-direct copy with NO shell — "~" would land
            # as a literal /app/~ directory (B4 bug: 9/10 arm-B patches carried
            # a ~/.psi/ git hunk, and the rules never reached /root/.psi, so
            # arm B ran with ZERO stdlib rules loaded). Always use the absolute
            # container path here.
            await environment.upload_file(str(pkg_src), "/root/.psi/packages/clean-code@0.0.1/rules.toml")
            # package glossary ships with the package (round 28: vocabulary
            # lives with its owner — no psi-core seed dictionary)
            glossary_src = Path(__file__).parent.parent / "vendor/tau/src/psi/packages/clean-code/0.0.1/glossary.toml"
            if glossary_src.exists():
                await environment.upload_file(str(glossary_src), "/root/.psi/packages/clean-code@0.0.1/glossary.toml")
        self.logger.info("psi layer staged (arm B — built into the fork)")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # instruction travels as a file: no shell-quoting exposure
        tmp_instruction = "/tmp/psi_instruction.md"
        tmp_local = Path(self.logs_dir) / "instruction.md"
        tmp_local.write_text(instruction)
        await environment.upload_file(str(tmp_local), tmp_instruction)

        # deep-swe tasks: repo lives at /app; only COMMITTED work is graded.
        # Model + Jev credentials travel per-exec (never baked into the image);
        # pier's --env-file loads them into the orchestrator env, not the container.
        agent_env = {}
        for var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            val = self._get_env(var)
            if val:
                agent_env[var] = val
        # psi gate: the layer ships INSIDE the fork now — the in-container
        # env decides whether hooks arm (arm B) or stay off (arm A).
        agent_env["PSI_ENABLED"] = "1" if _psi_enabled() else "0"
        # distiller model for the in-container fact agent (SEC 4): same eval model
        if self.model_name:
            agent_env["PSI_MODEL"] = self.model_name.partition("/")[2] or self.model_name
        model_flag = ""
        if self.model_name and "/" in self.model_name:
            # harbor's "provider/model" maps to tau's SEPARATE flags — tau does
            # NOT parse the slash form (--model openrouter/... parsed as provider
            # openai + garbage model id, verified in image).
            provider, _, model = self.model_name.partition("/")
            model_flag = f"--provider {shlex.quote(provider)} --model {shlex.quote(model)}"
        elif self.model_name:
            model_flag = f"--model {shlex.quote(self.model_name)}"
        # thinking level (arms C/D): TAU_THINKING env (max|high|...), unset = provider default
        thinking = os.environ.get("TAU_THINKING", "").strip()
        thinking_flag = f"--thinking {shlex.quote(thinking)}" if thinking else ""
        # filtered egress: pier scopes proxy vars to agent commands via
        # agent_process_env() — installed agents must route their execs through
        # it, or the main container (internal network only) has no DNS/egress.
        merged_env = environment.agent_process_env(agent_env or None)
        result = await environment.exec(
            f'psi {model_flag} {thinking_flag} -p "$(cat {tmp_instruction})"',
            cwd="/app",
            env=merged_env,
            timeout_sec=None,  # harbor's agent-timeout governs
        )
        # persist tau's full output host-side — exec stdout never reaches pier logs.
        # Scrub credential material: compose errors echo the exec command,
        # which includes per-exec env values.
        import re as _re

        def _scrub(text: str | None) -> str:
            if not text:
                return ""
            return _re.sub(r"(sk-or-|sk-ant-|sk-)[A-Za-z0-9_\-]+", "[REDACTED-KEY]", text)

        out_log = Path(self.logs_dir) / "tau-output.log"
        out_log.write_text(
            f"cmd: tau <flags> -p <instruction-file>\n"
            f"exit_code: {result.return_code}\n"
            f"--- stdout ---\n{_scrub(result.stdout)}\n--- stderr ---\n{_scrub(result.stderr)}\n"
        )

        commit = (
            'cd /app && git config --global --add safe.directory /app && '
            'git config user.email "agent@psi.local" && git config user.name "psi-agent" && '
            'git add -A && { git diff --cached --quiet || git commit -m "psi-agent work"; }'
        )
        commit_result = await environment.exec(commit, cwd="/app", timeout_sec=300)

        # reset /logs/artifacts ownership so pier's collect hook + docker-cp +
        # the verifier's artifact copy all work across rootless podman's
        # per-container subuid mappings (container root == host user).
        await environment.exec(
            "rm -rf /logs/artifacts && mkdir -p /logs/artifacts && "
            "chmod 0777 /logs/artifacts && chown 0:0 /logs/artifacts",
            user="root",
            timeout_sec=60,
        )

        # persist ψ telemetry host-side BEFORE teardown destroys the container
        # (B4 lesson: /root/.psi/telemetry.jsonl was lost with every trial —
        # L1d analysis then has no interception evidence at all).
        for src_name, dst_name in (("telemetry.jsonl", "psi-telemetry.jsonl"),
                                   ("ledger.jsonl", "psi-ledger.jsonl"),
                                   ("facts.jsonl", "psi-facts.jsonl")):
            pulled = await environment.exec(
                f"cat /root/.psi/{src_name} 2>/dev/null || true",
                timeout_sec=30,
            )
            body = (pulled.stdout or "").strip()
            # banner-tolerant: exec stdout carries the podman wrapper banner
            # first — keep only JSON-object lines (banner leaked into the
            # skrub smoke telemetry's first line)
            body = "\n".join(l for l in body.splitlines() if l.strip().startswith("{"))
            if body.strip():
                (Path(self.logs_dir) / dst_name).write_text(body + "\n")

        # persist the FULL tau session JSONLs (the replayable trace — round 28:
        # without these, our own runs can't feed the replay regression suite).
        # /logs/agent is pier's bind mount for the trial agent dir — files
        # dropped here are downloaded host-side by _maybe_download_logs.
        # tau stores each session as a DIRECTORY under /root/.psi/sessions/
        # (app-<id>/<hash>.jsonl + index.jsonl) — the old flat *.jsonl glob
        # matched nothing and `|| true` swallowed it (silent no-op, found
        # 2026-10-13 in the smoke trial). Copy the whole sessions tree.
        await environment.exec(
            "mkdir -p /logs/agent/tau-sessions && "
            "cp -r /root/.psi/sessions/. /logs/agent/psi-sessions/ && "
            "chmod -R a+rX /logs/agent/psi-sessions/ 2>/dev/null || true",
            user="root",
            timeout_sec=30,
        )

        context.metadata = {
            "agent": self.name(),
            "psi_enabled": _psi_enabled(),
            "exit_code": result.return_code,
            "commit_exit_code": commit_result.return_code,
        }

        # usage capture: sum tau's session JSONLs in-container. The exec stdout
        # carries the podman wrapper banner first - parse only brace lines.
        usage = await environment.exec(
            "python3 - <<'PYEOF'\n"
            "import json, glob\n"
            "in_=out=cache=0; n=0\n"
            "for p in glob.glob('/root/.psi/sessions/*.jsonl'):\n"
            "    n += 1\n"
            "    for line in open(p, errors='replace'):\n"
            "        try: e=json.loads(line)\n"
            "        except Exception: continue\n"
            "        u=e.get('usage') or {}\n"
            "        in_+=u.get('input_tokens',0); out+=u.get('output_tokens',0)\n"
            "        cache+=u.get('cache_read_input_tokens',0) or u.get('cache_read_tokens',0) or u.get('cache_tokens',0) or 0\n"
            "print(json.dumps({'n_input_tokens':in_,'n_output_tokens':out,'n_cache_tokens':cache,'sessions':n}))\n"
            "PYEOF",
            timeout_sec=30,
        )
        payload = [l for l in (usage.stdout or "").splitlines() if l.strip().startswith("{")]
        usage_data = {}
        if payload:
            try:
                usage_data = json.loads(payload[-1])
            except json.JSONDecodeError:
                usage_data = {}
        if usage_data:
            context.n_input_tokens = usage_data.get("n_input_tokens")
            context.n_output_tokens = usage_data.get("n_output_tokens")
            context.n_cache_tokens = usage_data.get("n_cache_tokens")
            context.metadata["usage"] = usage_data