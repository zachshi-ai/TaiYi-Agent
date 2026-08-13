# Durable Event Notifications and Reconnectable SSE

Phase 7B2.6 separates durable truth from delivery notification. Run events,
checkpoints, JobRecords, and terminal receipts remain authoritative. A new
append-only notification journal only tells a waiting Gateway that it should
re-read those facts. Clients can follow the same persisted run stream through
server-sent events and resume from an exact revision after disconnection.

## Two-layer event contract

```text
authoritative plane                    notification plane
-------------------                    ------------------
runs/<task>/events.jsonl  <----------  RunStore condition wake
runs/<task>/checkpoint.json            cross-process bounded fallback
jobs/<job>/result.json     <----------  jobs/notifications.jsonl
jobs/<job>/job.json                     one Gateway wake loop
```

Notification loss, duplication, truncation, or corruption cannot change task
state. The Gateway verifies the JobRecord and frozen continuation before
recovery. A missing notification is detected by the consumer-lease refresh; a
malformed complete notification is advanced past, identified by SHA-256 digest,
and written to the audit log. An unterminated final line is not consumed until
the append completes.

Every durable supervisor now receives its stable job id, operation id, and
notification journal path in the immutable worker request. After fsync of the
terminal `result.json`, it appends and fsyncs one
`taiyi.job-notification/v1` record. Startup failure, supervisor loss, bounded
cancellation fallback, and subscriber-only repository cancellation publish the
same kind of wake hint from the owning process.

## Repository wake behavior

The Gateway no longer scans every checkpoint and polls every index JobRecord at
50 ms intervals. It incrementally reads the notification journal by byte cursor,
keeps a small set of terminal job and cancelled-consumer hints, and refreshes
parked checkpoints only when the local RunStore generation changes or a consumer
lease is due. The lease-bound scan is intentionally retained as the correctness
fallback for another Gateway process or a lost hint.

RunStore task leases remain the serialization boundary. Multiple Gateways may
observe the same durable notification, but only one can advance a task. A wake
notification is therefore at-least-once and harmlessly repeatable; the task and
job operations remain exactly-once at their existing contract boundaries.

## Client protocol

The existing JSON cursor remains available:

```http
GET /v1/tasks/<task_id>/events?after=12&limit=200
```

It now supports bounded long polling:

```http
GET /v1/tasks/<task_id>/events?after=12&wait=30
```

For a reconnectable stream, send `Accept: text/event-stream` or `stream=true`:

```http
GET /v1/tasks/<task_id>/events
Accept: text/event-stream
Last-Event-ID: 12
```

Each frame uses the durable run revision as its SSE `id`, the typed run event as
its `event`, and the complete persisted event document as JSON `data`. On
reconnect, `Last-Event-ID` is used when an explicit `after` query is absent. No
event at or below that revision is replayed. A comment heartbeat keeps an idle
connection observable, and the stream closes after the `SETTLED` event.

Local writers notify blocked readers immediately through a condition. A bounded
file recheck observes events written by another process. This changes delivery,
not truth: reconnect always starts by reading the fsync'd JSONL journal.

## Failure and security boundaries

- Authentication and rate limiting are applied before opening a stream.
- `after`, `limit`, wait, and heartbeat inputs are bounded and validated.
- SSE event names cannot contain newlines; the event payload is JSON encoded.
- Broken clients stop their HTTP handler without cancelling the task.
- The stream never synthesizes completion; only persisted `SETTLED` ends it.
- Quality, balanced, and efficiency modes share identical event semantics.

The bundled stdlib transport uses one HTTP handler thread per connected SSE
client. The notification journal assumes a shared durable filesystem. Multi-host
fan-out, distributed fencing, retention/compaction, and backpressure for very
large subscriber counts remain future work.

## Verification evidence

Deterministic tests cover local and cross-RunStore blocking waits, SSE over the
real HTTP server, `Last-Event-ID` replay exclusion, partial and corrupt
notification records, terminal tool-job notifications, notification-free lease
fallback, cancellation, Gateway restart, and both runtime shapes.

The production path was exercised against:

```text
kubernetes/kubernetes@52ba90138eb40cab0987dac73e05c838149bdd1c
parked at revision 4 after 0.017874 s; SSE connection deliberately closed
reconnected with Last-Event-ID: 4; first resumed revision: 5
resumed revisions: 5..12, strictly increasing, no replay
durable job: one job id, one SUCCEEDED notification, 7.139454 s
task: attempt 2, COMPLETED / SETTLED, 7.337750 s total
source: 25,683 files, 59,825 chunks
wake work: 28 incremental notification reads, 8 checkpoint scans
```

The signed receipt is committed at
`research/benchmark/results/durable-events-kubernetes-v1/validation.json`. This
is production-path and real-repository evidence, not a cross-harness ranking or
live-provider SLA.

## Remaining boundary

Durable event delivery is now reconnectable on one shared-filesystem deployment.
The next runtime step is to park long tool-job continuations using the generic
terminal notification journal, then replace POSIX-only task ownership with
distributed fenced leases. Connector-specific delivery authorities and live
provider/network interruption remain separate verification tracks.
