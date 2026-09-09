"""Shared task ownership must fence stale writers, not merely detect overlap."""
from __future__ import annotations

import json
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from taiyi.gateway import build_gateway
from taiyi.benchmark.schema import FENCED_TASK_VALIDATION_SCHEMA, verify_artifact
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.policy import resolve_policy
from taiyi.runtime import RunPhase, RunStore, TaskContext, TaskLeaseLostError
from taiyi.tools import SandboxExecutor


class _Clock:
    def __init__(self, value: float = 1_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


def _context(task_id: str) -> TaskContext:
    return TaskContext(
        task_id=task_id,
        prompt="hold one durable task",
        scenario="default",
        policy=resolve_policy("balanced"),
    )


def _crash_owner(base_dir: str, queue) -> None:
    store = RunStore(
        base_dir,
        task_lease_seconds=0.3,
        task_lease_heartbeat=False,
    )
    ctx = _context("crashed-owner")
    store.record(ctx, RunPhase.READY, "run_created")
    lease = store.task_lease(ctx.task_id)
    queue.put(lease.token if lease is not None else None)
    # Exit without release/close, as a killed Gateway would.


class _BlockedStaleProvider:
    name = "test:blocked-stale-owner"

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, *, tools=None):
        self.entered.set()
        assert self.release.wait(timeout=5)
        return LLMResponse(tool_calls=[ToolCall("file:write", ["stale.txt", "bad\n"])])


def test_released_task_gets_a_strictly_newer_fencing_token(tmp_path):
    first = RunStore(tmp_path, task_lease_heartbeat=False)
    second = RunStore(tmp_path, task_lease_heartbeat=False)

    assert first.acquire_task_lease("monotonic", blocking=False)
    old = first.task_lease("monotonic")
    assert old is not None
    first.release_task_lease("monotonic")
    assert second.acquire_task_lease("monotonic", blocking=False)
    new = second.task_lease("monotonic")

    assert new is not None
    assert new.token == old.token + 1
    second.close()


def test_old_local_cleanup_cannot_release_a_new_execution_lease(tmp_path):
    store = RunStore(tmp_path, task_lease_heartbeat=False)
    ctx = _context("local-handoff")
    store.record(ctx, RunPhase.READY, "run_created")
    old = store.task_lease(ctx.task_id)
    store.record(ctx, RunPhase.WAITING_INPUT, "waiting_for_operator")
    assert store.acquire_task_lease(ctx.task_id, blocking=False)
    replacement = store.task_lease(ctx.task_id)
    assert old is not None and replacement is not None
    assert replacement.token > old.token

    store.release_task_lease(ctx.task_id, expected=old)
    store.release_task_lease(ctx.task_id, expected=None)

    assert store.task_lease(ctx.task_id) == replacement
    assert store.shared_task_lease(ctx.task_id) == replacement
    store.release_task_lease(ctx.task_id, expected=replacement)
    assert store.shared_task_lease(ctx.task_id) is None
    store.close()


def test_old_context_cannot_borrow_new_local_lease_for_a_write(tmp_path):
    store = RunStore(tmp_path, task_lease_heartbeat=False)
    ctx = _context("stale-context")
    store.record(ctx, RunPhase.READY, "run_created")
    store.record(ctx, RunPhase.WAITING_INPUT, "waiting_for_operator")
    frozen = store.load(ctx.task_id)
    revision = ctx.checkpoint_revision
    assert store.acquire_task_lease(ctx.task_id, blocking=False)

    with pytest.raises(TaskLeaseLostError, match="earlier lease"):
        store.record(ctx, RunPhase.SETTLED, "stale_cleanup")

    assert ctx.checkpoint_revision == revision
    assert store.load(ctx.task_id) == frozen
    store.close()


def test_receipt_for_another_task_cannot_release_same_numbered_token(tmp_path):
    store = RunStore(tmp_path, task_lease_heartbeat=False)
    assert store.acquire_task_lease("one", blocking=False)
    assert store.acquire_task_lease("two", blocking=False)
    one, two = store.task_lease("one"), store.task_lease("two")
    assert one is not None and two is not None and one.token == two.token

    store.release_task_lease("two", expected=one)

    assert store.task_lease("two") == two
    store.close()


