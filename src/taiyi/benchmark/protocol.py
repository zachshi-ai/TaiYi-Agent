"""Controlled TaiYi protocol-conformance benchmark.

The scripted provider is deliberately deterministic: this suite measures the
harness under fixed faults, not model intelligence.  Every run still traverses
the production Gateway, AgentRuntime, checkpoint, retry, Effect Ledger, and
completion-state code paths.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from taiyi.benchmark.schema import (
    BenchmarkCase,
    ExpectedOutcome,
    MeasurementStatus,
    RunReceipt,
    canonical_digest,
)
from taiyi.gateway import build_gateway
from taiyi.llm import LLMErrorKind, LLMMessage, LLMRequestError, LLMResponse, ToolCall
from taiyi.runtime import ExecResult, FailureKind, TaskState
from taiyi.scheduler import PlanStep


PROTOCOL_PROVIDER_ID = "scripted-fault-provider/v1"
PROTOCOL_HARNESS_ID = "taiyi"


def protocol_cases() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase(
            "clean_delivery",
            "One governed fixed-content delivery with no injected fault.",
            "none",
            tags=("delivery", "control"),
        ),
        BenchmarkCase(
            "llm_two_transient_failures",
            "Two typed transient model failures occur before the first usable response.",
            "llm_two_transient_failures",
            tags=("llm", "retry-budget"),
        ),
        BenchmarkCase(
            "effect_two_preapply_failures",
            "Two connector failures are independently proven not applied before success.",
            "effect_two_preapply_failures",
            tags=("effect", "retry-budget", "idempotency"),
        ),
        BenchmarkCase(
            "effect_applied_receipt_timeout",
            "The target mutation is applied once but its connector receipt times out.",
            "effect_applied_receipt_timeout",
            tags=("effect", "authority", "timeout"),
        ),
        BenchmarkCase(
            "effect_ambiguous",
            "The target differs from both frozen pre-state and intended post-state.",
            "effect_ambiguous",
            tags=("effect", "human-handoff", "false-completion"),
        ),
        BenchmarkCase(
            "large_repository_context",
            "A 1,200-file workspace is indexed before the governed delivery.",
            "none",
            repository_files=1200,
            tags=("repository", "context", "scale"),
        ),
    )


class _ProtocolProvider:
    name = "benchmark-scripted"
    model = PROTOCOL_PROVIDER_ID

    def __init__(self, fault: str):
        self.fault = fault
        self.calls = 0
        self.responded_with_tool = False
        self.repository_context_seen = False

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[str] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.repository_context_seen = self.repository_context_seen or any(
            "repository" in message.content.casefold()
            and "snapshot" in message.content.casefold()
            for message in messages
        )
        if self.fault == "llm_two_transient_failures" and self.calls <= 2:
            raise LLMRequestError(
                LLMErrorKind.LLM_SERVER_ERROR,
                "benchmark injected transient provider failure",
                retryable=True,
                phase="response",
                provider=self.name,
                status_code=503,
            )
        if not self.responded_with_tool:
            self.responded_with_tool = True
            return LLMResponse(
                tool_calls=[ToolCall("file:write", ["result.txt", "verified\n"])],
                model=self.model,
            )
        return LLMResponse(text="delivery prepared for independent verification", model=self.model)


class _ProtocolExecutor:
    environment = "workspace"

    def __init__(self, root: Path, fault: str):
        self.sandbox = root
        self.fault = fault
        self.calls = 0
        self.applied_effects = 0

    def execute(self, step: PlanStep) -> ExecResult:
        raise AssertionError("benchmark file connector must receive the stable idempotency key")

    def supports_idempotency(self, step: PlanStep) -> bool:
        return step.tool == "file:write" and len(step.args) >= 2

    def execute_idempotent(
        self,
        step: PlanStep,
        *,
        operation_id: str,
        idempotency_key: str,
    ) -> ExecResult:
        self.calls += 1
        target = self.sandbox / step.args[0]
        if self.fault == "effect_two_preapply_failures" and self.calls <= 2:
            return ExecResult(
                "benchmark connector failed before apply",
                ok=False,
                operation_id=operation_id,
                failure_kind=FailureKind.EXTERNAL_FAILURE.value,
                error="injected pre-apply connector failure",
            )
        if self.fault == "effect_applied_receipt_timeout":
            target.write_text(step.args[1], encoding="utf-8")
            self.applied_effects += 1
            return ExecResult(
                "benchmark receipt timed out after apply",
                ok=False,
                operation_id=operation_id,
                failure_kind=FailureKind.TOOL_TIMEOUT.value,
                error="injected lost receipt",
            )
        if self.fault == "effect_ambiguous":
            target.write_text("concurrent-change\n", encoding="utf-8")
            self.applied_effects += 1
            return ExecResult(
                "benchmark target changed without a matching receipt",
                ok=False,
                operation_id=operation_id,
                failure_kind=FailureKind.TOOL_TIMEOUT.value,
                error="injected ambiguous target outcome",
            )
        target.write_text(step.args[1], encoding="utf-8")
        self.applied_effects += 1
        return ExecResult("fixed content written", operation_id=operation_id)


class TaiYiProtocolAdapter:
    harness_id = PROTOCOL_HARNESS_ID
    harness_version = "0.1.0"
    adapter = "taiyi-in-process-production-runtime"
    model_id = PROTOCOL_PROVIDER_ID

    def run(self, case: BenchmarkCase, mode: str, run_root: str | Path) -> RunReceipt:
        root = Path(run_root)
        workspace = root / "workspace"
        state = root / "state"
        workspace.mkdir(parents=True, exist_ok=True)
        _materialize_workspace(workspace, case.repository_files)
        initial_snapshot = _workspace_snapshot(workspace)
        provider = _ProtocolProvider(case.fault)
        executor = _ProtocolExecutor(workspace, case.fault)
        gateway = build_gateway(
            base_dir=state,
            mode="agent",
            operating_mode=mode,
            executor=executor,
            provider=provider,
            validator=False,
            llm_sleep=lambda _delay: None,
        )

        started = time.monotonic()
        error = None
        try:
            ctx = gateway.submit(
                "Create result.txt with the exact required content and verify delivery.",
                operating_mode=mode,
            )
        except Exception as exc:  # the benchmark must emit a receipt for harness crashes
            ctx = None
            error = f"{type(exc).__name__}: {exc}"
        duration = max(0.0, time.monotonic() - started)

        final_snapshot = _workspace_snapshot(workspace)
        acceptance_path = workspace / case.acceptance_path
        observed_content = (
            acceptance_path.read_text(encoding="utf-8")
            if acceptance_path.is_file()
            else None
        )
        task_passed = observed_content == case.acceptance_content
        expected = _expected_outcome(case, mode)

        if ctx is None:
            reported_state = "HARNESS_ERROR"
            failure_kind = FailureKind.INTERNAL.value
            claimed_complete = False
            human_handoffs = 0
            events: tuple[dict[str, Any], ...] = ()
            effects: list[dict[str, Any]] = []
            context_evidence = None
        else:
            reported_state = ctx.state.value
            failure_kind = ctx.failure_kind
            claimed_complete = ctx.state in {TaskState.COMPLETED, TaskState.SIMULATED}
            human_handoffs = int(ctx.state is TaskState.NEEDS_INPUT)
            events = gateway.runtime.run_store.read_events(ctx.task_id)
            effects = [item.to_dict() for item in ctx.effects]
            context_evidence = ctx.repository_context

        duplicate_effects = max(0, executor.applied_effects - 1)
        false_completion = claimed_complete and not task_passed
        protocol_passed = _protocol_passed(
            expected=expected,
            reported_state=reported_state,
            task_passed=task_passed,
            false_completion=false_completion,
            duplicate_effects=duplicate_effects,
            failure_kind=failure_kind,
        )
        fault_injected = case.fault != "none"
        recovered = fault_injected and expected is ExpectedOutcome.DELIVER and task_passed
        case_value = case.to_dict()
        environment = {
            "provider": self.model_id,
            "adapter": self.adapter,
            "case_digest": canonical_digest(case_value),
            "initial_workspace_digest": canonical_digest(initial_snapshot),
            "acceptance_digest": canonical_digest({
                "path": case.acceptance_path,
                "content": case.acceptance_content,
            }),
        }
        evidence = {
            "initial_workspace": initial_snapshot,
            "final_workspace": final_snapshot,
            "observed_content_digest": (
                canonical_digest(observed_content) if observed_content is not None else None
            ),
            "event_count": len(events),
            "event_types": [str(event.get("event")) for event in events],
            "effects": effects,
            "repository_context": context_evidence,
            "repository_context_seen_by_provider": provider.repository_context_seen,
        }
        return RunReceipt(
            run_id=f"{self.harness_id}-{case.case_id}-{mode}-{uuid.uuid4().hex[:12]}",
            case_id=case.case_id,
            harness_id=self.harness_id,
            harness_version=self.harness_version,
            adapter=self.adapter,
            model_id=self.model_id,
            operating_mode=mode,
            measurement_status=(
                MeasurementStatus.MEASURED if ctx is not None else MeasurementStatus.ERROR
            ),
            reported_state=reported_state,
            expected_outcome=expected,
            task_passed=task_passed,
            protocol_passed=protocol_passed,
            claimed_complete=claimed_complete,
            false_completion=false_completion,
            fault_injected=fault_injected,
            recovered=recovered,
            human_handoffs=human_handoffs,
            connector_attempts=executor.calls,
            applied_effects=executor.applied_effects,
            duplicate_effects=duplicate_effects,
            llm_calls=provider.calls,
            duration_seconds=duration,
            failure_kind=failure_kind,
            case_digest=canonical_digest(case_value),
            environment_digest=canonical_digest(environment),
            evidence=evidence,
            error=error,
        )


def _expected_outcome(case: BenchmarkCase, mode: str) -> ExpectedOutcome:
    if case.case_id == "effect_ambiguous":
        return ExpectedOutcome.SAFE_HANDOFF
    if case.case_id == "effect_two_preapply_failures" and mode != "quality":
        return ExpectedOutcome.BUDGET_EXHAUSTED
    if case.case_id == "llm_two_transient_failures" and mode == "efficiency":
        return ExpectedOutcome.BUDGET_EXHAUSTED
    return ExpectedOutcome.DELIVER


def _protocol_passed(
    *,
    expected: ExpectedOutcome,
    reported_state: str,
    task_passed: bool,
    false_completion: bool,
    duplicate_effects: int,
    failure_kind: str | None,
) -> bool:
    if false_completion or duplicate_effects:
        return False
    if expected is ExpectedOutcome.DELIVER:
        return reported_state == TaskState.COMPLETED.value and task_passed
    if expected is ExpectedOutcome.SAFE_HANDOFF:
        return (
            reported_state == TaskState.NEEDS_INPUT.value
            and not task_passed
            and failure_kind == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
        )
    return reported_state == TaskState.FAILED.value and not task_passed and bool(failure_kind)


def _materialize_workspace(root: Path, repository_files: int) -> None:
    (root / "README.md").write_text(
        "# Harness benchmark fixture\n\nThe required artifact is result.txt.\n",
        encoding="utf-8",
    )
    if repository_files <= 0:
        return
    decoys = root / "packages"
    for index in range(repository_files):
        shard = decoys / f"group-{index // 100:02d}"
        shard.mkdir(parents=True, exist_ok=True)
        (shard / f"module_{index:04d}.py").write_text(
            f'"""Deterministic repository fixture {index}."""\nVALUE = {index}\n',
            encoding="utf-8",
        )


def _workspace_snapshot(root: Path) -> dict[str, Any]:
    files: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        files.append({"path": relative, "digest": _file_digest(path)})
    return {"file_count": len(files), "tree_digest": canonical_digest(files)}


def _file_digest(path: Path) -> str:
    import hashlib

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def cases_manifest() -> dict[str, Any]:
    cases = [case.to_dict() for case in protocol_cases()]
    return {
        "schema_version": "taiyi.protocol-cases/v1",
        "provider_id": PROTOCOL_PROVIDER_ID,
        "measurement_scope": "harness_protocol_conformance",
        "cases": cases,
        "cases_digest": canonical_digest(cases),
        "notes": [
            "The scripted provider controls model behavior; this is not a model-quality score.",
            "A task can fail its delivery objective while still passing fail-closed protocol conformance.",
            "External harness scores require the same model, prompt, workspace, budget, and evaluator.",
        ],
    }


__all__ = [
    "PROTOCOL_HARNESS_ID",
    "PROTOCOL_PROVIDER_ID",
    "TaiYiProtocolAdapter",
    "cases_manifest",
    "protocol_cases",
]
