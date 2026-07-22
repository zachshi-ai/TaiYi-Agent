"""SandboxExecutor — a real, constrained executor for cleared steps.

Replaces ``MockExecutor`` for the operations that actually touch the system:
shell commands and file I/O run inside a sandbox directory with a scrubbed
environment; URL tools are screened by the SSRF guard. The runtime calls this
ONLY after governance has cleared a step — this layer is the defense in depth
behind that gate, not a replacement for it.

Business-integration tools (sql:, notify:, tool:refund) have no connector yet, so
they return a clearly-labelled deferred result rather than pretending to run.

Two shell backends:
  * ``local``      — runs the command directly with a scrubbed env (the default,
                     and the only one available off macOS).
  * ``sandbox_exec`` — wraps each command in macOS ``sandbox-exec`` with a
                     deny-all profile that whitelists only sandbox-dir writes,
                     system-binary reads, and TMPDIR. This is real OS-level
                     isolation: a command that tries to write outside the sandbox
                     or reach the network is refused by the kernel, not by a
                     fragile denylist. Falls back to ``local`` off macOS.
``sandbox_exec`` uses Apple's deprecated-but-still-shipping ``sandbox-exec``; it
is a pragmatic single-machine defense, not a hardened multi-tenant boundary.
"""
from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path

from taiyi.runtime.executor import ExecResult
from taiyi.scheduler.planner import PlanStep
from taiyi.tools.credentials import safe_environment
from taiyi.tools.ssrf import SSRFError, SSRFGuard

_REAL_PREFIXES = ("shell:", "file:read", "file:write", "http:", "https:")


class SandboxExecutor:
    environment = "workspace"

    def __init__(
        self,
        sandbox: str | Path,
        *,
        ssrf_guard: SSRFGuard | None = None,
        env_allow: tuple[str, ...] = (),
        timeout: float = 30.0,
        backend: str = "local",
    ):
        self.sandbox = Path(sandbox).resolve()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.ssrf = ssrf_guard or SSRFGuard()
        self.env_allow = env_allow
        self.timeout = timeout
        # sandbox_exec only works on macOS and only if the binary exists; degrade
        # gracefully elsewhere so the same code runs on Linux CI.
        self.backend = self._resolve_backend(backend)

    def _resolve_backend(self, requested: str) -> str:
        if requested != "sandbox_exec":
            return "local"
        if platform.system() != "Darwin" or not shutil.which("sandbox-exec"):
            return "local"  # silent fallback: tests/CI on Linux keep working
        return "sandbox_exec"

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
            return ExecResult(f"[deferred:{tool}] no connector configured (args={step.args})", ok=False)
        except Exception as e:  # noqa: BLE001 — surface as a failed step, not a crash
            return ExecResult(f"executor error: {type(e).__name__}: {e}", ok=False)

    # --- shell ---------------------------------------------------------------
    def _run_shell(self, command: str, args: list[str]) -> ExecResult:
        argv = shlex.split(command) + list(args)
        if not argv:
            return ExecResult("empty command", ok=False)
        env = safe_environment(self.env_allow)
        env.setdefault("GIT_TERMINAL_PROMPT", "0")  # never block on a prompt
        if self.backend == "sandbox_exec":
            return self._run_shell_sandboxed(argv, env)
        return self._run_shell_local(argv, env)

    def _run_shell_local(self, argv: list[str], env: dict) -> ExecResult:
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

    def _run_shell_sandboxed(self, argv: list[str], env: dict) -> ExecResult:
        """Run argv under a macOS sandbox-exec deny-all profile.

        The profile allows: writing only inside the sandbox dir, reading system
        binaries/libs + the sandbox dir + TMPDIR, and denies all network. The
        command itself is exec'd inside the sandbox, so a write outside it is
        refused by the kernel — not by a denylist we hope is complete.
        """
        profile = self._build_profile()
        sandbox_argv = ["sandbox-exec", "-p", profile, "--", *argv]
        proc = subprocess.run(
            sandbox_argv,
            cwd=self.sandbox,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        out = (proc.stdout + proc.stderr).strip()
        return ExecResult(out or f"[exit {proc.returncode}]", ok=proc.returncode == 0)

    def _build_profile(self) -> str:
        """A deny-all sandbox profile: whitelist sandbox writes + system reads, no net.

        The baseline is ``(deny default)`` plus ``system.sb`` (Apple's bundle of
        OS-basics). On top of that we open the minimum holes a normal command
        needs: reading system binaries/libs/dyld, writing only the sandbox dir
        and TMPDIR, forking sub-processes, and exec'ing the command. Network is
        denied entirely.
        """
        sb = str(self.sandbox)
        tmpdir = os.environ.get("TMPDIR", "/tmp")
        return f"""(version 1)
(deny default)
(import "system.sb")
;; read system binaries, libraries, dyld, and the sandbox + tmp
(allow file-read* (subpath "/usr/bin") (subpath "/bin") (subpath "/usr/lib") (subpath "/usr/local") (subpath "/System/Library") (subpath "/Library") (subpath "/private/var/db/dyld") (subpath "/private/etc") (subpath "/etc"))
(allow file-read* (regex #"^/private/var/select/"))
(allow file-read* (subpath "{sb}"))
(allow file-read* (subpath "{tmpdir}"))
;; writes confined to the sandbox directory and tmp only
(allow file-write* (subpath "{sb}"))
(allow file-write* (subpath "{tmpdir}"))
;; a command may fork sub-processes and exec (e.g. `sh -c`)
(allow process-fork)
(allow process-exec)
;; no network from inside the sandbox
(deny network*)
"""

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
