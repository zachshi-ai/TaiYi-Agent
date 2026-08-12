"""Side-effect classification, evidence, idempotency, and ambiguity tests."""
from __future__ import annotations

import json
import time

import pytest

from taiyi.runtime import (
    EffectManager,
    EffectPolicyRegistry,
    EffectRecord,
    EffectStatus,
    FileWriteAuthority,
    HumanEffectResolution,
    ObservationStatus,
    RecoveryAction,
    ReplayPolicy,
    SideEffectClass,
)
from taiyi.gateway import build_gateway
from taiyi.gateway import GatewayApp
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import ExecResult, FailureKind, MockExecutor, RunPhase, TaskState
from taiyi.runtime.persistence import checkpoint_digest
from taiyi.policy import resolve_policy
from taiyi.tools import SandboxExecutor
from taiyi.scheduler import PlanStep


def _manager(tmp_path, *, environment="workspace"):
    return EffectManager(
        registry=EffectPolicyRegistry(
            environment=environment,
            simulated=(environment == "mock"),
        ),
        authorities=(FileWriteAuthority(tmp_path),),
    )


def test_effect_policy_is_harness_owned_and_unknown_mutations_fail_closed(tmp_path):
    manager = _manager(tmp_path)
    read = manager.prepare([], PlanStep("file:read", ["status.txt"]), "read")
    refund = manager.prepare([], PlanStep("tool:refund", ["amount=100"]), "refund")
    unknown = manager.prepare([], PlanStep("shell:python3", ["-c", "mutate()"]), "unknown")

    assert read.side_effect_class is SideEffectClass.NONE
    assert read.replay_policy is ReplayPolicy.SAFE
    assert refund.side_effect_class is SideEffectClass.IRREVERSIBLE
    assert refund.replay_policy is ReplayPolicy.NEVER
    assert unknown.side_effect_class is SideEffectClass.UNKNOWN
    assert unknown.replay_policy is ReplayPolicy.NEVER

    shell_that_sounds_read_only = manager.prepare(
        [], PlanStep("shell:git status", []), "shell-status"
    )
    assert shell_that_sounds_read_only.side_effect_class is SideEffectClass.UNKNOWN


def test_mock_tools_remain_side_effect_free_even_when_names_sound_dangerous(tmp_path):
    manager = _manager(tmp_path, environment="mock")
    record = manager.prepare([], PlanStep("tool:refund", ["amount=999"]), "simulated")

    assert record.side_effect_class is SideEffectClass.NONE
    assert record.replay_policy is ReplayPolicy.SAFE

    lying_registry = EffectPolicyRegistry(environment="mock", simulated=False)
    lying = EffectManager(registry=lying_registry).prepare(
        [], PlanStep("tool:refund", ["amount=999"]), "not-really-mock"
    )
    assert lying.side_effect_class is SideEffectClass.IRREVERSIBLE

    class ConnectorDisguisedAsMock(MockExecutor):
        pass

    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("notify:send", ["message"])]),
        LLMResponse(text="done"),
    ])
    gateway = build_gateway(
        base_dir=tmp_path / "subclass",
        mode="agent",
        executor=ConnectorDisguisedAsMock(),
        provider=provider,
        validator=False,
    )
    ctx = gateway.submit("send the message")
    assert ctx.effects[0].side_effect_class is SideEffectClass.IRREVERSIBLE
    assert ctx.effects[0].replay_policy is ReplayPolicy.NEVER


