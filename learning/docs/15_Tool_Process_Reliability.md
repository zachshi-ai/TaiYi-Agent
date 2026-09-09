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

`owned_process_group_settled` is intentionally narrow. It records that the main
process exited and the output capture threads finished after bounded termination.
It is not a census of every descendant: a child that redirects its output or
escapes into another OS session can fall outside this observation. Full process
containment still requires a container/cgroup, VM, or equivalent platform
authority, and a verified process-group liveness probe remains runtime work.

## CI regression: preserve termination cause through output cleanup

The Python 3.12 PR run for commit `15877bf` exposed a real race: an already
recognized hard timeout became `TOOL_LOST` because a capture thread was still
finishing after the first bounded join. The worker retained that observation
even after successful cleanup. The same race affected idle timeout and cancel.

Capture EOF and artifact finalization are now distinct facts. A slow fsync after
EOF does not imply a living descendant. The supervisor checks the final process
and capture-thread state, then classifies the result:

| Observation after cleanup | Result |
| --- | --- |
| Main process or capture still running | `LOST / TOOL_LOST`, settled false; retain timeout/reason/observed exit facts |
| Capture failed | `TOOL_OUTPUT_CAPTURE_ERROR`, with the capture error |
| Cancel or deadline initiated termination and cleanup settled | Preserve `TOOL_CANCELLED`, `TOOL_IDLE_TIMEOUT`, or `TOOL_HARD_TIMEOUT` |
| Natural parent exit left output pipes open and descendants needed termination | `TOOL_LOST`, even if subsequent cleanup succeeded |
| Natural exit, EOF observed, artifact finalization completed | Use the actual exit code/signal |

An exception after process startup is a lost supervision outcome, not a startup
failure; its receipt preserves known timeout, return code and signal. Startup
and permission failure classifications still apply when no child was launched.
These process facts do not resolve an external side effect: the Effect Ledger
continues to hold ambiguous writes for independent observation or human input.

`tests/test_durable_jobs.py` deterministically holds artifact finalization past
the initial grace period for hard timeout, idle timeout, cancellation and normal
exit. `tests/test_job_settlement_faults.py` injects incomplete cleanup and a late
supervisor persistence failure. Both use real child processes with controlled
faults, alongside the existing process-tree and three-mode matrix tests.

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
