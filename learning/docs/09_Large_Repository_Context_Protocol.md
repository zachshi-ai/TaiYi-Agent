# TaiYi Large-Repository Context Protocol

## Problem

A large repository does not become usable model context by increasing a history
limit. Linear replay creates three coupled failures:

1. irrelevant files and old tool output consume the response budget;
2. a provider reports context overflow, which a generic retry cannot repair;
3. truncation discards the exact state needed to resume after a process restart.

The context layer must therefore be a runtime protocol, not another prompt. It
must decide what source evidence is admitted, preserve the canonical transcript,
compact without inventing completion claims, and freeze the exact provider
projection before a request can be retried.

## Lifecycle

TaiYi uses four explicit stages around a model turn:

```text
repository INDEXING
        |
        v
budgeted ASSEMBLY ---- prompt over local budget ----> COMPACTING
        |                                               |
        v                                               v
    LLM_WAITING -- provider CONTEXT_OVERFLOW --> bounded compact/reduce + retry
        |
        v
permit / tool / validation
```

`INDEXING`, `COMPACTING`, and `LLM_WAITING` are persisted `RunPhase` values. A
user inspecting the task can distinguish repository work from model latency and
from an ordinary provider retry.

## Repository snapshot and incremental index

The sandbox workspace is indexed separately from user/session memory. A snapshot
contains:

- a repository identity derived from the resolved root without exposing it in
  retrieval citations;
- Git `HEAD` when present;
- the sorted path/content-digest set, including tracked and non-ignored
  untracked files;
- file, chunk, skipped, and omitted counts plus a `complete` flag.

The snapshot id changes if Git `HEAD` changes, including an empty commit, or if
any indexed file digest changes. Refresh uses size, mtime, and ctime to reuse
unchanged file chunks. Changed files are re-read and re-chunked; removed files
and their search rows are deleted in the same SQLite transaction. The persistent
database uses WAL, full synchronization, a busy deadline, and a process-local
lock so request and recovery threads do not share one connection concurrently.

Inventory is bounded and explicit. Binary files, unsupported formats, symlinks,
oversized files, and build/vendor directories do not silently enter prompts.
Crossing `repository_index_max_files` records the omitted count and marks the
snapshot partial instead of claiming complete coverage.

## Hierarchical, source-traceable retrieval

The first implementation is deterministic lexical retrieval:

- directory chunks describe repository hierarchy;
- Python top-level classes/functions become symbol chunks;
- other text files use bounded line chunks;
- FTS5 selects candidates, then path and symbol matches receive explicit boosts.

Every returned snippet includes snapshot id, relative path, exact line span,
chunk kind/symbol, and SHA-256 content digest. Repository content is labelled as
untrusted data, not system instructions. A retrieval result that exceeds its
mode budget omits whole chunks and reports the omitted match count; it never
cuts a source fragment and pretends the fragment is complete.

The modes change exploration budget only:

| Mode | Repository tokens | Chunks | Intact recent tokens | Provider-overflow recoveries |
| --- | ---: | ---: | ---: | ---: |
| quality | 16,000 | 24 | 24,000 | 2 |
| balanced | 8,000 | 12 | 12,000 | 1 |
| efficiency | 4,000 | 6 | 8,000 | 1 |

Governance instructions, the frozen Task Contract, current goal, checkpoint,
and completion evidence are not mode-adjustable.

## Budget admission and tool artifacts

Before every model request, TaiYi estimates English/code and CJK tokens and
admits at most:

```text
context_window_tokens - context_response_reserve_tokens
```

Repository retrieval is reduced before any invariant is removed. A large tool
result is clipped only in the provider projection; the canonical conversation
stays intact, and the complete stdout/stderr paths remain attached to the typed
`StepResult`, event, and model observation. This preserves diagnosis and
independent validation without repeatedly paying to send megabytes to a model.

If frozen system/contract/goal material alone cannot fit, TaiYi fails with
`CONTEXT_OVERFLOW`. It does not discard the acceptance contract to keep the run
moving.

## Structured compaction

Compaction is deterministic runtime state, not an LLM's unverified recollection.
It writes an atomic `taiyi.context-compaction/v1` JSON artifact containing the
complete pre-compaction canonical messages, source digest, kept/removed indices,
and parent compaction id. The model receives:

- the frozen goal and transcript digest;
- artifact reference;
- prior user corrections;
- the governed step ledger with verdict, execution bit, output digest, and a
  bounded preview;
- referenced files and latest validation evidence;
- intact recent message groups.

A tool call and its result form one atomic group and are never separated.
Repeated compactions form an artifact chain. A summary explicitly says it is
runtime metadata rather than proof of success and warns the model not to replay
executed effects.

## Overflow and restart semantics

`CONTEXT_OVERFLOW` remains non-retryable inside provider failover. The runtime
catches that typed error outside the provider runner, compacts the canonical
conversation, reduces repository allocation, persists `COMPACTING`, and starts a
new bounded model attempt. Authentication, ordinary transport retry, and context
recovery remain distinct paths.

Before each model call, the continuation stores both:

- canonical messages used for future compaction;
- the exact projected model messages (or Workflow model context) including the
  retrieved repository snapshot.

A restarted process therefore replays what the interrupted provider was actually
shown, even if workspace files changed after the crash. After a successful tool
result, the repository is marked dirty and refreshed before the next new model
turn. The retry boundary still ends before permit/tool execution, so compaction
recovery cannot duplicate a side effect.

## Evidence

`tests/test_context_kernel.py` covers:

- source path/line/digest retrieval and mode budgets;
- bounded partial inventories;
- a 1,200-file index with one-file incremental refresh and database reopen;
- Git-HEAD-only snapshot changes;
- artifact-backed compaction, atomic tool pairs, and large-result projection;
- Agent and Workflow repository injection with the untrusted-user-role boundary;
- observable degraded operation when repository indexing fails;
- refresh after a real governed workspace mutation;
- provider overflow recovery without provider failover or duplicate tools;
- bounded failure when recovery is exhausted;
- frozen repository projection across Agent and Workflow process restart;
- process exit after compaction with exactly one prior tool effect.

These are deterministic fault-injection tests. They prove harness semantics, not
the reliability or latency of an external model provider.

## Research basis and deliberate boundaries

Pi's [compaction design](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)
demonstrates threshold compaction with a response reserve, an intact recent
tail, structured persisted entries, cumulative file tracking, and a rule not to
cut at tool results. OpenClaw's
[context-engine lifecycle](https://github.com/openclaw/openclaw/blob/main/docs/concepts/context-engine.md)
and [tool-result guard](https://github.com/openclaw/openclaw/blob/main/src/agents/embedded-agent-runner/tool-result-context-guard.ts)
demonstrate a separate context-engine lifecycle,
mid-turn tool-result protection, and treating context overflow as compaction
rather than generic provider failover. TaiYi adopts those protocol ideas while
keeping its own immutable contracts, permits, artifacts, and completion truth.

ZCode's harness implementation is not available as an authoritative open-source
reference. It must remain a black-box benchmark under the same repository,
model, task, and environment; observed task completion is not evidence of a
specific internal mechanism.

Current deliberate boundaries:

- indexing is synchronous but bounded and observable; a future phase should move
  refresh to the durable job scheduler for very large monorepos;
- retrieval is lexical/structural, not an embedding claim;
- token counts are conservative estimates rather than provider tokenizers;
- the index stores the current snapshot, while the exact per-turn repository
  projection is frozen in the durable run checkpoint;
- real OpenClaw/ZCode/TaiYi comparison still requires a controlled external
  benchmark rather than repository unit tests.
