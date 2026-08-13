# Cross-Harness Fault Attribution

## The question this phase answers

`exec error` and `llm request timeout` are symptoms, not root causes. On a large
repository, the same visible timeout can mean that the harness never reached the
model, the connection never produced a first token, a stream stalled after it
started, a tool process hung, or context construction consumed the whole wall
budget.

Phase 7B2.1 tests one narrower invariant: when the model endpoint stalls, can the
harness evidence prove which LLM phase owned the timeout, terminate safely, and
avoid claiming completion?

## Frozen fault contract

The matrix contains two deterministic cases:

1. `model_first_token_timeout`: the endpoint accepts the request but emits no
   response event before the deadline;
2. `model_stream_idle_timeout`: the endpoint emits the first SSE event, then
   stops before completing the stream.

Every TaiYi, Pi, and OpenClaw cell receives the same controlled model identity,
prompt, workspace fixture, exact read/write tool surface, acceptance rule, and
fault schedule. Each external harness still runs with isolated home/config/state,
the deny-by-default macOS wrapper, exact-port loopback access, and process-group
termination. ZCode remains visible but unranked because there is no authoritative
batch evidence interface.

The fault endpoint records, for every request:

- delay from harness process start to request arrival;
- whether the first response bytes were emitted;
- whether the response completed;
- whether the client disconnected;
- model identity, message roles, and advertised tool names.

These observations are outside the harness under test. A receipt cannot turn a
generic error string into an LLM timeout without the matching delivery evidence.

## Normalized outcomes

| Observation | Normalized phase | Failure kind |
| --- | --- | --- |
| no model request before outer deadline | `PROCESS_START` | `HARNESS_STARTUP_TIMEOUT` |
| request accepted, no first response event | `LLM_FIRST_TOKEN` | `LLM_FIRST_TOKEN_TIMEOUT` |
| first event emitted, stream not completed | `LLM_STREAM_IDLE` | `LLM_STREAM_IDLE_TIMEOUT` |

An expected fault is `MEASURED` when the external observation matches the frozen
case—even if the harness process exits non-zero or is terminated by the outer
wall. It is comparable only if model identity and every request's tool surface
also match. A safe cell must remain uncompleted, produce no accepted artifact,
stay within the wall/model-request budget, and have no false-completion claim.

## Baseline result

The committed `comparative-faults-v1` result is complete:

| Harness | Comparable fault cells | Correct attribution | Safe failure | Mean first-request delay |
| --- | ---: | ---: | ---: | ---: |
| TaiYi | 2/2 | 2/2 | 2/2 | 0.239 s |
| Pi 0.84.1 | 2/2 | 2/2 | 2/2 | 0.442 s |
| OpenClaw 2026.8.1-beta.1 | 2/2 | 2/2 | 2/2 | 7.851 s |

All six comparable cells reached the same controlled endpoint, exposed the
frozen tool surface, were attributed to the expected phase, terminated safely,
and produced zero false completions. An independent repeat also completed 6/6
with zero false completions.

TaiYi used its phase deadline and three bounded attempts, settling both cases in
about 3.3 seconds. Pi and OpenClaw had no equivalent phase-terminal receipt in
these adapters, so the outer controller ended them at about 18 seconds. This is
a recovery-protocol observation, not a model-quality score.

The first-request delay is especially important for the reported OpenClaw
symptom. In this environment OpenClaw did not reach the controlled model until
roughly 7.7 seconds after process start. A five-second global timeout therefore
expires during harness startup, before any LLM request exists. Calling that an
`llm request timeout` would be provably wrong. The correct design keeps separate
startup, LLM-phase, tool-phase, and overall task budgets.

## Run and inspect

```bash
taiyi benchmark faults \
  --pi-executable /path/to/pinned/pi \
  --openclaw-executable /path/to/pinned/openclaw \
  --output research/benchmark/results/comparative-faults-v1
```

The output includes two signed manifests, eight signed cell receipts, one signed
aggregate report, a Markdown report, and sanitized per-cell logs. This layer is
not a productivity ranking and does not reproduce the user's private repository.

## Following fault layers

Phase 7B2.2 now delivers the production tool-process half:

- a tool process that stalls, exits by signal, or leaves a process tree;
- bounded stdout/stderr when a command produces very large output;
- restart/reattach after a tool has started;
- ambiguous and duplicate-effect traps.

The 15-cell signed baseline is described in
[`15_Tool_Process_Reliability.md`](./15_Tool_Process_Reliability.md). Phase
7B2.3 should combine it with repository indexing, context overflow, and provider
interruption on a frozen large-repository image. Every layer preserves the same
rule: the owning phase—not an error-message substring—decides retry,
reattachment, cancellation, or human handoff.
