"""Task Runtime — the PDCA main loop.

Drives a single task through Plan → Do → Check → Act, interleaving permit and
execution one step at a time. Executes only steps that governance cleared, and
records the whole trajectory to the shared audit chain so any task is replayable.
"""

from taiyi.runtime.state import TaskState
from taiyi.runtime.context import TaskContext, StepResult
from taiyi.runtime.effects import (
    EffectManager,
    EffectObservation,
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
from taiyi.runtime.executor import (
    DurableToolParked,
    DurableExecutor,
    EventedExecutor,
    ExecResult,
    Executor,
    IdempotentExecutor,
    MockExecutor,
    RecoverableExecutor,
)
from taiyi.runtime.jobs import JobHandle, JobRecord, JobStatus, JobStore
from taiyi.runtime.persistence import RunStore
from taiyi.runtime.protocol import FailureKind, RunPhase

__all__ = [
    "TaskState",
    "TaskContext",
    "StepResult",
    "EffectManager",
    "EffectObservation",
    "EffectPolicyRegistry",
    "EffectRecord",
    "EffectStatus",
    "FileWriteAuthority",
    "HumanEffectResolution",
    "ObservationStatus",
    "RecoveryAction",
    "ReplayPolicy",
    "SideEffectClass",
    "Executor",
    "DurableExecutor",
    "DurableToolParked",
    "EventedExecutor",
    "IdempotentExecutor",
    "RecoverableExecutor",
    "MockExecutor",
    "ExecResult",
    "TaskRuntime",
    "replay_task",
    "RunStore",
    "RunPhase",
    "FailureKind",
    "JobHandle",
    "JobRecord",
    "JobStatus",
    "JobStore",
]


def __getattr__(name):
    """Load the engine lazily so lower-level job primitives do not import context."""

    if name in {"TaskRuntime", "replay_task"}:
        from taiyi.runtime.engine import TaskRuntime, replay_task

        return {"TaskRuntime": TaskRuntime, "replay_task": replay_task}[name]
    raise AttributeError(name)
