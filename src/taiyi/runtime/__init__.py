"""Task Runtime — the PDCA main loop.

Drives a single task through Plan → Do → Check → Act, interleaving permit and
execution one step at a time. Executes only steps that governance cleared, and
records the whole trajectory to the shared audit chain so any task is replayable.
"""

from taiyi.runtime.state import TaskState
from taiyi.runtime.context import TaskContext, StepResult
from taiyi.runtime.executor import Executor, MockExecutor, ExecResult
from taiyi.runtime.engine import TaskRuntime, replay_task

__all__ = [
    "TaskState",
    "TaskContext",
    "StepResult",
    "Executor",
    "MockExecutor",
    "ExecResult",
    "TaskRuntime",
    "replay_task",
]
