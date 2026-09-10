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

## First vertical slice

The first implemented recovery path is a task suspended for human approval:

1. The workflow plan or ReAct conversation is checkpointed with the held step.
2. A new TaiYi process scans non-settled `WAITING_APPROVAL` checkpoints.
3. It rebuilds the typed context and approval queue and increments `attempt_id`.
4. Human approval still causes a fresh governance check before execution.
5. Completion writes a `SETTLED` checkpoint, so a later restart does not enqueue
   the approval again.

This slice deliberately does not auto-rerun a tool found in `TOOL_RUNNING` after
a crash. Its external effect may be ambiguous. Automatic recovery of that phase
requires the next milestone: durable job ids, effect classification,
idempotency keys, and authority-specific effect verification.

## Invariants

- No operating mode can disable checkpoints, phase attribution, governance, or
  evidence-based completion.
- `SETTLED` means no pending approval, tool, retry, compaction, or continuation.
- A mock side effect remains `SIMULATED`, never `COMPLETED`.
- A persisted task contract is immutable across automatic recovery.
- A resumed approval never bypasses a fresh governance decision.
- Ambiguous side effects are not silently repeated.

## Next milestones

1. Replace blocking command execution with durable background jobs, process
   groups, streaming output, heartbeats, cancellation, and reattachment.
2. Separate tool startup, progress-idle, and hard timeouts; separate LLM
   connect, first-token, stream-idle, and hard timeouts.
3. Add operation ids, side-effect classes, idempotency policies, and post-crash
   external verification before any retry.
4. Store large stdout/stderr as artifacts and provide bounded summaries to the
   model.
5. Add proactive structured compaction and large-repository indexing keyed by
   Git SHA.
6. Exercise kill/restart, network loss, 429/5xx, context overflow, duplicate
   effects, and huge-output faults in the harness benchmark.