def test_fresh_context_cannot_borrow_successor_lease_before_first_write(tmp_path):
    store = RunStore(tmp_path, task_lease_heartbeat=False)
    ctx = _context("late-first-write")
    store.record(ctx, RunPhase.READY, "run_created")
    store.record(ctx, RunPhase.WAITING_INPUT, "waiting_for_operator")
    checkpoint = store.load(ctx.task_id)
    assert store.acquire_task_lease(ctx.task_id, blocking=False)
    acquired = store.task_lease(ctx.task_id)
    fresh = _context(ctx.task_id)
    fresh.checkpoint_revision = ctx.checkpoint_revision
    store.release_task_lease(ctx.task_id, expected=acquired)
    assert store.acquire_task_lease(ctx.task_id, blocking=False)
    successor = store.task_lease(ctx.task_id)

    with pytest.raises(TaskLeaseLostError, match="replaced before binding"):
        store.bind_task_context(fresh, expected=acquired)
    with pytest.raises(TaskLeaseLostError, match="no bound write lease"):
        store.record(fresh, RunPhase.RECOVERING, "stale_first_write")

    assert store.load(ctx.task_id) == checkpoint
    assert store.task_lease(ctx.task_id) == successor
    store.close()


def test_committed_fenced_task_validation_is_signed_and_fail_closed():
    path = Path(
        "research/benchmark/results/fenced-task-process-v1/validation.json"
    )
    payload = verify_artifact(
        json.loads(path.read_text(encoding="utf-8")),
        schema_version=FENCED_TASK_VALIDATION_SCHEMA,
    )

    assert payload["measurement_scope"] == "taiyi_fenced_task_ownership"
    assert payload["lease"]["replacement_fencing_token"] > payload["lease"][
        "old_fencing_token"
    ]
    assert payload["assertions"]["stale_effect_absent"] is True
    assert payload["assertions"][
        "stale_model_response_fenced_before_tool_dispatch"
    ] is True
    assert payload["limitations"]["multi_host_qualified"] is False
    assert payload["ranking_eligible"] is False


def test_expired_owner_is_fenced_before_it_can_append_or_mutate_context(tmp_path):
    clock = _Clock()
    first = RunStore(
        tmp_path,
        task_lease_seconds=1,
        task_lease_clock=clock,
        task_lease_heartbeat=False,
    )
    ctx = _context("stale-writer")
    first.record(ctx, RunPhase.READY, "run_created")
    old = first.task_lease(ctx.task_id)
    assert old is not None

    clock.value += 2
    second = RunStore(
        tmp_path,
        task_lease_seconds=1,
        task_lease_clock=clock,
        task_lease_heartbeat=False,
    )
    assert second.acquire_task_lease(ctx.task_id, blocking=False)
    replacement = second.task_lease(ctx.task_id)
    assert replacement is not None and replacement.token > old.token

    with pytest.raises(TaskLeaseLostError, match="stale"):
        first.record(ctx, RunPhase.PLANNING, "stale_write")

    assert ctx.phase is RunPhase.READY
    assert ctx.checkpoint_revision == 1
    assert [event["event"] for event in second.read_events(ctx.task_id)] == [
        "run_created"
    ]
    second.close()


def test_one_shared_heartbeat_preserves_ownership_during_long_model_wait(tmp_path):
    first = RunStore(tmp_path, task_lease_seconds=0.3)
    ctx = _context("long-model-wait")
    first.record(ctx, RunPhase.LLM_WAITING, "run_created")
    token = first.task_lease(ctx.task_id).token

    time.sleep(0.7)
    contender = RunStore(
        tmp_path,
        task_lease_seconds=0.3,
        task_lease_heartbeat=False,
    )
    assert contender.acquire_task_lease(ctx.task_id, blocking=False) is False
    first.record(ctx, RunPhase.LLM_WAITING, "llm_request_still_waiting")

    assert first.task_lease(ctx.task_id).token == token
    heartbeat_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name == "taiyi-task-lease-heartbeat"
    ]
    assert heartbeat_threads
    first.close()
    contender.close()


def test_immediate_reacquire_starts_a_new_heartbeat_generation(tmp_path):
    store = RunStore(tmp_path, task_lease_seconds=0.3)
    ctx = _context("immediate-reacquire")
    store.record(ctx, RunPhase.READY, "run_created")
    store.record(ctx, RunPhase.WAITING_INPUT, "needs_input")
    assert store.task_lease(ctx.task_id) is None

    assert store.acquire_task_lease(ctx.task_id, blocking=False)
    token = store.task_lease(ctx.task_id).token
    time.sleep(0.7)
    contender = RunStore(
        tmp_path,
        task_lease_seconds=0.3,
        task_lease_heartbeat=False,
    )

    assert contender.acquire_task_lease(ctx.task_id, blocking=False) is False
    assert store.task_lease(ctx.task_id).token == token
    store.close()
    contender.close()


