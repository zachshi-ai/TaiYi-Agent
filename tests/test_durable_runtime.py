"""Fault-oriented tests for the durable run protocol."""
from __future__ import annotations

import json

import pytest

from taiyi.gateway import build_gateway
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import FailureKind, RunPhase, TaskState
from taiyi.runtime.persistence import checkpoint_digest


class TimeoutExecutor:
    environment = "fault-injection"

    def execute(self, step):
        raise TimeoutError(f"tool stalled: {step.tool}")


class TimeoutProvider:
    name = "timeout-provider"

    def complete(self, messages, *, tools=None):
        raise TimeoutError("first token never arrived")


def _events(base_dir, task_id):
    path = base_dir / "runs" / task_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_workflow_approval_survives_process_restart(tmp_path):
    first = build_gateway(base_dir=tmp_path, mode="workflow")
    held = first.submit("帮我生成上周周报", scenario="ops.report")

    assert held.state is TaskState.NEEDS_REVIEW
    assert held.phase is RunPhase.WAITING_APPROVAL
    assert not held.settled
    assert [result.step.tool for result in held.executed_steps] == ["sql:query"]
    approval_id = held.approval_id

    # Constructing a new gateway simulates a new process with no live Python
    # objects. It must rebuild the approval and execution history from disk.
    second = build_gateway(base_dir=tmp_path, mode="workflow")
    recovered = second.approvals.get(approval_id)
    assert recovered is not None
    assert recovered.ctx.task_id == held.task_id
    assert recovered.ctx.attempt_id == 2
    assert [result.step.tool for result in recovered.ctx.executed_steps] == ["sql:query"]

    resumed = second.resume(approval_id, approve=True)
    assert resumed.state is TaskState.SIMULATED
    assert resumed.phase is RunPhase.SETTLED
    assert resumed.settled
    assert [result.step.tool for result in resumed.executed_steps] == [
        "sql:query",
        "notify:feishu",
    ]

    third = build_gateway(base_dir=tmp_path, mode="workflow")
    assert third.approvals.get(approval_id) is None
    assert any(event["event"] == "run_recovered" for event in _events(tmp_path, held.task_id))


def test_agent_approval_restores_react_conversation_after_restart(tmp_path):
    first_provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git push", ["origin", "main"])])
    ])
    first = build_gateway(base_dir=tmp_path, mode="agent", provider=first_provider)
    held = first.submit("git push to origin main", scenario="dev.git")

    assert held.state is TaskState.NEEDS_REVIEW
    assert held.phase is RunPhase.WAITING_APPROVAL
    approval_id = held.approval_id

    resumed_provider = ScriptedProvider([LLMResponse(text="push delivery verified")])
    second = build_gateway(base_dir=tmp_path, mode="agent", provider=resumed_provider)
    recovered = second.approvals.get(approval_id)
    assert recovered is not None
    assert recovered.messages

    resumed = second.resume(approval_id, approve=True)
    assert resumed.state is TaskState.SIMULATED
    assert resumed.settled
    assert [result.step.tool for result in resumed.executed_steps] == ["shell:git push"]


def test_stale_approval_from_another_gateway_cannot_replay_resolved_step(tmp_path):
    first = build_gateway(base_dir=tmp_path, mode="workflow")
    held = first.submit("帮我生成上周周报", scenario="ops.report")
    approval_id = held.approval_id
    second = build_gateway(base_dir=tmp_path, mode="workflow")
    stale = build_gateway(base_dir=tmp_path, mode="workflow")

    resolved = second.resume(approval_id, approve=True)

    assert resolved.phase is RunPhase.SETTLED
    with pytest.raises(RuntimeError, match="stale or already resolved"):
        stale.resume(approval_id, approve=True)


def test_contract_drift_fails_closed_without_preventing_startup(tmp_path):
    first = build_gateway(base_dir=tmp_path, mode="workflow")
    held = first.submit("帮我生成上周周报", scenario="ops.report")
    checkpoint_path = tmp_path / "runs" / held.task_id / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["context"]["contract"]["contract_id"] = "sha256:changed"
    checkpoint["digest"] = checkpoint_digest(
        checkpoint["context"], checkpoint.get("continuation")
    )
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    restarted = build_gateway(base_dir=tmp_path, mode="workflow")

    assert restarted.approvals.get(held.approval_id) is None
    failures = [
        record for record in restarted.runtime.audit.records
        if record.event == "run_recovery_failed" and record.payload.get("task_id") == held.task_id
    ]
    assert failures
    assert "contract changed" in failures[-1].payload["error"]


def test_tool_timeout_is_attributed_to_running_tool_not_llm(tmp_path):
    gateway = build_gateway(
        base_dir=tmp_path,
        mode="workflow",
        executor=TimeoutExecutor(),
        validator=False,
    )
    ctx = gateway.submit("echo this", scenario="default")

    assert ctx.state is TaskState.FAILED
    assert ctx.failure_kind == FailureKind.TOOL_TIMEOUT.value
    assert ctx.settled
    events = _events(tmp_path, ctx.task_id)
    assert [event["phase"] for event in events][-2:] == [
        RunPhase.TOOL_RUNNING.value,
        RunPhase.SETTLED.value,
    ]
    assert events[-1]["payload"]["failed_phase"] == RunPhase.TOOL_RUNNING.value


def test_llm_timeout_is_attributed_only_while_model_is_in_flight(tmp_path):
    gateway = build_gateway(
        base_dir=tmp_path,
        mode="agent",
        provider=TimeoutProvider(),
        validator=False,
    )
    ctx = gateway.submit("answer this", scenario="default")

    assert ctx.state is TaskState.FAILED
    assert ctx.failure_kind == FailureKind.LLM_TIMEOUT.value
    events = _events(tmp_path, ctx.task_id)
    assert events[-1]["payload"]["failed_phase"] == RunPhase.LLM_WAITING.value


@pytest.mark.parametrize("operating_mode", ["quality", "balanced", "efficiency"])
def test_every_operating_mode_uses_the_same_durable_protocol(tmp_path, operating_mode):
    base = tmp_path / operating_mode
    gateway = build_gateway(base_dir=base, mode="workflow", validator=False)
    ctx = gateway.submit("echo this", scenario="default", operating_mode=operating_mode)

    assert ctx.operating_mode == operating_mode
    assert ctx.state is TaskState.SIMULATED
    assert ctx.settled
    phases = {event["phase"] for event in _events(base, ctx.task_id)}
    assert RunPhase.TOOL_RUNNING.value in phases
    assert RunPhase.SETTLED.value in phases
