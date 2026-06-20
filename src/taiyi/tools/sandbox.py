"""SandboxExecutor — a real, constrained executor for cleared steps.

Replaces ``MockExecutor`` for the operations that actually touch the system:
shell commands and file I/O run inside a sandbox directory with a scrubbed
environment; URL tools are screened by the SSRF guard. The runtime calls this
ONLY after governance has cleared a step — this layer is the defense in depth
behind that gate, not a replacement for it.

Business-integration tools (sql:, notify:, tool:refund) have no connector yet, so
they return a clearly-labelled deferred result rather than pretending to run.
Docker-backed isolation plugs in behind the same ``Executor`` interface and is a
later opt-in; ``LocalToolBackend`` (this module) is the first backend.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from taiyi.runtime.executor import ExecResult
from taiyi.scheduler.planner import PlanStep
from taiyi.tools.credentials import safe_environment
from taiyi.tools.ssrf import SSRFError, SSRFGuard

_REAL_PREFIXES = ("shell:", "file:read", "file:write", "http:", "https:")


class SandboxExecutor:
    def __init__(
        self,
        sandbox: str | Path,
        *,
        ssrf_guard: SSRFGuard | None = None,
        env_allow: tuple[str, ...] = (),
        timeout: float = 30.0,
    ):
        self.sandbox = Path(sandbox).resolve()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.ssrf = ssrf_guard or SSRFGuard()
        self.env_allow = env_allow
        self.timeout = timeout

    def execute(self, step: PlanStep) -> ExecResult:
        tool = step.tool
        try:
            if tool.startswith("shell:"):
                return self._run_shell(tool[len("shell:"):], step.args)
            if tool.startswith("file:read"):
                return self._read_file(step.args)
            if tool.startswith("file:write"):
                return self._write_file(step.args)
            if tool.startswith(("http:", "https:")):
                return self._screen_url(step.args)
            # No connector yet — do not pretend to perform a side effect.
            return ExecResult(f"[deferred:{tool}] no connector yet (args={step.args})", ok=True)
        except Exception as e:  # noqa: BLE001 — surface as a failed step, not a crash
            return ExecResult(f"executor error: {type(e).__name__}: {e}", ok=False)

    # --- shell ---------------------------------------------------------------
    def _run_shell(self, command: str, args: list[str]) -> ExecResult:
        argv = shlex.split(command) + list(args)
        if not argv:
            return ExecResult("empty command", ok=False)
        env = safe_environment(self.env_allow)
        env.setdefault("GIT_TERMINAL_PROMPT", "0")  # never block on a prompt
        proc = subprocess.run(
            argv,
            cwd=self.sandbox,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        out = (proc.stdout + proc.stderr).strip()
        return ExecResult(out or f"[exit {proc.returncode}]", ok=proc.returncode == 0)

    # --- files (confined to the sandbox) -------------------------------------
    def _resolve_in_sandbox(self, rel: str) -> Path:
        target = (self.sandbox / rel).resolve()
        if self.sandbox != target and self.sandbox not in target.parents:
            raise PermissionError(f"path escapes sandbox: {rel}")
        return target

    def _read_file(self, args: list[str]) -> ExecResult:
        if not args:
            return ExecResult("file:read needs a path", ok=False)
        path = self._resolve_in_sandbox(args[0])
        return ExecResult(path.read_text(encoding="utf-8"))

    def _write_file(self, args: list[str]) -> ExecResult:
        if len(args) < 2:
            return ExecResult("file:write needs a path and content", ok=False)
        path = self._resolve_in_sandbox(args[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args[1], encoding="utf-8")
        return ExecResult(f"wrote {len(args[1])} bytes to {args[0]}")

    # --- URL screening (enforce SSRF; fetch deferred to the connected phase) --
    def _screen_url(self, args: list[str]) -> ExecResult:
        url = args[0] if args else ""
        try:
            self.ssrf.check(url)
        except SSRFError as e:
            return ExecResult(f"SSRF blocked: {e}", ok=False)
        return ExecResult(f"[url cleared by SSRF guard; fetch deferred offline] {url}", ok=True)
