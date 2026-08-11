"""Fault-oriented tests for durable, reattachable tool processes."""
from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

import pytest

from taiyi.gateway import build_gateway
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import FailureKind, JobStatus, RunPhase, TaskState
from taiyi.scheduler import PlanStep
from taiyi.tools import SandboxExecutor


def _executor(
    tmp_path: Path,
    *,
    hard_timeout: float = 2.0,
    idle_timeout: float | None = None,
    output_limit: int = 16_384,
) -> SandboxExecutor:
    return SandboxExecutor(
        tmp_path / "sandbox",
        job_dir=tmp_path / "jobs",
        hard_timeout=hard_timeout,
        idle_timeout=idle_timeout,
        heartbeat_interval=0.05,
        output_limit=output_limit,
    )


def _python(code: str) -> PlanStep:
    return PlanStep(f"shell:{shlex.quote(sys.executable)}", ["-c", code])


def _events(base_dir: Path, task_id: str) -> list[dict]:
    path = base_dir / "runs" / task_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_long_command_runs_under_durable_supervisor(tmp_path):
    executor = _executor(tmp_path)

    result = executor.execute(
        _python("import time; time.sleep(0.2); print('finished')")
    )

    assert result.ok
    assert result.job_id
    assert result.operation_id
    assert result.exit_code == 0
    assert result.duration_seconds is not None and result.duration_seconds >= 0.15
    assert "finished" in result.output


def test_idle_timeout_is_distinct_from_hard_timeout(tmp_path):
    executor = _executor(tmp_path, hard_timeout=2, idle_timeout=0.2)

    result = executor.execute(_python("import time; time.sleep(2)"))

    assert not result.ok
    assert result.timeout_kind == "idle"
    assert result.failure_kind == FailureKind.TOOL_IDLE_TIMEOUT.value


def test_hard_timeout_wins_even_while_command_is_producing_output(tmp_path):
    executor = _executor(tmp_path, hard_timeout=0.3, idle_timeout=1)
    code = (
        "import time\n"
        "for i in range(50):\n"
        " print(i, flush=True)\n"
        " time.sleep(0.05)\n"
    )

    result = executor.execute(_python(code))

    assert not result.ok
    assert result.timeout_kind == "hard"
    assert result.failure_kind == FailureKind.TOOL_HARD_TIMEOUT.value
    assert result.output


def test_exit_code_and_stderr_artifact_are_preserved(tmp_path):
    executor = _executor(tmp_path)

    result = executor.execute(
        _python("import sys; print('bad', file=sys.stderr); raise SystemExit(7)")
    )

    assert not result.ok
    assert result.exit_code == 7
    assert result.signal is None
    assert result.failure_kind == FailureKind.TOOL_EXIT_NONZERO.value
    assert "bad" in result.output
    assert Path(result.stderr_artifact).read_text(encoding="utf-8").strip() == "bad"


