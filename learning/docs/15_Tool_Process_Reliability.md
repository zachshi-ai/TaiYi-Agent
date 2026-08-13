# Tool-Process Reliability and Bounded Output

Phase 7B2.2 closes the production `exec error` boundary. A shell command is not
one opaque call: it has a durable supervisor, an owned process group, two output
streams, multiple deadlines, a terminal receipt, and—because arbitrary shell
may affect systems outside the workspace—an independent side-effect outcome.

## The failure that this phase prevents

An outer timeout alone cannot answer any of these questions:

- Did the tool fail to start, exit non-zero, receive a signal, become idle, or
  exceed an absolute deadline?
- Did SIGTERM stop the whole owned process group, or was SIGKILL required?
- Did output stop, or did the artifact merely reach its storage limit?
- Did a gateway restart attach to the existing operation or launch it twice?
- Did the command mutate an external system before its receipt was lost?

Flattening all of them into `exec error` makes retry unsafe. Flattening them into
an LLM timeout assigns the failure to the wrong owner.

## Job protocol v2

`taiyi.job/v2` preserves the v1 operation claim and durable supervisor while
adding a terminal process/output receipt. Existing v1 job records remain
readable and migrate to safe defaults when loaded.

The receipt now records:

| Boundary | Fields |
| --- | --- |
| Process | exit code, terminating signal, failure kind, timeout kind |
| Termination | reason, whether TERM escalated to KILL, whether the owned process group settled |
| Full streams | stdout/stderr byte counts and SHA-256 digests |
| Retained artifacts | captured byte counts and independent truncation flags |
| Recovery | stable operation id, job id, duration and error |

The same fields flow through `JobRecord`, `ExecResult`, the runtime event,
`StepResult`, and the checkpoint. A restart therefore does not have to infer the
old process result from log text.

`owned_process_group_settled` is intentionally narrow. It proves that the
supervisor-owned process group closed its output boundary after bounded
termination. It does not claim that a hostile program that successfully escaped
into another OS session was contained. Strong multi-tenant containment still
requires a container/cgroup, VM, or equivalent platform authority.

## Output remains drainable but storage is bounded

Each stdout and stderr stream has a configurable `tool_artifact_limit` (8 MiB
per stream by default). The worker continues draining after that limit, so a
producer cannot deadlock on a full pipe and continued output still refreshes the
idle deadline.

For a stream larger than its artifact budget, TaiYi retains:

1. a bounded head sample;
2. an explicit truncation marker;
3. a bounded tail sample;
4. the total number of bytes observed; and
5. the SHA-256 digest of the complete byte stream.

The artifact itself never exceeds the configured limit. The full-stream digest
proves identity when the original output is available elsewhere; it does not
pretend that discarded middle bytes can be reconstructed. `tool_output_limit`
remains a separate, usually smaller limit for the model-visible projection.

If artifact capture fails, the worker keeps draining and settles with
`TOOL_OUTPUT_CAPTURE_ERROR`; a successful command cannot silently masquerade as
a fully evidenced result.

## Two correct failure layers

The process supervisor and Effect Ledger answer different questions.

```text
tool process receipt
  TOOL_HARD_TIMEOUT / TOOL_IDLE_TIMEOUT / TOOL_LOST / ...
                         |
                         v
independent effect observation
  APPLIED | NOT_APPLIED | UNKNOWN
                         |
                         v
task outcome
  continue once | bounded same-key replay | NEEDS_INPUT
```

For an arbitrary shell timeout, the process receipt can accurately say
`TOOL_HARD_TIMEOUT` while the task correctly enters
`EFFECT_OUTCOME_UNKNOWN / NEEDS_INPUT`. The first value explains the executor;
the second prevents unsafe replay when external mutation cannot be disproved.
Both are persisted. This is not error masking—it is layered attribution.

## Controlled baseline

Run:

```bash
taiyi benchmark tool-faults \
  --output research/benchmark/results/tool-faults-v1
```

The committed baseline runs five production cases in quality, balanced, and
efficiency modes:

- a SIGTERM-resistant parent and descendant requiring SIGKILL;
- a silent process reaching the idle deadline;
- a successful stdout/stderr flood with bounded head/tail artifacts;
- a descendant that remains after its parent exits; and
- gateway exit after durable attach followed by restart and reattachment.

All 15 cells passed the same reliability floor: 15/15 protocol receipts, zero
false completions, and zero duplicate effects. The restart case has one
`tool_started`, one `job_reattached`, one `run_recovered`, and one marker write.
The three operating modes do not alter any of these invariants.

Pi, OpenClaw, and ZCode are marked `NOT_COMPARABLE` in this layer. None is scored
until one frozen controlled-tool interface can prove the same process-tree,
signal, output, restart, and receipt semantics. Their CLI error strings alone
cannot establish comparability.

## Next fault boundary

Phase 7B2.3 should combine this executor protocol with a frozen large repository:

- repository scan/index under file-count and byte limits;
- context growth and compaction while tools emit large output;
- gateway restart during index, model wait, and tool execution;
- network/provider interruption after repository evidence is frozen; and
- a portable controlled-tool adapter for any external harness that can expose
  authoritative lifecycle evidence.

That combined scenario—not a synthetic speed score—is the next useful
reproduction of the reported “OpenClaw fails on a very large repository while
another harness finishes” problem.
