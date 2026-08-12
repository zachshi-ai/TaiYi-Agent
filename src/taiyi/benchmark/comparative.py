"""Controlled cross-harness transport and tool-loop comparison."""
from __future__ import annotations

import json
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from taiyi.benchmark.adapters import terminate_process_group
from taiyi.benchmark.model_fixture import (
    CONTROLLED_MODEL_ID,
    CONTROLLED_MODEL_POLICY,
    ControlledModelServer,
)
from taiyi.benchmark.schema import (
    COMPARATIVE_WORKER_SCHEMA,
    ComparativeReceipt,
    MeasurementStatus,
    canonical_digest,
    verify_artifact,
)


COMPARATIVE_CASE_ID = "fixed_file_delivery"
COMPARATIVE_SCOPE = "controlled_transport_tool_conformance"
COMPARATIVE_PROMPT = (
    "Create result.txt in the current workspace with the exact content verified. "
    "Use the available write tool, then finish."
)
ACCEPTANCE_PATH = "result.txt"
ACCEPTANCE_CONTENT = "verified"
WALL_TIMEOUT_SECONDS = 30.0


def comparative_manifest(model_policy_digest: str) -> dict[str, Any]:
    fixture = {"README.md": "# Controlled harness comparison fixture\n"}
    fixed = {
        "measurement_scope": COMPARATIVE_SCOPE,
        "case_id": COMPARATIVE_CASE_ID,
        "prompt": COMPARATIVE_PROMPT,
        "prompt_digest": canonical_digest(COMPARATIVE_PROMPT),
        "model_id": CONTROLLED_MODEL_ID,
        "model_policy": CONTROLLED_MODEL_POLICY,
        "model_policy_digest": model_policy_digest,
        "workspace_fixture": fixture,
        "workspace_fixture_digest": canonical_digest(fixture),
        "acceptance": {"path": ACCEPTANCE_PATH, "content": ACCEPTANCE_CONTENT},
        "acceptance_digest": canonical_digest({
            "path": ACCEPTANCE_PATH,
            "content": ACCEPTANCE_CONTENT,
        }),
        "budgets": {
            "wall_timeout_seconds": WALL_TIMEOUT_SECONDS,
            "max_model_requests": 2,
            "allowed_tools": ["read", "write"],
        },
        "secret_policy": {
            "inherit_user_home": False,
            "inherit_provider_credentials": False,
            "credential": "non-secret loopback placeholder",
        },
        "evaluator": "independent exact-content observation",
        "ranking_eligible": False,
    }
    fixed["comparability_signature"] = canonical_digest(fixed)
    fixed["claim_boundary"] = [
        "This compares adapter transport, isolated tool execution, and completion truth.",
        "The controlled endpoint is not a real intelligence or coding-quality model.",
        "Real-provider ranking requires a separately frozen provider/model revision and budget.",
    ]
    return fixed


