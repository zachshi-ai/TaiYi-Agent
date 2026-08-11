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
import uuid
from pathlib import Path

from taiyi.runtime.executor import ExecResult
from taiyi.runtime.jobs import JobHandle, JobRecord, JobStatus, JobStore
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
        timeout: float | None = None,
        hard_timeout: float = 1800.0,
        idle_timeout: float | None = None,
        heartbeat_interval: float = 1.0,
        job_dir: str | Path | None = None,
        output_limit: int = 16_384,
        backend: str = "local",
    ):
        self.sandbox = Path(sandbox).resolve()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.ssrf = ssrf_guard or SSRFGuard()
        self.env_allow = env_allow
        self.hard_timeout = timeout if timeout is not None else hard_timeout
        self.idle_timeout = idle_timeout
        if self.hard_timeout is not None and self.hard_timeout <= 0:
            raise ValueError("hard_timeout must be positive")
        if self.idle_timeout is not None and self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")
        self.heartbeat_interval = max(0.05, heartbeat_interval)
        self.output_limit = max(256, output_limit)
        self.timeout = self.hard_timeout  # backward-compatible public attribute
        default_job_dir = self.sandbox.parent / f".{self.sandbox.name}.taiyi-jobs"
        self.jobs = JobStore(job_dir or default_job_dir)
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
                handle = self.start(step, operation_id=f"adhoc:{uuid.uuid4().hex}")
                return self.wait(handle.job_id)
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
    def supports_jobs(self, step: PlanStep) -> bool:
        return step.tool.startswith("shell:")

    def start(self, step: PlanStep, *, operation_id: str) -> JobHandle:
        if not self.supports_jobs(step):
            raise ValueError(f"tool does not support durable jobs: {step.tool}")
        argv, env = self._shell_argv(step.tool[len("shell:"):], step.args)
        return self.jobs.start(
            argv,
            cwd=self.sandbox,
            env=env,
            operation_id=operation_id,
            tool=step.tool,
            hard_timeout=self.hard_timeout,
            idle_timeout=self.idle_timeout,
            heartbeat_interval=self.heartbeat_interval,
        )

    def poll(self, job_id: str) -> JobRecord:
        return self.jobs.poll(job_id)

    def find(self, operation_id: str) -> JobHandle | None:
        record = self.jobs.find_by_operation(operation_id)
        return None if record is None else JobHandle(record.job_id, record.operation_id)

    def cancel(self, job_id: str) -> JobRecord:
        return self.jobs.cancel(job_id)

    def wait(self, job_id: str) -> ExecResult:
        record = self.jobs.wait(job_id)
        output, truncated = self.jobs.output_tail(job_id, max_bytes=self.output_limit)
        stdout_path, stderr_path = self.jobs.output_paths(job_id)
        if not output:
            output = self._empty_output(record)
        duration = None
        if record.started_at is not None and record.finished_at is not None:
            duration = max(0.0, record.finished_at - record.started_at)
        return ExecResult(
            output=output,
            ok=record.status is JobStatus.SUCCEEDED,
            operation_id=record.operation_id,
            job_id=record.job_id,
            exit_code=record.returncode,
            signal=record.signal,
            failure_kind=record.failure_kind,
            timeout_kind=record.timeout_kind,
            stdout_artifact=str(stdout_path),
            stderr_artifact=str(stderr_path),
            output_truncated=truncated,
            duration_seconds=duration,
            error=record.error,
        )

    def _shell_argv(self, command: str, args: list[str]) -> tuple[list[str], dict[str, str]]:
        argv = shlex.split(command) + list(args)
        if not argv:
            raise ValueError("empty command")
        env = safe_environment(self.env_allow)
        env.setdefault("GIT_TERMINAL_PROMPT", "0")  # never block on a prompt
        env["PWD"] = str(self.sandbox)
        if self.backend == "sandbox_exec":
            argv = ["sandbox-exec", "-p", self._build_profile(), "--", *argv]
        return argv, env

    @staticmethod
    def _empty_output(record: JobRecord) -> str:
        if record.status is JobStatus.SUCCEEDED:
            return "[exit 0]"
        if record.timeout_kind:
            return f"[{record.timeout_kind} timeout]"
        if record.signal is not None:
            return f"[signal {record.signal}]"
        if record.returncode is not None:
            return f"[exit {record.returncode}]"
        return record.error or f"[{record.status.value.lower()}]"

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
