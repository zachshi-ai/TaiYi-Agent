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

## Phase 3: task recovery and asynchronous clients

The runtime now scans active continuations as well as suspended approvals. Before
advancing one it claims `runs/<task_id>/.task.lock`; the POSIX lease is released
by the OS on process death. A second gateway can serve other traffic, but cannot
continue the same task concurrently.

For a `TOOL_RUNNING` checkpoint, recovery verifies the frozen round, step, tool,
arguments, deterministic operation id, and recorded job id. It then looks up the
operation index and waits on that same supervisor job. Workflow restores its
frozen plan and next step. ReAct restores its exact message list and step budget,
adds the recovered observation once, and asks the model for the next turn. A
process exit while waiting for an LLM can likewise replay the frozen message list;
no tool effect has occurred at that point.

There is one narrow safe launch-recovery case: the runtime checkpointed the
operation before launch, but no job or operation index exists. It asks governance
again, then starts the same operation id. If an operation index exists, it always
reattaches. If a non-durable tool may already have produced an effect, automatic
recovery fails closed rather than guessing.

Clients no longer need to hold a synchronous request for a long task:

- `POST /v1/tasks` with `{"async": true}` or `POST /v1/tasks/async` returns `202`;
- `GET /v1/tasks/<task_id>` returns phase, state, attempt, continuation, and job heartbeat;
- `GET /v1/tasks/<task_id>/events?after=<revision>&limit=<n>` returns a bounded,
  cursor-based page of the persisted typed progress stream;
- `POST /v1/tasks/<task_id>/cancel` cancels an attached durable job and its process group.

Fault tests exit the gateway immediately after job attachment, construct a new
gateway, and prove that Workflow and ReAct both settle while a marker side effect
occurs exactly once. They also prove that a concurrent gateway cannot recover a
leased task, a frozen LLM turn survives restart, and cancellation is reflected as
`TOOL_CANCELLED` rather than generic failure.

### Deliberate Phase 3 boundary

Progress is currently reconnectable polling over persisted events, not SSE or
WebSocket streaming. Model timeouts are attributed to `LLM_WAITING`, but connect,
first-token, stream-idle, and hard deadlines plus budgeted retry/backoff remain a
separate slice. Retrying an external effect still requires an explicit effect
class, idempotency contract, and authority-specific post-crash verification.

## Invariants

- No operating mode can disable checkpoints, phase attribution, governance, or
  evidence-based completion.
- `SETTLED` means no pending approval, tool, retry, compaction, or continuation.
- A mock side effect remains `SIMULATED`, never `COMPLETED`.
- A persisted task contract is immutable across automatic recovery.
- A resumed approval never bypasses a fresh governance decision.
- Ambiguous side effects are not silently repeated.

## Next milestones

1. Separate LLM connect, first-token, stream-idle, and hard timeouts, with
   retry/backoff that respects provider and task budgets.
2. Add side-effect classes, idempotency policies, and post-crash
   external verification before any retry.
3. Add SSE progress streaming on top of the persisted event cursor.
4. Add proactive structured compaction and large-repository indexing keyed by
   Git SHA.
5. Exercise network loss, 429/5xx, context overflow, duplicate
   effects, and huge-output faults in the harness benchmark.