def materialize_fixture(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text(
        "# Controlled harness comparison fixture\n", encoding="utf-8"
    )


def run_taiyi_cell(
    *,
    server: ControlledModelServer,
    manifest: dict[str, Any],
    run_root: Path,
    artifact_dir: Path | None = None,
) -> ComparativeReceipt:
    workspace = run_root / "workspace"
    isolated_home = run_root / "home"
    temporary = run_root / "tmp"
    process_logs = run_root / "logs"
    for path in (workspace, isolated_home, temporary, process_logs):
        path.mkdir(parents=True, exist_ok=True)
    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
    materialize_fixture(workspace)
    initial = workspace_snapshot(workspace)
    request_start = server.request_count
    result_path = run_root / "worker-result.json"
    process_stdout = process_logs / "stdout.log"
    process_stderr = process_logs / "stderr.log"
    argv = [
        sys.executable,
        "-m",
        "taiyi.benchmark.taiyi_worker",
        "--base-url",
        server.base_url,
        "--run-root",
        str(run_root),
        "--prompt",
        COMPARATIVE_PROMPT,
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(isolated_home),
        "TMPDIR": str(temporary),
        "NO_COLOR": "1",
        "PYTHONHASHSEED": "0",
    }
    started = time.monotonic()
    timed_out = False
    exit_code = None
    error = None
    with process_stdout.open("wb") as stdout, process_stderr.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                argv,
                cwd=workspace,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=WALL_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                error = "outer benchmark timeout"
                terminate_process_group(process)
                exit_code = process.returncode
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
    duration = max(0.0, time.monotonic() - started)
    worker: dict[str, Any] | None = None
    if result_path.is_file():
        try:
            worker = verify_artifact(
                json.loads(result_path.read_text(encoding="utf-8")),
                schema_version=COMPARATIVE_WORKER_SCHEMA,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            if error is None:
                error = f"invalid TaiYi worker receipt: {type(exc).__name__}: {exc}"
    if worker is not None and worker.get("error") and error is None:
        error = str(worker["error"])

    final = workspace_snapshot(workspace)
    task_passed = _acceptance_passed(workspace)
    claimed_complete = bool(
        exit_code == 0
        and worker is not None
        and worker.get("reported_state") == "COMPLETED"
        and not timed_out
    )
    requests = server.request_summaries(since=request_start)
    budget_passed = (
        not timed_out
        and duration <= float(manifest["budgets"]["wall_timeout_seconds"])
        and len(requests) <= int(manifest["budgets"]["max_model_requests"])
    )
    status = (
        MeasurementStatus.MEASURED
        if exit_code == 0 and worker is not None and error is None and not timed_out
        else MeasurementStatus.ERROR
    )
    expected_taiyi_tools = sorted(
        f"file:{item}" for item in manifest["budgets"]["allowed_tools"]
    )
    model_visible_tools = sorted(
        str(item) for item in (worker or {}).get("model_visible_tools", [])
    )
    executor_allowed_tools = sorted(
        str(item) for item in (worker or {}).get("executor_allowed_tools", [])
    )
    tool_surface_matches = (
        model_visible_tools == expected_taiyi_tools
        and executor_allowed_tools == expected_taiyi_tools
    )
    comparison_blockers = (
        ()
        if tool_surface_matches
        else ("observed tool surface does not match the frozen manifest",)
    )
    private_roots = (run_root, workspace, Path.home(), Path(sys.executable).parent)
    _sanitize_log(process_stdout, private_roots)
    _sanitize_log(process_stderr, private_roots)
    if artifact_dir is not None:
        shutil.copy2(process_stdout, artifact_dir / "stdout.log")
        shutil.copy2(process_stderr, artifact_dir / "stderr.log")
    return ComparativeReceipt(
        run_id=f"taiyi-{uuid.uuid4().hex[:12]}",
        harness_id="taiyi",
        harness_version="0.1.0",
        adapter="taiyi-live-openai-production-runtime",
        measurement_status=status,
        comparable=status is MeasurementStatus.MEASURED and tool_surface_matches,
        blockers=comparison_blockers,
        model_id=CONTROLLED_MODEL_ID,
        case_id=COMPARATIVE_CASE_ID,
        reported_state=(
            str(worker.get("reported_state")) if worker is not None else "HARNESS_ERROR"
        ),
        task_passed=task_passed,
        claimed_complete=claimed_complete,
        false_completion=claimed_complete and not task_passed,
        timed_out=timed_out,
        exit_code=exit_code,
        duration_seconds=duration,
        budget_passed=budget_passed,
        model_requests=len(requests),
        tool_calls=(int(worker.get("tool_calls", 0)) if worker is not None else 0),
        initial_workspace_digest=initial["tree_digest"],
        final_workspace_digest=final["tree_digest"],
        comparability_signature=manifest["comparability_signature"],
        evidence={
            "initial_workspace": initial,
            "final_workspace": final,
            "model_requests": requests,
            "model_visible_tools": model_visible_tools,
            "executor_allowed_tools": executor_allowed_tools,
            "tool_surface_matches": tool_surface_matches,
            "execution_environment": (
                worker.get("execution_environment") if worker is not None else "workspace"
            ),
            "effect_statuses": (
                list(worker.get("effect_statuses", [])) if worker is not None else []
            ),
            "isolation": {
                "separate_process": True,
                "isolated_home": True,
                "minimal_environment": True,
                "tool_workspace_confined": True,
                "kernel_sandbox": False,
            },
            "stdout_log": ("raw/taiyi/stdout.log" if artifact_dir is not None else None),
            "stderr_log": ("raw/taiyi/stderr.log" if artifact_dir is not None else None),
            "acceptance_observed_digest": _acceptance_digest(workspace),
            "secret_env_names": [],
        },
        error=error,
    )


def run_pi_cell(
    *,
    pi_executable: str | Path,
    server: ControlledModelServer,
    manifest: dict[str, Any],
    run_root: Path,
    artifact_dir: Path,
) -> ComparativeReceipt:
    executable = Path(pi_executable).expanduser().resolve()
    workspace = run_root / "workspace"
    config = run_root / "pi-config"
    sessions = run_root / "pi-sessions"
    isolated_home = run_root / "home"
    temporary = run_root / "tmp"
    process_logs = run_root / "logs"
    for path in (
        workspace,
        config,
        sessions,
        isolated_home,
        temporary,
        process_logs,
        artifact_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    materialize_fixture(workspace)
    initial = workspace_snapshot(workspace)

    blockers = _pi_blockers(executable)
    version = _command_version((str(executable), "--version")) if executable.is_file() else None
    if blockers:
        return blocked_receipt(
            harness_id="pi",
            harness_version=version,
            adapter="pi-json-sandbox-exec",
            blockers=blockers,
            manifest=manifest,
            workspace=workspace,
        )

    _write_pi_config(config, server.base_url)
    profile = _macos_profile(
        run_root=run_root,
        executable=executable,
        model_port=server.port,
    )
    isolation = _prove_profile(profile, run_root)
    if not isolation["passed"]:
        return blocked_receipt(
            harness_id="pi",
            harness_version=version,
            adapter="pi-json-sandbox-exec",
            blockers=("macOS sandbox canary did not prove workspace confinement",),
            manifest=manifest,
            workspace=workspace,
            evidence={"isolation": isolation, "sandbox_profile_digest": canonical_digest(profile)},
        )

    node_executable = Path(shutil.which("node") or "").resolve()
    argv = [
        "sandbox-exec", "-p", profile, "--",
        str(node_executable), str(executable.resolve()),
        "--mode", "json", "--print", "--no-session",
        "--provider", "taiyi-benchmark", "--model", CONTROLLED_MODEL_ID,
        "--api-key", "benchmark-local-only", "--thinking", "off",
        "--tools", "read,write", "--no-extensions", "--no-skills",
        "--no-prompt-templates", "--no-context-files", "--no-approve",
        "--offline", COMPARATIVE_PROMPT,
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(isolated_home),
        "TMPDIR": str(temporary),
        "PI_CODING_AGENT_DIR": str(config),
        "PI_CODING_AGENT_SESSION_DIR": str(sessions),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        # Pi imports its native clipboard module even in non-interactive JSON mode.
        # Disabling that irrelevant desktop integration avoids reading the real
        # macOS preference domain from an otherwise isolated benchmark process.
        "TERMUX_VERSION": "taiyi-headless-benchmark",
        "NO_COLOR": "1",
    }
    process_stdout = process_logs / "stdout.log"
    process_stderr = process_logs / "stderr.log"
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    request_start = server.request_count
    started = time.monotonic()
    timed_out = False
    exit_code = None
    error = None
    with process_stdout.open("wb") as stdout, process_stderr.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                argv,
                cwd=workspace,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=WALL_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                error = "outer benchmark timeout"
                terminate_process_group(process)
                exit_code = process.returncode
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
    duration = max(0.0, time.monotonic() - started)
    events, parse_errors = _read_jsonl(process_stdout)
    event_types = [str(event.get("type") or "") for event in events]
    tool_ends = [event for event in events if event.get("type") == "tool_execution_end"]
    agent_ended = "agent_end" in event_types
    final = workspace_snapshot(workspace)
    task_passed = _acceptance_passed(workspace)
    claimed_complete = bool(exit_code == 0 and agent_ended and not timed_out)
    requests = server.request_summaries(since=request_start)
    advertised_tools = _advertised_tools(requests)
    allowed_tools = sorted(str(item) for item in manifest["budgets"]["allowed_tools"])
    tool_surface_matches = _requests_match_tool_surface(requests, allowed_tools)
    budget_passed = (
        not timed_out
        and duration <= float(manifest["budgets"]["wall_timeout_seconds"])
        and len(requests) <= int(manifest["budgets"]["max_model_requests"])
    )

    private_roots = [
        run_root,
        workspace,
        executable.parent,
        executable.resolve().parent,
        _node_package_root(executable),
        Path.home(),
    ]
    private_roots.append(node_executable.parent)
    _sanitize_log(process_stdout, tuple(private_roots))
    _sanitize_log(process_stderr, tuple(private_roots))
    shutil.copy2(process_stdout, stdout_path)
    shutil.copy2(process_stderr, stderr_path)
    if parse_errors and error is None:
        error = f"{parse_errors} invalid JSONL event(s)"
    status = (
        MeasurementStatus.MEASURED
        if exit_code == 0 and agent_ended and not timed_out and not parse_errors
        else MeasurementStatus.ERROR
    )
    if status is MeasurementStatus.ERROR and error is None:
        error = f"Pi exited {exit_code} without a terminal agent event"
    comparison_blockers = (
        ()
        if tool_surface_matches
        else ("observed tool surface does not match the frozen manifest",)
    )
    return ComparativeReceipt(
        run_id=f"pi-{uuid.uuid4().hex[:12]}",
        harness_id="pi",
        harness_version=version,
        adapter="pi-json-sandbox-exec",
        measurement_status=status,
        comparable=status is MeasurementStatus.MEASURED and tool_surface_matches,
        blockers=comparison_blockers,
        model_id=CONTROLLED_MODEL_ID,
        case_id=COMPARATIVE_CASE_ID,
        reported_state=("COMPLETED" if claimed_complete else "FAILED"),
        task_passed=task_passed,
        claimed_complete=claimed_complete,
        false_completion=claimed_complete and not task_passed,
        timed_out=timed_out,
        exit_code=exit_code,
        duration_seconds=duration,
        budget_passed=budget_passed,
        model_requests=len(requests),
        tool_calls=len(tool_ends),
        initial_workspace_digest=initial["tree_digest"],
        final_workspace_digest=final["tree_digest"],
        comparability_signature=manifest["comparability_signature"],
        evidence={
            "initial_workspace": initial,
            "final_workspace": final,
            "model_requests": requests,
            "advertised_tools": advertised_tools,
            "tool_surface_matches": tool_surface_matches,
            "event_count": len(events),
            "event_types": event_types,
            "tool_errors": sum(bool(event.get("isError")) for event in tool_ends),
            "isolation": isolation,
            "native_clipboard_disabled": True,
            "direct_node_entrypoint": True,
            "sandbox_profile_digest": canonical_digest(profile),
            "stdout_log": "raw/pi/stdout.log",
            "stderr_log": "raw/pi/stderr.log",
            "acceptance_observed_digest": _acceptance_digest(workspace),
            "secret_env_names": [],
        },
        error=error,
    )


def run_openclaw_cell(
    *,
    openclaw_executable: str | Path,
    server: ControlledModelServer,
    manifest: dict[str, Any],
    run_root: Path,
    artifact_dir: Path,
) -> ComparativeReceipt:
    """Run the published headless OpenClaw contract inside a confined process."""

    executable = Path(openclaw_executable).expanduser().resolve()
    workspace = run_root / "workspace"
    isolated_home = run_root / "home"
    temporary = run_root / "tmp"
    state = run_root / "state"
    process_logs = run_root / "logs"
    config_path = run_root / "openclaw.json"
    for path in (
        workspace,
        isolated_home,
        temporary,
        state,
        process_logs,
        artifact_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    materialize_fixture(workspace)
    initial = workspace_snapshot(workspace)

    blockers = _openclaw_blockers(executable)
    version = _command_version((str(executable), "--version")) if executable.is_file() else None
    if blockers:
        return blocked_receipt(
            harness_id="openclaw",
            harness_version=version,
            adapter="openclaw-agent-exec-json-sandbox-exec",
            blockers=blockers,
            manifest=manifest,
            workspace=workspace,
        )

    _write_openclaw_config(config_path, server.base_url)
    profile = _macos_profile(
        run_root=run_root,
        executable=executable,
        model_port=server.port,
    )
    isolation = _prove_profile(profile, run_root)
    if not isolation["passed"]:
        return blocked_receipt(
            harness_id="openclaw",
            harness_version=version,
            adapter="openclaw-agent-exec-json-sandbox-exec",
            blockers=("macOS sandbox canary did not prove workspace confinement",),
            manifest=manifest,
            workspace=workspace,
            evidence={"isolation": isolation, "sandbox_profile_digest": canonical_digest(profile)},
        )

    node_executable = Path(shutil.which("node") or "").resolve()
    argv = [
        "sandbox-exec", "-p", profile, "--",
        str(node_executable), str(executable.resolve()),
        "agent", "exec",
        "--config", str(config_path),
        "--cwd", str(workspace),
        "--state-dir", str(state),
        "--model", f"taiyi-benchmark/{CONTROLLED_MODEL_ID}",
        "--code-mode", "direct",
        "--local-model-lean",
        "--thinking", "off",
        "--timeout", str(int(WALL_TIMEOUT_SECONDS - 5)),
        "--json",
        COMPARATIVE_PROMPT,
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(isolated_home),
        "TMPDIR": str(temporary),
        "NO_COLOR": "1",
        "OPENCLAW_TELEMETRY": "0",
    }
    process_stdout = process_logs / "stdout.log"
    process_stderr = process_logs / "stderr.log"
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    request_start = server.request_count
    started = time.monotonic()
    timed_out = False
    exit_code = None
    error = None
    with process_stdout.open("wb") as stdout, process_stderr.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                argv,
                cwd=workspace,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=WALL_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                error = "outer benchmark timeout"
                terminate_process_group(process)
                exit_code = process.returncode
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
    duration = max(0.0, time.monotonic() - started)
    envelope, parse_error = _read_json_object(process_stdout)
    if parse_error and error is None:
        error = parse_error
    structured_status = (
        str(envelope.get("status") or "") if envelope is not None else ""
    )
    structured = (
        envelope is not None
        and isinstance(envelope.get("ok"), bool)
        and structured_status in {"ok", "error", "timeout"}
    )
    if structured_status == "timeout":
        timed_out = True
    if structured and not envelope.get("ok") and error is None:
        detail = envelope.get("error")
        if isinstance(detail, dict) and detail.get("message"):
            error = str(detail["message"])
        else:
            error = f"OpenClaw reported {structured_status}"

    final = workspace_snapshot(workspace)
    task_passed = _acceptance_passed(workspace)
    claimed_complete = bool(
        exit_code == 0
        and structured
        and envelope is not None
        and envelope.get("ok") is True
        and structured_status == "ok"
        and not timed_out
    )
    requests = server.request_summaries(since=request_start)
    advertised_tools = _advertised_tools(requests)
    allowed_tools = sorted(str(item) for item in manifest["budgets"]["allowed_tools"])
    tool_surface_matches = _requests_match_tool_surface(requests, allowed_tools)
    budget_passed = (
        not timed_out
        and duration <= float(manifest["budgets"]["wall_timeout_seconds"])
        and len(requests) <= int(manifest["budgets"]["max_model_requests"])
    )
    status = (
        MeasurementStatus.MEASURED
        if structured and not parse_error and not (timed_out and envelope is None)
        else MeasurementStatus.ERROR
    )
    identity_matches = bool(
        envelope is not None
        and envelope.get("model") == CONTROLLED_MODEL_ID
        and envelope.get("provider") == "taiyi-benchmark"
    )
    tool_summary = envelope.get("toolSummary") if envelope is not None else None
    tool_calls = (
        int(tool_summary.get("calls", 0)) if isinstance(tool_summary, dict) else 0
    )

    private_roots = (
        run_root,
        workspace,
        executable.parent,
        executable.resolve().parent,
        _node_package_root(executable),
        Path.home(),
        node_executable.parent,
    )
    replacements = {
        server.base_url: "<CONTROLLED_MODEL_ENDPOINT>",
        f"http://127.0.0.1:{server.port}": "<CONTROLLED_MODEL_ORIGIN>",
    }
    _sanitize_log(process_stdout, private_roots, replacements=replacements)
    _sanitize_log(process_stderr, private_roots, replacements=replacements)
    if error is not None:
        error = _sanitize_text(error, private_roots, replacements=replacements)
    shutil.copy2(process_stdout, stdout_path)
    shutil.copy2(process_stderr, stderr_path)
    if status is MeasurementStatus.ERROR and error is None:
        error = f"OpenClaw exited {exit_code} without a stable JSON envelope"
    comparison_blockers = tuple(
        reason
        for condition, reason in (
            (
                not identity_matches,
                "reported model/provider identity does not match the frozen manifest",
            ),
            (
                not tool_surface_matches,
                "observed tool surface does not match the frozen manifest",
            ),
        )
        if condition
    )

    return ComparativeReceipt(
        run_id=f"openclaw-{uuid.uuid4().hex[:12]}",
        harness_id="openclaw",
        harness_version=version,
        adapter="openclaw-agent-exec-json-sandbox-exec",
        measurement_status=status,
        comparable=(
            status is MeasurementStatus.MEASURED
            and identity_matches
            and tool_surface_matches
        ),
        blockers=comparison_blockers,
        model_id=CONTROLLED_MODEL_ID,
        case_id=COMPARATIVE_CASE_ID,
        reported_state=(
            "COMPLETED"
            if claimed_complete
            else ("TIMEOUT" if timed_out else "FAILED")
        ),
        task_passed=task_passed,
        claimed_complete=claimed_complete,
        false_completion=claimed_complete and not task_passed,
        timed_out=timed_out,
        exit_code=exit_code,
        duration_seconds=duration,
        budget_passed=budget_passed,
        model_requests=len(requests),
        tool_calls=tool_calls,
        initial_workspace_digest=initial["tree_digest"],
        final_workspace_digest=final["tree_digest"],
        comparability_signature=manifest["comparability_signature"],
        evidence={
            "initial_workspace": initial,
            "final_workspace": final,
            "model_requests": requests,
            "identity_matches": identity_matches,
            "advertised_tools": advertised_tools,
            "tool_surface_matches": tool_surface_matches,
            "assistant_turns": (
                envelope.get("assistantTurns") if envelope is not None else None
            ),
            "code_mode_engaged": (
                envelope.get("codeModeEngaged") if envelope is not None else None
            ),
            "bridge_calls": (
                envelope.get("bridgeCalls") if envelope is not None else None
            ),
            "tool_summary": tool_summary,
            "isolation": isolation,
            "isolated_home": True,
            "explicit_config": True,
            "explicit_state_dir": True,
            "minimal_environment": True,
            "direct_node_entrypoint": True,
            "sandbox_profile_digest": canonical_digest(profile),
            "stdout_log": "raw/openclaw/stdout.log",
            "stderr_log": "raw/openclaw/stderr.log",
            "acceptance_observed_digest": _acceptance_digest(workspace),
            "secret_env_names": [],
        },
        error=error,
    )


def blocked_zcode_receipt(
    manifest: dict[str, Any],
    root: Path,
) -> ComparativeReceipt:
    workspace = root / "zcode" / "workspace"
    materialize_fixture(workspace)
    zcode_reason = (
        "ZCode desktop is installed but has no documented non-interactive batch interface"
        if Path("/Applications/ZCode.app").exists()
        else "ZCode desktop and batch interface are unavailable"
    )
    return blocked_receipt(
        harness_id="zcode",
        harness_version=None,
        adapter="zcode-desktop",
        blockers=(zcode_reason,),
        manifest=manifest,
        workspace=workspace,
    )


def blocked_receipt(
    *,
    harness_id: str,
    harness_version: str | None,
    adapter: str,
    blockers: tuple[str, ...],
    manifest: dict[str, Any],
    workspace: Path,
    evidence: dict[str, Any] | None = None,
) -> ComparativeReceipt:
    snapshot = workspace_snapshot(workspace)
    return ComparativeReceipt(
        run_id=f"{harness_id}-{uuid.uuid4().hex[:12]}",
        harness_id=harness_id,
        harness_version=harness_version,
        adapter=adapter,
        measurement_status=MeasurementStatus.NOT_COMPARABLE,
        comparable=False,
        blockers=blockers,
        model_id=CONTROLLED_MODEL_ID,
        case_id=COMPARATIVE_CASE_ID,
        reported_state="NOT_RUN",
        task_passed=False,
        claimed_complete=False,
        false_completion=False,
        timed_out=False,
        exit_code=None,
        duration_seconds=0.0,
        budget_passed=False,
        model_requests=0,
        tool_calls=0,
        initial_workspace_digest=snapshot["tree_digest"],
        final_workspace_digest=snapshot["tree_digest"],
        comparability_signature=manifest["comparability_signature"],
        evidence={"workspace": snapshot, **(evidence or {})},
        error=None,
    )


def workspace_snapshot(root: Path) -> dict[str, Any]:
    files: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        files.append({
            "path": path.relative_to(root).as_posix(),
            "digest": canonical_digest(path.read_bytes().hex()),
        })
    return {"file_count": len(files), "tree_digest": canonical_digest(files)}


def _acceptance_passed(workspace: Path) -> bool:
    path = workspace / ACCEPTANCE_PATH
    return path.is_file() and path.read_text(encoding="utf-8") == ACCEPTANCE_CONTENT


def _acceptance_digest(workspace: Path) -> str | None:
    path = workspace / ACCEPTANCE_PATH
    return canonical_digest(path.read_text(encoding="utf-8")) if path.is_file() else None


def _advertised_tools(requests: list[dict[str, Any]]) -> list[str]:
    return sorted({
        str(name)
        for request in requests
        for name in request.get("tool_names", [])
    })


def _requests_match_tool_surface(
    requests: list[dict[str, Any]],
    allowed_tools: list[str],
) -> bool:
    return bool(requests) and all(
        sorted({str(name) for name in request.get("tool_names", [])})
        == allowed_tools
        for request in requests
    )


def _write_pi_config(config: Path, base_url: str) -> None:
    value = {
        "providers": {
            "taiyi-benchmark": {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": "benchmark-local-only",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                    "supportsUsageInStreaming": True,
                    "maxTokensField": "max_tokens",
                },
                "models": [{
                    "id": CONTROLLED_MODEL_ID,
                    "reasoning": False,
                    "contextWindow": 16384,
                    "maxTokens": 1024,
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                }],
            }
        }
    }
    (config / "models.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (config / "settings.json").write_text(
        json.dumps({
            "enableInstallTelemetry": False,
            "defaultProjectTrust": "never",
        }, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_openclaw_config(path: Path, base_url: str) -> None:
    model_ref = f"taiyi-benchmark/{CONTROLLED_MODEL_ID}"
    value = {
        "agents": {
            "defaults": {
                "model": {"primary": model_ref},
                "models": {model_ref: {"alias": "TaiYi Controlled"}},
            }
        },
        "models": {
            "mode": "merge",
            "providers": {
                "taiyi-benchmark": {
                    "baseUrl": base_url,
                    "apiKey": "benchmark-local-only",
                    "api": "openai-completions",
                    "models": [{
                        "id": CONTROLLED_MODEL_ID,
                        "name": "TaiYi Controlled Model",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                        "contextWindow": 16384,
                        "maxTokens": 1024,
                    }],
                }
            },
        },
        "tools": {
            "profile": "coding",
            "allow": ["read", "write"],
            # OpenClaw treats `write` as implicitly compatible with
            # `apply_patch`; deny it explicitly so the model sees exactly the
            # two capabilities frozen in the comparative manifest.
            "deny": [
                "edit",
                "apply_patch",
                "exec",
                "process",
                "sessions_yield",
                "tool_call",
                "tool_describe",
                "tool_search",
            ],
            "toolSearch": False,
            "codeMode": False,
            "fs": {"workspaceOnly": True},
        },
    }
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pi_blockers(executable: Path) -> tuple[str, ...]:
    blockers: list[str] = []
    if not executable.is_file():
        blockers.append("Pi executable was not supplied or does not exist")
    if platform.system() != "Darwin" or shutil.which("sandbox-exec") is None:
        blockers.append("this baseline requires the macOS sandbox-exec isolation wrapper")
    if shutil.which("node") is None:
        blockers.append("Pi requires Node.js but no node executable is available")
    return tuple(blockers)


def _openclaw_blockers(executable: Path) -> tuple[str, ...]:
    blockers: list[str] = []
    if not executable.is_file():
        blockers.append("OpenClaw executable was not supplied or does not exist")
    if platform.system() != "Darwin" or shutil.which("sandbox-exec") is None:
        blockers.append("this baseline requires the macOS sandbox-exec isolation wrapper")
    if shutil.which("node") is None:
        blockers.append("OpenClaw requires Node.js but no node executable is available")
    if executable.is_file() and not _openclaw_agent_exec_available(executable):
        blockers.append(
            "installed OpenClaw release lacks the isolated agent exec batch interface"
        )
    return tuple(blockers)


def _macos_profile(*, run_root: Path, executable: Path, model_port: int) -> str:
    install_root = _node_package_root(executable)
    allowed = [run_root.resolve(), install_root.resolve()]
    node = shutil.which("node")
    if node:
        allowed.append(Path(node).resolve().parent)
    read_rules = " ".join(f"(subpath {json.dumps(str(path))})" for path in allowed)
    metadata_paths = {Path("/")}
    for path in allowed:
        metadata_paths.add(path)
        metadata_paths.update(path.parents)
    metadata_rules = " ".join(
        f"(literal {json.dumps(str(path))})"
        for path in sorted(metadata_paths, key=lambda item: str(item))
    )
    write_rule = f"(subpath {json.dumps(str(run_root.resolve()))})"
    return f"""(version 1)
(deny default)
(import "system.sb")
(allow file-read* (subpath "/usr/bin") (subpath "/bin") (subpath "/usr/lib") (subpath "/System/Library") (subpath "/Library") (subpath "/private/var/db/dyld") (subpath "/private/etc") (subpath "/etc") {read_rules})
(allow file-read-metadata {metadata_rules} (subpath "/tmp") (subpath "/private/tmp"))
(allow file-read* (regex #"^/private/var/select/"))
(allow file-write* {write_rule})
(allow process-fork)
(allow process-exec)
(deny network*)
(allow network-outbound (remote ip "localhost:{int(model_port)}"))
"""


def _node_package_root(executable: Path) -> Path:
    resolved = executable.resolve()
    for parent in resolved.parents:
        if parent.name == "node_modules":
            return parent.parent
    return resolved.parent


def _prove_profile(profile: str, run_root: Path) -> dict[str, Any]:
    allowed = run_root / ".isolation-allowed"
    denied = run_root.parent / f".taiyi-isolation-denied-{uuid.uuid4().hex}"
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(run_root / "home"),
        "TMPDIR": str(run_root / "tmp"),
    }
    node = shutil.which("node")
    if node is None:
        return {
            "passed": False,
            "workspace_write_allowed": False,
            "outside_write_denied": False,
        }
    node = str(Path(node).resolve())
    code = 'require("fs").writeFileSync(process.argv[1], "x")'
    allowed_run = subprocess.run(
        ["sandbox-exec", "-p", profile, "--", node, "-e", code, str(allowed)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
        env=env,
    )
    denied_run = subprocess.run(
        ["sandbox-exec", "-p", profile, "--", node, "-e", code, str(denied)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
        env=env,
    )
    allowed_exists = allowed.exists()
    denied_exists = denied.exists()
    allowed.unlink(missing_ok=True)
    denied.unlink(missing_ok=True)
    network_denied = _prove_unlisted_loopback_denied(profile, node, env)
    return {
        "passed": (
            allowed_run.returncode == 0
            and allowed_exists
            and denied_run.returncode != 0
            and not denied_exists
            and network_denied
        ),
        "workspace_write_allowed": allowed_run.returncode == 0 and allowed_exists,
        "outside_write_denied": denied_run.returncode != 0 and not denied_exists,
        "unlisted_loopback_denied": network_denied,
    }


def _prove_unlisted_loopback_denied(
    profile: str,
    node: str,
    env: dict[str, str],
) -> bool:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.2)
    port = int(listener.getsockname()[1])
    code = (
        'const net=require("net");'
        'const s=net.connect({host:"127.0.0.1",port:Number(process.argv[1])});'
        's.on("connect",()=>process.exit(0));'
        's.on("error",()=>process.exit(7));'
        'setTimeout(()=>process.exit(8),1500);'
    )
    try:
        result = subprocess.run(
            ["sandbox-exec", "-p", profile, "--", node, "-e", code, str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            env=env,
        )
        try:
            connection, _address = listener.accept()
        except TimeoutError:
            connected = False
        else:
            connected = True
            connection.close()
        return result.returncode != 0 and not connected
    finally:
        listener.close()


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    errors = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors += 1
            continue
        if isinstance(value, dict):
            events.append(value)
        else:
            errors += 1
    return events, errors


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid JSON response: {type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return None, "invalid JSON response: expected an object"
    return value, None


def _sanitize_log(
    path: Path,
    roots: tuple[Path, ...],
    *,
    replacements: dict[str, str] | None = None,
) -> None:
    text = _sanitize_text(
        path.read_text(encoding="utf-8", errors="replace"),
        roots,
        replacements=replacements,
    )
    path.write_text(text, encoding="utf-8")


def _sanitize_text(
    text: str,
    roots: tuple[Path, ...],
    *,
    replacements: dict[str, str] | None = None,
) -> str:
    for root in sorted({str(item.resolve()) for item in roots}, key=len, reverse=True):
        text = text.replace(root, "<ISOLATED_ROOT>")
    for source, replacement in (replacements or {}).items():
        text = text.replace(source, replacement)
    return text


def _openclaw_agent_exec_available(executable: str | Path) -> bool:
    try:
        result = subprocess.run(
            [executable, "agent", "exec", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "--state-dir" in result.stdout and "--isolated" in result.stdout


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
    lines = result.stdout.strip().splitlines()
    return lines[0][:300] if lines else None


__all__ = [
    "ACCEPTANCE_CONTENT",
    "ACCEPTANCE_PATH",
    "COMPARATIVE_CASE_ID",
    "COMPARATIVE_PROMPT",
    "COMPARATIVE_SCOPE",
    "blocked_zcode_receipt",
    "comparative_manifest",
    "run_openclaw_cell",
    "run_pi_cell",
    "run_taiyi_cell",
    "workspace_snapshot",
]
