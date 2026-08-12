"""External-harness capability probes and a fail-closed command adapter."""
from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from taiyi.benchmark.schema import HarnessProbe, MeasurementStatus, canonical_digest


@dataclass(frozen=True)
class CommandRun:
    status: MeasurementStatus
    reported_state: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout_path: str
    stderr_path: str
    result_path: str | None
    result: Mapping[str, Any]
    error: str | None = None


class CommandHarnessAdapter:
    """Run a batch harness without inferring success from process exit alone.

    A harness must write a normalized result JSON file containing at least
    ``reported_state``. Exit code zero only proves the adapter process returned;
    it is never treated as task completion.
    """

    def __init__(
        self,
        *,
        harness_id: str,
        argv: tuple[str, ...],
        version_argv: tuple[str, ...],
        model_id: str,
        isolated_workspace: bool,
        environment: Mapping[str, str] | None = None,
    ):
        self.harness_id = harness_id
        self.argv = argv
        self.version_argv = version_argv
        self.model_id = model_id
        self.isolated_workspace = isolated_workspace
        self.environment = dict(environment or {})

    def probe(self) -> HarnessProbe:
        executable = shutil.which(self.argv[0])
        if executable is None:
            return HarnessProbe(
                harness_id=self.harness_id,
                adapter="command",
                status=MeasurementStatus.UNAVAILABLE,
                model=self.model_id,
                batch_command=self.argv,
                isolated_workspace=self.isolated_workspace,
                reason=f"executable not found: {self.argv[0]}",
            )
        version = _command_version(self.version_argv)
        status = (
            MeasurementStatus.MEASURED
            if self.isolated_workspace
            else MeasurementStatus.NOT_COMPARABLE
        )
        reason = None if self.isolated_workspace else (
            "adapter cannot prove that tool execution is confined to the supplied workspace"
        )
        return HarnessProbe(
            harness_id=self.harness_id,
            adapter="command",
            status=status,
            version=version,
            model=self.model_id,
            batch_command=self.argv,
            isolated_workspace=self.isolated_workspace,
            same_model_configurable=True,
            reason=reason,
        )

    def run(
        self,
        *,
        workspace: str | Path,
        prompt_file: str | Path,
        artifact_dir: str | Path,
        timeout_seconds: float,
    ) -> CommandRun:
        probe = self.probe()
        artifacts = Path(artifact_dir)
        artifacts.mkdir(parents=True, exist_ok=True)
        stdout_path = artifacts / "stdout.log"
        stderr_path = artifacts / "stderr.log"
        result_path = artifacts / "normalized-result.json"
        # Never let a previous invocation's receipt certify this process.
        result_path.unlink(missing_ok=True)
        if probe.status is not MeasurementStatus.MEASURED:
            return CommandRun(
                status=probe.status,
                reported_state="UNAVAILABLE",
                exit_code=None,
                timed_out=False,
                duration_seconds=0.0,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                result_path=None,
                result={},
                error=probe.reason,
            )

        replacements = {
            "workspace": str(Path(workspace).resolve()),
            "prompt_file": str(Path(prompt_file).resolve()),
            "result_file": str(result_path.resolve()),
            "run_id": uuid.uuid4().hex,
        }
        argv = [part.format(**replacements) for part in self.argv]
        env = {"PATH": os.environ.get("PATH", ""), **self.environment}
        started = time.monotonic()
        timed_out = False
        exit_code = None
        error = None
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=Path(workspace),
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                try:
                    exit_code = process.wait(timeout=max(0.1, float(timeout_seconds)))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    error = "outer benchmark timeout"
                    terminate_process_group(process)
                    exit_code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                error = "outer benchmark timeout"
            except OSError as exc:
                error = f"{type(exc).__name__}: {exc}"

        result: dict[str, Any] = {}
        status = MeasurementStatus.ERROR
        reported_state = "UNKNOWN"
        if result_path.is_file():
            try:
                loaded = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    result = loaded
                    raw_state = result.get("reported_state")
                    if not isinstance(raw_state, str) or not raw_state.strip():
                        error = "normalized result has no non-empty reported_state"
                    else:
                        reported_state = raw_state.strip()
                        status = MeasurementStatus.MEASURED
                else:
                    error = "normalized result is not a JSON object"
            except (OSError, json.JSONDecodeError) as exc:
                error = f"invalid normalized result: {type(exc).__name__}: {exc}"
        elif error is None:
            error = "harness returned without a normalized result receipt"
        if timed_out:
            # A receipt written before a later hang is useful evidence but not a
            # terminal measurement. The outer deadline remains authoritative.
            status = MeasurementStatus.ERROR

        return CommandRun(
            status=status,
            reported_state=reported_state,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_seconds=max(0.0, time.monotonic() - started),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            result_path=(str(result_path) if result_path.is_file() else None),
            result=result,
            error=error,
        )


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop a timed-out harness and every child it started in its session."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=2)


