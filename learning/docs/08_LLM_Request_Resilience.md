# TaiYi LLM Request Resilience

## Problem

`llm request timeout` is not one failure. The request may fail before a TCP/TLS
connection exists, after connection but before the first response byte, between
stream chunks, or after a stream has remained active beyond the task's acceptable
wall time. Treating all four as one exception makes diagnosis, retry, and recovery
unsafe.

TaiYi therefore treats one model turn as a durable protocol that ends before any
returned tool proposal is permitted or executed.

## One attempt, four deadlines

The OpenAI-compatible adapter always asks for a stream and enforces:

| Deadline | Default | Failure kind | Meaning |
| --- | ---: | --- | --- |
| connect | 10 s | `LLM_CONNECT_TIMEOUT` | DNS/TCP/TLS could not be established |
| first token | 60 s | `LLM_FIRST_TOKEN_TIMEOUT` | no response body arrived |
| stream idle | 30 s | `LLM_STREAM_IDLE_TIMEOUT` | a started response stopped making progress |
| hard | 180 s | `LLM_HARD_TIMEOUT` | the complete attempt exceeded wall time |

The values are deployment configuration (`llm_connect_timeout`,
`llm_first_token_timeout`, `llm_stream_idle_timeout`, `llm_hard_timeout`) and can
also be supplied as `TAIYI_LLM_*` environment variables.

HTTP and transport failures are typed independently:

- 429 is `RATE_LIMIT`; `Retry-After` is a lower bound and cannot exceed the task's
  remaining retry budget;
- 5xx, 408, and 425 are retryable `LLM_SERVER_ERROR`;
- connection/transport faults are retryable;
- 401/403 are non-retryable `LLM_AUTH_ERROR`;
- other invalid responses are non-retryable `LLM_PROTOCOL_ERROR`;
- context length/overflow is non-retryable inside provider retry. The outer
  runtime now invokes the Phase 5 structured compaction protocol and retries the
  transformed turn under a separate bounded context-recovery budget.

No adapter fabricates a response after failure.

## Mode budgets and routing

The invariant is shared; only retry budget and provider preference differ:

| Mode | Attempts | Primary attempts | Total retry window | Backoff |
| --- | ---: | ---: | ---: | --- |
| quality | 4 | 2 | 180 s | 1–30 s |
| balanced | 3 | 1 | 60 s | 0.5–10 s |
| efficiency | 2 | 1 | 15 s | 0.1–2 s |

Quality preserves its strongest model for a second attempt before degrading.
Balanced and efficiency can switch after the first retryable failure. Candidate
order is mode-aware and duplicate provider/model pairs are removed. A failover is
observable evidence, never a silent replacement.

Authentication and protocol failures stop immediately in every mode. Context
overflow never triggers provider failover; it either succeeds through structured
compaction or remains a typed `CONTEXT_OVERFLOW` failure.
No mode can enlarge its retry count or deadline beyond its resolved task policy.

## Durable state machine

```text
LLM_WAITING --retryable failure--> RETRY_BACKOFF --deadline reached--> LLM_WAITING
     |                                  |
     | non-retryable / exhausted        | process exit
     v                                  v
  SETTLED(FAILED)                 RECOVERING -> RETRY_BACKOFF
     |
     | response accepted
     v
permit / tool / validation (outside retry scope)
```

Each retry checkpoint freezes the Workflow planning round or the complete ReAct
message list plus:

- `attempts_used`;
- absolute `deadline_at`;
- absolute `retry_not_before`.

After restart, the task lease prevents two processes from advancing the same
turn. The new process resumes the remaining backoff, selects the next candidate,
and preserves the original attempt budget. It cannot reset a rate limit simply by
restarting TaiYi.

`provider_route` records failure kind, status, provider/model, the resolved
failover route, and the final response model. Events record attempt start,
failure, scheduled/resumed backoff, budget exhaustion, and success.

## Side-effect boundary

The retry runner accepts messages and returns one response. It has no executor,
permit, or connector authority. Only after it returns does the runtime inspect a
tool proposal, request governance clearance, create a deterministic operation id,
and execute. Consequently:

- a timed-out request can be retried;
- a returned tool proposal is handled once by the caller;
- tool execution is never inside model retry;
- a crash after tool launch follows the durable job reattachment protocol, not
  this protocol.

## Fault evidence

The automated suite injects delayed first chunks, stalled streams, hard deadline
overrun, connect failure, 429 with `Retry-After`, 5xx, auth failure, context
overflow, provider failover, retry-budget exhaustion, and process exit during
backoff. It verifies both Workflow and ReAct recovery and proves that model
failover does not duplicate the governed tool action.

This is protocol-level evidence using deterministic transports/providers. A
future benchmark must still repeat the matrix against real providers and unstable
networks; passing mocks is not evidence that an external service is reliable.
