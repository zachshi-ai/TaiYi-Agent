"""Large-repository snapshots, retrieval, and artifact-backed compaction."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

from taiyi.context import ContextEngine, RepositoryContextIndex
from taiyi.gateway import build_gateway
from taiyi.llm import LLMErrorKind, LLMMessage, LLMRequestError, LLMResponse, ToolCall
from taiyi.policy import resolve_policy
from taiyi.runtime import RunPhase, TaskContext, TaskState
from taiyi.runtime.context import StepResult
from taiyi.scheduler import PlanStep
from taiyi.tools import SandboxExecutor


def _context(prompt="fix payment retry"):
    return TaskContext(
        task_id="ctx-1",
        prompt=prompt,
        scenario="default",
        policy=resolve_policy("balanced"),
    )


def test_repository_snapshot_refresh_is_incremental_and_source_traceable(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "payments.py").write_text(
        "def charge():\n    return 'ok'\n\ndef retry_payment():\n    return charge()\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Store\nPayment service\n", encoding="utf-8")
    index = RepositoryContextIndex(repo, db_path=tmp_path / "index.sqlite3")

    first = index.refresh()
    assert first.changed_files == 2
    assert first.reused_files == 0
    assert first.complete is True
    assert first.snapshot_id.startswith("sha256:")

    hit = index.retrieve("retry payment implementation", token_budget=2000, max_chunks=5)
    assert hit.snapshot_id == first.snapshot_id
    assert hit.snippets
    snippet = next(item for item in hit.snippets if item.path == "src/payments.py")
    assert snippet.symbol == "retry_payment"
    assert snippet.start_line == 4
    assert snippet.digest.startswith("sha256:")
    assert "src/payments.py:4-5" in hit.render()

    second = index.refresh()
    assert second.changed_files == 0
    assert second.reused_files == 2
    assert second.snapshot_id == first.snapshot_id

    (repo / "README.md").write_text("# Store\nReliable payment service\n", encoding="utf-8")
    third = index.refresh()
    assert third.changed_files == 1
    assert third.reused_files == 1
    assert third.snapshot_id != first.snapshot_id


def test_repository_index_reports_bounded_partial_inventory(tmp_path):
    repo = tmp_path / "huge"
    repo.mkdir()
    for number in range(5):
        (repo / f"file_{number}.txt").write_text(f"needle {number}\n", encoding="utf-8")
    index = RepositoryContextIndex(repo, max_files=3)

    result = index.refresh()

    assert result.file_count == 3
    assert result.omitted_files == 2
    assert result.complete is False


def test_large_inventory_reuses_all_but_one_file_and_persists_index(tmp_path):
    repo = tmp_path / "large-repo"
    repo.mkdir()
    for number in range(1200):
        (repo / f"module_{number:04d}.py").write_text(
            f"def symbol_{number}():\n    return 'value-{number}'\n", encoding="utf-8"
        )
    db = tmp_path / "context" / "repositories.sqlite3"
    index = RepositoryContextIndex(repo, db_path=db, max_files=2000)
    first = index.refresh()
    index.close()

    reopened = RepositoryContextIndex(repo, db_path=db, max_files=2000)
    assert reopened.latest().snapshot_id == first.snapshot_id
    (repo / "module_0777.py").write_text(
        "def target_symbol_777():\n    return 'changed-target'\n", encoding="utf-8"
    )
    second = reopened.refresh()
    hit = reopened.retrieve("target_symbol_777", token_budget=1000, max_chunks=3)

    assert second.changed_files == 1
    assert second.reused_files == 1199
    assert second.snapshot_id != first.snapshot_id
    assert hit.snippets[0].path == "module_0777.py"
    assert "changed-target" in hit.snippets[0].content


def test_snapshot_identity_changes_when_git_head_changes_without_file_changes(tmp_path):
    repo = tmp_path / "git-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "TaiYi Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "taiyi@example.test"], check=True)
    (repo / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "service.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    index = RepositoryContextIndex(repo)
    first = index.refresh()

    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-qm", "checkpoint"], check=True)
    second = index.refresh()

    assert first.git_head != second.git_head
    assert first.snapshot_id != second.snapshot_id
    assert second.changed_files == 0
    assert second.reused_files == 1


def test_three_modes_change_repository_and_recent_context_budgets_only():
    quality = resolve_policy("quality")
    balanced = resolve_policy("balanced")
    efficiency = resolve_policy("efficiency")

    assert quality.repository_context_tokens > balanced.repository_context_tokens > efficiency.repository_context_tokens
    assert quality.repository_context_chunks > balanced.repository_context_chunks > efficiency.repository_context_chunks
    assert quality.context_keep_recent_tokens > balanced.context_keep_recent_tokens > efficiency.context_keep_recent_tokens
    assert quality.max_context_recovery_attempts >= balanced.max_context_recovery_attempts


def test_compaction_preserves_invariants_pairs_and_full_source_artifact(tmp_path):
    ctx = _context("repair the checkout flow")
    ctx.step_results.append(StepResult(
        step=PlanStep("shell:pytest", ["tests/test_checkout.py"]),
        verdict="ALLOW",
        output="failed before the fix",
        executed=True,
    ))
    messages = [
        LLMMessage("system", "immutable governance"),
        LLMMessage("system", "Task contract: checkout must pass"),
        LLMMessage("user", "old session question " + "x" * 4000),
        LLMMessage("assistant", "old session answer " + "y" * 4000),
        LLMMessage("user", ctx.prompt),
        LLMMessage("assistant", "tool_call: shell:pytest ['tests/test_checkout.py']"),
        LLMMessage("user", "[tool result] shell:pytest\n" + "failure\n" * 2000),
        LLMMessage("assistant", "tool_call: file:read ['src/checkout.py']"),
        LLMMessage("user", "[tool result] file:read\nrecent observation"),
    ]
    engine = ContextEngine(
        base_dir=tmp_path,
        context_window_tokens=5000,
        response_reserve_tokens=1000,
        tool_result_max_tokens=500,
    )

    assembled = engine.assemble(ctx, messages, force_compaction=True)

    assert assembled.compaction is not None
    assert assembled.estimated_tokens <= assembled.prompt_budget_tokens
    canonical = assembled.canonical_messages
    assert any(message.content == "immutable governance" for message in canonical)
    assert any(message.content == ctx.prompt for message in canonical)
    assert any(message.content.startswith("[TaiYi structured compaction v1]") for message in canonical)
    # A tool result is never kept without its immediately preceding call.
    for index, message in enumerate(canonical):
        if message.content.startswith("[tool result]"):
            assert index > 0 and canonical[index - 1].content.startswith("tool_call:")

    artifact = assembled.compaction["artifact"]
    payload = json.loads(Path(artifact).read_text(encoding="utf-8"))
    assert payload["source_digest"] == assembled.compaction["source_digest"]
    assert len(payload["messages"]) == len(messages)
    assert "old session question" in payload["messages"][2]["content"]


def test_large_tool_result_is_clipped_only_in_projection():
    ctx = _context()
    huge = "[tool result] shell:test\n" + "z" * 20_000
    messages = [LLMMessage("system", "rules"), LLMMessage("user", ctx.prompt), LLMMessage("user", huge)]
    engine = ContextEngine(
        context_window_tokens=20_000,
        response_reserve_tokens=1000,
        tool_result_max_tokens=500,
    )

    assembled = engine.assemble(ctx, messages)

    assert assembled.canonical_messages[-1].content == huge
    assert len(assembled.messages[-1].content) < len(huge)
    assert "projection truncated" in assembled.messages[-1].content


def test_cjk_tool_result_obeys_token_budget_and_repeated_compaction_keeps_state(tmp_path):
    ctx = _context("保留历史约束")
    huge = "[tool result] shell:test\n" + "错误上下文" * 4000
    messages = [
        LLMMessage("system", "frozen contract"),
        LLMMessage("user", ctx.prompt),
        LLMMessage("assistant", "tool_call: shell:test []"),
        LLMMessage("user", huge),
    ]
    engine = ContextEngine(
        base_dir=tmp_path,
        context_window_tokens=6000,
        response_reserve_tokens=1000,
        tool_result_max_tokens=500,
    )

    projection = engine.assemble(ctx, messages)
    first = engine.assemble(ctx, messages, force_compaction=True)
    next_messages = [
        *first.canonical_messages,
        LLMMessage("assistant", "tool_call: shell:verify []"),
        LLMMessage("user", "[tool result] shell:verify\nverified"),
    ]
    second = engine.assemble(ctx, next_messages, force_compaction=True)

    projected_tool_result = next(
        item.content for item in projection.messages if item.content.startswith("[tool result]")
    )
    assert len(projected_tool_result) < len(huge)
    assert projection.estimated_tokens <= projection.prompt_budget_tokens
    second_summary = next(
        item.content for item in second.canonical_messages
        if item.content.startswith("[TaiYi structured compaction v1]")
    )
    assert "Previous compacted state (bounded)" in second_summary
    second_artifact = json.loads(Path(second.compaction["artifact"]).read_text(encoding="utf-8"))
    assert second_artifact["parent_compaction_id"] == first.compaction["compaction_id"]


class _SequenceProvider:
    name = "context-test"
    model = "context-test"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.seen = []

    def complete(self, messages, *, tools=None):
        self.seen.append(list(messages))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_agent_indexes_sandbox_repository_and_injects_budgeted_source_context(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "checkout.py").write_text(
        "def retry_checkout():\n    return 'stable'\n", encoding="utf-8"
    )
    provider = _SequenceProvider([LLMResponse(text="located the implementation")])
    gateway = build_gateway(
        base_dir=tmp_path / "state",
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=provider,
        validator=False,
    )

    ctx = gateway.submit("inspect retry_checkout in src/checkout.py")

    assert ctx.state is TaskState.COMPLETED
    assert ctx.repository_context["status"] == "ready"
    assert ctx.repository_context["snapshot_id"].startswith("sha256:")
    assert ctx.context_state["repository_snippets"] > 0
    visible = "\n".join(message.content for message in provider.seen[0])
    assert "src/checkout.py:1-2" in visible
    assert "def retry_checkout" in visible
    repository_messages = [
        message
        for message in provider.seen[0]
        if "Repository context (retrieved from an immutable indexed snapshot)" in message.content
    ]
    assert len(repository_messages) == 1
    assert repository_messages[0].role == "user"
    assert provider.seen[0][-1].role == "user"
    assert provider.seen[0][-1].content == "inspect retry_checkout in src/checkout.py"
    events = gateway.runtime.run_store.read_events(ctx.task_id)
    assert any(event["phase"] == RunPhase.INDEXING.value for event in events)
    assert any(event["event"] == "repository_index_finished" for event in events)


def test_repository_index_failure_is_observable_degradation_not_false_context(tmp_path):
    missing = tmp_path / "missing-workspace"
    context_engine = ContextEngine(repository=RepositoryContextIndex(missing))
    provider = _SequenceProvider([LLMResponse(text="continue with explicit tools")])
    gateway = build_gateway(
        base_dir=tmp_path / "state",
        provider=provider,
        context_engine=context_engine,
        validator=False,
    )

    ctx = gateway.submit("inspect the workspace")

    assert ctx.state is TaskState.COMPLETED
    assert ctx.repository_context["status"] == "degraded"
    assert "does not exist" in ctx.repository_context["error"]
    assert len(provider.seen) == 1
    events = gateway.runtime.run_store.read_events(ctx.task_id)
    assert any(event["event"] == "repository_index_failed" for event in events)


def test_agent_refreshes_snapshot_after_a_governed_tool_may_change_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "service.py"
    target.write_text("STATE = 'old_value'\n", encoding="utf-8")
    code = "from pathlib import Path; Path('service.py').write_text(\"STATE = 'new_value'\\n\")"
    provider = _SequenceProvider([
        LLMResponse(tool_calls=[ToolCall("shell:python3", ["-c", code])]),
        LLMResponse(text="updated and observed"),
    ])
    gateway = build_gateway(
        base_dir=tmp_path / "state",
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=provider,
        validator=False,
    )

    ctx = gateway.submit("change service.py from old_value to new_value")

    assert ctx.state is TaskState.COMPLETED
    assert target.read_text(encoding="utf-8") == "STATE = 'new_value'\n"
    first_snapshot = next(
        event["payload"]["snapshot"]["snapshot_id"]
        for event in gateway.runtime.run_store.read_events(ctx.task_id)
        if event["event"] == "repository_index_finished"
    )
    assert ctx.repository_context["snapshot_id"] != first_snapshot
    second_turn = "\n".join(message.content for message in provider.seen[1])
    assert "new_value" in second_turn


def test_provider_context_overflow_compacts_and_retries_without_replaying_tool(tmp_path):
    overflow = LLMRequestError(
        LLMErrorKind.CONTEXT_OVERFLOW,
        "injected provider context overflow",
        retryable=False,
    )
    provider = _SequenceProvider([
        LLMResponse(tool_calls=[ToolCall("echo", ["once"])]),
        overflow,
        LLMResponse(text="recovered after compaction"),
    ])
    gateway = build_gateway(base_dir=tmp_path, provider=provider, validator=False)

    ctx = gateway.submit("perform one action then finish", operating_mode="balanced")

    assert ctx.state is TaskState.SIMULATED
    assert ctx.executed_action_count == 1
    assert len(ctx.executed_steps) == 1
    assert len(provider.seen) == 3
    recovered_prompt = "\n".join(message.content for message in provider.seen[2])
    assert "[TaiYi structured compaction v1]" in recovered_prompt
    assert "do not repeat executed effects" in recovered_prompt
    assert ctx.context_state["compaction_count"] == 1
    assert ctx.context_state["overflow_recoveries"] == 1
    events = gateway.runtime.run_store.read_events(ctx.task_id)
    assert sum(event["event"] == "context_compacted" for event in events) == 1
    assert sum(event["event"] == "tool_started" for event in events) == 1


def test_context_overflow_recovery_is_bounded_by_mode(tmp_path):
    def overflow():
        return LLMRequestError(
            LLMErrorKind.CONTEXT_OVERFLOW, "still too large", retryable=False
        )

    provider = _SequenceProvider([overflow(), overflow(), LLMResponse(text="must not run")])
    gateway = build_gateway(base_dir=tmp_path, provider=provider, validator=False)

    ctx = gateway.submit("small prompt", operating_mode="efficiency")

    assert ctx.state is TaskState.FAILED
    assert ctx.failure_kind == "CONTEXT_OVERFLOW"
    assert len(provider.seen) == 2  # initial request + one mode-budgeted recovery
    assert not any(record.event == "llm_retry_scheduled" for record in gateway.runtime.audit.records)


def test_frozen_goal_that_cannot_fit_fails_in_compacting_phase_without_calling_model(tmp_path):
    provider = _SequenceProvider([LLMResponse(text="must not be called")])
    gateway = build_gateway(
        base_dir=tmp_path,
        provider=provider,
        validator=False,
        context_window_tokens=4096,
        context_response_reserve_tokens=1000,
    )

    ctx = gateway.submit("不可丢弃的任务目标" * 5000, operating_mode="efficiency")

    assert ctx.state is TaskState.FAILED
    assert ctx.failure_kind == "CONTEXT_OVERFLOW"
    assert provider.seen == []
    events = gateway.runtime.run_store.read_events(ctx.task_id)
    failure = next(event for event in events if event["event"] == "run_failed")
    assert failure["payload"]["failed_phase"] == RunPhase.COMPACTING.value
    assert any(event["event"] == "context_budget_exhausted" for event in events)


def test_workflow_planner_receives_repository_context_and_recovers_overflow(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "billing.py").write_text(
        "def reconcile_invoice():\n    return True\n", encoding="utf-8"
    )
    overflow = LLMRequestError(
        LLMErrorKind.CONTEXT_OVERFLOW, "planner prompt too large", retryable=False
    )
    provider = _SequenceProvider([overflow, LLMResponse(text="repository inspected")])
    gateway = build_gateway(
        base_dir=tmp_path / "state",
        mode="workflow",
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=provider,
        validator=False,
    )

    ctx = gateway.submit("inspect reconcile_invoice in billing.py", operating_mode="balanced")

    assert ctx.state is TaskState.COMPLETED
    assert len(provider.seen) == 2
    assert "def reconcile_invoice" in "\n".join(m.content for m in provider.seen[0])
    assert all(
        "Repository context (retrieved from an immutable indexed snapshot)" not in message.content
        for message in provider.seen[0]
        if message.role == "system"
    )
    assert provider.seen[0][-1].role == "user"
    assert "inspect reconcile_invoice in billing.py" in provider.seen[0][-1].content
    events = gateway.runtime.run_store.read_events(ctx.task_id)
    assert any(event["phase"] == RunPhase.INDEXING.value for event in events)
    assert any(event["event"] == "context_projection_reduced" for event in events)
    assert not any(record.event == "llm_retry_scheduled" for record in gateway.runtime.audit.records)


class _CrashProvider:
    name = "context-crash"
    model = "context-crash"

    def __init__(self):
        self.seen = []

    def complete(self, messages, *, tools=None):
        self.seen.append(list(messages))
        raise SystemExit("fault injection: context owner process exited")


def _wait_settled(base: Path, task_id: str, timeout: float = 5.0):
    path = base / "runs" / task_id / "checkpoint.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if checkpoint["context"]["phase"] == RunPhase.SETTLED.value:
            return checkpoint
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not settle")


def test_restart_replays_frozen_repository_projection_not_changed_workspace(tmp_path):
    base = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "policy.py"
    source.write_text("POLICY = 'original_snapshot'\n", encoding="utf-8")
    crashing = _CrashProvider()
    first = build_gateway(
        base_dir=base,
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=crashing,
        validator=False,
    )

    try:
        first.submit("inspect original_snapshot in policy.py")
    except SystemExit:
        pass
    else:
        raise AssertionError("fault injection did not exit")
    checkpoint_path = next((base / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    assert interrupted["continuation"]["model_messages"]
    source.write_text("POLICY = 'changed_after_crash'\n", encoding="utf-8")
    first.runtime.run_store.release_task_lease(task_id)

    recovered = _SequenceProvider([LLMResponse(text="continued frozen turn")])
    restarted = build_gateway(
        base_dir=base,
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=recovered,
        validator=False,
    )
    settled = _wait_settled(base, task_id)

    assert settled["context"]["state"] == TaskState.COMPLETED.value
    replayed = "\n".join(message.content for message in recovered.seen[0])
    assert "original_snapshot" in replayed
    assert "changed_after_crash" not in replayed
    thread = restarted.runtime._recovery_threads.get(task_id)
    if thread is not None:
        thread.join(timeout=2)


def test_workflow_restart_replays_frozen_repository_projection(tmp_path):
    base = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "routing.py"
    source.write_text("ROUTE = 'frozen_route'\n", encoding="utf-8")
    first = build_gateway(
        base_dir=base,
        mode="workflow",
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=_CrashProvider(),
        validator=False,
    )

    try:
        first.submit("inspect frozen_route in routing.py")
    except SystemExit:
        pass
    else:
        raise AssertionError("fault injection did not exit")
    checkpoint_path = next((base / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    assert "frozen_route" in interrupted["continuation"]["model_prompt"]
    source.write_text("ROUTE = 'changed_route'\n", encoding="utf-8")
    first.runtime.run_store.release_task_lease(task_id)

    recovered = _SequenceProvider([LLMResponse(text="workflow recovered")])
    restarted = build_gateway(
        base_dir=base,
        mode="workflow",
        executor=SandboxExecutor(workspace, job_dir=tmp_path / "jobs"),
        provider=recovered,
        validator=False,
    )
    settled = _wait_settled(base, task_id)

    assert settled["context"]["state"] == TaskState.COMPLETED.value
    replayed = "\n".join(message.content for message in recovered.seen[0])
    assert "frozen_route" in replayed
    assert "changed_route" not in replayed
    thread = restarted.runtime._recovery_threads.get(task_id)
    if thread is not None:
        thread.join(timeout=2)


def test_restart_after_overflow_compaction_keeps_one_tool_effect(tmp_path):
    base = tmp_path / "state"
    overflow = LLMRequestError(
        LLMErrorKind.CONTEXT_OVERFLOW, "injected overflow", retryable=False
    )
    first_provider = _SequenceProvider([
        LLMResponse(tool_calls=[ToolCall("echo", ["one-effect"])]),
        overflow,
        SystemExit("fault injection: exit after compacted prompt checkpoint"),
    ])
    first = build_gateway(base_dir=base, provider=first_provider, validator=False)

    try:
        first.submit("perform exactly one effect then complete")
    except SystemExit:
        pass
    else:
        raise AssertionError("fault injection did not exit")
    checkpoint_path = next((base / "runs").glob("*/checkpoint.json"))
    interrupted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    task_id = interrupted["context"]["task_id"]
    assert interrupted["context"]["phase"] == RunPhase.LLM_WAITING.value
    frozen = "\n".join(item["content"] for item in interrupted["continuation"]["model_messages"])
    assert "[TaiYi structured compaction v1]" in frozen
    first.runtime.run_store.release_task_lease(task_id)

    recovered = _SequenceProvider([LLMResponse(text="finished after restart")])
    restarted = build_gateway(base_dir=base, provider=recovered, validator=False)
    settled = _wait_settled(base, task_id)

    assert settled["context"]["state"] == TaskState.SIMULATED.value
    assert settled["context"]["executed_action_count"] == 1
    events = restarted.runtime.run_store.read_events(task_id)
    assert sum(event["event"] == "tool_started" for event in events) == 1
    assert sum(event["event"] == "context_compacted" for event in events) == 1
    thread = restarted.runtime._recovery_threads.get(task_id)
    if thread is not None:
        thread.join(timeout=2)
