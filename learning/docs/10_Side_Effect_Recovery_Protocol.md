# TaiYi Side-Effect Recovery Protocol

## Problem

`exec error`, timeout, and process exit describe what the caller observed. They
do not prove what the target system did. A refund may have completed before the
connection closed; a notification may have been delivered before its receipt was
lost; a process may have changed files and then exited non-zero. Retrying from the
exception alone can duplicate the real-world effect.

TaiYi therefore separates three facts:

1. **execution result** — what the connector or process reported;
2. **effect outcome** — what an independent authority observes in the target;
3. **task completion** — whether the frozen acceptance contract has passing
   evidence.

None can substitute for another.

## Intent-before-effect ledger

Before dispatch, the runtime writes a `taiyi.effect/v1` record into the atomic
task checkpoint. It freezes:

- deterministic logical `operation_id` and a digest of exact tool arguments;
- harness-owned side-effect class and replay policy;
- stable idempotency key derived from the logical operation;
- verification authority and its pre-dispatch snapshot;
- attempt and recovery counts.

The policy is frozen before connector code runs. A model, tool response, or
connector-provided label cannot loosen it after a failure. Rebinding the same
operation id to different arguments or a changed policy is checkpoint
incompatibility, not a retry.

## Side-effect classes and replay policies

| Effect class | Meaning | Default replay |
| --- | --- | --- |
| `NONE` | trusted harness implementation is side-effect-free | `SAFE` |
| `IDEMPOTENT` | repeated fixed operation converges to the same state | authority or key required |
| `REVERSIBLE` | mutation has an explicit compensation path | no replay without its protocol |
| `IRREVERSIBLE` | money, outbound send, remote delivery, or equivalent | `NEVER` without server key |
| `UNKNOWN` | harness cannot prove semantics | `NEVER` |

Tool names that merely sound read-only do not receive `NONE`. Arbitrary shell,
SQL, and HTTP requests remain `UNKNOWN`, because flags, hooks, stored procedures,
redirects, or target behavior can mutate state.

The initial built-in authority covers fixed-content `file:write`. It freezes the
pre-write existence/digest and target digest, then returns exactly one state:

- `APPLIED`: target equals the frozen target digest;
- `NOT_APPLIED`: target still equals the frozen pre-dispatch state;
- `UNKNOWN`: target matches neither state.

This authority is intentionally narrow and source-specific. A refund connector
must provide a refund-receipt authority; a notification connector must provide a
delivery authority. They cannot reuse file evidence or executor success text.

## Recovery decision table

| Observation | Frozen policy | Runtime action |
| --- | --- | --- |
| `APPLIED` | any | accept the logical action once and continue |
| `NOT_APPLIED` | `SAFE` / `VERIFY_THEN_RETRY` | bounded replay after checkpoint |
| any | `IDEMPOTENCY_KEY` and connector enforces key | bounded replay with same key |
| `UNKNOWN` | any | suspend in `WAITING_INPUT` |
| `NOT_APPLIED` | `NEVER` | fail this run; require a newly authorized task |

Durable subprocesses continue to reattach through the JobStore operation index.
The effect protocol does not create a second durable process attempt behind that
journal. It handles non-durable connectors and reconciles a terminal `LOST`,
timeout, signal, or non-zero result when an external mutation may already exist.

Three operating modes may change only the bounded recovery count: quality has
two effect-recovery attempts, balanced and efficiency have one. They use the
same classifier, evidence states, idempotency requirements, human boundary, and
prohibition on false completion.

## State machine

```text
PREPARED (checkpointed intent)
    |
    v
DISPATCHING ---- executor success ----> EXECUTOR_SUCCEEDED
    |                                      |
    | timeout / loss / non-zero            | frozen authority exists
    v                                      v
EXECUTOR_FAILED --------------------> EFFECT_VERIFYING
                                         /    |    \
                                  APPLIED  NOT_APPLIED  UNKNOWN
                                     |       |          |
                         CONFIRMED_APPLIED   bounded     AMBIGUOUS
                                     |       retry      |
                                     v                  v
                              continue task       WAITING_INPUT
```

`EFFECT_VERIFYING` and `WAITING_INPUT` are observable run phases. An ambiguous
task is not `SETTLED`, not `COMPLETED`, and its step is not marked executed.

## Human resolution is not approval

Pre-execution approval answers “may TaiYi perform this action?” Effect resolution
answers “what already happened?” Combining them would make an `approve` click
silently repeat an uncertain operation.

`POST /v1/tasks/{task_id}/effects/resolve` therefore accepts one of:

- `applied`: a human provides a receipt/evidence note; TaiYi records that
  authority and continues to normal validation;
- `not_applied`: the human confirms absence; TaiYi retries only if the frozen
  replay policy already permits it and governance grants a fresh permit;
- `abandon`: TaiYi settles the task as `EFFECT_OUTCOME_UNKNOWN`.

A non-empty audit note is mandatory. Concurrent or repeated resolutions are
serialized by the task lease and rejected after the checkpoint advances.

Human confirmation cannot directly produce `COMPLETED`. The remaining task and
independent acceptance checklist still run.

## Research basis

Stripe's [idempotent request contract](https://docs.stripe.com/api/idempotent_requests)
demonstrates the critical connector property: the same key returns the first
request result, including failures, so a network retry does not create a second
object. AWS's
[Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
distinguishes caller intent from superficially identical requests and treats the
idempotency token as part of the API contract. TaiYi applies those ideas at the
harness boundary but does not claim exactly-once execution: when neither a target
authority nor a server-enforced key can prove the outcome, it stops at
`AMBIGUOUS`.

## Evidence and deliberate boundaries

`tests/test_effect_protocol.py` exercises:

- fail-closed classification and policy/argument rebinding rejection;
- target-state observations for applied, not-applied, and concurrent-change
  outcomes;
- connector-returned failures and raised timeouts;
- bounded same-key replay after proven non-application;
- process exit during a non-durable effect, restart, and reconciliation in both
  Workflow and Agent runtimes;
- persisted ambiguity, three-state human resolution, duplicate-resolution
  rejection, and `NEVER` replay enforcement;
- effect ledger serialization and mode budgets.

The current implementation deliberately does not claim:

- a generic authority for arbitrary shell commands;
- production refund, notification, or SaaS connector idempotency;
- automatic compensation for reversible effects;
- distributed leases across machines beyond the current filesystem-backed run
  lease and operation journal.

Those claims require connector-specific implementations and controlled external
fault tests, not a broader prompt or a permissive default.
