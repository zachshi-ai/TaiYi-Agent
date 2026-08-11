"""Crash/restart tests across the runtime checkpoint and durable job boundary."""
from __future__ import annotations

import json
import time

import pytest

from taiyi.gateway import build_gateway
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import RunPhase, TaskState
from taiyi.tools import SandboxExecutor


class CrashAfterJobStart:
    """Model a gateway process dying after attach but before observing completion."""

    environment = "workspace"

    def __init__(self, inner: SandboxExecutor):
        self.inner = inner
        self.last_job_id: str | None = None
        self.last_operation_id: str | None = None

    def execute(self, step):
        return self.inner.execute(step)

    def supports_jobs(self, step):
        return self.inner.supports_jobs(step)

    def start(self, step, *, operation_id):
        handle = self.inner.start(step, operation_id=operation_id)
        self.last_job_id = handle.job_id
        self.last_operation_id = handle.operation_id
        return handle

    def find(self, operation_id):
        return self.inner.find(operation_id)

    def cancel(self, job_id):
        return self.inner.cancel(job_id)

    def wait(self, job_id):
        raise SystemExit("fault injection: gateway exited after durable job attach")


class CrashDuringLLMRequest:
    name = "fault:llm-process-exit"

    def complete(self, messages, *, tools=None):
        raise SystemExit("fault injection: gateway exited while awaiting the model")


class RecordingFinalProvider:
    name = "offline:recovered-final"

    def __init__(self):
        self.messages = None

    def complete(self, messages, *, tools=None):
        self.messages = list(messages)
        return LLMResponse(text="recovered model turn completed", model=self.name)


def _events(base, task_id):
    path = base / "runs" / task_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _wait_for_settled(base, task_id, timeout=8.0):
    path = base / "runs" / task_id / "checkpoint.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if checkpoint["context"]["phase"] == RunPhase.SETTLED.value:
            return checkpoint
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not settle after recovery")


@pytest.mark.parametrize("runtime_mode", ["workflow", "agent"])
def test_gateway_restart_reattaches_job_and_executes_side_effect_once(tmp_path, runtime_mode):
    base = tmp_path / runtime_mode
    workspace = base / "workspace"
    jobs = base / "jobs"
    marker = workspace / "marker.txt"
    inner = SandboxExecutor(workspace, job_dir=jobs, heartbeat_interval=0.05)
    crashing = CrashAfterJobStart(inner)
    code = (
        "import pathlib,time; time.sleep(0.2); "
        "p=pathlib.Path('marker.txt'); "
        "p.write_text((p.read_text() if p.exists() else '')+'once\\n')"
    )
    first_provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:python3", ["-c", code])])
    ])
    first = build_gateway(
        base_dir=base,
        mode=runtime_mode,
        executor=crashing,
        provider=first_provider,
        validator=False,
    )

    with pytest.raises(SystemExit, match="fault injection"):
        first.submit("write the marker exactly once", scenario="default")

    assert crashing.last_operation_id is not None
    task_id = crashing.last_operation_id.split(":round:", 1)[0]
    interrupted = json.loads(
        (base / "runs" / task_id / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert interrupted["context"]["phase"] == RunPhase.TOOL_RUNNING.value
    assert interrupted["continuation"]["job_id"] == crashing.last_job_id

    # A concurrently starting gateway must not advance the same task while the
    # original process still owns its lease.
    blocked_provider = ScriptedProvider([LLMResponse(text="should not be consumed")])
    blocked = build_gateway(
        base_dir=base,
        mode=runtime_mode,
        executor=SandboxExecutor(workspace, job_dir=jobs, heartbeat_interval=0.05),
        provider=blocked_provider,
        validator=False,
    )
    assert task_id not in blocked.runtime._recovery_threads

    # In production the OS releases this flock when the process exits. The fault
    # is injected in-process, so release it explicitly before constructing the
    # replacement gateway.
    first.runtime.run_store.release_task_lease(task_id)
    resumed_provider = ScriptedProvider(
        [LLMResponse(text="marker delivery verified")] if runtime_mode == "agent" else []
    )
    restarted = build_gateway(
        base_dir=base,
        mode=runtime_mode,
        executor=SandboxExecutor(workspace, job_dir=jobs, heartbeat_interval=0.05),
        provider=resumed_provider,
        validator=False,
    )
    assert restarted.runtime.recover_pending() == 0

    settled = _wait_for_settled(base, task_id)
    assert settled["context"]["state"] == TaskState.COMPLETED.value
    assert settled["context"]["attempt_id"] == 2
    assert marker.read_text(encoding="utf-8").splitlines() == ["once"]
    events = _events(base, task_id)
    assert sum(event["event"] == "tool_started" for event in events) == 1
    assert sum(event["event"] == "job_reattached" for event in events) == 1
    assert sum(event["event"] == "run_recovered" for event in events) == 1


def test_agent_restart_retries_inflight_llm_turn_from_frozen_messages(tmp_path):
    first = build_gateway(
        base_dir=tmp_path,
        mode="agent",
        provider=CrashDuringLLMRequest(),
        validator=False,
    )

    with pytest.raises(SystemExit, match="awaiting the model"):
        first.submit("preserve this exact user request", scenario="default")

    checkpoint_path = next((tmp_path / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    assert interrupted["context"]["phase"] == RunPhase.LLM_WAITING.value
    assert interrupted["continuation"]["kind"] == "agent_continue"
    first.runtime.run_store.release_task_lease(task_id)

    provider = RecordingFinalProvider()
    restarted = build_gateway(
        base_dir=tmp_path,
        mode="agent",
        provider=provider,
        validator=False,
    )
    settled = _wait_for_settled(tmp_path, task_id)

    assert settled["context"]["state"] == TaskState.COMPLETED.value
    assert settled["context"]["attempt_id"] == 2
    assert settled["context"]["final_output"] == "recovered model turn completed"
    assert provider.messages is not None
    assert provider.messages[-1].role == "user"
    assert provider.messages[-1].content == "preserve this exact user request"
    assert task_id not in restarted.runtime._recovery_threads
