"""Fault-injection tests for model retry, failover, and durable backoff."""
from __future__ import annotations

import json
import time

import pytest

from taiyi.gateway import build_gateway
from taiyi.llm import (
    LLMErrorKind,
    LLMRequestError,
    LLMResponse,
    ProviderRouter,
    ResilientProvider,
    ScriptedProvider,
    ToolCall,
)
from taiyi.policy import resolve_policy
from taiyi.runtime import FailureKind, RunPhase, TaskState
from taiyi.runtime.protocol import classify_exception


class SequenceProvider:
    def __init__(self, name, model, outcomes):
        self.name = name
        self.model = model
        self.outcomes = list(outcomes)
        self.calls = 0

    def complete(self, messages, *, tools=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


def transient(kind=LLMErrorKind.LLM_SERVER_ERROR, *, retry_after=None):
    return LLMRequestError(
        kind,
        "injected transient model failure",
        retryable=True,
        retry_after=retry_after,
    )


def test_three_modes_have_distinct_retry_budgets_without_changing_risk_floor():
    quality = resolve_policy("quality")
    balanced = resolve_policy("balanced")
    efficiency = resolve_policy("efficiency")

    assert quality.max_llm_attempts > balanced.max_llm_attempts > efficiency.max_llm_attempts
    assert (
        quality.llm_retry_budget_seconds
        > balanced.llm_retry_budget_seconds
        > efficiency.llm_retry_budget_seconds
    )
    assert quality.llm_primary_attempts == 2
    assert balanced.llm_primary_attempts == efficiency.llm_primary_attempts == 1


def test_quality_retries_primary_before_failing_over_and_records_each_attempt():
    primary = SequenceProvider("quality", "strong", [transient(), transient()])
    fallback = SequenceProvider("default", "standard", [LLMResponse(text="ok", model="standard")])
    router = ProviderRouter(fallback, strongest_capable=primary)
    clock = FakeClock()
    events = []
    provider = ResilientProvider(
        router,
        resolve_policy("quality"),
        observer=lambda event, payload: events.append((event, payload)),
        sleep=clock.sleep,
        clock=clock,
    )

    response = provider.complete([])

    assert response.text == "ok"
    assert primary.calls == 2
    assert fallback.calls == 1
    assert clock.sleeps == [1.0, 2.0]
    scheduled = [payload for event, payload in events if event == "llm_retry_scheduled"]
    assert scheduled[0]["failover"] is False
    assert scheduled[1]["failover"] is True


def test_retry_after_is_honored_and_cannot_overrun_the_mode_budget():
    clock = FakeClock()
    primary = SequenceProvider("adaptive", "adaptive", [
        transient(LLMErrorKind.RATE_LIMIT, retry_after=3),
    ])
    fallback = SequenceProvider("default", "default", [LLMResponse(text="ok")])
    provider = ResilientProvider(
        ProviderRouter(fallback, adaptive=primary),
        resolve_policy("balanced"),
        sleep=clock.sleep,
        clock=clock,
    )
    assert provider.complete([]).text == "ok"
    assert clock.sleeps == [3.0]

    too_slow = SequenceProvider("fast", "fast", [
        transient(LLMErrorKind.RATE_LIMIT, retry_after=20),
    ])
    never = SequenceProvider("default", "default", [LLMResponse(text="wrong")])
    clock = FakeClock()
    with pytest.raises(LLMRequestError) as failure:
        ResilientProvider(
            ProviderRouter(never, fastest_capable=too_slow),
            resolve_policy("efficiency"),
            sleep=clock.sleep,
            clock=clock,
        ).complete([])
    assert failure.value.kind is LLMErrorKind.RATE_LIMIT
    assert never.calls == 0
    assert clock.sleeps == []


def test_recovery_honors_the_remaining_persisted_backoff_before_retrying():
    clock = FakeClock()
    primary = SequenceProvider("adaptive", "adaptive", [LLMResponse(text="wrong")])
    fallback = SequenceProvider("default", "default", [LLMResponse(text="recovered")])
    provider = ResilientProvider(
        ProviderRouter(fallback, adaptive=primary),
        resolve_policy("balanced"),
        sleep=clock.sleep,
        clock=clock,
        initial_attempts=1,
        deadline_at=1060,
        retry_not_before=1003,
    )

    assert provider.complete([]).text == "recovered"
    assert clock.sleeps == [3.0]
    assert primary.calls == 0
    assert fallback.calls == 1


def test_auth_and_context_failures_do_not_retry_or_fail_over():
    for kind in (LLMErrorKind.LLM_AUTH_ERROR, LLMErrorKind.CONTEXT_OVERFLOW):
        primary = SequenceProvider("adaptive", "adaptive", [
            LLMRequestError(kind, "stop", retryable=False),
        ])
        fallback = SequenceProvider("default", "default", [LLMResponse(text="wrong")])
        with pytest.raises(LLMRequestError) as failure:
            ResilientProvider(
                ProviderRouter(fallback, adaptive=primary),
                resolve_policy("balanced"),
                sleep=lambda _: None,
            ).complete([])
        assert failure.value.kind is kind
        assert primary.calls == 1
        assert fallback.calls == 0


def test_typed_model_failure_survives_runtime_classification():
    error = LLMRequestError(
        LLMErrorKind.LLM_FIRST_TOKEN_TIMEOUT,
        "late",
        retryable=True,
    )
    assert classify_exception(error, RunPhase.LLM_WAITING) is FailureKind.LLM_FIRST_TOKEN_TIMEOUT


def test_agent_failover_does_not_duplicate_the_tool_side_effect():
    failing = SequenceProvider("adaptive", "adaptive", [transient(), transient()])
    default = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("echo", ["once"])], model="default"),
        LLMResponse(text="done", model="default"),
    ])
    default.name = "default"
    default.model = "default"
    gateway = build_gateway(
        provider=default,
        provider_router=ProviderRouter(default, adaptive=failing),
        validator=False,
        llm_sleep=lambda _: None,
    )

    ctx = gateway.submit("perform one action", operating_mode="balanced")

    assert ctx.state is TaskState.SIMULATED
    assert ctx.executed_action_count == 1
    assert len(ctx.executed_steps) == 1
    assert ctx.executed_steps[0].output == "[mock] shell:echo ['once']"
    assert sum(r.event == "llm_retry_scheduled" for r in gateway.runtime.audit.records) == 2