def test_file_write_authority_distinguishes_not_applied_applied_and_unknown(tmp_path):
    target = tmp_path / "result.txt"
    target.write_text("before", encoding="utf-8")
    step = PlanStep("file:write", ["result.txt", "after"])

    manager = _manager(tmp_path)
    untouched = manager.prepare([], step, "untouched")
    observation = manager.observe(untouched, step)
    assert observation.status is ObservationStatus.NOT_APPLIED
    assert untouched.status is EffectStatus.CONFIRMED_NOT_APPLIED
    assert manager.recovery_action(
        untouched, observation, idempotency_supported=False
    ) is RecoveryAction.RETRY

    applied = manager.prepare([], step, "applied")
    target.write_text("after", encoding="utf-8")
    observation = manager.observe(applied, step)
    assert observation.status is ObservationStatus.APPLIED
    assert applied.status is EffectStatus.CONFIRMED_APPLIED
    assert manager.recovery_action(
        applied, observation, idempotency_supported=False
    ) is RecoveryAction.ACCEPT_APPLIED

    target.write_text("before", encoding="utf-8")
    changed = manager.prepare([], step, "changed")
    target.write_text("third-party-change", encoding="utf-8")
    observation = manager.observe(changed, step)
    assert observation.status is ObservationStatus.UNKNOWN
    assert changed.status is EffectStatus.AMBIGUOUS
    assert manager.recovery_action(
        changed, observation, idempotency_supported=False
    ) is RecoveryAction.WAIT_FOR_HUMAN


def test_frozen_effect_cannot_be_rebound_to_different_arguments(tmp_path):
    manager = _manager(tmp_path)
    effects = []
    manager.prepare(effects, PlanStep("file:write", ["x", "one"]), "same")

    try:
        manager.prepare(effects, PlanStep("file:write", ["x", "two"]), "same")
    except ValueError as exc:
        assert "different tool arguments" in str(exc)
    else:  # pragma: no cover - explicit fail-closed assertion
        raise AssertionError("effect operation was rebound")


def test_effect_record_round_trip_preserves_evidence_and_human_resolution(tmp_path):
    manager = _manager(tmp_path)
    record = manager.prepare([], PlanStep("shell:python3", ["-c", "send()"]), "op")
    observation = manager.observe(record, PlanStep("shell:python3", ["-c", "send()"] ))
    assert observation.status is ObservationStatus.UNKNOWN
    manager.apply_human_resolution(
        record,
        HumanEffectResolution.APPLIED,
        note="checked external receipt ext-123",
    )

    restored = EffectRecord.from_dict(record.to_dict())
    assert restored.status is EffectStatus.CONFIRMED_APPLIED
    assert restored.human_resolution == "applied"
    assert restored.observations[-1].authority == "human"
    assert "ext-123" in restored.observations[-1].evidence


