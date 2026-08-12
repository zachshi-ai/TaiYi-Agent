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
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows falls back to process-local claims
    fcntl = None

from taiyi.llm.base import LLMMessage
from taiyi.policy import EvidenceLedger, EvidenceRecord
from taiyi.runtime.context import StepResult, TaskContext
from taiyi.runtime.protocol import CheckpointIncompatibleError, RunPhase
from taiyi.runtime.quality import prepare_quality_contract
from taiyi.runtime.state import TaskState
from taiyi.scheduler import ExecutionPlan, PlanStep
from taiyi.policy import resolve_policy

CHECKPOINT_SCHEMA = "taiyi.run-checkpoint/v1"
EVENT_SCHEMA = "taiyi.run-event/v1"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")
_PROCESS_LEASE_LOCK = threading.RLock()
_PROCESS_LEASES: set[str] = set()


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
            output_truncated=bool(item.get("output_truncated", False)),
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
        contract=contract,
        validation_checklist=checklist,
        evidence=evidence,
        created_at=float(snapshot.get("created_at", time.time())),
        updated_at=float(snapshot.get("updated_at", time.time())),
    )
    if value_stream is not None:
        ctx.goal = value_stream.anchor(ctx.prompt, ctx.scenario)
    return ctx


class RunStore:
    """Persist typed transitions and the latest recoverable checkpoint per task."""

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self._lock = threading.RLock()
        self._task_leases: dict[str, object] = {}

    @property
    def persistent(self) -> bool:
        return self.base_dir is not None

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
            ctx.phase = phase
            ctx.updated_at = time.time()
            ctx.checkpoint_revision += 1
            if not self.persistent:
                return
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
                "payload": payload,
            }
            self._append_jsonl(run_dir / "events.jsonl", event_doc)
            context = serialize_context(ctx)
            checkpoint = {
                "schema_version": CHECKPOINT_SCHEMA,
                "saved_at": ctx.updated_at,
                "context": context,
                "continuation": continuation,
                "digest": checkpoint_digest(context, continuation),
            }
            self._atomic_json(run_dir / "checkpoint.json", checkpoint)
            if phase in {
                RunPhase.SETTLED,
                RunPhase.WAITING_APPROVAL,
                RunPhase.WAITING_INPUT,
            }:
                self.release_task_lease(ctx.task_id)

    def acquire_task_lease(self, task_id: str, *, blocking: bool = True) -> bool:
        """Claim the sole right to advance a persisted task.

        The open file descriptor owns the POSIX lock, so a process crash releases
        it automatically. The process-local map also prevents duplicate recovery
        threads when file locking is unavailable.
        """

        if not self.persistent:
            return True
        with self._lock:
            if task_id in self._task_leases:
                return False
            run_dir = self._run_dir(task_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            handle = (run_dir / ".task.lock").open("a+b")
            if fcntl is not None:
                flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                try:
                    fcntl.flock(handle.fileno(), flags)
                except BlockingIOError:
                    handle.close()
                    return False
            else:  # pragma: no cover - exercised only on platforms without flock
                key = str((run_dir / ".task.lock").resolve())
                with _PROCESS_LEASE_LOCK:
                    if key in _PROCESS_LEASES:
                        handle.close()
                        return False
                    _PROCESS_LEASES.add(key)
            self._task_leases[task_id] = handle
            return True

    def release_task_lease(self, task_id: str) -> None:
        if not self.persistent:
            return
        with self._lock:
            handle = self._task_leases.pop(task_id, None)
            if handle is None:
                return
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            else:  # pragma: no cover - exercised only on platforms without flock
                key = str((self._run_dir(task_id) / ".task.lock").resolve())
                with _PROCESS_LEASE_LOCK:
                    _PROCESS_LEASES.discard(key)
            handle.close()

    def load(self, task_id: str) -> dict[str, Any] | None:
        if not self.persistent:
            return None
        path = self._run_dir(task_id) / "checkpoint.json"
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
