# TaiYi Durable Runtime Protocol

## Why this is the next foundation

Operating modes decide how much exploration, verification, repair, time, and
cost a task may use. They must not decide whether the harness records state,
classifies errors truthfully, or can recover after restart. Those are shared
runtime invariants.

An audit log alone is insufficient. It can prove that an event happened but it
does not necessarily contain enough typed state to continue the task. TaiYi
therefore keeps its tamper-evident audit chain and adds a separate durable run
protocol for recovery.

## Two independent state dimensions

`TaskState` is the user-visible outcome, such as `COMPLETED`, `NEEDS_REVIEW`, or
`FAILED`. `RunPhase` reports what the harness is currently doing or waiting on:

```text
READY -> PARSING -> PLANNING / LLM_WAITING
      -> AWAITING_PERMIT -> TOOL_RUNNING -> TOOL_RESULT
      -> VALIDATING -> SETTLED

WAITING_INPUT and WAITING_APPROVAL are suspended, not settled.
RECOVERING, RETRY_BACKOFF, and COMPACTING are explicit lifecycle phases.
```

This separation is a correctness boundary. A timeout observed in
`TOOL_RUNNING` is a `TOOL_TIMEOUT`; it cannot trigger an LLM fallback or be
displayed as `LLM_TIMEOUT`.

## Persistence contract

When `base_dir` is configured, every transition produces:

- `runs/<task_id>/events.jsonl`: append-only, fsync'd typed transitions;
- `runs/<task_id>/checkpoint.json`: the latest atomic, versioned checkpoint.

The checkpoint includes the frozen task contract, policy mode, plan or ReAct
conversation, permitted/executed steps, evidence, current phase, attempt id,
failure kind, and continuation data. On restart, a checkpoint is rehydrated only
if the regenerated contract has the same immutable contract id. Contract drift
fails closed instead of silently continuing under different acceptance rules.

## Phase 1: approval recovery

The first implemented recovery path is a task suspended for human approval:

1. The workflow plan or ReAct conversation is checkpointed with the held step.
2. A new TaiYi process scans non-settled `WAITING_APPROVAL` checkpoints.
3. It rebuilds the typed context and approval queue and increments `attempt_id`.
4. Human approval still causes a fresh governance check before execution.
5. Completion writes a `SETTLED` checkpoint, so a later restart does not enqueue
   the approval again.

This slice deliberately does not auto-rerun a tool found in `TOOL_RUNNING` after
a crash. Its external effect may be ambiguous.

## Phase 2: durable shell jobs

Shell execution no longer runs as one opaque `subprocess.run(..., timeout=30)`
inside the gateway request. `SandboxExecutor` starts a separate supervisor and
persists a job under `base_dir/jobs/<job_id>/` (or a private sibling directory
of the sandbox when no persistence root is configured):

- `job.json` is the latest typed job record;
- `request.json` is mode `0600` and contains the launch request;
- `heartbeat.json` reports supervisor progress, child pid/process token,
  process group, output sizes, and last-output time;
- `stdout.log` and `stderr.log` keep the complete output as artifacts;
- `result.json` is the terminal status written atomically by the supervisor.

The supervisor is independent of the gateway process, owns the child process
group, drains output continuously, and enforces two different deadlines. An
idle timeout means the process produced no output for the configured period; a
hard timeout is the absolute wall-clock limit even when output continues.
Cancellation terminates the whole child process group and has a bounded
fail-safe when the supervisor itself stops responding.

Every governed step gets a deterministic operation id before launch. The
operation index is claimed before the worker is spawned and serialized across
gateway processes. Repeating the same operation attaches to its existing job;
attempting to reuse the id for different command, working directory, tool, or
environment fails closed. This is the minimum safe behavior after an uncertain
client or gateway interruption: observe existing work, never silently replay
it.

The runtime records `tool_started`, then `job_attached`, then a typed
`tool_finished` event containing the job id, exact exit code or signal, timeout
kind, failure kind, artifact paths, and truncation marker. Only a bounded output
tail enters the model context; full stdout/stderr remain available for diagnosis
and independent validation.

Failure kinds now distinguish `TOOL_IDLE_TIMEOUT`, `TOOL_HARD_TIMEOUT`,
`TOOL_STARTUP_ERROR`, `TOOL_EXIT_NONZERO`, `TOOL_SIGNAL`, `TOOL_CANCELLED`, and
`TOOL_LOST`. A generic tool timeout remains available for legacy executors that
raise a timeout without the durable protocol.

### Deliberate Phase 2 boundary

The job substrate can be polled, cancelled, or reattached by `job_id` and
`operation_id`, including from a new executor instance. The current task API is
still synchronous, however, and the runtime does not yet scan a
`TOOL_RUNNING` task checkpoint and automatically continue its model loop after a
gateway restart. It also never retries an ambiguous external side effect.
Those require runtime-level job recovery plus effect classes and
authority-specific verification. LLM connect/first-token/stream-idle deadlines
are also a separate next slice. These boundaries are explicit so a durable
child process is not misrepresented as complete end-to-end task recovery.

## Invariants

- No operating mode can disable checkpoints, phase attribution, governance, or
  evidence-based completion.
- `SETTLED` means no pending approval, tool, retry, compaction, or continuation.
- A mock side effect remains `SIMULATED`, never `COMPLETED`.
- A persisted task contract is immutable across automatic recovery.
- A resumed approval never bypasses a fresh governance decision.
- Ambiguous side effects are not silently repeated.

## Next milestones

1. Rehydrate `TOOL_RUNNING` checkpoints, reattach their existing job, and
   continue the Workflow/ReAct loop without opening a duplicate operation.
2. Separate LLM connect, first-token, stream-idle, and hard timeouts, with
   retry/backoff that respects provider and task budgets.
3. Add side-effect classes, idempotency policies, and post-crash
   external verification before any retry.
4. Add an asynchronous task/job API so clients can submit, disconnect, poll,
   stream progress, cancel, and reconnect without holding one HTTP request.
5. Add proactive structured compaction and large-repository indexing keyed by
   Git SHA.
6. Exercise gateway kill/restart, network loss, 429/5xx, context overflow, duplicate
   effects, and huge-output faults in the harness benchmark.
