"""Repository index progress, coverage, and restart guarantees."""
from __future__ import annotations

import json
import threading
import time

import pytest

from taiyi.context import (
    ContextEngine,
    RepositoryContextIndex,
    RepositoryIndexJobError,
    RepositoryIndexJobManager,
)
from taiyi.gateway import build_gateway
from taiyi.llm import LLMResponse
from taiyi.runtime import RunPhase, TaskState
from taiyi.tools import SandboxExecutor


class _SequenceProvider:
    name = "repository-resilience-test"
    model = "repository-resilience-test"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.seen = []

    def complete(self, messages, *, tools=None):
        self.seen.append(list(messages))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _InterruptingRepositoryIndex(RepositoryContextIndex):
    def refresh(self, *, progress=None, progress_every_files=250):
        def interrupt(observed):
            if progress is not None:
                progress(observed)
            if observed.processed_files >= 250:
                raise SystemExit("fault injection: gateway exited during repository index")

        return super().refresh(
            progress=interrupt,
            progress_every_files=progress_every_files,
        )


class _CrashAfterIndexAttachEngine(ContextEngine):
    """Inject a gateway-owner crash after the durable worker is checkpointed."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.crashed = False

    def ensure_repository(self, ctx, *, attached=None, **kwargs):
        def crash_after_checkpoint(handle):
            if attached is not None:
                attached(handle)
            if not self.crashed:
                self.crashed = True
                raise SystemExit("fault injection: gateway exited after index attach")

        return super().ensure_repository(
            ctx,
            attached=crash_after_checkpoint,
            **kwargs,
        )


def _write_repository(root, count=600):
    root.mkdir()
    for number in range(count):
        content = (
            "def frozen_large_repo_target():\n    return 'indexed-after-restart'\n"
            if number == 511
            else f"VALUE_{number} = {number}\n"
        )
        (root / f"module_{number:04d}.py").write_text(content, encoding="utf-8")


def _wait_settled(base, task_id, timeout=10):
    path = base / "runs" / task_id / "checkpoint.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if checkpoint["context"]["phase"] == RunPhase.SETTLED.value:
            return checkpoint
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not settle")


def _wait_status(gateway, task_id, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = gateway.task_status(task_id)
        if last is not None and predicate(last):
            return last
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} status did not match; last={last}")


def test_unchanged_path_set_reuses_directory_chunks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "service.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    index = RepositoryContextIndex(repo)

    first = index.refresh()
    second = index.refresh()
    target.write_text("VALUE = 2\n", encoding="utf-8")
    third = index.refresh()
    (repo / "added.py").write_text("ADDED = True\n", encoding="utf-8")
    fourth = index.refresh()

    assert first.directory_chunks_rebuilt is True
    assert second.directory_chunks_rebuilt is False
    assert third.changed_files == 1
    assert third.directory_chunks_rebuilt is False
    assert fourth.directory_chunks_rebuilt is True


def test_repository_refresh_reports_progress_and_rolls_back_interruption(tmp_path):
    repo = tmp_path / "large-progress-repo"
    _write_repository(repo)
    index = RepositoryContextIndex(repo, db_path=tmp_path / "index.sqlite3")
    observed = []

    def interrupt(progress):
        observed.append(progress.to_dict())
        if progress.processed_files >= 250:
            raise SystemExit("fault injection: index owner exited")

    with pytest.raises(SystemExit, match="index owner exited"):
        index.refresh(progress=interrupt)

    assert [item["processed_files"] for item in observed] == [0, 250]
    assert all(item["total_files"] == 600 for item in observed)
    assert index.latest() is None
    assert index.refresh().file_count == 600


def test_repository_context_separates_inventory_and_searchable_coverage(tmp_path):
    repo = tmp_path / "mixed-repo"
    repo.mkdir()
    (repo / "service.py").write_text(
        "def visible_target():\n    return True\n", encoding="utf-8"
    )
    (repo / "opaque.bin").write_bytes(b"\x00\x01\x02")
    index = RepositoryContextIndex(repo)

    result = index.refresh()
    visible = index.retrieve("visible_target", token_budget=1000, max_chunks=3)
    missing = index.retrieve("symbol_that_is_not_searchable", token_budget=1000, max_chunks=3)

    assert result.complete is True
    assert result.to_dict()["inventory_complete"] is True
    assert result.to_dict()["searchable_complete"] is False
    assert result.to_dict()["unsearchable_files"] == 1
    assert visible.unsearchable_files == 1
    assert "Coverage warning" in visible.render()
    assert not missing.snippets
    assert "No matching searchable source chunk" in missing.render()
    assert "Do not infer" in missing.render()


def test_durable_index_generation_coalesces_live_work_then_refreshes_next_task(tmp_path):
    repo = tmp_path / "coalesced-repo"
    _write_repository(repo)
    db = tmp_path / "state" / "context" / "repositories.sqlite3"
    manager = RepositoryIndexJobManager(
        tmp_path / "state" / "context" / "index-runtime",
        heartbeat_interval=0.05,
        poll_interval=0.01,
        worker_progress_delay=0.1,
    )
    barrier = threading.Barrier(2)
    attachments = [[], []]
    results = [None, None]
    errors = []
    engines = [
        ContextEngine(
            repository=RepositoryContextIndex(repo, db_path=db),
            index_jobs=manager,
        )
        for _ in range(2)
    ]

    def index_in_task(slot):
        try:
            ctx = _repository_task_context(f"concurrent-{slot}")
            barrier.wait(timeout=2)
            results[slot] = engines[slot].ensure_repository(
                ctx,
                force=True,
                attached=lambda handle: attachments[slot].append(handle),
            )
        except BaseException as exc:  # make thread failures visible to pytest
            errors.append(exc)

    threads = [threading.Thread(target=index_in_task, args=(slot,)) for slot in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert results[0].snapshot_id == results[1].snapshot_id
    assert attachments[0][0].job_id == attachments[1][0].job_id
    assert attachments[0][0].operation_id.endswith(":generation:0")

    next_attachments = []
    next_engine = ContextEngine(
        repository=RepositoryContextIndex(repo, db_path=db),
        index_jobs=manager,
    )
    next_result = next_engine.ensure_repository(
        _repository_task_context("next-task"),
        force=True,
        attached=lambda handle: next_attachments.append(handle),
    )

    assert next_attachments[0].job_id != attachments[0][0].job_id
    assert next_attachments[0].operation_id.endswith(":generation:1")
    assert next_result.snapshot_id == results[0].snapshot_id
    assert next_result.changed_files == 0
    assert next_result.reused_files == 600


def test_repository_index_worker_failure_has_repository_specific_failure_kind(tmp_path):
    manager = RepositoryIndexJobManager(tmp_path / "index-runtime", poll_interval=0.01)

    with pytest.raises(RepositoryIndexJobError) as raised:
        manager.run(
            repository_root=tmp_path / "missing-repository",
            db_path=tmp_path / "index.sqlite3",
            max_files=100,
            max_file_bytes=100_000,
            chunk_lines=80,
            operation_id="repository:missing:generation:0",
            consumer_id="failure-test",
        )

    assert raised.value.failure_kind == "REPOSITORY_INDEX_FAILED"


def _repository_task_context(task_id):
    from taiyi.policy import resolve_policy
    from taiyi.runtime import TaskContext

    return TaskContext(
        task_id=task_id,
        prompt="inspect the repository",
        scenario="default",
        policy=resolve_policy("balanced"),
    )


def test_async_repository_index_is_observable_and_cancellable(tmp_path):
    base = tmp_path / "state"
    workspace = tmp_path / "workspace"
    _write_repository(workspace)
    manager = RepositoryIndexJobManager(
        base / "context" / "index-runtime",
        heartbeat_interval=0.05,
        poll_interval=0.01,
        worker_progress_delay=0.25,
    )
    provider = _SequenceProvider([LLMResponse(text="must not be called")])
    gateway = build_gateway(
        base_dir=base,
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=provider,
        context_engine=ContextEngine(
            repository=RepositoryContextIndex(
                workspace, db_path=base / "context" / "repositories.sqlite3"
            ),
            base_dir=base,
            index_jobs=manager,
        ),
        validator=False,
    )

    task_id = gateway.submit_async("inspect frozen_large_repo_target")
    indexing = _wait_status(
        gateway,
        task_id,
        lambda status: status["phase"] == RunPhase.INDEXING.value
        and status.get("job", {}).get("status") == "RUNNING",
    )

    assert indexing["settled"] is False
    assert indexing["job"]["job_kind"] == "repository_index"
    assert indexing["continuation"]["kind"] == "agent_continue"
    cancelled = gateway.cancel_task(task_id)
    assert cancelled["cancelled"] is True
    assert cancelled["job"]["job_kind"] == "repository_index"

    settled = _wait_status(
        gateway, task_id, lambda status: status["phase"] == RunPhase.SETTLED.value
    )
    assert settled["state"] == TaskState.FAILED.value
    assert settled["failure_kind"] == "REPOSITORY_INDEX_CANCELLED"
    assert provider.seen == []


def test_cancelling_one_coalesced_task_does_not_cancel_shared_index_job(tmp_path):
    base = tmp_path / "state"
    workspace = tmp_path / "workspace"
    _write_repository(workspace)
    manager = RepositoryIndexJobManager(
        base / "context" / "index-runtime",
        heartbeat_interval=0.05,
        poll_interval=0.01,
        worker_progress_delay=0.25,
    )
    provider = _SequenceProvider([LLMResponse(text="shared index completed")])
    gateway = build_gateway(
        base_dir=base,
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=provider,
        context_engine=ContextEngine(
            repository=RepositoryContextIndex(
                workspace, db_path=base / "context" / "repositories.sqlite3"
            ),
            base_dir=base,
            index_jobs=manager,
        ),
        validator=False,
    )

    first_task = gateway.submit_async("inspect frozen_large_repo_target first")
    second_task = gateway.submit_async("inspect frozen_large_repo_target second")
    first_indexing = _wait_status(
        gateway,
        first_task,
        lambda status: status["phase"] == RunPhase.INDEXING.value
        and status.get("job", {}).get("status") == "RUNNING",
    )
    second_indexing = _wait_status(
        gateway,
        second_task,
        lambda status: status["phase"] == RunPhase.INDEXING.value
        and status.get("job", {}).get("status") == "RUNNING",
    )
    assert first_indexing["job"]["job_id"] == second_indexing["job"]["job_id"]

    cancelled = gateway.cancel_task(first_task)
    assert cancelled["cancelled"] is True
    assert cancelled["shared_job_continues"] is True
    assert cancelled["job"]["status"] == "RUNNING"

    first_settled = _wait_status(
        gateway, first_task, lambda status: status["phase"] == RunPhase.SETTLED.value
    )
    second_settled = _wait_status(
        gateway, second_task, lambda status: status["phase"] == RunPhase.SETTLED.value
    )
    assert first_settled["failure_kind"] == "REPOSITORY_INDEX_CANCELLED"
    assert second_settled["state"] == TaskState.COMPLETED.value
    assert second_settled["final_output"] == "shared index completed"
    assert len(provider.seen) == 1


def test_gateway_restart_reattaches_same_durable_repository_index_job(tmp_path):
    base = tmp_path / "state"
    workspace = tmp_path / "workspace"
    _write_repository(workspace)
    db = base / "context" / "repositories.sqlite3"
    first_manager = RepositoryIndexJobManager(
        base / "context" / "index-runtime",
        heartbeat_interval=0.05,
        poll_interval=0.01,
        worker_progress_delay=0.2,
    )
    unused = _SequenceProvider([LLMResponse(text="must not be called")])
    first = build_gateway(
        base_dir=base,
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=unused,
        context_engine=_CrashAfterIndexAttachEngine(
            repository=RepositoryContextIndex(workspace, db_path=db),
            base_dir=base,
            index_jobs=first_manager,
        ),
        validator=False,
    )

    with pytest.raises(SystemExit, match="gateway exited after index attach"):
        first.submit("inspect frozen_large_repo_target")

    checkpoint_path = next((base / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    original_job_id = interrupted["continuation"]["job_id"]
    assert interrupted["context"]["phase"] == RunPhase.INDEXING.value
    assert interrupted["context"]["repository_context"]["index_generation"] == 0
    assert first_manager.poll(original_job_id).status.value in {"RUNNING", "SUCCEEDED"}
    first.runtime.run_store.release_task_lease(task_id)

    recovered = _SequenceProvider([LLMResponse(text="recovered indexed evidence")])
    second_manager = RepositoryIndexJobManager(
        base / "context" / "index-runtime",
        heartbeat_interval=0.05,
        poll_interval=0.01,
        worker_progress_delay=0.2,
    )
    restarted = build_gateway(
        base_dir=base,
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=recovered,
        context_engine=ContextEngine(
            repository=RepositoryContextIndex(workspace, db_path=db),
            base_dir=base,
            index_jobs=second_manager,
        ),
        validator=False,
    )
    settled = _wait_settled(base, task_id)

    assert settled["context"]["state"] == TaskState.COMPLETED.value
    assert settled["context"]["repository_context"]["file_count"] == 600
    assert "frozen_large_repo_target" in "\n".join(
        message.content for message in recovered.seen[0]
    )
    attached_job_ids = {
        event["payload"]["job_id"]
        for event in restarted.runtime.run_store.read_events(task_id)
        if event["event"] == "repository_index_attached"
    }
    assert attached_job_ids == {original_job_id}
    assert second_manager.jobs.load(original_job_id).operation_id.endswith(":generation:0")
    thread = restarted.runtime._recovery_threads.get(task_id)
    if thread is not None:
        thread.join(timeout=2)


def test_gateway_restart_recovers_index_heartbeat_without_partial_snapshot(tmp_path):
    base = tmp_path / "state"
    workspace = tmp_path / "workspace"
    _write_repository(workspace)
    db = base / "context" / "repositories.sqlite3"
    interrupted_index = _InterruptingRepositoryIndex(workspace, db_path=db)
    unused = _SequenceProvider([LLMResponse(text="must not be called")])
    first = build_gateway(
        base_dir=base,
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=unused,
        context_engine=ContextEngine(repository=interrupted_index, base_dir=base),
        validator=False,
    )

    with pytest.raises(SystemExit, match="gateway exited during repository index"):
        first.submit("inspect frozen_large_repo_target")

    checkpoint_path = next((base / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    events = first.runtime.run_store.read_events(task_id)
    heartbeat = [event for event in events if event["event"] == "repository_index_heartbeat"][-1]
    assert unused.seen == []
    assert interrupted["context"]["phase"] == RunPhase.INDEXING.value
    assert interrupted["continuation"]["kind"] == "agent_continue"
    assert heartbeat["payload"]["progress"]["processed_files"] == 250
    assert interrupted_index.latest() is None
    first.runtime.run_store.release_task_lease(task_id)

    recovered = _SequenceProvider([LLMResponse(text="recovered indexed evidence")])
    restarted = build_gateway(
        base_dir=base,
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=recovered,
        context_engine=ContextEngine(
            repository=RepositoryContextIndex(workspace, db_path=db),
            base_dir=base,
        ),
        validator=False,
    )
    settled = _wait_settled(base, task_id)

    assert settled["context"]["state"] == TaskState.COMPLETED.value
    assert settled["context"]["repository_context"]["file_count"] == 600
    assert "frozen_large_repo_target" in "\n".join(
        message.content for message in recovered.seen[0]
    )
    recovered_events = restarted.runtime.run_store.read_events(task_id)
    assert sum(event["event"] == "run_recovered" for event in recovered_events) == 1
    thread = restarted.runtime._recovery_threads.get(task_id)
    if thread is not None:
        thread.join(timeout=2)
