# Large-Repository Combined Resilience

Phase 7B2.3 combines repository indexing, context admission, provider retry,
process restart, large tool output, and effect deduplication in one production
path. The motivation is the reported failure pattern where one harness emits
`exec error` or `llm request timeout` on a very large checkout while another
finishes. A total wall-clock timeout cannot diagnose that incident: the owner
may be repository indexing, the provider, a tool process, or recovery itself.

## Protocol boundary

One model turn now exposes this durable sequence:

```text
INDEXING --heartbeat--> atomic snapshot
    |                         |
    | process exit            v
    +---- RECOVERING ----> context projection frozen
                                  |
                    provider fault or process exit
                                  |
                     retry/replay frozen projection
                                  |
                    permit -> one tool effect -> result
                                  |
                     overflow -> COMPACTING -> model
```

The reliability floor is mode-independent:

- an interrupted index publishes no partial snapshot;
- progress and the exact interrupted phase survive process loss;
- retry and restart reuse the same snapshot-backed model projection;
- provider retry ends before tool authority, so it cannot duplicate an effect;
- compaction preserves the canonical transcript and prior effect ledger;
- completion is false if the frozen acceptance evidence is missing;
- inventory coverage and text-search coverage are reported separately.

Quality, balanced, and efficiency retain their different repository-token,
recent-history, provider-attempt, and recovery budgets. None can weaken the
atomic snapshot, checkpoint, effect, or completion rules.

## Index progress and the performance fault found

Repository refresh now persists a heartbeat at the start, every 250 files, and
at completion. The heartbeat records processed/total files, changed/reused/
skipped counts, omitted inventory, and elapsed time. A callback failure or
process exit rolls the SQLite transaction back; the existing workflow/Agent
continuation lets a new gateway re-enter `INDEXING` safely.

The real-repository run found a separate performance defect. Unchanged files
were correctly reused, but directory FTS chunks were still deleted and rebuilt
on every turn. On the pinned Kubernetes checkout, metadata reuse reached 25,500
files in about 0.2 seconds and then waited roughly 248 seconds rebuilding an
unchanged directory index. Directory chunks now rebuild only when the path set
changes. The same timeout-recovery cells subsequently completed in roughly
1.0–2.5 seconds each, while preserving a fresh file-content scan.

This is an important harness lesson: activity, owner, and progress must be
observable independently. A CPU-bound `INDEXING` interval is neither an exec
failure nor an LLM request timeout.

## Signed combined matrix

Run the dependency-free CI fixture:

```bash
taiyi benchmark large-repo-faults \
  --output research/benchmark/results/large-repository-fixture-v1
```

Or run the identical contract against a pinned real checkout:

```bash
taiyi benchmark large-repo-faults \
  --repository /path/to/pinned/checkout \
  --repository-query "known symbol or path" \
  --output research/benchmark/results/large-repository-real-v1
```

Four cases run in all three operating modes:

1. process exit after an index heartbeat, followed by atomic rebuild;
2. first-token timeout after source projection is frozen;
3. one large-output tool effect, provider context overflow, and compaction;
4. process exit during model wait, followed by exact frozen-projection replay.

Every manifest, receipt, and aggregate report is versioned and SHA-256 signed.
The committed real baseline uses
`kubernetes/kubernetes@52ba90138eb40cab0987dac73e05c838149bdd1c`:

- 25,683 inventoried files and 59,825 searchable chunks;
- 1,492 inventoried files explicitly marked unsearchable;
- zero omitted inventory files;
- 12/12 protocol passes;
- zero false completions and zero duplicate effects;
- about 40.8 seconds summed cell time in this local run.

The baseline records origin, Git HEAD, snapshot digest, coverage counts, query,
and signed receipts. It does not commit Kubernetes source or a local checkout
path.

## External harness comparison

Pi, OpenClaw, and ZCode remain `NOT_COMPARABLE` for this layer. A valid external
cell must expose the same pinned checkout, model endpoint, prompt/context budget,
fault timing, tool surface, completion evaluator, and authoritative restart and
effect receipts. CLI strings such as `exec error` and `request timeout` are
diagnostic observations, not evidence of equivalent failure ownership.

## Remaining boundary

Index work still occupies the submitting gateway worker even though it is now
heartbeat-observable and restart-safe. The next runtime step is a durable
background index job that lets a request park and reattach. Live provider/network
fault tests also require user-authorized credentials and cost; deterministic
transport injection is not presented as proof of any provider SLA.