def probe_external_harnesses() -> list[HarnessProbe]:
    """Return observed capability cells without manufacturing benchmark scores."""

    pi = shutil.which("pi")
    openclaw = shutil.which("openclaw")
    openclaw_isolated = _openclaw_isolated() if openclaw else False
    zcode_app = Path("/Applications/ZCode.app")
    probes = [
        HarnessProbe(
            harness_id="pi",
            adapter="pi-json",
            status=(MeasurementStatus.NOT_COMPARABLE if pi else MeasurementStatus.UNAVAILABLE),
            version=(_command_version(("pi", "--version")) if pi else None),
            batch_command=("pi", "--mode", "json", "--no-session", "--no-approve"),
            isolated_workspace=False,
            same_model_configurable=bool(pi),
            reason=(
                "installed CLI still needs an isolated wrapper, common model route, and normalized receipt adapter"
                if pi
                else "pi CLI is not installed"
            ),
            source_url="https://github.com/earendil-works/pi",
        ),
        HarnessProbe(
            harness_id="openclaw",
            adapter="openclaw-agent-json",
            status=(
                MeasurementStatus.NOT_COMPARABLE
                if openclaw
                else MeasurementStatus.UNAVAILABLE
            ),
            version=(_command_version(("openclaw", "--version")) if openclaw else None),
            batch_command=("openclaw", "agent", "--local", "--json", "--message-file"),
            isolated_workspace=openclaw_isolated,
            same_model_configurable=bool(openclaw),
            reason=(
                (
                    "installed profile is isolated but a common model route and normalized receipt adapter are required"
                    if openclaw_isolated
                    else "installed profile is host-running or isolation could not be proven; a dedicated benchmark profile and common model route are required"
                )
                if openclaw
                else "openclaw CLI is not installed"
            ),
            source_url="https://github.com/openclaw/openclaw",
        ),
        HarnessProbe(
            harness_id="zcode",
            adapter="zcode-desktop",
            status=MeasurementStatus.UNAVAILABLE,
            version=None,
            batch_command=(),
            isolated_workspace=False,
            same_model_configurable=False,
            reason=(
                "desktop app is installed but no documented non-interactive CLI/adapter is available"
                if zcode_app.exists()
                else "ZCode desktop app and batch CLI are unavailable"
            ),
            source_url="https://zcode.z.ai/en/docs/agents",
        ),
    ]
    return probes


def probe_set_digest(probes: list[HarnessProbe]) -> str:
    stable = []
    for probe in probes:
        value = probe.to_dict()
        value.pop("observed_at", None)
        stable.append(value)
    return canonical_digest(stable)


def _openclaw_isolated() -> bool:
    try:
        result = subprocess.run(
            ["openclaw", "sandbox", "explain", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            text=True,
        )
        value = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return False
    sandbox = value.get("sandbox") if isinstance(value, dict) else None
    return bool(
        isinstance(sandbox, dict)
        and sandbox.get("sessionIsSandboxed") is True
        and sandbox.get("mode") != "off"
        and sandbox.get("workspaceSource") != "direct"
    )


def _command_version(argv: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = result.stdout.strip().splitlines()
    return line[0][:300] if line else None


__all__ = [
    "CommandHarnessAdapter",
    "CommandRun",
    "probe_external_harnesses",
    "probe_set_digest",
    "terminate_process_group",
]