class _UncertainFileExecutor:
    environment = "workspace"

    def __init__(self, root, *, first_outcome):
        self.sandbox = root
        self.first_outcome = first_outcome
        self.calls = 0

    def execute(self, step):
        raise AssertionError("file connector should receive the idempotency key")

    def supports_idempotency(self, step):
        return step.tool == "file:write"

    def execute_idempotent(self, step, *, operation_id, idempotency_key):
        self.calls += 1
        target = self.sandbox / step.args[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.calls == 1:
            if self.first_outcome == "applied":
                target.write_text(step.args[1], encoding="utf-8")
            elif self.first_outcome == "unknown":
                target.write_text("unrelated concurrent value", encoding="utf-8")
            return ExecResult(
                "request timed out without a response",
                ok=False,
                operation_id=operation_id,
                failure_kind=FailureKind.TOOL_TIMEOUT.value,
                error="connector response timeout",
            )
        target.write_text(step.args[1], encoding="utf-8")
        return ExecResult("write accepted", operation_id=operation_id)


class _RaisedTimeoutAfterWrite(_UncertainFileExecutor):
    def execute_idempotent(self, step, *, operation_id, idempotency_key):
        self.calls += 1
        target = self.sandbox / step.args[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.calls == 1:
            target.write_text(step.args[1], encoding="utf-8")
            raise TimeoutError("socket closed after server applied the write")
        target.write_text(step.args[1], encoding="utf-8")
        return ExecResult("write accepted", operation_id=operation_id)


class _RaisedPermissionAfterWrite(_UncertainFileExecutor):
    def execute_idempotent(self, step, *, operation_id, idempotency_key):
        self.calls += 1
        target = self.sandbox / step.args[0]
        target.write_text(step.args[1], encoding="utf-8")
        raise PermissionError("receipt store denied after target write")


class _NeverAppliedFileExecutor(_UncertainFileExecutor):
    def execute_idempotent(self, step, *, operation_id, idempotency_key):
        self.calls += 1
        return ExecResult(
            "request failed before a receipt",
            ok=False,
            operation_id=operation_id,
            failure_kind=FailureKind.EXTERNAL_FAILURE.value,
            error="connector unavailable",
        )


class _RetryRaisesAfterWrite(_UncertainFileExecutor):
    def execute_idempotent(self, step, *, operation_id, idempotency_key):
        self.calls += 1
        if self.calls == 1:
            return ExecResult(
                "first request failed before apply",
                ok=False,
                operation_id=operation_id,
                failure_kind=FailureKind.EXTERNAL_FAILURE.value,
                error="connector unavailable",
            )
        target = self.sandbox / step.args[0]
        target.write_text(step.args[1], encoding="utf-8")
        raise TimeoutError("second response was lost after apply")


class _FalseSuccessThenWrite(_UncertainFileExecutor):
    def execute_idempotent(self, step, *, operation_id, idempotency_key):
        self.calls += 1
        if self.calls == 1:
            return ExecResult("connector claimed success", operation_id=operation_id)
        target = self.sandbox / step.args[0]
        target.write_text(step.args[1], encoding="utf-8")
        return ExecResult("write accepted", operation_id=operation_id)


def _file_agent(tmp_path, executor):
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("file:write", ["result.txt", "target"])]),
        LLMResponse(text="verified delivery"),
    ])
    return build_gateway(
        base_dir=tmp_path / "state",
        mode="agent",
        executor=executor,
        provider=provider,
        validator=False,
    )


