"""Production tool-process fault matrix shared by all operating modes.

This is deliberately a TaiYi kernel benchmark, not a cross-harness ranking.  A
foreign harness is comparable only after it exposes the same controlled process
tree, signal, output, restart, and receipt boundary; CLI error strings are not a
substitute for that interface.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from taiyi.benchmark.schema import (
    TOOL_FAULT_MANIFEST_SCHEMA,
    TOOL_FAULT_RECEIPT_SCHEMA,
    TOOL_FAULT_REPORT_SCHEMA,
    canonical_digest,
    write_artifact,
)
from taiyi.gateway import build_gateway
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import FailureKind, RunPhase, TaskState
from taiyi.tools import SandboxExecutor

OPERATING_MODES = ("quality", "balanced", "efficiency")
TOOL_FAULT_SCOPE = "taiyi_production_tool_process_reliability"
ARTIFACT_LIMIT = 4096
MODEL_OUTPUT_LIMIT = 1024

TOOL_FAULT_CASES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "hard_timeout_sigterm_resistant_tree",
        "description": "A parent and descendant ignore SIGTERM until the deadline escalates.",
        "expected_state": TaskState.NEEDS_INPUT.value,
        "expected_failure_kind": FailureKind.EFFECT_OUTCOME_UNKNOWN.value,
        "expected_executor_failure_kind": FailureKind.TOOL_HARD_TIMEOUT.value,
    },
    {
        "case_id": "idle_timeout_silent_process",
        "description": "A silent tool exceeds its no-output deadline before its hard deadline.",
        "expected_state": TaskState.NEEDS_INPUT.value,
        "expected_failure_kind": FailureKind.EFFECT_OUTCOME_UNKNOWN.value,
        "expected_executor_failure_kind": FailureKind.TOOL_IDLE_TIMEOUT.value,
    },
    {
        "case_id": "stdout_stderr_flood",
        "description": "Both streams exceed artifact and model budgets while the command succeeds.",
        "expected_state": TaskState.COMPLETED.value,
        "expected_failure_kind": None,
        "expected_executor_failure_kind": None,
    },
    {
        "case_id": "lingering_descendant",
        "description": "A descendant survives its parent and keeps the owned output pipes open.",
        "expected_state": TaskState.NEEDS_INPUT.value,
        "expected_failure_kind": FailureKind.EFFECT_OUTCOME_UNKNOWN.value,
        "expected_executor_failure_kind": FailureKind.TOOL_LOST.value,
    },
    {
        "case_id": "gateway_restart_reattach_once",
        "description": "The gateway exits after attach; restart reattaches without replaying the effect.",
        "expected_state": TaskState.COMPLETED.value,
        "expected_failure_kind": None,
        "expected_executor_failure_kind": None,
    },
)


class _CrashAfterAttach:
    """Inject gateway loss after the durable operation is frozen and started."""

    environment = "workspace"

    def __init__(self, inner: SandboxExecutor):
        self.inner = inner
        self.last_operation_id: str | None = None

    def execute(self, step):
        return self.inner.execute(step)

    def supports_jobs(self, step):
        return self.inner.supports_jobs(step)

    def start(self, step, *, operation_id):
        handle = self.inner.start(step, operation_id=operation_id)
        self.last_operation_id = handle.operation_id
        return handle

    def find(self, operation_id):
        return self.inner.find(operation_id)

    def cancel(self, job_id):
        return self.inner.cancel(job_id)

    def wait(self, job_id):
        raise SystemExit("benchmark fault: gateway exited after durable job attach")


def tool_fault_manifest() -> dict[str, Any]:
    cases = [dict(case) for case in TOOL_FAULT_CASES]
    contract = {
        "measurement_scope": TOOL_FAULT_SCOPE,
        "operating_modes": list(OPERATING_MODES),
        "artifact_limit_per_stream": ARTIFACT_LIMIT,
        "model_output_limit": MODEL_OUTPUT_LIMIT,
        "cases": cases,
        "reliability_floor": [
            "phase-correct failure attribution",
            "bounded model and disk output",
            "full-stream byte count and digest",
            "bounded TERM-to-KILL escalation",
            "durable reattachment without duplicate effect",
            "no false completion",
        ],
    }
    return {**contract, "contract_digest": canonical_digest(contract)}


def run_tool_fault_matrix(output_dir: str | Path) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = tool_fault_manifest()
    write_artifact(
        destination / "manifest.json",
        TOOL_FAULT_MANIFEST_SCHEMA,
        manifest,
    )
    receipts: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="taiyi-tool-faults-") as temporary:
        scratch = Path(temporary)
        for case in TOOL_FAULT_CASES:
            for mode in OPERATING_MODES:
                receipt = _run_case(case, mode, scratch / str(case["case_id"]) / mode)
                receipts.append(receipt)
                write_artifact(
                    destination / "runs" / f"{case['case_id']}--{mode}.json",
                    TOOL_FAULT_RECEIPT_SCHEMA,
                    receipt,
                )

    passed = [item for item in receipts if item["protocol_passed"]]
    report = {
        "measurement_scope": TOOL_FAULT_SCOPE,
        "generated_at": time.time(),
        "manifest_digest": manifest["contract_digest"],
        "case_count": len(TOOL_FAULT_CASES),
        "run_count": len(receipts),
        "protocol_passed_count": len(passed),
        "all_protocol_passed": len(passed) == len(receipts),
        "false_completions": sum(bool(item["false_completion"]) for item in receipts),
        "duplicate_effects": sum(int(item["duplicate_effects"]) for item in receipts),
        "by_mode": {
            mode: {
                "runs": sum(item["operating_mode"] == mode for item in receipts),
                "passed": sum(
                    item["operating_mode"] == mode and item["protocol_passed"]
                    for item in receipts
                ),
            }
            for mode in OPERATING_MODES
        },
        "runs": receipts,
        "external_comparison": {
            "status": "NOT_COMPARABLE",
            "reason": (
                "Pi, OpenClaw, and ZCode do not yet expose one frozen controlled-tool "
                "interface proving identical process-tree, signal, output, and restart semantics."
            ),
        },
        "ranking_eligible": False,
    }
    write_artifact(destination / "report.json", TOOL_FAULT_REPORT_SCHEMA, report)
    (destination / "REPORT.md").write_text(_render_report(report), encoding="utf-8")
    return report


def _run_case(case: dict[str, Any], mode: str, root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    error = None
    try:
        if case["case_id"] == "gateway_restart_reattach_once":
            observed = _run_restart_case(root, mode)
        else:
            observed = _run_process_case(root, mode, str(case["case_id"]))
    except Exception as exc:  # every ordinary cell failure must emit a signed receipt
        observed = {
            "reported_state": "HARNESS_ERROR",
            "failure_kind": FailureKind.INTERNAL.value,
            "claimed_complete": False,
            "case_evidence_passed": False,
            "duplicate_effects": 0,
            "evidence": {},
        }
        error = f"{type(exc).__name__}: {exc}"
    duration = max(0.0, time.monotonic() - started)
    expected_state = str(case["expected_state"])
    expected_failure = case["expected_failure_kind"]
    protocol_passed = (
        observed["reported_state"] == expected_state
        and observed["failure_kind"] == expected_failure
        and bool(observed["case_evidence_passed"])
        and int(observed["duplicate_effects"]) == 0
        and not bool(observed.get("false_completion", False))
        and error is None
    )
    return {
        "run_id": f"taiyi-{case['case_id']}-{mode}-{uuid.uuid4().hex[:12]}",
        "case_id": case["case_id"],
        "operating_mode": mode,
        "measurement_status": "MEASURED" if error is None else "ERROR",
        "expected_state": expected_state,
        "reported_state": observed["reported_state"],
        "expected_failure_kind": expected_failure,
        "expected_executor_failure_kind": case.get("expected_executor_failure_kind"),
        "failure_kind": observed["failure_kind"],
        "claimed_complete": observed["claimed_complete"],
        "false_completion": bool(observed.get("false_completion", False)),
        "case_evidence_passed": bool(observed["case_evidence_passed"]),
        "protocol_passed": protocol_passed,
        "duplicate_effects": int(observed["duplicate_effects"]),
        "duration_seconds": duration,
        "manifest_case_digest": canonical_digest(case),
        "evidence": observed["evidence"],
        "error": error,
    }


def _run_process_case(root: Path, mode: str, case_id: str) -> dict[str, Any]:
    workspace = root / "workspace"
    executor = _executor(root, case_id)
    tool_call, expected = _process_tool_call(case_id)
    responses = [LLMResponse(tool_calls=[tool_call])]
    if case_id == "stdout_stderr_flood":
        responses.append(LLMResponse(text="bounded output receipt observed"))
    gateway = build_gateway(
        base_dir=root / "state",
        mode="agent",
        operating_mode=mode,
        executor=executor,
        provider=ScriptedProvider(responses),
        validator=False,
    )
    ctx = gateway.submit(
        "Run the frozen controlled tool fault case.", operating_mode=mode
    )
    events = gateway.runtime.run_store.read_events(ctx.task_id)
    terminal = next(
        (
            event["payload"]
            for event in reversed(events)
            if event["event"] in {"tool_finished", "tool_recovered"}
        ),
        {},
    )
    step = ctx.step_results[0] if ctx.step_results else None
    evidence_passed = _evaluate_process_case(case_id, ctx, step, terminal, expected)
    claimed_complete = ctx.state in {TaskState.COMPLETED, TaskState.SIMULATED}
    false_completion = claimed_complete and not evidence_passed
    evidence = {
        "event_count": len(events),
        "event_types": [event["event"] for event in events],
        "executor_failure_kind": (
            step.original_failure_kind
            if step is not None
            else terminal.get("failure_kind")
        ),
        "timeout_kind": step.timeout_kind
        if step is not None
        else terminal.get("timeout_kind"),
        "signal": step.signal if step is not None else terminal.get("signal"),
        "termination_reason": (
            step.termination_reason
            if step is not None
            else terminal.get("termination_reason")
        ),
        "termination_escalated": (
            step.termination_escalated
            if step is not None
            else terminal.get("termination_escalated", False)
        ),
        "owned_process_group_settled": (
            step.owned_process_group_settled
            if step is not None
            else terminal.get("owned_process_group_settled")
        ),
        "stdout_bytes": step.stdout_bytes
        if step is not None
        else terminal.get("stdout_bytes", 0),
        "stderr_bytes": step.stderr_bytes
        if step is not None
        else terminal.get("stderr_bytes", 0),
        "stdout_artifact_bytes": (
            step.stdout_artifact_bytes
            if step is not None
            else terminal.get("stdout_artifact_bytes", 0)
        ),
        "stderr_artifact_bytes": (
            step.stderr_artifact_bytes
            if step is not None
            else terminal.get("stderr_artifact_bytes", 0)
        ),
        "stdout_digest": step.stdout_digest
        if step is not None
        else terminal.get("stdout_digest"),
        "stderr_digest": step.stderr_digest
        if step is not None
        else terminal.get("stderr_digest"),
        "stdout_artifact_truncated": (
            step.stdout_artifact_truncated
            if step is not None
            else terminal.get("stdout_artifact_truncated", False)
        ),
        "stderr_artifact_truncated": (
            step.stderr_artifact_truncated
            if step is not None
            else terminal.get("stderr_artifact_truncated", False)
        ),
        "model_output_truncated": (
            step.output_truncated
            if step is not None
            else terminal.get("output_truncated", False)
        ),
        "workspace_file_count": sum(
            1 for item in workspace.rglob("*") if item.is_file()
        ),
    }
    return {
        "reported_state": ctx.state.value,
        "failure_kind": ctx.failure_kind,
        "claimed_complete": claimed_complete,
        "false_completion": false_completion,
        "case_evidence_passed": evidence_passed,
        "duplicate_effects": 0,
        "evidence": evidence,
    }


def _executor(root: Path, case_id: str) -> SandboxExecutor:
    hard_timeout = 0.3 if case_id == "hard_timeout_sigterm_resistant_tree" else 3.0
    idle_timeout = 0.25 if case_id == "idle_timeout_silent_process" else 2.0
    return SandboxExecutor(
        root / "workspace",
        job_dir=root / "jobs",
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
        heartbeat_interval=0.05,
        output_limit=MODEL_OUTPUT_LIMIT,
        artifact_limit=ARTIFACT_LIMIT,
    )


def _process_tool_call(case_id: str) -> tuple[ToolCall, dict[str, Any]]:
    expected: dict[str, Any] = {}
    if case_id == "hard_timeout_sigterm_resistant_tree":
        child = (
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(10)"
        )
        code = (
            "import signal,subprocess,sys,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "print('tree-ready', flush=True); time.sleep(10)"
        )
    elif case_id == "idle_timeout_silent_process":
        code = "import time; time.sleep(10)"
    elif case_id == "stdout_stderr_flood":
        stdout = b"HEAD" + (b"x" * 1_000_000) + b"TAIL"
        stderr = b"ERR-HEAD" + (b"y" * 750_000) + b"ERR-TAIL"
        expected = {
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
            "stdout_digest": "sha256:" + hashlib.sha256(stdout).hexdigest(),
            "stderr_digest": "sha256:" + hashlib.sha256(stderr).hexdigest(),
        }
        code = (
            "import sys; "
            "sys.stdout.buffer.write(b'HEAD'+b'x'*1000000+b'TAIL'); "
            "sys.stdout.buffer.flush(); "
            "sys.stderr.buffer.write(b'ERR-HEAD'+b'y'*750000+b'ERR-TAIL'); "
            "sys.stderr.buffer.flush()"
        )
    elif case_id == "lingering_descendant":
        code = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "
            "print('parent-exited', flush=True)"
        )
    else:  # pragma: no cover - manifest and dispatch are frozen together
        raise ValueError(f"unknown tool fault case: {case_id}")
    return ToolCall(f"shell:{shlex.quote(sys.executable)}", ["-c", code]), expected


def _evaluate_process_case(case_id, ctx, step, terminal, expected) -> bool:
    if step is None:
        return False
    if case_id == "hard_timeout_sigterm_resistant_tree":
        return (
            ctx.state is TaskState.NEEDS_INPUT
            and ctx.failure_kind == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
            and step.original_failure_kind == FailureKind.TOOL_HARD_TIMEOUT.value
            and step.timeout_kind == "hard"
            and step.termination_reason == "hard_timeout"
            and step.termination_escalated is True
            and step.owned_process_group_settled is True
            and step.signal == 9
        )
    if case_id == "idle_timeout_silent_process":
        return (
            ctx.state is TaskState.NEEDS_INPUT
            and ctx.failure_kind == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
            and step.original_failure_kind == FailureKind.TOOL_IDLE_TIMEOUT.value
            and step.timeout_kind == "idle"
            and step.termination_reason == "idle_timeout"
            and step.owned_process_group_settled is True
        )
    if case_id == "lingering_descendant":
        return (
            ctx.state is TaskState.NEEDS_INPUT
            and ctx.failure_kind == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
            and step.original_failure_kind == FailureKind.TOOL_LOST.value
            and step.termination_reason == "lingering_descendants"
            and step.owned_process_group_settled is True
        )
    stdout_path = Path(str(step.stdout_artifact))
    stderr_path = Path(str(step.stderr_artifact))
    return (
        ctx.state is TaskState.COMPLETED
        and step.stdout_artifact_truncated
        and step.stderr_artifact_truncated
        and step.output_truncated
        and step.stdout_bytes == expected["stdout_bytes"]
        and step.stderr_bytes == expected["stderr_bytes"]
        and step.stdout_digest == expected["stdout_digest"]
        and step.stderr_digest == expected["stderr_digest"]
        and stdout_path.stat().st_size <= ARTIFACT_LIMIT
        and stderr_path.stat().st_size <= ARTIFACT_LIMIT
        and b"TAIYI OUTPUT TRUNCATED" in stdout_path.read_bytes()
        and b"TAIYI OUTPUT TRUNCATED" in stderr_path.read_bytes()
    )


def _run_restart_case(root: Path, mode: str) -> dict[str, Any]:
    workspace = root / "workspace"
    jobs = root / "jobs"
    state = root / "state"
    inner = SandboxExecutor(
        workspace,
        job_dir=jobs,
        heartbeat_interval=0.05,
        output_limit=MODEL_OUTPUT_LIMIT,
        artifact_limit=ARTIFACT_LIMIT,
    )
    crashing = _CrashAfterAttach(inner)
    code = (
        "import pathlib,time; time.sleep(0.2); p=pathlib.Path('marker.txt'); "
        "p.write_text((p.read_text() if p.exists() else '')+'once\\n')"
    )
    first = build_gateway(
        base_dir=state,
        mode="agent",
        operating_mode=mode,
        executor=crashing,
        provider=ScriptedProvider(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(f"shell:{shlex.quote(sys.executable)}", ["-c", code])
                    ]
                )
            ]
        ),
        validator=False,
    )
    try:
        first.submit("Write the marker exactly once.", operating_mode=mode)
    except SystemExit as exc:
        if "benchmark fault" not in str(exc):
            raise
    if crashing.last_operation_id is None:
        raise RuntimeError("restart case never attached a durable operation")
    task_id = crashing.last_operation_id.split(":round:", 1)[0]
    first.runtime.run_store.release_task_lease(task_id)

    restarted = build_gateway(
        base_dir=state,
        mode="agent",
        operating_mode=mode,
        executor=SandboxExecutor(
            workspace,
            job_dir=jobs,
            heartbeat_interval=0.05,
            output_limit=MODEL_OUTPUT_LIMIT,
            artifact_limit=ARTIFACT_LIMIT,
        ),
        provider=ScriptedProvider([LLMResponse(text="reattached delivery verified")]),
        validator=False,
    )
    checkpoint = _wait_for_settled(state, task_id)
    thread = restarted.runtime._recovery_threads.get(task_id)
    if thread is not None:
        thread.join(timeout=2.0)
    events = restarted.runtime.run_store.read_events(task_id)
    marker = workspace / "marker.txt"
    marker_lines = (
        marker.read_text(encoding="utf-8").splitlines() if marker.is_file() else []
    )
    event_types = [event["event"] for event in events]
    duplicate_effects = max(0, marker_lines.count("once") - 1)
    evidence_passed = (
        checkpoint["context"]["phase"] == RunPhase.SETTLED.value
        and checkpoint["context"]["state"] == TaskState.COMPLETED.value
        and marker_lines == ["once"]
        and event_types.count("tool_started") == 1
        and event_types.count("job_reattached") == 1
        and event_types.count("run_recovered") == 1
    )
    return {
        "reported_state": checkpoint["context"]["state"],
        "failure_kind": checkpoint["context"].get("failure_kind"),
        "claimed_complete": checkpoint["context"]["state"] == TaskState.COMPLETED.value,
        "false_completion": (
            checkpoint["context"]["state"] == TaskState.COMPLETED.value
            and not evidence_passed
        ),
        "case_evidence_passed": evidence_passed,
        "duplicate_effects": duplicate_effects,
        "evidence": {
            "attempt_id": checkpoint["context"]["attempt_id"],
            "marker_line_count": len(marker_lines),
            "tool_started_count": event_types.count("tool_started"),
            "job_reattached_count": event_types.count("job_reattached"),
            "run_recovered_count": event_types.count("run_recovered"),
            "settled": checkpoint["context"]["phase"] == RunPhase.SETTLED.value,
        },
    }


def _wait_for_settled(
    state: Path, task_id: str, timeout: float = 8.0
) -> dict[str, Any]:
    path = state / "runs" / task_id / "checkpoint.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if checkpoint["context"]["phase"] == RunPhase.SETTLED.value:
            return checkpoint
        time.sleep(0.02)
    raise TimeoutError(f"restart benchmark did not settle task {task_id}")


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        "# TaiYi Production Tool-Process Fault Matrix",
        "",
        f"- Cases: {report['case_count']}",
        f"- Runs: {report['run_count']}",
        f"- Protocol passed: {report['protocol_passed_count']} / {report['run_count']}",
        f"- False completions: {report['false_completions']}",
        f"- Duplicate effects: {report['duplicate_effects']}",
        f"- Ranking eligible: {'YES' if report['ranking_eligible'] else 'NO'}",
        "",
        "| Case | Mode | State | Failure | Evidence | Protocol | Duration (s) |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for run in report["runs"]:
        lines.append(
            f"| {run['case_id']} | {run['operating_mode']} | {run['reported_state']} | "
            f"{run['failure_kind'] or '-'} | "
            f"{'yes' if run['case_evidence_passed'] else 'no'} | "
            f"{'yes' if run['protocol_passed'] else 'no'} | "
            f"{run['duration_seconds']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## External comparison boundary",
            "",
            f"- Status: `{report['external_comparison']['status']}`",
            f"- {report['external_comparison']['reason']}",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "TOOL_FAULT_CASES",
    "TOOL_FAULT_SCOPE",
    "run_tool_fault_matrix",
    "tool_fault_manifest",
]
