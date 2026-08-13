# Durable Repository Index Jobs

Phase 7B2.4 moves repository refresh out of the task owner and into TaiYi's
durable supervisor. A large checkout is no longer one opaque synchronous call:
the HTTP request can return, the index process has its own stable job identity
and heartbeat, and a restarted Gateway reattaches to the same work instead of
starting a duplicate scan.

## Runtime protocol

```text
new task ── claim repository generation ── start/attach durable index job
   │                                                │
   │ HTTP 202                                       ├─ worker heartbeat
   │                                                ├─ atomic progress receipt
   │                                                └─ atomic snapshot receipt
   v
status: INDEXING + job_kind=repository_index
   │
   ├─ Gateway restart ── checkpoint generation + job_id ── reattach once
   ├─ task cancel ── detach consumer; cancel worker only when unshared
   └─ terminal receipt ── assemble bounded source context ── LLM_WAITING
```

The worker request freezes the resolved repository root, SQLite database,
inventory/file/chunk limits, progress path, receipt path, and logical operation
id. The operation id is:

```text
repository:<repository_id>:generation:<n>
```

`JobStore` claims that id before spawn. A retry can only attach to the existing
job; binding the same id to different work fails closed. The child writes no
model-facing output. It publishes bounded progress and one typed result receipt
with atomic replace and fsync, while the repository snapshot itself remains one
SQLite transaction.

## Freshness, coalescing, and recovery

A generation is a freshness claim, not a task number:

- new tasks share the current generation while its job is pending or running;
- the first new task after terminal settlement atomically advances the
  generation and performs a fresh metadata/content scan;
- a task recovering from a checkpoint keeps its persisted generation and
  therefore reattaches to the original job, even if that job already finished;
- a governed workspace mutation advances the persistent generation before the
  next model turn.

This avoids both opposite failures: concurrent readers do not create multiple
SQLite writers, while a later task cannot incorrectly reuse an old completed
job forever. Cross-process file locks protect generation and immutable request
claims; JobStore separately protects operation claims.

## Subscriber-level cancellation

Coalescing means a job may have more than one task consumer. Cancelling one task
must not destroy another task's source dependency. Each task therefore writes a
persisted attachment keyed by task id. Cancellation first writes that
consumer's durable cancellation marker:

- with no other active consumer, the supervisor terminates the index process;
- with another consumer, the cancelled task detaches and settles as
  `REPOSITORY_INDEX_CANCELLED`, while the shared job continues;
- the waiting runtime checks its own marker, so cancellation is not inferred
  from a shared process state.

Attachment cleanup is best-effort on normal settlement. Phase 7B2.5 adds
renewable expiry to these consumers, so a destroyed host does not remain an
immortal subscriber. See
[`18_Parked_Repository_Continuations.md`](./18_Parked_Repository_Continuations.md).

## Failure truth and observability

Index failures are not tool or model failures:

| Condition | Failure kind | Runtime behavior |
| --- | --- | --- |
| worker/startup/receipt failure | `REPOSITORY_INDEX_FAILED` | observable degraded source context; tools may remain available |
| task subscription cancelled | `REPOSITORY_INDEX_CANCELLED` | task fails without calling the model |
| supervisor ownership lost | `REPOSITORY_INDEX_LOST` | task fails and recovery remains explicit |

`GET /v1/tasks/<id>` exposes the authoritative JobRecord during `INDEXING` and
adds `job_kind=repository_index`, so clients do not poll the shell executor by
mistake. Events distinguish `repository_index_started`, `attached`, `heartbeat`,
`finished`, and `failed`. Quality, balanced, and efficiency share all of these
semantics; mode budgets cannot weaken durability, cancellation isolation,
failure attribution, or snapshot truth.

## Verification evidence

Deterministic tests prove:

- concurrent tasks receive the same live `job_id` and generation;
- the next sequential task receives a new generation and scans freshness;
- asynchronous submission exposes index JobRecord heartbeats and cancellation;
- cancelling one coalesced task leaves the other task and shared job running;
- a Gateway-owner exit immediately after attachment recovers with the same
  `job_id`, produces one snapshot, and reaches the model only after settlement;
- worker startup failure is classified as `REPOSITORY_INDEX_FAILED`;
- existing transaction rollback, context freezing, tool effects, and runtime
  recovery continue to pass in the full suite.

The production path was also exercised against the same pinned source used by
Phase 7B2.3:

```text
kubernetes/kubernetes@52ba90138eb40cab0987dac73e05c838149bdd1c
generation 0: 25,683 files, 59,825 chunks, SUCCEEDED, 7.190 s
generation 1: 0 changed, 25,683 reused, directory rebuild=false,
              SUCCEEDED, 0.551 s
snapshot: sha256:0c8150939370149dc60c8c15cb4a03dc09380c9913a42c551c2544f2cc6f7d72
unsearchable files: 1,492; Kubelet query produced source matches
```

The two generations had distinct durable job ids. This validates the production
worker and real repository scale; it is not a cross-harness ranking or a live
provider SLA measurement. The self-verifying receipt is committed at
`research/benchmark/results/durable-index-kubernetes-v1/validation.json` and is
checked by the automated suite.

## Remaining boundary

Phase 7B2.5 now parks asynchronous continuations without retaining one Python
waiter thread per task, renews expiring consumer attachments, and resumes through
one Gateway-wide wake loop. The remaining boundary is a durable event bus and
distributed, fenced task/generation leases across multiple hosts. Those
improvements must preserve the operation, receipt, and checkpoint contracts
defined here.
