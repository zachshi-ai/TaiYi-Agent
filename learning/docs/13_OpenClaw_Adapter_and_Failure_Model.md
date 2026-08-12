# OpenClaw Adapter and Failure Model

## Why this adapter matters

The reported symptom—OpenClaw alternates between `exec error` and
`llm request timeout` on a very large repository while ZCode finishes—is not
enough to identify a model problem. A harness can fail before the first model
request, while waiting for the first token, during a tool process, while draining
large output, or while compacting/reconstructing context. If all of those become
one generic timeout, retries happen at the wrong boundary and may duplicate
effects.

The OpenClaw comparison adapter is therefore an executable probe of the harness
boundary, not an attempt to imitate OpenClaw internally. It uses the published
[`openclaw agent exec`](https://github.com/openclaw/openclaw/blob/main/docs/cli/agent.md)
contract and only treats a run as measured when it receives the documented
stable JSON envelope.

## Pinned and isolated execution

The v2 baseline pins published OpenClaw `2026.8.1-beta.1`. The system-installed
stable `2026.7.1-2` is not reused because it lacks `agent exec`. Version/interface
drift is a typed blocker, not permission to fall back to a less isolated command.

Each run gets its own:

- workspace, home, temporary, state, configuration, and log directories;
- controlled provider/model identity and non-secret loopback placeholder;
- 25-second inner deadline and 30-second outer process-group deadline;
- deny-by-default macOS profile with writes restricted to the run root;
- network rule restricted to the exact controlled-model loopback port;
- acceptance observation performed after the harness exits.

Before the cell can run, three canaries prove that an in-root write succeeds, an
out-of-root write fails, and a different loopback listener cannot be reached.
Logs are created inside the confined root, sanitized, and only then copied into
the benchmark artifact.

## Tool-surface equality

OpenClaw's [tool policy](https://github.com/openclaw/openclaw/blob/main/docs/gateway/config-tools.md)
supports profiles plus allow/deny rules. The adapter permits only `read` and
`write`, explicitly denies `apply_patch` and broader execution/search/session
tools, disables code mode and tool search, and restricts filesystem tools to the
workspace.

Configuration is necessary but not sufficient. The controlled endpoint records
the tools advertised in every actual model request. The cell is comparable only
when every request exposes exactly the frozen manifest's `read/write` set. This
gate caught an early adapter version that appeared to pass but exposed ten tools,
including `exec` and `apply_patch`; that result was discarded and regenerated.

TaiYi is held to the same boundary. Its benchmark worker restricts both the
model-visible tool hint and the executor capability policy to `file:read` and
`file:write`. An unlisted shell action is rejected at the executor even if a
model proposes it.

## Normalized failure phases

The next real-provider matrix should preserve raw evidence but normalize every
failure into the phase that owned the deadline:

| Phase | Evidence | Safe reaction |
| --- | --- | --- |
| `PROCESS_START` | no PID or spawn error; zero model requests | fix adapter/environment; do not retry the LLM |
| `LLM_CONNECT` | request began but connection failed | bounded provider retry/failover |
| `LLM_FIRST_TOKEN` | connected, no first token before deadline | bounded provider retry/failover |
| `LLM_STREAM_IDLE` | stream began, then stalled | retry only before any tool proposal is accepted |
| `TOOL_RUNNING` | tool receipt/job id exists | reattach or cancel the process group; do not label as LLM timeout |
| `OUTPUT_DRAIN` | child exited or blocked with oversized stdout/stderr | preserve bounded logs and terminal signal |
| `CONTEXT_COMPACTION` | repository projection/compaction receipt exists | resume from frozen projection, not a fresh prompt |
| `EFFECT_UNKNOWN` | tool may have changed external state | independent observation or human resolution; never blind replay |

This is the core design answer to the large-repository symptom: timeout values
alone will not make TaiYi robust. It needs phase ownership, durable continuation,
bounded output, process-tree control, exact context artifacts, and effect truth.

## What v2 proves—and does not prove

V2 proves that TaiYi, Pi, and the pinned OpenClaw beta can traverse the same
streaming endpoint, observe the same read/write capability surface, execute one
confined write, emit a terminal receipt, and pass an independent artifact check.
It also proves that unavailable ZCode automation stays unranked.

V2 does not prove coding quality, production speed, large-repository stability,
or superiority over another harness. Phase 7B2.1 now covers controlled
first-token and stream-idle timeout attribution; see
[`14_Cross_Harness_Fault_Attribution.md`](./14_Cross_Harness_Fault_Attribution.md).
The remaining Phase 7B2 work still requires the same real provider/model
revision, repository image, context/token budgets, fault schedule, tool
permissions, and evaluator across every ranked cell. It should next target
context overflow, tool-process timeout, large output, process-tree cancellation,
restart continuation, and ambiguous side effects.
