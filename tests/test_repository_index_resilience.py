"""Repository index progress, coverage, and restart guarantees."""
from __future__ import annotations

import json
import time

import pytest

from taiyi.context import ContextEngine, RepositoryContextIndex
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
