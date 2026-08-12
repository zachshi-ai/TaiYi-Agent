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
    DurableExecutor,
    ExecResult,
    Executor,
    IdempotentExecutor,
    MockExecutor,
    RecoverableExecutor,
)
from taiyi.runtime.jobs import JobHandle, JobRecord, JobStatus, JobStore
from taiyi.runtime.engine import TaskRuntime, replay_task
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