def test_workflow_planning_uses_the_same_failover_protocol():
    failing = SequenceProvider("adaptive", "adaptive", [transient()])
    default = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("echo", ["planned"])], model="default")
    ])
    default.name = "default"
    default.model = "default"
    gateway = build_gateway(
        mode="workflow",
        provider=default,
        provider_router=ProviderRouter(default, adaptive=failing),
        validator=False,
        llm_sleep=lambda _: None,
    )

    ctx = gateway.submit("plan it", operating_mode="balanced")

    assert ctx.state is TaskState.SIMULATED
    assert ctx.executed_steps[0].output == "[mock] echo ['planned']"
    assert ctx.provider_route["failover"] is True


class CrashInBackoff:
    def __call__(self, delay):
        raise SystemExit("fault injection: process exited during model retry backoff")


def _wait_for_settled(base, task_id, timeout=5.0):
    checkpoint_path = base / "runs" / task_id / "checkpoint.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint["context"]["phase"] == RunPhase.SETTLED.value:
            return checkpoint
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not settle")


@pytest.mark.parametrize("runtime_mode", ["agent", "workflow"])
def test_process_restart_resumes_from_durable_retry_backoff(tmp_path, runtime_mode):
    failing = SequenceProvider("adaptive", "adaptive", [transient()])
    unused = ScriptedProvider([LLMResponse(text="unused")])
    unused.name = "default"
    unused.model = "default"
    first = build_gateway(
        base_dir=tmp_path,
        mode=runtime_mode,
        provider=unused,
        provider_router=ProviderRouter(unused, adaptive=failing),
        validator=False,
        llm_sleep=CrashInBackoff(),
    )

    with pytest.raises(SystemExit, match="retry backoff"):
        first.submit("resume this model turn", operating_mode="balanced")

    checkpoint_path = next((tmp_path / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    assert interrupted["context"]["phase"] == RunPhase.RETRY_BACKOFF.value
    assert interrupted["continuation"]["llm_retry"]["attempts_used"] == 1
    first.runtime.run_store.release_task_lease(task_id)

    recovered = ScriptedProvider([LLMResponse(text="recovered", model="default")])
    recovered.name = "default"
    recovered.model = "default"
    restarted = build_gateway(
        base_dir=tmp_path,
        mode=runtime_mode,
        provider=recovered,
        provider_router=ProviderRouter(recovered, adaptive=SequenceProvider(
            "adaptive", "adaptive", [transient()]
        )),
        validator=False,
        llm_sleep=lambda _: None,
    )

    settled = _wait_for_settled(tmp_path, task_id)
    assert settled["context"]["state"] in {
        TaskState.COMPLETED.value,
        TaskState.SIMULATED.value,
    }
    assert settled["context"]["attempt_id"] == 2
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / task_id / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert sum(event["event"] == "run_recovered" for event in events) == 1
    thread = restarted.runtime._recovery_threads.get(task_id)
    if thread is not None:
        thread.join(timeout=2.0)
    assert task_id not in restarted.runtime._recovery_threads