def test_checkpoint_and_events_expose_the_authoritative_write_fence(tmp_path):
    store = RunStore(tmp_path, task_lease_heartbeat=False)
    ctx = _context("observable-fence")
    store.record(ctx, RunPhase.READY, "run_created")
    lease = store.task_lease(ctx.task_id)
    store.record(ctx, RunPhase.PLANNING, "phase_changed")

    checkpoint = json.loads(
        (tmp_path / "runs" / ctx.task_id / "checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    events = store.read_events(ctx.task_id)

    assert checkpoint["write_fence"]["fencing_token"] == lease.token
    assert {event["fencing_token"] for event in events} == {lease.token}
    assert checkpoint["write_fence"]["owner_id"] == lease.owner_id
    store.close()


@pytest.mark.parametrize("operating_mode", ["quality", "balanced", "efficiency"])
def test_operating_modes_cannot_disable_task_fencing(tmp_path, operating_mode):
    gateway = build_gateway(
        base_dir=tmp_path / operating_mode,
        mode="workflow",
        operating_mode=operating_mode,
        validator=False,
        repository_index_enabled=False,
    )
    ctx = gateway.submit("complete one fenced task")
    checkpoint = gateway.runtime.run_store.load(ctx.task_id)
    events = gateway.runtime.run_store.read_events(ctx.task_id)

    assert checkpoint["write_fence"]["fencing_token"] >= 1
    assert all(event["fencing_token"] >= 1 for event in events)
    assert {event["lease_owner_id"] for event in events} == {
        checkpoint["write_fence"]["owner_id"]
    }
    gateway.close()


def test_only_one_concurrent_run_store_claims_a_task(tmp_path):
    stores = [
        RunStore(tmp_path, task_lease_heartbeat=False)
        for _ in range(8)
    ]
    barrier = threading.Barrier(len(stores))
    outcomes: list[tuple[RunStore, bool]] = []
    lock = threading.Lock()

    def claim(store: RunStore) -> None:
        barrier.wait()
        result = store.acquire_task_lease("contended", blocking=False)
        with lock:
            outcomes.append((store, result))

    threads = [threading.Thread(target=claim, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    winners = [store for store, acquired in outcomes if acquired]
    assert len(outcomes) == len(stores)
    assert len(winners) == 1
    for store in stores:
        store.close()


def test_new_process_reclaims_crashed_owner_with_a_higher_token(tmp_path):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_crash_owner, args=(str(tmp_path), queue))
    process.start()
    old_token = queue.get(timeout=5)
    process.join(timeout=5)
    assert process.exitcode == 0

    time.sleep(0.4)
    replacement = RunStore(
        tmp_path,
        task_lease_seconds=0.3,
        task_lease_heartbeat=False,
    )
    assert replacement.acquire_task_lease("crashed-owner", blocking=False)
    new = replacement.task_lease("crashed-owner")

    assert new is not None
    assert new.token > old_token
    replacement.close()


def test_replacement_gateway_fences_a_delayed_model_response_before_tool_dispatch(
    tmp_path,
):
    base = tmp_path / "state"
    workspace = tmp_path / "workspace"
    stale_provider = _BlockedStaleProvider()
    first = build_gateway(
        base_dir=base,
        mode="agent",
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=stale_provider,
        validator=False,
        repository_index_enabled=False,
        task_lease_seconds=0.8,
    )
    # Simulate a partitioned host whose heartbeat can no longer reach the
    # shared lease authority while its model socket eventually returns.
    first.runtime.run_store._task_lease_heartbeat_enabled = False
    task_id = first.submit_async("wait for a delayed model")
    assert stale_provider.entered.wait(timeout=2)

    replacement = build_gateway(
        base_dir=base,
        mode="agent",
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=ScriptedProvider([LLMResponse(text="replacement owner completed")]),
        validator=False,
        repository_index_enabled=False,
        task_lease_seconds=0.8,
    )
    before_expiry = replacement.task_status(task_id)
    assert before_expiry["settled"] is False
    assert before_expiry["attempt_id"] == 1
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = replacement.task_status(task_id)
        if status is not None and status.get("settled") is True:
            break
        time.sleep(0.02)
    assert status["final_output"] == "replacement owner completed"

    stale_provider.release.set()
    deadline = time.monotonic() + 2
    while task_id in first._task_threads and time.monotonic() < deadline:
        time.sleep(0.02)
    events = replacement.runtime.run_store.read_events(task_id)

    assert not (workspace / "stale.txt").exists()
    assert sum(event["event"] == "run_recovered" for event in events) == 1
    assert sum(event["event"] == "run_settled" for event in events) == 1
    assert len({event["fencing_token"] for event in events}) == 2
    assert any(record.event == "run_fenced" for record in first.runtime.audit.records)
    assert task_id not in first._task_errors
    replacement.close()
