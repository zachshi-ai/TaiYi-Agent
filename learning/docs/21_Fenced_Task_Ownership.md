# Fenced Task Ownership

Phase 7B2.8 prevents an expired or partitioned Runtime from continuing to write
task state after another owner has recovered the same checkpoint. Mutual
exclusion alone is insufficient: an old process may pause, lose connectivity,
then resume after a replacement has already advanced the task. TaiYi now binds
every authoritative task write to a monotonically increasing fencing token.

## Lease and fence contract

```text
shared lease authority
  namespace=task, task_id
  owner_id, fencing_token, expires_at
  monotonic counter survives release

Gateway A acquires token 41
  -> one RunStore heartbeat renews every locally owned task
  -> event/checkpoint writes enter lease transaction
  -> validate owner=Gateway A, token=41, not expired
  -> renew lease, fsync event, atomically replace checkpoint

Gateway A loses lease heartbeat
Gateway B acquires token 42 after expiry
Gateway A receives a delayed model response
  -> write guard rejects token 41
  -> run_fenced audit event
  -> no task failure, governance call, tool dispatch, or stale checkpoint
```

Lease release deletes only the active owner row. A separate counter retains the
highest token, so a future owner always receives a strictly larger value. An
expired owner cannot reacquire its old token. `BEGIN IMMEDIATE` serializes lease
replacement with guarded task writes, closing the validate-then-write race.

The default clock comes from the lease database, not the Gateway host. Two
owners therefore do not disagree about expiry because their wall clocks drift.
Tests may inject a deterministic clock, but production does not trust one.

## Runtime integration

`RunStore.record` requires a current local `FencedLease` for every persistent
transition. The event document stores `fencing_token` and `lease_owner_id`; the
checkpoint stores the complete `write_fence` receipt. Older v1 checkpoints need
no migration rewrite: recovery acquires a fresh token before appending their
next event.

A single `taiyi-task-lease-heartbeat` thread per active RunStore renews all local
tasks. It is not one thread per task and exits when the RunStore owns no tasks.
`task_lease_seconds` is configurable through YAML or
`TAIYI_TASK_LEASE_SECONDS`. Long model requests remain owned while the shared
heartbeat is healthy; parked repository/tool continuations release ownership
until their durable notification is ready.

`TaskLeaseLostError` is an ownership signal, not a task outcome. The stale
Runtime appends `run_fenced` to the independent audit log and stops. It cannot
write `FAILED`, because that would itself be an unauthorized stale transition.
The replacement owner remains responsible for the authoritative task result.
Persistent AuditLog appends take a cross-process file lock, reload the latest
chain head inside that lock, append and fsync one record. A stale Gateway can
therefore record its fencing without forking or corrupting the shared hash chain.

A replacement may start before the old lease expires. Its startup recovery is
correctly refused while the old token is active, then the shared Gateway wake
loop rechecks the non-parked continuation and automatically retries recovery
after database-authoritative expiry. No second manual restart is required.

## Local ownership handoff

CI subsequently exposed a same-RunStore handoff race. Recovery wrote
`WAITING_INPUT` and released token A. An operator's effect resolution then
acquired token B, but the old recovery thread's `finally` released ownership by
task id and accidentally revoked B. The operator received HTTP 409 despite
having acquired the task legitimately.

Every Runtime cleanup now captures its execution receipt and supplies that
receipt to release. The store compares namespace, task key, owner and token;
expired cleanup cannot remove either the successor's local claim or its shared
authority row. An explicitly missing receipt is a no-op. Unscoped release is
reserved for administrative store close and controlled crash simulations.
Contexts also retain a non-persisted write receipt, so an old context cannot
borrow a new token from the same RunStore. Restored contexts must bind the exact
receipt acquired by that execution before their first write; binding fails if
that claim was already replaced. Creation binds atomically with its first claim.
Approval resume restores a fresh
context from the authoritative checkpoint before binding its new claim.

Checkpoint scans are candidates rather than ownership proofs. After claiming a
task, recovery re-reads the checkpoint and discards a scan whose digest has
changed. Approval recovery likewise revalidates the snapshot. This prevents a
delayed scan from resurrecting an already suspended or settled continuation.
Recovery-thread cleanup removes its tracking entry only if it still names that
same thread. A genuinely competing owner still produces HTTP 409.

The deterministic Gateway regression holds old recovery after `WAITING_INPUT`,
lets an operator acquire the successor lease, runs the old cleanup, and only
then lets the operator continue. Agent and Workflow must preserve the successor
claim and return HTTP 200 with one resolution event. A separate held scan must
leave the newer checkpoint and event journal unchanged.

## Fault evidence

Automated tests prove:

- only one of eight concurrent RunStores can claim a task;
- release and expiry both produce a strictly higher next token;
- an expired owner cannot append an event or mutate its context revision;
- the shared heartbeat retains one token across a long model wait;
- a process that exits without release is reclaimed after expiry;
- checkpoint and event receipts expose the same write fence;
- eight concurrent audit writers preserve one verified hash chain;
- a delayed model response from an expired Gateway is rejected before its
  proposed `file:write`, while a replacement Gateway started before expiry
  waits, recovers automatically, and settles once.

A controlled two-process run observed:

```text
Gateway A: token 1, model request deliberately held past lease expiry
Gateway B: token 2, recovered attempt 2, COMPLETED / SETTLED
Gateway A response released after settlement: fenced before tool dispatch
stale-owner.txt: absent
run_recovered: 1
run_settled: 1
event revisions: unique and strictly increasing
both process exit codes: 0
```

The self-verifying receipt is committed at
`research/benchmark/results/fenced-task-process-v1/validation.json`. This is a
controlled ownership/failure test, not a cross-harness ranking.

## Deployment boundary

`FencedLeaseStore` is a backend-neutral contract with a SQLite reference
implementation. SQLite is appropriate for one host or storage whose locking and
WAL guarantees are explicitly supported. This phase does **not** claim arbitrary
NFS or multi-region correctness. Production multi-host deployment still needs a
PostgreSQL/etcd implementation, repository-consumer fencing, shared event
fan-out, backend health policy, and network-partition tests against that exact
deployment topology.
