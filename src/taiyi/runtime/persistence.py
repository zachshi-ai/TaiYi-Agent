"""Append-only run events and atomic task checkpoints.

The audit chain proves what happened. This store serves a different purpose: it
persists enough typed runtime state to recover a suspended task after process
restart. Checkpoints are atomic JSON documents; events are fsync'd JSONL.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from taiyi.llm.base import LLMMessage
from taiyi.policy import EvidenceLedger, EvidenceRecord
from taiyi.runtime.context import StepResult, TaskContext
from taiyi.runtime.effects import EffectRecord
from taiyi.runtime.leases import FencedLease, FencedLeaseLost, FencedLeaseStore
from taiyi.runtime.protocol import CheckpointIncompatibleError, RunPhase
from taiyi.runtime.quality import prepare_quality_contract
from taiyi.runtime.state import TaskState
from taiyi.scheduler import ExecutionPlan, PlanStep
from taiyi.policy import resolve_policy

CHECKPOINT_SCHEMA = "taiyi.run-checkpoint/v1"
EVENT_SCHEMA = "taiyi.run-event/v1"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")
TASK_LEASE_NAMESPACE = "task"
_UNSCOPED_RELEASE = object()


def _step_to_dict(step: PlanStep) -> dict[str, Any]:
    return {"tool": step.tool, "args": list(step.args)}


def _step_from_dict(data: dict[str, Any]) -> PlanStep:
    return PlanStep(tool=str(data["tool"]), args=[str(arg) for arg in data.get("args", [])])


def serialize_messages(messages: Iterable[LLMMessage]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages]


def deserialize_messages(messages: Iterable[dict[str, Any]]) -> list[LLMMessage]:
    return [LLMMessage(str(message["role"]), str(message["content"])) for message in messages]


def checkpoint_digest(context: dict[str, Any], continuation: dict | None) -> str:
    """Bind a checkpoint to the exact context and continuation it will resume."""

    payload = {
        "schema_version": CHECKPOINT_SCHEMA,
        "context": context,
        "continuation": continuation,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def serialize_context(ctx: TaskContext) -> dict[str, Any]:
    plan = None
    if ctx.plan is not None:
        plan = {
            "skill_name": ctx.plan.skill_name,
            "steps": [_step_to_dict(step) for step in ctx.plan.steps],
            "rationale": ctx.plan.rationale,
            "provider_model": ctx.plan.provider_model,
            "planner_output": ctx.plan.planner_output,
        }
    return {
        "task_id": ctx.task_id,
        "runtime_mode": ctx.runtime_mode,
        "prompt": ctx.prompt,
        "scenario": ctx.scenario,
        "session_id": ctx.session_id,
        "user_id": ctx.user_id,
        "channel": ctx.channel,
        "state": ctx.state.value,
        "phase": ctx.phase.value,
        "attempt_id": ctx.attempt_id,
        "checkpoint_revision": ctx.checkpoint_revision,
        "failure_kind": ctx.failure_kind,
        "plan": plan,
        "step_results": [result.to_dict() for result in ctx.step_results],
        "final_output": ctx.final_output,
        "error": ctx.error,
        "approval_id": ctx.approval_id,
        "round": ctx.round,
        "executed_action_count": ctx.executed_action_count,
        "validation_attempts": ctx.validation_attempts,
        "validation_summary": ctx.validation_summary,
        "operating_mode": ctx.operating_mode,
        "execution_environment": ctx.execution_environment,
        "selected_skill": ctx.selected_skill,
        "scenario_definition": ctx.scenario_definition,
        "skill_instructions": ctx.skill_instructions,
        "provider_route": ctx.provider_route,
        "repository_context": ctx.repository_context,
        "context_state": ctx.context_state,
        "effects": [effect.to_dict() for effect in ctx.effects],
        "contract": ctx.contract.to_dict() if ctx.contract else None,
        "evidence": ctx.evidence.to_dict(),
        "created_at": ctx.created_at,
        "updated_at": ctx.updated_at,
    }


def restore_context(snapshot: dict[str, Any], *, validator=None, value_stream=None) -> TaskContext:
    """Rehydrate a context and fail closed if its frozen contract has drifted."""

    policy = resolve_policy(snapshot.get("operating_mode"), scenario=str(snapshot["scenario"]))
    contract, checklist = prepare_quality_contract(
        validator=validator,
        prompt=str(snapshot["prompt"]),
        scenario=str(snapshot["scenario"]),
        policy=policy,
        selected_skill=snapshot.get("selected_skill"),
    )
    saved_contract = snapshot.get("contract") or {}
    saved_contract_id = saved_contract.get("contract_id")
    if saved_contract_id and contract.contract_id != saved_contract_id:
        raise CheckpointIncompatibleError(
            "frozen task contract changed since checkpoint; refusing automatic resume"
        )

    plan_data = snapshot.get("plan")
    plan = None
    if plan_data:
        plan = ExecutionPlan(
            skill_name=plan_data.get("skill_name"),
            steps=[_step_from_dict(step) for step in plan_data.get("steps", [])],
            rationale=str(plan_data.get("rationale", "")),
            provider_model=plan_data.get("provider_model"),
            planner_output=plan_data.get("planner_output"),
        )

    step_results = [
        StepResult(
            step=_step_from_dict(item),
            verdict=str(item.get("verdict", "")),
            reason=str(item.get("reason", "")),
            matched_rule_id=item.get("matched_rule_id"),
            output=item.get("output"),
            executed=bool(item.get("executed", False)),
            stdout_artifact=item.get("stdout_artifact"),
            stderr_artifact=item.get("stderr_artifact"),
            job_id=item.get("job_id"),
            exit_code=item.get("exit_code"),
            signal=item.get("signal"),
            failure_kind=item.get("failure_kind"),
            timeout_kind=item.get("timeout_kind"),
            stdout_bytes=int(item.get("stdout_bytes", 0)),
            stderr_bytes=int(item.get("stderr_bytes", 0)),
            stdout_artifact_bytes=int(item.get("stdout_artifact_bytes", 0)),
            stderr_artifact_bytes=int(item.get("stderr_artifact_bytes", 0)),
            stdout_digest=item.get("stdout_digest"),
            stderr_digest=item.get("stderr_digest"),
            stdout_artifact_truncated=bool(
                item.get("stdout_artifact_truncated", False)
            ),
            stderr_artifact_truncated=bool(
                item.get("stderr_artifact_truncated", False)
            ),
            output_truncated=bool(item.get("output_truncated", False)),
            termination_reason=item.get("termination_reason"),
            termination_escalated=bool(item.get("termination_escalated", False)),
            owned_process_group_settled=item.get("owned_process_group_settled"),
            duration_seconds=item.get("duration_seconds"),
            error=item.get("error"),
            operation_id=item.get("operation_id"),
            effect_status=item.get("effect_status"),
            effect_evidence=item.get("effect_evidence"),
            original_failure_kind=item.get("original_failure_kind"),
        )
        for item in snapshot.get("step_results", [])
    ]
    evidence = EvidenceLedger(records=[
        EvidenceRecord(**record) for record in (snapshot.get("evidence") or {}).get("records", [])
    ])
    ctx = TaskContext(
        task_id=str(snapshot["task_id"]),
        runtime_mode=str(snapshot.get("runtime_mode", "workflow")),
        prompt=str(snapshot["prompt"]),
        scenario=str(snapshot["scenario"]),
        session_id=str(snapshot.get("session_id", "s1")),
        user_id=str(snapshot.get("user_id", "u1")),
        channel=str(snapshot.get("channel", "cli")),
        state=TaskState(str(snapshot.get("state", TaskState.PENDING.value))),
        phase=RunPhase(str(snapshot.get("phase", RunPhase.READY.value))),
        attempt_id=int(snapshot.get("attempt_id", 1)),
        checkpoint_revision=int(snapshot.get("checkpoint_revision", 0)),
        failure_kind=snapshot.get("failure_kind"),
        plan=plan,
        step_results=step_results,
        final_output=snapshot.get("final_output"),
        error=snapshot.get("error"),
        approval_id=snapshot.get("approval_id"),
        round=int(snapshot.get("round", 0)),
        executed_action_count=int(snapshot.get("executed_action_count", 0)),
        validation_attempts=int(snapshot.get("validation_attempts", 0)),
        validation_summary=snapshot.get("validation_summary"),
        operating_mode=policy.requested_mode.value,
        execution_environment=str(snapshot.get("execution_environment", "unknown")),
        selected_skill=snapshot.get("selected_skill"),
        scenario_definition=snapshot.get("scenario_definition"),
        skill_instructions=snapshot.get("skill_instructions"),
        policy=policy,
        provider_route=snapshot.get("provider_route"),
        repository_context=snapshot.get("repository_context"),
        context_state=snapshot.get("context_state"),
        effects=[EffectRecord.from_dict(item) for item in snapshot.get("effects", [])],
        contract=contract,
        validation_checklist=checklist,
        evidence=evidence,
        created_at=float(snapshot.get("created_at", time.time())),
        updated_at=float(snapshot.get("updated_at", time.time())),
    )
    if value_stream is not None:
        ctx.goal = value_stream.anchor(ctx.prompt, ctx.scenario)
    return ctx


class TaskLeaseLostError(FencedLeaseLost):
    """A stale Runtime attempted to advance a task after ownership changed."""


class RunStore:
    """Persist typed transitions and the latest recoverable checkpoint per task."""

    def __init__(
        self,
        base_dir: str | Path | None = None,
        *,
        task_lease_seconds: float = 30.0,
        task_lease_owner_id: str | None = None,
        task_lease_clock: Callable[[], float] | None = None,
        task_lease_heartbeat: bool = True,
    ):
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self._lock = threading.RLock()
        self._events_changed = threading.Condition(self._lock)
        self._event_generation = 0
        self._task_lease_seconds = max(0.1, float(task_lease_seconds))
        self._task_lease_heartbeat_enabled = bool(task_lease_heartbeat)
        self._task_leases: dict[str, FencedLease] = {}
        self._lease_stop = threading.Event()
        self._lease_thread: threading.Thread | None = None
        self._lease_store = (
            FencedLeaseStore(
                self.base_dir / "runs" / "task-leases.sqlite3",
                lease_seconds=self._task_lease_seconds,
                owner_id=task_lease_owner_id,
                clock=task_lease_clock,
            )
            if self.base_dir is not None
            else None
        )

    @property
    def persistent(self) -> bool:
        return self.base_dir is not None

    @property
    def task_lease_seconds(self) -> float:
        return self._task_lease_seconds

    def record(
        self,
        ctx: TaskContext,
        phase: RunPhase,
        event: str,
        *,
        continuation: dict[str, Any] | None = None,
        **payload: Any,
    ) -> None:
        with self._lock:
            if event == "run_created":
                if not self.acquire_task_lease(ctx.task_id, blocking=False):
                    raise RuntimeError(f"task {ctx.task_id} is already owned by another runtime")
                ctx._write_lease = self._task_leases.get(ctx.task_id)
            if not self.persistent:
                ctx.phase = phase
                ctx.updated_at = time.time()
                ctx.checkpoint_revision += 1
                return
            lease = self._task_leases.get(ctx.task_id)
            if lease is None or self._lease_store is None:
                raise TaskLeaseLostError(
                    f"task {ctx.task_id} has no active fenced write lease"
                )
            if ctx._write_lease is None:
                raise TaskLeaseLostError(f"task {ctx.task_id} context has no bound write lease")
            elif (ctx._write_lease.namespace, ctx._write_lease.key,
                  ctx._write_lease.owner_id, ctx._write_lease.token) != (
                lease.namespace, lease.key, lease.owner_id, lease.token
            ):
                raise TaskLeaseLostError(
                    f"task {ctx.task_id} context belongs to an earlier lease"
                )
            try:
                with self._lease_store.guard(lease) as refreshed:
                    self._task_leases[ctx.task_id] = refreshed
                    ctx.phase = phase
                    ctx.updated_at = time.time()
                    ctx.checkpoint_revision += 1
                    run_dir = self._run_dir(ctx.task_id)
                    run_dir.mkdir(parents=True, exist_ok=True)
                    event_doc = {
                        "schema_version": EVENT_SCHEMA,
                        "task_id": ctx.task_id,
                        "attempt_id": ctx.attempt_id,
                        "revision": ctx.checkpoint_revision,
                        "timestamp": ctx.updated_at,
                        "event": event,
                        "phase": phase.value,
                        "state": ctx.state.value,
                        "failure_kind": ctx.failure_kind,
                        "fencing_token": refreshed.token,
                        "lease_owner_id": refreshed.owner_id,
                        "payload": payload,
                    }
                    self._append_jsonl(run_dir / "events.jsonl", event_doc)
                    context = serialize_context(ctx)
                    checkpoint = {
                        "schema_version": CHECKPOINT_SCHEMA,
                        "saved_at": ctx.updated_at,
                        "context": context,
                        "continuation": continuation,
                        "write_fence": refreshed.to_dict(),
                        "digest": checkpoint_digest(context, continuation),
                    }
                    self._atomic_json(run_dir / "checkpoint.json", checkpoint)
                    self._event_generation += 1
                    self._events_changed.notify_all()
            except FencedLeaseLost as exc:
                self._task_leases.pop(ctx.task_id, None)
                raise TaskLeaseLostError(str(exc)) from exc
            release_after = phase in {
                RunPhase.SETTLED,
                RunPhase.WAITING_APPROVAL,
                RunPhase.WAITING_INPUT,
            }
            if release_after:
                self.release_task_lease(ctx.task_id, expected=lease)

    def acquire_task_lease(self, task_id: str, *, blocking: bool = True) -> bool:
        """Claim the sole right to advance a persisted task.

        The shared lease carries a monotonically increasing fencing token. A
        process-local map prevents duplicate recovery threads, while the shared
        store rejects concurrent owners and stale writers across processes.
        """

        if not self.persistent:
            return True
        assert self._lease_store is not None
        while True:
            with self._lock:
                if task_id in self._task_leases:
                    return False
                lease = self._lease_store.acquire(TASK_LEASE_NAMESPACE, task_id)
                if lease is not None:
                    self._task_leases[task_id] = lease
                    self._ensure_lease_heartbeat()
                    return True
            if not blocking:
                return False
            time.sleep(min(0.1, self._task_lease_seconds / 3))

    def bind_task_context(self, ctx: TaskContext, *, expected: FencedLease | None) -> None:
        """Bind restored state to the receipt this execution actually acquired."""

        with self._lock:
            lease = self._task_leases.get(ctx.task_id)
            if self.persistent and (lease is None or expected is None or (
                expected.namespace, expected.key, expected.owner_id, expected.token
            ) != (lease.namespace, lease.key, lease.owner_id, lease.token)):
                raise TaskLeaseLostError(f"task {ctx.task_id} acquired lease was replaced before binding")
            ctx._write_lease = expected

    def release_task_lease(
        self, task_id: str, *, expected: FencedLease | None | object = _UNSCOPED_RELEASE
    ) -> None:
        """Release a claim, without letting old execution cleanup revoke its successor.

        Runtime cleanup must pass its captured receipt. Omitting it is reserved
        for store-wide administrative close and explicit claim management.
        """
        if not self.persistent:
            return
        with self._lock:
            lease = self._task_leases.get(task_id)
            if lease is None or self._lease_store is None:
                return
            if expected is not _UNSCOPED_RELEASE:
                if not isinstance(expected, FencedLease) or (
                    expected.namespace, expected.key, expected.owner_id, expected.token
                ) != (lease.namespace, lease.key, lease.owner_id, lease.token):
                    return
            self._task_leases.pop(task_id, None)
            self._lease_store.release(lease)
            if not self._task_leases:
                self._lease_stop.set()

    def task_lease(self, task_id: str) -> FencedLease | None:
        """Return this RunStore's active local ownership receipt, if any."""

        with self._lock:
            return self._task_leases.get(task_id)

    def shared_task_lease(self, task_id: str) -> FencedLease | None:
        """Read the current authority row without claiming task ownership."""

        if self._lease_store is None:
            return None
        return self._lease_store.current(TASK_LEASE_NAMESPACE, task_id)

    def close(self) -> None:
        """Stop the shared heartbeat and relinquish all local task ownership."""

        self._lease_stop.set()
        thread = self._lease_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._lock:
            for task_id in tuple(self._task_leases):
                self.release_task_lease(task_id)
            self._lease_thread = None

    def _ensure_lease_heartbeat(self) -> None:
        if self._lease_thread is not None and self._lease_stop.is_set():
            # The previous heartbeat is already signalled to exit but may still
            # be unwinding. Give the new generation its own stop event; the old
            # thread retains the signalled object for its current wait call.
            self._lease_thread = None
            self._lease_stop = threading.Event()
        elif self._lease_thread is not None and not self._lease_thread.is_alive():
            self._lease_thread = None
        if not self._task_lease_heartbeat_enabled or self._lease_thread is not None:
            return
        self._lease_stop.clear()
        thread = threading.Thread(
            target=self._heartbeat_task_leases,
            name="taiyi-task-lease-heartbeat",
            daemon=True,
        )
        self._lease_thread = thread
        thread.start()

    def _heartbeat_task_leases(self) -> None:
        interval = max(0.05, self._task_lease_seconds / 3)
        try:
            while not self._lease_stop.wait(interval):
                with self._lock:
                    if self._lease_store is None:
                        return
                    if not self._task_leases:
                        return
                    for task_id, lease in tuple(self._task_leases.items()):
                        try:
                            self._task_leases[task_id] = self._lease_store.renew(lease)
                        except FencedLeaseLost:
                            self._task_leases.pop(task_id, None)
        finally:
            with self._lock:
                if self._lease_thread is threading.current_thread():
                    self._lease_thread = None

    def load(self, task_id: str) -> dict[str, Any] | None:
        if not self.persistent:
            return None
        path = self._run_dir(task_id) / "checkpoint.json"
        # A local reader must not observe a suspended/settled checkpoint before
        # the writer has completed its matching lease release. Cross-process
        # readers still rely on the atomic file plus the shared lease authority.
        with self._lock:
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            self._validate_checkpoint(data, path)
            return data

    def iter_checkpoints(self) -> Iterable[dict[str, Any]]:
        if not self.persistent:
            return ()
        checkpoints: list[dict[str, Any]] = []
        for path in sorted((self.base_dir / "runs").glob("*/checkpoint.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._validate_checkpoint(data, path)
            except (OSError, json.JSONDecodeError, CheckpointIncompatibleError) as exc:
                checkpoints.append({
                    "_load_error": f"{type(exc).__name__}: {exc}",
                    "context": {"task_id": path.parent.name},
                })
                continue
            checkpoints.append(data)
        return tuple(checkpoints)

    def read_events(self, task_id: str) -> tuple[dict[str, Any], ...]:
        """Return the task's persisted progress stream in append order."""

        if not self.persistent:
            return ()
        path = self._run_dir(task_id) / "events.jsonl"
        with self._lock:
            try:
                raw = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return ()
        lines = raw.splitlines()
        # A reader in another process may observe the last append between bytes.
        # Ignore only that unterminated tail; a malformed completed line is still
        # a hard integrity error.
        if raw and not raw.endswith("\n"):
            lines = lines[:-1]
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CheckpointIncompatibleError(
                    f"invalid run event at {path}:{line_number}"
                ) from exc
            if event.get("schema_version") != EVENT_SCHEMA or event.get("task_id") != task_id:
                raise CheckpointIncompatibleError(
                    f"run event identity mismatch at {path}:{line_number}"
                )
            events.append(event)
        return tuple(events)

    @property
    def event_generation(self) -> int:
        """Process-local change token used to block without losing durable truth."""

        with self._lock:
            return self._event_generation

    def event_page(
        self,
        task_id: str,
        *,
        after: int = 0,
        limit: int = 200,
    ) -> tuple[tuple[dict[str, Any], ...], int, bool]:
        """Read one revision-addressed page from the persisted event journal."""

        cursor = max(0, int(after))
        page_limit = min(1000, max(1, int(limit)))
        pending = tuple(
            event
            for event in self.read_events(task_id)
            if int(event.get("revision", 0)) > cursor
        )
        selected = pending[:page_limit]
        next_after = int(selected[-1]["revision"]) if selected else cursor
        return selected, next_after, len(pending) > len(selected)

    def wait_for_events(
        self,
        task_id: str,
        *,
        after: int = 0,
        limit: int = 200,
        timeout: float = 15.0,
        cross_process_interval: float = 1.0,
    ) -> tuple[tuple[dict[str, Any], ...], int, bool]:
        """Block until a newer durable revision exists or the deadline expires.

        Local writers notify the condition immediately. A bounded fallback read
        observes writers in a different Gateway process; the JSONL journal, not
        the condition, remains the authoritative source across restart.
        """

        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._lock:
                observed_generation = self._event_generation
            page = self.event_page(task_id, after=after, limit=limit)
            if page[0] or page[2]:
                return page
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return page
            with self._events_changed:
                if self._event_generation != observed_generation:
                    continue
                self._events_changed.wait_for(
                    lambda: self._event_generation != observed_generation,
                    timeout=min(
                        remaining,
                        max(0.05, float(cross_process_interval)),
                    ),
                )

    @staticmethod
    def _validate_checkpoint(data: dict[str, Any], path: Path) -> None:
        if data.get("schema_version") != CHECKPOINT_SCHEMA:
            raise CheckpointIncompatibleError(f"unsupported checkpoint schema in {path}")
        context = data.get("context")
        if not isinstance(context, dict):
            raise CheckpointIncompatibleError(f"checkpoint has no context in {path}")
        expected = checkpoint_digest(context, data.get("continuation"))
        if data.get("digest") != expected:
            raise CheckpointIncompatibleError(f"checkpoint digest mismatch in {path}")

    def _run_dir(self, task_id: str) -> Path:
        assert self.base_dir is not None
        safe = _SAFE_ID.sub("_", task_id)
        if not safe or safe in {".", ".."}:
            raise ValueError(f"unsafe task id: {task_id!r}")
        return self.base_dir / "runs" / safe

    @staticmethod
    def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _atomic_json(path: Path, data: dict[str, Any]) -> None:
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("x", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def workflow_continuation(approval_id: str, held_index: int, steps: Iterable[PlanStep]) -> dict:
    return {
        "kind": "workflow_approval",
        "approval_id": approval_id,
        "held_index": held_index,
        "steps": [_step_to_dict(step) for step in steps],
    }


def agent_continuation(
    approval_id: str,
    held_index: int,
    messages: Iterable[LLMMessage],
) -> dict:
    return {
        "kind": "agent_approval",
        "approval_id": approval_id,
        "held_index": held_index,
        "messages": serialize_messages(messages),
    }


def continuation_steps(data: dict[str, Any]) -> list[PlanStep]:
    return [_step_from_dict(step) for step in data.get("steps", [])]