def test_process_startup_failure_is_typed(tmp_path):
    executor = _executor(tmp_path)

    result = executor.execute(PlanStep("shell:/definitely/not/a/taiyi-command", []))

    assert not result.ok
    assert result.exit_code is None
    assert result.failure_kind == FailureKind.TOOL_STARTUP_ERROR.value
    assert "FileNotFoundError" in result.error


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_signal_termination_is_not_flattened_into_an_exit_code(tmp_path):
    executor = _executor(tmp_path)

    result = executor.execute(
        _python("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
    )

    assert not result.ok
    assert result.exit_code == -15
    assert result.signal == 15
    assert result.failure_kind == FailureKind.TOOL_SIGNAL.value


def test_large_output_is_bounded_for_model_but_preserved_as_artifact(tmp_path):
    executor = _executor(tmp_path, output_limit=1024)

    result = executor.execute(_python("import sys; sys.stdout.write('x' * 100000)"))

    assert result.ok
    assert result.output_truncated
    assert len(result.output.encode("utf-8")) <= 1024
    assert Path(result.stdout_artifact).stat().st_size == 100000


def test_new_executor_instance_reattaches_to_running_job(tmp_path):
    first = _executor(tmp_path)
    step = _python("import time; time.sleep(0.25); print('reattached')")
    handle = first.start(step, operation_id="task:round:1:step:0")

    second = _executor(tmp_path)
    duplicate = second.start(step, operation_id="task:round:1:step:0")
    result = second.wait(duplicate.job_id)

    assert duplicate.job_id == handle.job_id
    assert result.ok
    assert "reattached" in result.output


def test_worker_heartbeat_repairs_crash_window_before_parent_saved_pid(tmp_path):
    first = _executor(tmp_path)
    step = _python("import time; time.sleep(0.3); print('recovered')")
    handle = first.start(step, operation_id="crash-window")
    job_dir = tmp_path / "jobs" / handle.job_id
    heartbeat = job_dir / "heartbeat.json"
    deadline = time.monotonic() + 2
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert heartbeat.exists()

    job_path = job_dir / "job.json"
    persisted = json.loads(job_path.read_text(encoding="utf-8"))
    persisted["status"] = JobStatus.PENDING.value
    persisted["worker_pid"] = None
    persisted["worker_process_token"] = None
    job_path.write_text(json.dumps(persisted), encoding="utf-8")

    second = _executor(tmp_path)
    result = second.wait(handle.job_id)

    assert result.ok
    assert "recovered" in result.output


def test_duplicate_operation_never_replays_side_effect(tmp_path):
    executor = _executor(tmp_path)
    marker = tmp_path / "sandbox" / "marker.txt"
    step = _python(
        "from pathlib import Path; import time; "
        "p=Path('marker.txt'); "
        "p.write_text(p.read_text() + 'x' if p.exists() else 'x'); "
        "time.sleep(0.2)"
    )

    first = executor.start(step, operation_id="stable-operation")
    second = executor.start(step, operation_id="stable-operation")
    executor.wait(second.job_id)

    assert first.job_id == second.job_id
    assert marker.read_text(encoding="utf-8") == "x"


def test_operation_id_cannot_be_rebound_to_different_work(tmp_path):
    executor = _executor(tmp_path)
    executor.start(_python("print('one')"), operation_id="collision")

    with pytest.raises(RuntimeError, match="different work"):
        executor.start(_python("print('two')"), operation_id="collision")


def test_running_job_can_be_cancelled(tmp_path):
    executor = _executor(tmp_path, hard_timeout=10)
    handle = executor.start(
        _python("import time; time.sleep(10)"), operation_id="cancel-me"
    )

    record = executor.cancel(handle.job_id)

    assert record.status is JobStatus.CANCELLED
    assert record.failure_kind == FailureKind.TOOL_CANCELLED.value


def test_background_descendant_cannot_escape_the_job_boundary(tmp_path):
    executor = _executor(tmp_path, hard_timeout=5)
    code = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "
        "print('parent exited')"
    )

    result = executor.execute(_python(code))

    assert not result.ok
    assert result.failure_kind == FailureKind.TOOL_LOST.value
    assert "descendants remained" in result.error
    assert "parent exited" in result.output


@pytest.mark.parametrize("runtime_mode", ["workflow", "agent"])
def test_runtime_records_job_attachment_and_typed_result(tmp_path, runtime_mode):
    base = tmp_path / runtime_mode
    executor = _executor(base)
    provider = ScriptedProvider(
        [LLMResponse(tool_calls=[ToolCall("shell:echo", ["durable"])])]
        if runtime_mode == "workflow"
        else [
            LLMResponse(tool_calls=[ToolCall("shell:echo", ["durable"])]),
            LLMResponse(text="done"),
        ]
    )
    gateway = build_gateway(
        base_dir=base,
        mode=runtime_mode,
        executor=executor,
        provider=provider,
        validator=False,
    )

    ctx = gateway.submit("echo durable", scenario="default")

    assert ctx.state is TaskState.COMPLETED
    events = _events(base, ctx.task_id)
    attached = [event for event in events if event["event"] == "job_attached"]
    finished = [event for event in events if event["event"] == "tool_finished"]
    assert attached and attached[-1]["phase"] == RunPhase.TOOL_RUNNING.value
    assert attached[-1]["payload"]["job_id"].startswith("j_")
    assert finished[-1]["payload"]["job_id"] == attached[-1]["payload"]["job_id"]
    assert finished[-1]["payload"]["exit_code"] == 0
    assert finished[-1]["payload"]["stdout_artifact"]
