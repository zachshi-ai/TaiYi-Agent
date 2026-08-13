# Parked Tool Continuations

Phase 7B2.7 removes the task-sized waiter from ordinary long-running durable
shell tools. An asynchronous Agent or Workflow invocation checkpoints the exact
governed operation and durable job, releases its transient task thread and task
lease, and lets the supervisor finish independently. A terminal notification
wakes one shared Gateway loop; recovery then verifies the authoritative
JobRecord and continues from the frozen task state.

## Commit-before-park boundary

```text
model/plan proposes tool
  -> governance permit
  -> freeze operation id + arguments
  -> prepare Effect Record
  -> checkpoint tool_started
  -> mark effect dispatch + checkpoint
  -> durable executor claims operation id
  -> checkpoint job_id + parked=true
  -> release task thread and task lease

supervisor writes result.json
  -> supervisor appends fsync'd job_terminal hint
  -> shared Gateway waker verifies terminal JobRecord
  -> task lease winner reattaches the same operation/job
  -> Effect Ledger reconciliation
  -> observation returns to Agent or next Workflow step
```

The order is the safety property. Parking before Effect Ledger dispatch would
lose the uncertainty boundary. Parking before `job_id` attachment would leave
recovery unable to distinguish an absent dispatch from an unobserved one.
Resuming before the JobRecord is terminal would merely recreate the waiter that
this phase removes.

`DurableToolParked` is an internal control signal, not a task failure. Top-level
runtimes do not call `_finish` for it. They release the task lease and return the
still-unsettled context. Non-evented or non-durable executors retain the existing
synchronous behavior.

## One generic wake loop

The Gateway now monitors two independent notification cursors:

- repository index jobs, including subscriber-cancellation hints;
- ordinary durable tool jobs, including terminal status and failure kind.

Notification records are at-least-once hints. On every candidate wake, recovery
reloads the checkpoint, claims the RunStore task lease, finds the job through the
stable operation index, compares the recorded `job_id`, and reads the terminal
receipt. Duplicate Gateways can observe the same hint, but only one lease holder
can advance the task.

A periodic checkpoint refresh remains the correctness fallback. If the
notification append is lost, corrupt, or written by another process, the waker
polls the authoritative JobRecord and resumes a terminal task. It does not poll
each parked job at the old 50 ms cadence.

## Failure and effect truth

Parking changes ownership, not result semantics:

- non-zero exit, signal, hard timeout, idle timeout, cancellation, and lost
  supervisor retain their exact executor failure kind;
- an arbitrary shell command remains an unclassified possible mutation, so an
  unprovable failed effect still becomes `EFFECT_OUTCOME_UNKNOWN` while retaining
  the executor cause as `original_failure_kind`;
- successful jobs without an independent effect authority keep the existing
  connector receipt behavior;
- recovery never calls `start` when the frozen checkpoint already names a job;
- cancellation targets the attached durable job and its process group, then the
  same terminal wake path reconciles the result.

Quality, balanced, and efficiency modes share all these invariants. Modes may
change budgets and completion rigor, but cannot disable governance, durable
identity, Effect Ledger handling, or error attribution.

## Verification

Deterministic tests cover both Agent and Workflow parking, absence of a waiter
while the job remains `RUNNING`, one recovery and one operation, Gateway
replacement, missing-notification fallback, durable cancellation, repository
wake regressions, and SSE replay exclusion.

The production path was exercised against:

```text
kubernetes/kubernetes@52ba90138eb40cab0987dac73e05c838149bdd1c
read-only tool input: 31,297 files, 277,608,112 bytes
parked: 0.023271 s after submission; task thread absent while Job was RUNNING
Gateway 1 closed; Gateway 2 recovered the same job id
job: SUCCEEDED in 2.146555 s; one terminal notification
SSE: disconnected after revision 8; resumed at 9; settled at 14; no replay
task: attempt 2, COMPLETED / SETTLED, 2.408519 s total
wake work after restart: 9 notification reads, 4 checkpoint scans
```

The self-verifying receipt is committed at
`research/benchmark/results/parked-tool-kubernetes-v1/validation.json`. This is
real-repository, production-path reliability evidence. It is not a latency
ranking and does not claim superiority over another harness.

## Remaining boundary

The shared-filesystem deployment now parks repository and ordinary durable tool
continuations. Multi-host execution still needs fenced distributed task and
consumer leases plus event fan-out. External refund/notification connectors need
authority-specific observation and compensation. Live provider/network-loss
validation remains separate from this local tool-lifecycle proof.