def test_runtime_accepts_independently_confirmed_effect_after_connector_timeout(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _UncertainFileExecutor(workspace, first_outcome="applied")

    ctx = _file_agent(tmp_path, executor).submit("write the exact target")

    assert ctx.state is TaskState.COMPLETED
    assert executor.calls == 1
    assert ctx.effects[0].status is EffectStatus.CONFIRMED_APPLIED
    assert ctx.effects[0].observations[-1].status is ObservationStatus.APPLIED
    assert ctx.step_results[0].executed


def test_raised_timeout_is_reclassified_by_observed_effect_not_exception_text(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _RaisedTimeoutAfterWrite(workspace, first_outcome="applied")

    ctx = _file_agent(tmp_path, executor).submit("write the exact target")

    assert ctx.state is TaskState.COMPLETED
    assert executor.calls == 1
    assert ctx.effects[0].executor_failure_kind == FailureKind.TOOL_TIMEOUT.value
    assert ctx.effects[0].status is EffectStatus.CONFIRMED_APPLIED


def test_connector_permission_error_does_not_prove_effect_absence(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _RaisedPermissionAfterWrite(workspace, first_outcome="applied")

    ctx = _file_agent(tmp_path, executor).submit("write the exact target")

    assert ctx.state is TaskState.COMPLETED
    assert executor.calls == 1
    assert ctx.effects[0].executor_failure_kind == FailureKind.PERMISSION_DENIED.value
    assert ctx.effects[0].status is EffectStatus.CONFIRMED_APPLIED
    assert ctx.step_results[0].original_failure_kind == FailureKind.PERMISSION_DENIED.value


def test_runtime_retries_only_after_authority_proves_effect_not_applied(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _UncertainFileExecutor(workspace, first_outcome="not_applied")

    ctx = _file_agent(tmp_path, executor).submit("write the exact target")

    assert ctx.state is TaskState.COMPLETED
    assert executor.calls == 2
    assert ctx.effects[0].recovery_attempts == 1
    assert ctx.effects[0].observations[0].status is ObservationStatus.NOT_APPLIED
    assert ctx.effects[0].observations[-1].status is ObservationStatus.APPLIED
    events = ctx.effects[0].to_dict()
    assert events["idempotency_key"].startswith("taiyi:")


def test_retry_exception_is_reconciled_against_effect_authority(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _RetryRaisesAfterWrite(workspace, first_outcome="not_applied")

    ctx = _file_agent(tmp_path, executor).submit("write the exact target")

    assert ctx.state is TaskState.COMPLETED
    assert executor.calls == 2
    assert ctx.effects[0].recovery_attempts == 1
    assert ctx.effects[0].executor_failure_kind == FailureKind.TOOL_TIMEOUT.value
    assert ctx.effects[0].status is EffectStatus.CONFIRMED_APPLIED
    assert ctx.step_results[0].original_failure_kind == FailureKind.TOOL_TIMEOUT.value


def test_connector_success_cannot_override_proven_non_application(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _FalseSuccessThenWrite(workspace, first_outcome="not_applied")

    ctx = _file_agent(tmp_path, executor).submit("write the exact target")

    assert ctx.state is TaskState.COMPLETED
    assert executor.calls == 2
    assert ctx.effects[0].observations[0].status is ObservationStatus.NOT_APPLIED
    assert ctx.effects[0].observations[-1].status is ObservationStatus.APPLIED
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "target"


def test_effect_retry_is_bounded_by_mode_without_changing_fail_closed_policy(tmp_path):
    quality = resolve_policy("quality")
    balanced = resolve_policy("balanced")
    efficiency = resolve_policy("efficiency")
    assert quality.max_effect_recovery_attempts > balanced.max_effect_recovery_attempts
    assert balanced.max_effect_recovery_attempts == efficiency.max_effect_recovery_attempts

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _NeverAppliedFileExecutor(workspace, first_outcome="not_applied")
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("file:write", ["result.txt", "target"])]),
    ])
    gateway = build_gateway(
        base_dir=tmp_path / "state",
        mode="agent",
        operating_mode="efficiency",
        executor=executor,
        provider=provider,
        validator=False,
    )

    ctx = gateway.submit("write the exact target", operating_mode="efficiency")

    assert ctx.state is TaskState.FAILED
    assert ctx.failure_kind == FailureKind.EXTERNAL_FAILURE.value
    assert executor.calls == 2  # initial dispatch + one efficiency recovery
    assert ctx.effects[0].recovery_attempts == 1
    assert ctx.effects[0].status is EffectStatus.CONFIRMED_NOT_APPLIED


def test_runtime_suspends_unknown_effect_instead_of_retrying_or_failing_closed_as_done(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _UncertainFileExecutor(workspace, first_outcome="unknown")
    gateway = _file_agent(tmp_path, executor)

    ctx = gateway.submit("write the exact target")

    assert ctx.state is TaskState.NEEDS_INPUT
    assert ctx.phase is RunPhase.WAITING_INPUT
    assert ctx.failure_kind == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
    assert executor.calls == 1
    assert not ctx.step_results[0].executed
    assert ctx.effects[0].status is EffectStatus.AMBIGUOUS
    assert not ctx.settled
    checkpoint = gateway.runtime.run_store.load(ctx.task_id)
    assert checkpoint["continuation"]["kind"] == "agent_effect_resolution"


class _CrashDuringFileEffect:
    environment = "workspace"

    def __init__(self, inner, *, outcome):
        self.inner = inner
        self.sandbox = inner.sandbox
        self.outcome = outcome

    def execute(self, step):
        return self.inner.execute(step)

    def supports_idempotency(self, step):
        return self.inner.supports_idempotency(step)

    def execute_idempotent(self, step, *, operation_id, idempotency_key):
        target = self.sandbox / step.args[0]
        if self.outcome == "applied":
            target.write_text(step.args[1], encoding="utf-8")
        elif self.outcome == "unknown":
            target.write_text("concurrent mutation", encoding="utf-8")
        raise SystemExit("fault injection: owner exited inside non-durable connector")


def _wait_checkpoint(base, task_id, phase, timeout=5.0):
    path = base / "runs" / task_id / "checkpoint.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if checkpoint["context"]["phase"] == phase:
            return checkpoint
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {phase}")


@pytest.mark.parametrize("runtime_mode", ["workflow", "agent"])
@pytest.mark.parametrize(
    ("outcome", "expected_state", "expected_effect_status"),
    [
        ("applied", TaskState.COMPLETED, EffectStatus.CONFIRMED_APPLIED),
        ("not_applied", TaskState.COMPLETED, EffectStatus.CONFIRMED_APPLIED),
        ("unknown", TaskState.NEEDS_INPUT, EffectStatus.AMBIGUOUS),
    ],
)
def test_restart_reconciles_non_durable_effect_before_any_replay(
    tmp_path, runtime_mode, outcome, expected_state, expected_effect_status
):
    base = tmp_path / f"{runtime_mode}-{outcome}"
    workspace = base / "workspace"
    workspace.mkdir(parents=True)
    inner = SandboxExecutor(workspace, job_dir=base / "jobs")
    crashing = _CrashDuringFileEffect(inner, outcome=outcome)
    first_provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("file:write", ["result.txt", "target"])]),
    ])
    first = build_gateway(
        base_dir=base,
        mode=runtime_mode,
        executor=crashing,
        provider=first_provider,
        validator=False,
    )

    with pytest.raises(SystemExit, match="non-durable connector"):
        first.submit("write the exact target")

    checkpoint_path = next((base / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    assert interrupted["context"]["effects"][0]["status"] == EffectStatus.DISPATCHING.value
    first.runtime.run_store.release_task_lease(task_id)

    recovered_provider = ScriptedProvider(
        [LLMResponse(text="effect reconciled")] if runtime_mode == "agent" else []
    )
    restarted = build_gateway(
        base_dir=base,
        mode=runtime_mode,
        executor=SandboxExecutor(workspace, job_dir=base / "jobs"),
        provider=recovered_provider,
        validator=False,
    )
    expected_phase = (
        RunPhase.WAITING_INPUT.value
        if expected_state is TaskState.NEEDS_INPUT
        else RunPhase.SETTLED.value
    )
    settled = _wait_checkpoint(base, task_id, expected_phase)

    assert settled["context"]["state"] == expected_state.value
    assert settled["context"]["effects"][0]["status"] == expected_effect_status.value
    if outcome == "unknown":
        assert (workspace / "result.txt").read_text(encoding="utf-8") == "concurrent mutation"
        assert not settled["context"]["step_results"][0]["executed"]
        resolved = restarted.resolve_effect(
            task_id,
            resolution="applied",
            note="operator matched the external change to receipt recovery-1",
        )
        assert resolved.state is TaskState.COMPLETED
        assert resolved.executed_action_count == 1
        assert resolved.effects[0].human_resolution == "applied"
    else:
        assert (workspace / "result.txt").read_text(encoding="utf-8") == "target"
        assert settled["context"]["executed_action_count"] == 1


def test_recovery_rejects_tampered_effect_policy_without_replay(tmp_path):
    base = tmp_path / "tampered-policy"
    workspace = base / "workspace"
    workspace.mkdir(parents=True)
    inner = SandboxExecutor(workspace, job_dir=base / "jobs")
    crashing = _CrashDuringFileEffect(inner, outcome="applied")
    first = build_gateway(
        base_dir=base,
        mode="agent",
        executor=crashing,
        provider=ScriptedProvider([
            LLMResponse(tool_calls=[ToolCall("file:write", ["result.txt", "target"])]),
        ]),
        validator=False,
    )

    with pytest.raises(SystemExit, match="non-durable connector"):
        first.submit("write the exact target")

    checkpoint_path = next((base / "runs").glob("*/checkpoint.json"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = checkpoint["context"]["task_id"]
    checkpoint["context"]["effects"][0]["policy_digest"] = "sha256:tampered"
    checkpoint["digest"] = checkpoint_digest(
        checkpoint["context"], checkpoint.get("continuation")
    )
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    first.runtime.run_store.release_task_lease(task_id)

    build_gateway(
        base_dir=base,
        mode="agent",
        executor=SandboxExecutor(workspace, job_dir=base / "jobs"),
        provider=ScriptedProvider([]),
        validator=False,
    )
    settled = _wait_checkpoint(base, task_id, RunPhase.SETTLED.value)

    assert settled["context"]["state"] == TaskState.FAILED.value
    assert settled["context"]["failure_kind"] == FailureKind.CHECKPOINT_INCOMPATIBLE.value
    assert settled["context"]["effects"][0]["attempt_count"] == 1
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "target"


def test_effect_resolution_endpoint_requires_evidence_and_continues_after_applied(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _UncertainFileExecutor(workspace, first_outcome="unknown")
    gateway = _file_agent(tmp_path, executor)
    ctx = gateway.submit("write the exact target")
    app = GatewayApp(gateway)

    bad_status, bad = app.handle(
        "POST",
        f"/v1/tasks/{ctx.task_id}/effects/resolve",
        {},
        json.dumps({"resolution": "applied", "note": ""}),
    )
    assert bad_status == 400
    assert "audit note" in bad["error"]

    status, resolved = app.handle(
        "POST",
        f"/v1/tasks/{ctx.task_id}/effects/resolve",
        {},
        json.dumps({
            "resolution": "applied",
            "note": "operator verified external receipt receipt-42",
        }),
    )

    assert status == 200
    assert resolved["state"] == TaskState.COMPLETED.value
    assert resolved["effects"][0]["human_resolution"] == "applied"
    assert resolved["executed_action_count"] == 1
    assert executor.calls == 1

    duplicate_status, _ = app.handle(
        "POST",
        f"/v1/tasks/{ctx.task_id}/effects/resolve",
        {},
        json.dumps({"resolution": "applied", "note": "repeat"}),
    )
    assert duplicate_status == 409


def test_human_not_applied_replay_counts_against_mode_recovery_budget(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _UncertainFileExecutor(workspace, first_outcome="unknown")
    gateway = _file_agent(tmp_path, executor)
    ctx = gateway.submit("write the exact target")

    resolved = gateway.resolve_effect(
        ctx.task_id,
        resolution="not_applied",
        note="operator checked the target system and authorized the frozen replay",
    )

    assert resolved.state is TaskState.COMPLETED
    assert executor.calls == 2
    assert resolved.effects[0].recovery_attempts == 1
    assert resolved.effects[0].attempt_count == 2
    assert resolved.effects[0].status is EffectStatus.CONFIRMED_APPLIED


class _AmbiguousUnknownMutation:
    environment = "workspace"

    def __init__(self):
        self.calls = 0

    def execute(self, step):
        self.calls += 1
        return ExecResult(
            "transport ended after dispatch",
            ok=False,
            failure_kind=FailureKind.EXTERNAL_FAILURE.value,
            error="no external receipt",
        )


def test_human_not_applied_does_not_override_a_never_replay_policy(tmp_path):
    executor = _AmbiguousUnknownMutation()
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:custom-mutation", ["send"])]),
    ])
    gateway = build_gateway(
        base_dir=tmp_path,
        mode="agent",
        executor=executor,
        provider=provider,
        validator=False,
    )
    ctx = gateway.submit("perform the custom mutation")
    assert ctx.state is TaskState.NEEDS_INPUT

    resolved = gateway.resolve_effect(
        ctx.task_id,
        resolution="not_applied",
        note="operator found no matching receipt",
    )

    assert resolved.state is TaskState.FAILED
    assert resolved.failure_kind == FailureKind.EXTERNAL_FAILURE.value
    assert executor.calls == 1
    assert resolved.effects[0].human_resolution == "not_applied"
