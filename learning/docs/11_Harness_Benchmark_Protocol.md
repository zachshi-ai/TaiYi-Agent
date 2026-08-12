# TaiYi Harness Benchmark Protocol

## Why this benchmark exists

When one coding agent finishes a large-repository task and another reports
`exec error` or `llm request timeout`, the observation does not identify the
cause. The model, provider, context projection, subprocess supervisor, retry
policy, workspace isolation, or completion controller may differ. A benchmark
that changes several of those variables at once cannot attribute the result to
the harness.

TaiYi therefore uses two separate measurement layers:

1. **Protocol conformance** fixes model behavior with a deterministic scripted
   provider and injects typed faults into the production TaiYi runtime. It
   measures whether the harness retries, recovers, stops, and reports truthfully.
2. **Comparative execution** fixes the model, prompt, repository, budgets,
   workspace image, evaluator, and artifact extractor, then changes only the
   harness. It measures a real cross-harness outcome.

Protocol conformance is implemented here. Comparative execution is deliberately
fail-closed: a missing batch interface, unconfined workspace, or unavailable
common-model route produces `UNAVAILABLE` or `NOT_COMPARABLE`, never a synthetic
score.

## The measurement contract

Every benchmark cell freezes:

- case definition and digest;
- harness, adapter, model, and operating mode;
- initial workspace and acceptance-artifact digests;
- expected outcome: `DELIVER`, `BUDGET_EXHAUSTED`, or `SAFE_HANDOFF`;
- reported task state and typed failure;
- model calls, connector attempts, confirmed applications, duplicate effects,
  human handoffs, and duration;
- evidence from repository context, run events, and the Effect Ledger.

JSON outputs use versioned schemas and a canonical SHA-256 envelope. This makes
accidental or retrospective edits detectable. An exit code of zero is never
accepted as proof of task success: an external adapter must emit a normalized
receipt, and the evaluator must still observe the requested artifact.

## Deterministic fault matrix

The current suite executes six cases in all three modes through the production
Gateway, ReAct runtime, repository index, LLM retry path, checkpoint store,
Effect Ledger, and completion controller.

| Case | Injected condition | Quality | Balanced | Efficiency |
| --- | --- | --- | --- | --- |
| `clean_delivery` | none | deliver | deliver | deliver |
| `llm_two_transient_failures` | two provider 503 failures | deliver | deliver | budget exhausted |
| `effect_two_preapply_failures` | two connector failures, authority proves no apply | deliver | budget exhausted | budget exhausted |
| `effect_applied_receipt_timeout` | write applies, connector receipt is lost | deliver once | deliver once | deliver once |
| `effect_ambiguous` | target matches neither pre-state nor intended state | safe handoff | safe handoff | safe handoff |
| `large_repository_context` | 1,200 fixture files plus README | deliver | deliver | deliver |

The three modes change bounded effort, not truth semantics. In particular, all
modes must stop on an ambiguous effect, and none may claim completion simply to
improve delivery rate.

## Baseline interpretation

The committed `protocol-v1` baseline contains 18 measured runs:

| Mode | Artifact delivery | Protocol conformance | False completions | Duplicate effects |
| --- | ---: | ---: | ---: | ---: |
| Quality | 83.3% (5/6) | 100% | 0 | 0 |
| Balanced | 66.7% (4/6) | 100% | 0 | 0 |
| Efficiency | 50.0% (3/6) | 100% | 0 | 0 |

Quality is not shown as 6/6 because the deliberately ambiguous-effect case
correctly transfers control to a human without producing the requested artifact.
Calling that a successful delivery would reward false completion. Protocol
conformance is the score that recognizes the safe handoff.

Run the benchmark with:

```bash
taiyi benchmark protocol --output research/benchmark/results/protocol-v1
taiyi benchmark comparative --pi-executable /path/to/pinned/pi \
  --output research/benchmark/results/comparative-smoke-v1
taiyi benchmark probe --output /tmp/taiyi-harness-probes
```

The protocol command writes a manifest, external capability cells, one receipt
per run, a machine-readable aggregate, and `REPORT.md`. The comparative command
runs the controlled transport/tool layer described in
[`12_Controlled_Cross_Harness_Comparison.md`](./12_Controlled_Cross_Harness_Comparison.md).
The probe command performs only read-only capability discovery.

## External harness cells

External harnesses are reported separately until they satisfy the comparison
contract:

- **Pi** documents print/JSON, RPC, SDK, session, and compaction interfaces. A
  pinned Pi 0.84.1 executable now has a normalized, sandboxed same-endpoint
  adapter in the controlled transport/tool layer. This is not yet a live-model
  quality comparison. See the [Pi coding-agent package](https://github.com/earendil-works/pi/tree/main/packages/coding-agent).
- **OpenClaw** provides an agent JSON interface, but its main-session tools run
  on the host unless sandboxing is configured. The observed local profile is
  therefore not comparable until a dedicated isolated profile and common model
  route exist. See the [OpenClaw repository](https://github.com/openclaw/openclaw).
- **ZCode** documents workspace, execution, Goal Mode, recovery, `/goal`, and
  `/compact`, but no authoritative non-interactive batch adapter was found. The
  installed desktop application remains an unscored black-box cell. See the
  [ZCode agent documentation](https://zcode.z.ai/en/docs/agents) and
  [commands](https://zcode.z.ai/en/docs/commands).

This is a capability statement, not a quality ranking.

## Requirements for a real cross-harness run

Before TaiYi, Pi, OpenClaw, and ZCode results can share a table, each cell must
have the same comparability signature:

1. exact repository snapshot and clean isolated workspace;
2. exact task prompt, acceptance tests, and patch extraction;
3. the same provider endpoint and model revision;
4. fixed model-call, token, wall-clock, and tool-output budgets;
5. the same injected fault schedule;
6. no inherited user home, credentials, or prior session memory;
7. complete stdout/stderr, process-exit, model-attempt, and tool receipts;
8. an evaluator outside the harness under test.

The methodology follows the controlled-runtime principle used by
[Claw-SWE-Bench](https://arxiv.org/abs/2606.12344), while adding harness-specific
failure measures that patch-only evaluation misses: false completion, recovery,
duplicate effects, context continuity, and safe human handoff.

## What this does and does not prove

The protocol baseline proves that the current TaiYi implementation behaves as
specified under these deterministic faults. It does not prove that an arbitrary
provider will be reliable, that the 1,200-file fixture represents every monorepo,
or that TaiYi outperforms another harness.

The next benchmark phase must extend the controlled adapter contract to real
same-model tasks with context overflow,
stream idle, hard LLM timeout, long subprocess output, process-tree timeout,
restart, and duplicate-effect traps. Any harness that cannot be isolated or
cannot emit sufficient evidence remains visible but unranked.
