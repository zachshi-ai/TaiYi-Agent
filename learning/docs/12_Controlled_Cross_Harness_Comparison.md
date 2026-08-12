# Controlled Cross-Harness Comparison

## Why Phase 7B starts with a controlled model

A cross-harness result is useful only when the harness is the variable. A live
model adds sampling, provider load, tokenization, rate limits, and model-revision
drift before the adapter contract has even been proven. Phase 7B1 therefore uses
a deterministic OpenAI-compatible loopback endpoint to test the transport,
tool loop, process boundary, evidence capture, and completion truth first.

The endpoint is intentionally not an intelligence benchmark. It always requests
one exact write and, after observing the tool result, emits one fixed terminal
message. A result from this layer must never be presented as a coding-quality or
productivity ranking.

## Frozen comparison contract

The `comparative-smoke-v1` manifest freezes:

- one prompt: create `result.txt` containing exactly `verified`;
- one initial workspace fixture and digest;
- one controlled model identity and policy digest;
- a 30-second outer wall deadline and two expected model requests;
- only read/write tools;
- no inherited user home, provider credentials, session, extensions, Skills,
  context files, or approval state;
- independent exact-content acceptance outside the harness under test;
- one comparability signature shared by every measured and blocked cell.

The aggregate passes a comparable cell only when the artifact is independently
observed **and** the harness emits a terminal completion. This catches both false
completion and silent delivery without a valid terminal receipt.

## Adapter boundary

TaiYi runs through its production OpenAI-compatible provider, Gateway, ReAct
runtime, sandbox executor, Effect Ledger, and completion controller. Pi runs via
its documented JSON mode, explicit custom provider/model configuration, and a
pinned `@earendil-works/pi-coding-agent` executable. Pi documents the relevant
[JSON event stream](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/json.md),
[custom model/provider configuration](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md),
and [container isolation guidance](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/containerization.md).

The TaiYi cell also runs in a separate child process with an isolated home,
minimal environment, outer wall timeout, and process-group termination. Its
receipt explicitly records that the current local file executor is path-confined
but not wrapped in a kernel sandbox. The Pi macOS adapter adds a deny-by-default
`sandbox-exec` wrapper because
Pi itself states that its tools run with the caller's permissions by default.
The wrapper permits writes only below the per-run root and permits outbound
network only to the exact controlled-model loopback port. Three canaries must
pass before Pi is marked comparable:

1. a write inside the run root succeeds;
2. a write immediately outside the run root fails;
3. a connection to a separately listening, unlisted loopback port fails.

Pi stdout/stderr are first written inside that root. After exit, the benchmark
controller replaces private filesystem roots and copies the sanitized logs into
the result artifact. Native clipboard loading is disabled in this non-interactive
cell so a transport benchmark cannot read the real macOS preference domain.

## What the first baseline found

| Harness | Version observed | Status | Requests | Tool calls | Independent acceptance |
| --- | --- | --- | ---: | ---: | --- |
| TaiYi | 0.1.0 | `MEASURED` | 2 | 1 | pass |
| Pi | 0.84.1 | `MEASURED` | 2 | 1 | pass |
| OpenClaw | 2026.7.1-2 | `NOT_COMPARABLE` | 0 | 0 | not run |
| ZCode | desktop application | `NOT_COMPARABLE` | 0 | 0 | not run |

TaiYi and Pi both traversed a real HTTP stream, executed one write, observed the
result, emitted a terminal completion, and passed the external file check. There
were zero false completions. The result proves only controlled transport/tool
conformance.

The adapter work also exposed a real failure-classification case. Pi initially
exited with `SIGABRT` before making any model request. The causes were in the
benchmark wrapper: a native desktop integration touched a denied preference
domain, and inherited stdout/stderr descriptors pointed outside the run root.
Neither failure was an LLM timeout. The normalized receipt made that visible
through `model_requests=0`, the process exit signal, isolation evidence, and
sanitized stderr. This is the same observability principle TaiYi needs for the
reported OpenClaw `exec error` / `llm request timeout` problem: classify by the
active phase and retain enough evidence to prove the classification.

OpenClaw's current main-branch documentation describes a headless
[`openclaw agent exec`](https://github.com/openclaw/openclaw/blob/main/docs/cli/agent.md)
interface with temporary state, isolated configuration, and stable JSON, plus a
separate [sandbox model](https://github.com/openclaw/openclaw/blob/main/docs/gateway/sandboxing.md).
The locally observed 2026.7.1-2 release does not expose that subcommand, so this
baseline records a versioned blocker instead of silently running its host-enabled
agent command. ZCode documents interactive commands including `/goal` and
`/compact`, but its [command documentation](https://zcode.z.ai/en/docs/commands)
does not provide an authoritative non-interactive evidence stream; it therefore
remains an explicit black-box cell.

## Run and inspect

Install a pinned Pi executable outside the repository, then pass its exact path:

```bash
taiyi benchmark comparative \
  --pi-executable /path/to/pinned/pi \
  --output research/benchmark/results/comparative-smoke-v1
```

The output contains the frozen manifest, one signed receipt per harness, the
aggregate report, and sanitized Pi stdout/stderr. `NOT_COMPARABLE` is a valid and
important outcome: unavailable evidence is never converted into a score.

## Next comparison layer

Phase 7B2 should preserve the same adapter and evaluator boundaries while adding
a frozen real provider/model revision and a small task matrix:

- repository search and patch generation;
- context overflow and compaction continuity;
- first-token, stream-idle, and hard model timeouts;
- large stdout/stderr and process-tree cancellation;
- harness restart and task continuation;
- ambiguous and duplicate-effect traps.

Only cells with the same repository image, model revision, token/model-call/wall
budgets, tool permissions, and evaluator may share a ranking. Every other result
stays visible but unranked.
