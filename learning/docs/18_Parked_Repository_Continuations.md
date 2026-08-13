# Parked Repository Continuations

Phase 7B2.5 removes the task-sized waiter from durable repository indexing. An
asynchronous task now persists its continuation, releases its task lease and
transient submission thread, and waits to be resumed by one Gateway-wide wake
loop. Index duration therefore no longer consumes one Python thread per task or
requires an LLM/HTTP request to remain alive.

## Runtime protocol

```text
async submit -> INDEXING checkpoint -> attach durable repository job
                                      -> parked=true
                                      -> release task lease and task thread

one Gateway wake loop -> scan parked checkpoints
                      -> renew active consumer leases
                      -> terminal/cancel receipt observed
                      -> recover_pending() claims task lease
                      -> reattach same job id -> model -> SETTLED
```

Synchronous callers remain blocking by contract. Parking is an execution option
used by asynchronous Gateway submission, not a different task result. Agent and
Workflow runtimes use the same protocol and preserve their own continuation
kind, frozen contract, prompt state, index generation, operation id, and job id.

The `repository_index_parked` event is written before the control signal leaves
the runtime. A parked run is deliberately not passed through `_finish`: it is
still `INDEXING`, not completed, failed, or simulated. The transient submission
thread only records a result after `SETTLED` and always removes itself from the
Gateway task-thread registry.

## Shared wake ownership

Each Gateway owns at most one `taiyi-repository-waker` thread, independent of
the number of parked tasks. The loop lives until `Gateway.close()` and scans
authoritative persisted checkpoints. Keeping it for the Gateway lifetime avoids
an idle-exit race in which a task could park just after the final scan.

When a job becomes terminal, or a task-specific cancellation marker appears,
the loop invokes the existing recovery scanner. `RunStore` task leases serialize
multiple Gateways: two wake loops may notice the same receipt, but only one can
advance that task. Recovery reattaches the checkpointed job; it cannot create a
new generation or a second SQLite writer.

Closing a Gateway stops only its monitor. It does not cancel the independently
supervised index worker. A replacement Gateway detects the parked checkpoint at
startup, renews its subscription, and resumes from the same job receipt.

## Expiring consumers

Every persisted repository attachment now contains `attached_at`, `renewed_at`,
and `lease_expires_at`. A synchronous waiter or the shared wake loop renews the
lease. An abandoned host cannot remain an immortal coalesced-job consumer:
expired attachments are reclaimed explicitly and ignored by cancellation when
deciding whether another live consumer still needs the job.

These are expiring consumer leases, not yet a complete distributed scheduler.
The task lease is still a host-local POSIX file lock, and lease expiry uses wall
clock time. Multi-host fencing, clock-skew bounds, and a durable event bus remain
future work.

## Cancellation and failure truth

- cancelling a sole parked consumer writes its marker, stops the durable worker,
  and wakes the continuation into `REPOSITORY_INDEX_CANCELLED`;
- cancelling one of several consumers detaches only that task while the shared
  job continues;
- worker failure wakes the same frozen continuation and remains
  `REPOSITORY_INDEX_FAILED` or `REPOSITORY_INDEX_LOST`, never `LLM_TIMEOUT`;
- a parked continuation never counts as a delivered result.

Quality, balanced, and efficiency modes share these lifecycle guarantees. Modes
may change context and retry budgets but cannot choose weaker parking, lease,
cancellation, or recovery semantics.

## Verification evidence

Deterministic tests cover Agent and Workflow parking, absence of a task waiter
while the index is running, shared wake ownership, lease renewal beyond the
lease period, abandoned-consumer expiry, isolated cancellation, same-job resume
after Gateway monitor restart, and explicit monitor shutdown.

The production path was exercised against:

```text
kubernetes/kubernetes@52ba90138eb40cab0987dac73e05c838149bdd1c
repository_index_parked: 0.013 s after run creation
durable job: SUCCEEDED, 7.129 s, same job id attached before and after wake
task: attempt 1 parked -> attempt 2 recovered -> SETTLED, 7.232 s total
snapshot: 25,683 files, 59,825 chunks
snapshot id: sha256:0c8150939370149dc60c8c15cb4a03dc09380c9913a42c551c2544f2cc6f7d72
```

The signed receipt is committed at
`research/benchmark/results/parked-index-kubernetes-v1/validation.json`. It
validates TaiYi's production path at real repository scale; it is not a live
provider SLA or cross-harness ranking.

## Remaining boundary

Phase 7B2.6 replaces the 50 ms full checkpoint/JobRecord scan with an incremental
durable notification journal and adds reconnectable SSE for clients. See
[`19_Durable_Event_Notifications_and_SSE.md`](./19_Durable_Event_Notifications_and_SSE.md).
The remaining boundary is parking generic long tool-job continuations, followed
by distributed task leases and fencing. Connector-specific effect authorities
and live-provider network-loss verification remain separate M18 workstreams.
