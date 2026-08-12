"""Durable side-effect intent, reconciliation, and replay policy.

The model and tool output are never authorities for whether a mutation happened.
Before dispatch, TaiYi freezes a harness-owned effect policy and (when available)
a read-only verification snapshot.  After a timeout, lost worker, or process
restart, that snapshot decides whether the logical operation was applied, was
not applied, or remains ambiguous.  Only the first two outcomes can progress
automatically; ambiguity requires an explicit human resolution.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from taiyi.scheduler import PlanStep


EFFECT_SCHEMA_VERSION = "taiyi.effect/v1"


class SideEffectClass(str, Enum):
    NONE = "NONE"
    IDEMPOTENT = "IDEMPOTENT"
    REVERSIBLE = "REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"
    UNKNOWN = "UNKNOWN"


class ReplayPolicy(str, Enum):
    SAFE = "SAFE"
    IDEMPOTENCY_KEY = "IDEMPOTENCY_KEY"
    VERIFY_THEN_RETRY = "VERIFY_THEN_RETRY"
    NEVER = "NEVER"


class EffectStatus(str, Enum):
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    EXECUTOR_SUCCEEDED = "EXECUTOR_SUCCEEDED"
    EXECUTOR_FAILED = "EXECUTOR_FAILED"
    CONFIRMED_APPLIED = "CONFIRMED_APPLIED"
    CONFIRMED_NOT_APPLIED = "CONFIRMED_NOT_APPLIED"
    AMBIGUOUS = "AMBIGUOUS"
    ABANDONED = "ABANDONED"


class ObservationStatus(str, Enum):
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"


class RecoveryAction(str, Enum):
    ACCEPT_APPLIED = "ACCEPT_APPLIED"
    RETRY = "RETRY"
    WAIT_FOR_HUMAN = "WAIT_FOR_HUMAN"


class HumanEffectResolution(str, Enum):
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    ABANDON = "abandon"

    @classmethod
    def parse(cls, value: str) -> "HumanEffectResolution":
        try:
            return cls(str(value).strip().casefold())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in cls)
            raise ValueError(f"unknown effect resolution {value!r}; expected: {allowed}") from exc


@dataclass(frozen=True)
class EffectSpec:
    name: str
    side_effect_class: SideEffectClass
    replay_policy: ReplayPolicy
    description: str
    authority: str | None = None

    @property
    def policy_digest(self) -> str:
        payload = json.dumps(
            {
                "schema_version": EFFECT_SCHEMA_VERSION,
                "name": self.name,
                "side_effect_class": self.side_effect_class.value,
                "replay_policy": self.replay_policy.value,
                "description": self.description,
                "authority": self.authority,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _digest(payload.encode("utf-8"))


@dataclass(frozen=True)
class EffectObservation:
    status: ObservationStatus
    authority: str
    evidence: str
    evidence_digest: str
    observed_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        status: ObservationStatus,
        *,
        authority: str,
        evidence: str,
    ) -> "EffectObservation":
        return cls(status, authority, evidence, _digest(evidence.encode("utf-8")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "authority": self.authority,
            "evidence": self.evidence,
            "evidence_digest": self.evidence_digest,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EffectObservation":
        return cls(
            status=ObservationStatus(str(value["status"])),
            authority=str(value["authority"]),
            evidence=str(value["evidence"]),
            evidence_digest=str(value["evidence_digest"]),
            observed_at=float(value.get("observed_at", 0.0)),
        )


@dataclass
class EffectRecord:
    operation_id: str
    step_digest: str
    policy_digest: str
    spec_name: str
    side_effect_class: SideEffectClass
    replay_policy: ReplayPolicy
    authority: str | None
    idempotency_key: str
    verification_snapshot: dict[str, Any]
    status: EffectStatus = EffectStatus.PREPARED
    attempt_count: int = 0
    recovery_attempts: int = 0
    observations: list[EffectObservation] = field(default_factory=list)
    executor_failure_kind: str | None = None
    human_resolution: str | None = None
    human_note: str | None = None
    prepared_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EFFECT_SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "step_digest": self.step_digest,
            "policy_digest": self.policy_digest,
            "spec_name": self.spec_name,
            "side_effect_class": self.side_effect_class.value,
            "replay_policy": self.replay_policy.value,
            "authority": self.authority,
            "idempotency_key": self.idempotency_key,
            "verification_snapshot": dict(self.verification_snapshot),
            "status": self.status.value,
            "attempt_count": self.attempt_count,
            "recovery_attempts": self.recovery_attempts,
            "observations": [item.to_dict() for item in self.observations],
            "executor_failure_kind": self.executor_failure_kind,
            "human_resolution": self.human_resolution,
            "human_note": self.human_note,
            "prepared_at": self.prepared_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EffectRecord":
        version = value.get("schema_version", EFFECT_SCHEMA_VERSION)
        if version != EFFECT_SCHEMA_VERSION:
            raise ValueError(f"unsupported effect schema: {version!r}")
        return cls(
            operation_id=str(value["operation_id"]),
            step_digest=str(value["step_digest"]),
            policy_digest=str(value["policy_digest"]),
            spec_name=str(value["spec_name"]),
            side_effect_class=SideEffectClass(str(value["side_effect_class"])),
            replay_policy=ReplayPolicy(str(value["replay_policy"])),
            authority=(str(value["authority"]) if value.get("authority") else None),
            idempotency_key=str(value["idempotency_key"]),
            verification_snapshot=dict(value.get("verification_snapshot") or {}),
            status=EffectStatus(str(value.get("status", EffectStatus.PREPARED.value))),
            attempt_count=int(value.get("attempt_count", 0)),
            recovery_attempts=int(value.get("recovery_attempts", 0)),
            observations=[
                EffectObservation.from_dict(item)
                for item in value.get("observations", [])
            ],
            executor_failure_kind=value.get("executor_failure_kind"),
            human_resolution=value.get("human_resolution"),
            human_note=value.get("human_note"),
            prepared_at=float(value.get("prepared_at", 0.0)),
            updated_at=float(value.get("updated_at", 0.0)),
        )


@runtime_checkable
class EffectAuthority(Protocol):
    name: str

    def freeze(self, step: PlanStep) -> Mapping[str, Any]: ...

    def observe(
        self,
        step: PlanStep,
        snapshot: Mapping[str, Any],
    ) -> EffectObservation: ...


class FileWriteAuthority:
    """Independently compare a fixed-content file write with its pre-state."""

    name = "workspace-file-content"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def freeze(self, step: PlanStep) -> Mapping[str, Any]:
        if step.tool != "file:write" or len(step.args) < 2:
            raise ValueError("file write effect requires a relative path and fixed content")
        path = self._resolve(step.args[0])
        before_exists = path.is_file()
        before_digest = _file_digest(path) if before_exists else None
        return {
            "path": step.args[0],
            "before_exists": before_exists,
            "before_digest": before_digest,
            "target_digest": _digest(step.args[1].encode("utf-8")),
        }

    def observe(
        self,
        step: PlanStep,
        snapshot: Mapping[str, Any],
    ) -> EffectObservation:
        path = self._resolve(str(snapshot["path"]))
        exists = path.is_file()
        current = _file_digest(path) if exists else None
        target = str(snapshot["target_digest"])
        before = snapshot.get("before_digest")
        before_exists = bool(snapshot.get("before_exists"))
        if exists and current == target:
            return EffectObservation.create(
                ObservationStatus.APPLIED,
                authority=self.name,
                evidence=f"{snapshot['path']} has the frozen target content digest {target}",
            )
        if exists == before_exists and current == before:
            return EffectObservation.create(
                ObservationStatus.NOT_APPLIED,
                authority=self.name,
                evidence=f"{snapshot['path']} still matches the frozen pre-dispatch state",
            )
        return EffectObservation.create(
            ObservationStatus.UNKNOWN,
            authority=self.name,
            evidence=(
                f"{snapshot['path']} matches neither the frozen target nor pre-dispatch state; "
                f"observed_digest={current or 'absent'}"
            ),
        )

    def _resolve(self, relative: str) -> Path:
        target = (self.root / relative).resolve()
        if target != self.root and self.root not in target.parents:
            raise PermissionError(f"effect authority path escapes workspace: {relative}")
        return target


class EffectPolicyRegistry:
    """Harness-owned classification; an LLM or tool cannot loosen these rules."""

    def __init__(self, *, environment: str = "unknown", simulated: bool = False):
        self.environment = environment
        self.simulated = bool(simulated)

    def resolve(self, step: PlanStep) -> EffectSpec:
        if self.simulated:
            return EffectSpec(
                "mock-no-effect",
                SideEffectClass.NONE,
                ReplayPolicy.SAFE,
                "The configured mock executor cannot create a real external effect.",
            )
        tool = step.tool.casefold().strip()
        if self._read_only(tool):
            return EffectSpec(
                "read-only",
                SideEffectClass.NONE,
                ReplayPolicy.SAFE,
                "The trusted tool classifier marks this exact operation read-only.",
            )
        if tool == "file:write":
            return EffectSpec(
                "fixed-file-write",
                SideEffectClass.IDEMPOTENT,
                ReplayPolicy.VERIFY_THEN_RETRY,
                "A fixed-content local write is retried only after pre-state verification.",
                authority=FileWriteAuthority.name,
            )
        if tool.startswith(("notify:", "tool:refund", "shell:git push")):
            return EffectSpec(
                "irreversible-external-mutation",
                SideEffectClass.IRREVERSIBLE,
                ReplayPolicy.NEVER,
                "The operation may create an irreversible external effect and has no retry proof.",
            )
        return EffectSpec(
            "unclassified-mutation",
            SideEffectClass.UNKNOWN,
            ReplayPolicy.NEVER,
            "The harness has no proof that replaying this operation is safe.",
        )

    @staticmethod
    def _read_only(tool: str) -> bool:
        if tool == "echo":
            return True
        # file:read is implemented by the confined executor. Arbitrary shell,
        # SQL, and HTTP syntax stays UNKNOWN: flags, hooks, aliases, stored
        # procedures, or redirects can mutate even when a command sounds read-only.
        return tool == "file:read"


class EffectManager:
    def __init__(
        self,
        *,
        registry: EffectPolicyRegistry | None = None,
        authorities: tuple[EffectAuthority, ...] = (),
    ):
        self.registry = registry or EffectPolicyRegistry()
        self.authorities = {authority.name: authority for authority in authorities}

    def prepare(
        self,
        effects: list[EffectRecord],
        step: PlanStep,
        operation_id: str,
    ) -> EffectRecord:
        spec = self.registry.resolve(step)
        existing = self.find(effects, operation_id)
        if existing is not None:
            self.validate(existing, step)
            return existing
        snapshot: dict[str, Any] = {}
        if spec.authority:
            authority = self.authorities.get(spec.authority)
            if authority is None:
                raise RuntimeError(
                    f"effect policy {spec.name!r} requires unavailable authority {spec.authority!r}"
                )
            snapshot = dict(authority.freeze(step))
        record = EffectRecord(
            operation_id=operation_id,
            step_digest=step_digest(step),
            policy_digest=spec.policy_digest,
            spec_name=spec.name,
            side_effect_class=spec.side_effect_class,
            replay_policy=spec.replay_policy,
            authority=spec.authority,
            idempotency_key="taiyi:" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest(),
            verification_snapshot=snapshot,
        )
        effects.append(record)
        return record

    def validate(self, record: EffectRecord, step: PlanStep) -> None:
        spec = self.registry.resolve(step)
        if record.step_digest != step_digest(step):
            raise ValueError("effect operation is bound to different tool arguments")
        if record.policy_digest != spec.policy_digest:
            raise ValueError("effect policy changed since the operation was frozen")

    @staticmethod
    def find(effects: list[EffectRecord], operation_id: str) -> EffectRecord | None:
        return next((item for item in effects if item.operation_id == operation_id), None)

    @staticmethod
    def mark_dispatch(record: EffectRecord, *, recovery: bool = False) -> None:
        record.status = EffectStatus.DISPATCHING
        record.attempt_count += 1
        if recovery:
            record.recovery_attempts += 1
        record.updated_at = time.time()

    @staticmethod
    def mark_result(record: EffectRecord, *, ok: bool, failure_kind: str | None = None) -> None:
        record.status = EffectStatus.EXECUTOR_SUCCEEDED if ok else EffectStatus.EXECUTOR_FAILED
        record.executor_failure_kind = failure_kind
        record.updated_at = time.time()

    def observe(self, record: EffectRecord, step: PlanStep) -> EffectObservation:
        self.validate(record, step)
        if record.authority:
            authority = self.authorities.get(record.authority)
            if authority is None:
                observation = EffectObservation.create(
                    ObservationStatus.UNKNOWN,
                    authority=record.authority,
                    evidence="the frozen effect authority is not configured in this runtime",
                )
            else:
                observation = authority.observe(step, record.verification_snapshot)
        elif record.side_effect_class is SideEffectClass.NONE:
            observation = EffectObservation.create(
                ObservationStatus.NOT_APPLIED,
                authority="effect-policy",
                evidence="the frozen operation is side-effect-free and can be replayed",
            )
        else:
            observation = EffectObservation.create(
                ObservationStatus.UNKNOWN,
                authority="effect-policy",
                evidence="no independent authority can determine whether the mutation occurred",
            )
        record.observations.append(observation)
        if observation.status is ObservationStatus.APPLIED:
            record.status = EffectStatus.CONFIRMED_APPLIED
        elif observation.status is ObservationStatus.NOT_APPLIED:
            record.status = EffectStatus.CONFIRMED_NOT_APPLIED
        else:
            record.status = EffectStatus.AMBIGUOUS
        record.updated_at = time.time()
        return observation

    @staticmethod
    def recovery_action(
        record: EffectRecord,
        observation: EffectObservation,
        *,
        idempotency_supported: bool,
    ) -> RecoveryAction:
        if observation.status is ObservationStatus.APPLIED:
            return RecoveryAction.ACCEPT_APPLIED
        if observation.status is ObservationStatus.NOT_APPLIED and record.replay_policy in {
            ReplayPolicy.SAFE,
            ReplayPolicy.VERIFY_THEN_RETRY,
        }:
            return RecoveryAction.RETRY
        if (
            record.replay_policy is ReplayPolicy.IDEMPOTENCY_KEY
            and idempotency_supported
        ):
            return RecoveryAction.RETRY
        return RecoveryAction.WAIT_FOR_HUMAN

    def reconcile_result(
        self,
        record: EffectRecord,
        step: PlanStep,
        result,
        *,
        executor,
        max_recovery_attempts: int,
        before_retry: Callable[[EffectRecord], None] | None = None,
    ):
        """Turn an executor result into an evidence-backed effect outcome.

        Automatic replay is intentionally narrower than the policy decision:
        durable process tools reattach through their own journal, while this
        method only retries a non-durable operation proven safe by a frozen
        authority or a connector that enforces the supplied idempotency key.
        """

        from taiyi.runtime.executor import (
            DurableExecutor,
            ExecResult,
            IdempotentExecutor,
            execute_step,
        )
        from taiyi.runtime.protocol import RunPhase, classify_exception

        self.mark_result(record, ok=result.ok, failure_kind=result.failure_kind)
        if record.side_effect_class is SideEffectClass.NONE:
            return result
        if result.ok and record.authority is None:
            # Final acceptance authorities may still verify the delivery. The
            # effect protocol only overrides a successful connector when it has
            # its own frozen, independent postcondition to compare.
            return result

        while True:
            observation = self.observe(record, step)
            action = self.recovery_action(
                record,
                observation,
                idempotency_supported=(
                    isinstance(executor, IdempotentExecutor)
                    and executor.supports_idempotency(step)
                ),
            )
            if observation.status is ObservationStatus.APPLIED:
                original_output = result.output
                result.ok = True
                result.original_failure_kind = result.failure_kind
                result.failure_kind = None
                result.error = None
                result.output = (
                    "[effect independently confirmed applied]\n"
                    f"{observation.evidence}\n"
                    "[original executor observation]\n"
                    f"{original_output}"
                )
                result.effect_status = EffectStatus.CONFIRMED_APPLIED.value
                result.effect_evidence = observation.evidence
                return result

            if result.ok:
                # A connector said success but the independent authority did
                # not see the frozen mutation. Never let self-report win. A
                # proven NOT_APPLIED state may use the normal bounded recovery
                # path; UNKNOWN must suspend.
                result.original_failure_kind = result.failure_kind
                result.ok = False
                result.error = observation.evidence
                result.effect_status = record.status.value
                result.effect_evidence = observation.evidence
                if observation.status is ObservationStatus.UNKNOWN:
                    result.failure_kind = "EFFECT_OUTCOME_UNKNOWN"
                    return result
                result.failure_kind = "EXTERNAL_FAILURE"

            supports_durable_job = (
                isinstance(executor, DurableExecutor) and executor.supports_jobs(step)
            )
            if (
                action is RecoveryAction.RETRY
                and not supports_durable_job
                and record.recovery_attempts < max(0, int(max_recovery_attempts))
            ):
                self.mark_dispatch(record, recovery=True)
                if before_retry is not None:
                    before_retry(record)
                try:
                    result = execute_step(
                        executor,
                        step,
                        operation_id=record.operation_id,
                        idempotency_key=record.idempotency_key,
                    )
                except Exception as exc:
                    failure_kind = classify_exception(exc, RunPhase.TOOL_RUNNING)
                    result = ExecResult(
                        f"executor error: {type(exc).__name__}: {exc}",
                        ok=False,
                        operation_id=record.operation_id,
                        failure_kind=failure_kind.value,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                self.mark_result(record, ok=result.ok, failure_kind=result.failure_kind)
                continue

            result.effect_status = record.status.value
            result.effect_evidence = observation.evidence
            if observation.status is ObservationStatus.UNKNOWN:
                result.original_failure_kind = result.failure_kind
                result.failure_kind = "EFFECT_OUTCOME_UNKNOWN"
                result.error = observation.evidence
            return result

    @staticmethod
    def apply_human_resolution(
        record: EffectRecord,
        resolution: HumanEffectResolution,
        *,
        note: str,
    ) -> None:
        record.human_resolution = resolution.value
        record.human_note = note
        record.updated_at = time.time()
        evidence = f"human resolution={resolution.value}; note={note or 'not provided'}"
        if resolution is HumanEffectResolution.APPLIED:
            record.status = EffectStatus.CONFIRMED_APPLIED
            status = ObservationStatus.APPLIED
        elif resolution is HumanEffectResolution.NOT_APPLIED:
            record.status = EffectStatus.CONFIRMED_NOT_APPLIED
            status = ObservationStatus.NOT_APPLIED
        else:
            record.status = EffectStatus.ABANDONED
            status = ObservationStatus.UNKNOWN
        record.observations.append(
            EffectObservation.create(status, authority="human", evidence=evidence)
        )


def step_digest(step: PlanStep) -> str:
    payload = json.dumps(
        {"tool": step.tool, "args": list(step.args)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _digest(payload.encode("utf-8"))


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


__all__ = [
    "EFFECT_SCHEMA_VERSION",
    "EffectAuthority",
    "EffectManager",
    "EffectObservation",
    "EffectPolicyRegistry",
    "EffectRecord",
    "EffectSpec",
    "EffectStatus",
    "FileWriteAuthority",
    "HumanEffectResolution",
    "ObservationStatus",
    "RecoveryAction",
    "ReplayPolicy",
    "SideEffectClass",
    "step_digest",
]
