# 太一 / The One (Taiyi)

> An Agent harness / Agent OS prototype for *deterministic* production tasks
> (code, transactions, compliance, process execution). Its reason to exist is one design
> decision: **governance authority and scheduling authority are physically
> separated.** A module that both does the work and signs off on the work has an
> incentive to skip the sign-off — Taiyi removes that incentive by design.
>
> 中文说明见 [`README_zh.md`](./README_zh.md)。

The project started from a real incident: an agent reported "task complete" while
silently swapping the git commit author, recording the user's code under someone
else's name. Surface success ≠ actually correct. Taiyi's goal is to make the
*implicit* acceptance criteria — authorship, compliance, safety — into code the
model **cannot bypass**, rather than rules it is merely asked to remember.

## Where things are — organized as 学 · 研 · 产 · 用 (learning · research · production · practices)

| Path | What |
|---|---|
| **Production (产)** — the Agent itself, stays at root | |
| `src/taiyi/` | **Production code** — reliable runtime, context, governance, execution, and validation modules |
| `tests/` | 391 collected tests covering governance, operating modes, durable jobs/effect recovery, repository context, LLM faults, benchmark contracts, and executable Skill gates |
| `web/` | Bundled React web UI (build output in `web/dist`) |
| `deploy/` | Dockerfile + docker-compose |
| `pyproject.toml` · `taiyi.example.yaml` | Packaging + config template |
| **Learning (学)** — why it's designed this way (`learning/`) | |
| [`learning/docs/`](./learning/docs/) | Design philosophy + five-layer architecture |
| [`learning/prd/`](./learning/prd/) | Product requirements & version plan |
| [`learning/tech/`](./learning/tech/) | Components, interfaces, deployment |
| [`learning/research/`](./learning/research/) | Borrowed-pattern analysis (how others do it) |
| [`learning/assets/`](./learning/assets/) | Interactive architecture diagram |
| [`learning/DEVELOPMENT_PLAN.md`](./learning/DEVELOPMENT_PLAN.md) | The modular build order |
| **Research (研)** — turning theory into a path; test & demo (`research/`) | |
| [`research/examples/`](./research/examples/) | Runnable examples against the production package |
| [`research/benchmark/`](./research/benchmark/) | Versioned harness protocol benchmark, per-run receipts, and baselines |
| [`research/demo/`](./research/demo/) | Phase 0 throwaway demo (mock everything; reference only) |
| **Practices (用)** — production-baked best practices (`practices/`) | |
| [`practices/`](./practices/) | Field-tested skills, prompts, ops notes (growing) |

## Current status

**The 17-module skeleton and the core vertical paths are built; Taiyi is an L3
production prototype evolving toward L4.** Governance, permits, ReAct, sandboxing,
validation, audit, and human resume have runnable implementations, and a real LLM
path has been verified with DeepSeek. The default mock executor and deferred
business connectors remain explicit non-production boundaries: green tests are
not treated as proof of real-world task quality.
A request enters via the CLI, HTTP, or the bundled web UI, is anchored to a
business goal, matched to a scenario, planned (rule- or LLM-driven), gated
step-by-step by governance, given a second-opinion review by the expert
committee, executed by the configured backend only when cleared, independently
validated (a failed check bounces it back), scored for value contribution,
traced/metered, remembered, and fed to the OODA outer loop — which turns a
recurring failure into a permanent governance check and sediments repeated work
into a gated skill. The loop is real: trajectories persist across restarts,
suggestions are filed automatically every task, and a human approves them into
the read-only rule/skill set on the next start. Every request also resolves a
quality/balanced/efficiency policy, freezes a Task Contract, and records per-
criterion evidence bound to the immutable contract, checker kind, and current
artifact digest before the independent completion controller can say done. The
contract also freezes operation parameters (for example Git remote/ref and refund
amount), and validation observes full tool calls rather than tool names alone. See
[`learning/docs/05_Immutable_Acceptance_Contracts.md`](./learning/docs/05_Immutable_Acceptance_Contracts.md).

Completion truth is independent from operating mode. A tool action run by the
side-effect-free mock executor ends in `SIMULATED`, even when every Harness check
passes; only a non-mock execution can end in `COMPLETED`. Simulations are not
remembered, value-scored, or mined into new Skills as delivered work.

Skill admission is no longer inferred from a complete Markdown file. Every
Skill needs at least three automatic cases; the nine cases shipped with the
three built-in Skills execute through the current
governance, scheduling, and validation path, produce an artifact-bound release
lock, and rerun when the gateway loads. Their evidence environment is explicitly
`mock`, and successful action cases end in `SIMULATED`: they prove Harness
behaviour, not live SQL, notification, refund, or Git
connector readiness. See
[`learning/docs/03_Executable_Skill_Quality_Gates.md`](./learning/docs/03_Executable_Skill_Quality_Gates.md).

### Three operating modes

- **Quality** — clarify material ambiguity, exhaustive evidence, up to 3 validation attempts.
- **Balanced** — ask on high-impact ambiguity, standard evidence, risk-triggered independent review, up to 2 attempts.
- **Efficiency** — AI-led reversible defaults, critical evidence, 1 attempt.

In Agent Runtime and model-backed Workflow Runtime the modes route to `quality_model`, `balanced_model`, and
`efficiency_model`. An unset route falls back to the default `model` and records
`fallback=true` in task evidence. All modes share the same governance and
authorization floor. See
[`learning/docs/02_Operating_Modes.md`](./learning/docs/02_Operating_Modes.md) and
[`learning/docs/04_Provider_Routing.md`](./learning/docs/04_Provider_Routing.md).

Quality mode also requires at least one objective-specific checker. Baseline
hygiene such as non-empty output cannot certify an unknown task as correct;
low-risk efficiency work may use that path only with an explicit
`coverage=baseline_only` label.

### Durable run protocol

All three modes share one persistence and recovery floor. `TaskState` describes
the user-visible outcome while an independent `RunPhase` reports whether the
harness is waiting for the model, requesting a permit, running a tool,
validating, waiting for approval, recovering, or truly `SETTLED`. A timeout in
`TOOL_RUNNING` therefore cannot be mislabeled as an LLM timeout, and a suspended
approval cannot be mistaken for a fully finished run.

With `base_dir` configured, every task writes fsync'd typed events and an atomic
checkpoint. Workflow and ReAct approvals persist the frozen contract, prior
steps, plan or conversation, and continuation point; a new process restores the
approval queue and still re-checks governance before execution.

Sandbox shell tools now run under a persistent supervisor rather than a
30-second blocking subprocess. Each operation gets a stable id before launch;
repeating that id reattaches to the same job instead of replaying its side
effect. Heartbeats, process-group cancellation, distinct idle/hard timeouts,
exact exit/signal status, and bounded stdout/stderr artifacts make long commands
observable while only a smaller tail enters model context. The worker keeps
draining after the artifact cap and records complete-stream byte counts and
SHA-256 digests, so storage stays bounded without inventing an idle timeout. A new executor can
reattach to running work, and a restarted gateway now claims a per-task lease,
reattaches the existing operation, restores the frozen Workflow plan or ReAct
conversation, and continues from the exact next step. `POST /v1/tasks` also
supports `async=true`; task status, typed events, job heartbeats, and cancellation
are available without holding one HTTP request open. TaiYi still never auto-reruns
an ambiguous non-durable external effect. See
[`learning/docs/07_Durable_Runtime_Protocol.md`](./learning/docs/07_Durable_Runtime_Protocol.md).

### Side-effect recovery protocol

Before either runtime dispatches a governed tool, TaiYi now checkpoints a frozen
Effect Record: the logical operation and argument digest, a harness-owned effect
class, replay policy, stable idempotency key, and any independent observation
authority. Connector text never gets to declare itself safe or idempotent.

After a timeout, cancellation, connector exception, or process exit, the harness
distinguishes `APPLIED`, `NOT_APPLIED`, and `UNKNOWN`. An independently observed
application continues exactly once; proven non-application permits only a bounded
policy-authorized replay with the same key; unknown state enters `WAITING_INPUT`
without marking the step executed. Human `applied`, `not_applied`, and `abandon`
resolutions require an audit note and are separate from pre-execution approval.
Arbitrary shell/SQL/HTTP and irreversible operations default to `NEVER` replay.
Quality, balanced, and efficiency change only recovery budgets, never this truth
boundary. See
[`learning/docs/10_Side_Effect_Recovery_Protocol.md`](./learning/docs/10_Side_Effect_Recovery_Protocol.md).

Model requests use a separate resilience protocol. OpenAI-compatible responses
are streamed under distinct connect, first-token, stream-idle, and hard
deadlines. Typed 429/5xx/network/timeouts may retry or move through a
mode-prioritized provider pool; auth failures and invalid requests stop
immediately. Context overflow never triggers provider failover: it enters the
separate structured compaction protocol described below. Every failure, backoff, failover, and successful
recovery is persisted. A process restart honors the remaining backoff and frozen
message/plan, while the retry boundary ends before any model response can trigger
a tool, so a model retry cannot replay an external side effect. Quality uses the
largest retry budget and tries its strongest route twice; balanced switches
after one failure; efficiency has the shortest two-attempt budget. See
[`learning/docs/08_LLM_Request_Resilience.md`](./learning/docs/08_LLM_Request_Resilience.md).

### Large-repository context protocol

With a sandbox workspace, TaiYi builds a persistent incremental repository
snapshot keyed by Git HEAD and file-content digests. It indexes directory
structure, Python symbols, and bounded line chunks; retrieval gives the model
only mode-budgeted snippets carrying snapshot id, path, exact line span, and
content digest. Repository text is explicitly untrusted data, never a system
instruction.
Inventory completeness and text-search coverage are reported separately, so
binary, oversized, unsupported, or unreadable files cannot disappear behind a
misleading `complete` flag. Index progress is checkpointed every bounded batch;
an interrupted transaction publishes no partial snapshot. Unchanged directory
FTS chunks are reused unless the path set changes.

Before every Agent or model-backed Workflow request, the context engine reserves
response space, bounds large tool-result projections, and compacts old history
when necessary. Compaction is a deterministic, atomic JSON artifact containing
the complete canonical source transcript plus a structured step/evidence ledger;
tool calls are never separated from their results. The exact provider projection
is frozen in the checkpoint, so restart replays the same repository evidence even
if files changed after the crash. A typed provider `CONTEXT_OVERFLOW` can only
take a bounded compact-and-retry path; it cannot silently switch models or replay
a prior tool effect. Quality, balanced, and efficiency change retrieval/recent-
history budgets, not contracts or recovery truth. See
[`learning/docs/09_Large_Repository_Context_Protocol.md`](./learning/docs/09_Large_Repository_Context_Protocol.md).

With `executor: sandbox`, Taiyi can also enable a read-only Git authority. It
snapshots HEAD and repository-local identity before execution, then independently
proves that a new commit exists and that both author and committer match the
frozen identity. See
[`learning/docs/06_External_Authority_Checks.md`](./learning/docs/06_External_Authority_Checks.md).

An opt-in Git remote authority freezes the intended commit, remote URL digest,
and branch before a push, then runs a separate read-only `git ls-remote` check.
A successful executor receipt cannot certify a remote branch that did not move.

For GitHub remotes, an additional opt-in platform authority queries GitHub after
the push. It separately proves that the GitHub branch points to the frozen SHA
and that GitHub maps both author and committer to the configured expected login.
This closes the gap between a locally correct email and the account attribution
shown by GitHub.

Sandbox business tools fail closed when no SQL, notification, or refund Connector
is configured; a `[deferred:...]` result is never treated as successful execution.
Task responses expose `execution_environment=mock|workspace|custom` so simulated
work cannot be mistaken for workspace execution; mock tool actions have the
distinct terminal state `SIMULATED` rather than `COMPLETED`.

The layers: M1
Governance Core (rules-as-data, fail-closed, audit log); M2 Scheduler + boundary
(no execution capability; permits only); M3 Task Runtime (PDCA loop + state
machine); M4 LLM layer (a model **cannot bypass governance**; the
**OpenAI-compatible adapter is wired and verified** — Ollama / DeepSeek / 智谱 /
Moonshot / OpenAI all work via one `base_url`); M5 Tool Runtime (sandboxed
execution, credential isolation, SSRF, **macOS `sandbox-exec` deny-all
isolation**); M6 Validation Engine (cheapest-first checklists, isolated/
calibrated model judge, bounce-back); M7 Memory (5-layer SQLite/FTS5/vector/
Honcho, **multi-turn session history**); M8 Scenario + Skill engine (scenarios
as data; runtime admission requires executable cases, an artifact-bound release
lock, and a current-process rerun); M9
Gateway (stdlib HTTP + CLI, auth/rate-limit, OpenAI-compatible endpoint,
**bundled React web UI served same-origin**); M10 Value Stream (dual-mode goal
anchoring, value-contribution scoring, bottleneck detection); M11 Observability
(per-task traces, Prometheus `/metrics`, structured logs); M12 Iteration/OODA
(**closed loop**: SQLite-persisted trajectories, auto-filed suggestions,
human-approved rule/skill patches, validator regression set); M13 Multi-agent
(expert matrix with red-line veto and precedence arbitration — **wired as a
second permit gate that only tightens**, never loosens a governance decision);
M14 MCP server + channel adapter + Skill market (Taiyi callable by MCP clients,
still governed; gated skill installs); M15 Configuration & deployment
(taiyi.yaml + Docker); M16 Iterative agent loop (reason → act → observe, the
default "highest decision-maker" path); M17 Human approval & resume (HITL, with
**resume re-checking the permit** so a rule tightened during suspend is honored).
To go live: `pip install -e ".[live]"` (adds httpx), set provider + base_url in
the config, restart. (Phase 0's demo remains under `demo/` as reference.)

M18 Phase 7A adds a controlled harness benchmark: six deterministic
fault/large-repository cases run across all three operating modes through the
production retry, context, checkpoint, Effect Ledger, and completion paths. The
committed 18-run baseline has 100% protocol conformance, zero false completions,
and zero duplicate effects. Pi, OpenClaw, and ZCode remain capability cells—not
ranked results—until the same model, isolated workspace, and batch/evidence
contract can be proven. See
[`learning/docs/11_Harness_Benchmark_Protocol.md`](./learning/docs/11_Harness_Benchmark_Protocol.md).

Phase 7B1.1 adds a same-endpoint transport/tool comparison. TaiYi, pinned Pi
0.84.1, and published OpenClaw 2026.8.1-beta.1 each completed the controlled
write through real streaming HTTP with two model requests, one tool call,
external artifact acceptance, and zero false completions. Their model-visible
and executable tool surfaces are constrained to the frozen read/write budget and
verified from observed requests. ZCode remains visible `NOT_COMPARABLE` until an
authoritative batch/evidence interface is callable. This is explicitly not a
model-quality ranking. See
[`learning/docs/12_Controlled_Cross_Harness_Comparison.md`](./learning/docs/12_Controlled_Cross_Harness_Comparison.md).

Phase 7B2.1 adds deterministic first-token and stream-idle stalls. Across TaiYi,
Pi, and OpenClaw, all six comparable fault cells were attributed to the correct
LLM phase, terminated safely, and produced zero false completions. The receipt
also separates harness startup from model waiting: in this local baseline,
OpenClaw reached its first model request in about 7.8 seconds versus about 0.2
for TaiYi and 0.4 for Pi. Those cold-start observations explain why a short
overall timeout can expire before OpenClaw has contacted the model; they are
diagnostic evidence, not a productivity ranking. See
[`learning/docs/14_Cross_Harness_Fault_Attribution.md`](./learning/docs/14_Cross_Harness_Fault_Attribution.md).

Phase 7B2.2 moves that diagnosis into the production tool runtime. Five real
process faults run under all three operating modes: a SIGTERM-resistant process
tree, a silent idle timeout, stdout/stderr flood, a lingering descendant, and a
gateway restart after job attach. The signed 15-cell baseline has 15/15 protocol
passes, zero false completions, and zero duplicate effects. Arbitrary shell
timeouts keep their exact executor failure while safely escalating an unknown
external effect to `NEEDS_INPUT`. Pi, OpenClaw, and ZCode remain
`NOT_COMPARABLE` for this layer until they expose the same authoritative
controlled-tool lifecycle. See
[`learning/docs/15_Tool_Process_Reliability.md`](./learning/docs/15_Tool_Process_Reliability.md).

Phase 7B2.3 combines the previously separate boundaries in one pinned large
repository task. The signed Kubernetes baseline indexes 25,683 files into 59,825
searchable chunks, explicitly reports 1,492 unsearchable files, and injects
index-process exit, frozen-snapshot first-token timeout, large tool output plus
context overflow, and model-wait process exit in all three modes. All 12 cells
passed with zero false completions and zero duplicate effects. The run also found
and fixed an unchanged-directory-index rebuild that had cost roughly 248 seconds
per refresh; equivalent post-fix cells completed in roughly 1.0–2.5 seconds.
External harnesses remain `NOT_COMPARABLE` until the same source/model/fault and
authoritative receipt boundary is observable. See
[`learning/docs/16_Large_Repository_Combined_Resilience.md`](./learning/docs/16_Large_Repository_Combined_Resilience.md).

Phase 7B2.4 moves repository refresh into the durable supervisor. Concurrent
tasks share one live generation, later tasks perform a fresh generation, and a
restarted Gateway reattaches the checkpointed job instead of creating another
SQLite writer. Cancellation is per task subscriber: it terminates a sole index
job but cannot break another task sharing the same generation. Status exposes
the repository JobRecord separately from tool jobs. The production path indexed
the same pinned 25,683-file Kubernetes checkout in 7.190 seconds and completed
an unchanged second-generation scan in 0.551 seconds. See
[`learning/docs/17_Durable_Repository_Index_Jobs.md`](./learning/docs/17_Durable_Repository_Index_Jobs.md).

Phase 7B2.5 parks asynchronous Agent and Workflow continuations after they attach
to that durable index job. Their transient task thread and task lease are
released while indexing continues; one Gateway-wide wake loop renews expiring
consumer leases and resumes the frozen continuation from the same job receipt.
A replacement Gateway can perform that wake after the first monitor closes. On
the pinned Kubernetes checkout, the task parked 0.013 seconds after creation,
the 25,683-file job completed in 7.129 seconds, and attempt 2 settled from the
same job id. See
[`learning/docs/18_Parked_Repository_Continuations.md`](./learning/docs/18_Parked_Repository_Continuations.md).

Phase 7B2.6 makes those wakes durable and client-visible. Every supervisor emits
an fsync'd terminal notification after its authoritative result; the Gateway
incrementally consumes that journal instead of scanning every parked checkpoint
and JobRecord at 50 ms intervals. Task events now support bounded long polling
and SSE with revision ids plus `Last-Event-ID` recovery. A real Kubernetes run
disconnected after parked revision 4, resumed at revision 5 with no replay, and
settled at revision 12 from one 7.139-second index job. See
[`learning/docs/19_Durable_Event_Notifications_and_SSE.md`](./learning/docs/19_Durable_Event_Notifications_and_SSE.md).

Phase 7B2.7 extends the same parked-continuation contract to ordinary durable
shell tools. After governance clearance, Effect Ledger dispatch, and stable
operation/job attachment are checkpointed, an asynchronous task releases its
Python waiter and task lease while the supervisor continues. A generic Gateway
waker consumes tool and repository notifications, verifies terminal JobRecords,
then reattaches the frozen continuation without replaying the command. A
Gateway-restart run read 31,297 files (277.6 MB) from the pinned Kubernetes
checkout, parked in 0.023 seconds, resumed SSE at the next revision, and settled
from one 2.147-second Job. See
[`learning/docs/20_Parked_Tool_Continuations.md`](./learning/docs/20_Parked_Tool_Continuations.md).

Phase 7B2.8 replaces process-local task ownership with renewable, monotonically
fenced leases. Every event and checkpoint records its write token; an owner must
renew and validate that token inside the lease transaction before it can publish
another transition. A delayed model response from an expired owner is therefore
stopped before governance or tool dispatch rather than misreported as a task
failure. A controlled two-process run advanced ownership from token 1 to 2,
settled once through the replacement Gateway, and produced no stale file effect.
See [`learning/docs/21_Fenced_Task_Ownership.md`](./learning/docs/21_Fenced_Task_Ownership.md).

### Run it yourself

One command, straight from GitHub (repo is public, no clone needed). pipx is
recommended — a global `taiyi` command in its own isolated environment:

```bash
pipx install "taiyi[live] @ git+https://github.com/zachshi-ai/TaiYi-Agent.git"
# or with pip:  pip install "taiyi[live] @ git+https://github.com/zachshi-ai/TaiYi-Agent.git"
taiyi init                                  # interactively write taiyi.yaml (optional — defaults work)
taiyi serve --config taiyi.yaml             # the `taiyi` command is now on your PATH
# uninstall:  pipx uninstall taiyi   (or pip uninstall taiyi)
```

Or clone and install editable (for development):

```bash
pip install -e ".[dev]"                     # core + tests (including live-adapter tests)
pip install -e ".[live]"                    # adds httpx — required for a real LLM
cp taiyi.example.yaml taiyi.yaml            # edit: provider/base_url/model/api_key, executor, auth…
taiyi serve --config taiyi.yaml             # HTTP gateway (+ /metrics, OpenAI API)
# → open http://127.0.0.1:8080/ for the bundled web UI
#     (chat/tasks, approvals, OODA review, memory/metrics, config)
# or:  docker compose -f deploy/docker-compose.yml up
# set `executor: sandbox` + `sandbox_backend: sandbox_exec` (macOS) for real,
#   kernel-isolated, governed execution
```

Go live with a model — pick one, set it in `taiyi.yaml` (or the web Config
panel), restart:

```yaml
# Local Ollama (no key needed)
provider: ollama
base_url: http://localhost:11434/v1
model: qwen2.5:7b
api_key: null

# Or any OpenAI-compatible cloud model (DeepSeek / 智谱 / Moonshot / OpenAI / …)
provider: openai_compat
base_url: https://api.deepseek.com/v1
model: deepseek-v4-flash
api_key: sk-...
```

### Explore the layers

```bash
# Taiyi as an MCP server — governed tools for Claude Code / Cursor / etc.
python3 examples/mcp_demo.py        # or:  PYTHONPATH=src python3 -m taiyi.cli mcp

# Multi-agent review: red-line veto + precedence arbitration (contract review)
python3 examples/multi_agent_demo.py

# Iteration/OODA: a failure becomes a permanent check; repeated work becomes a skill
python3 examples/iteration_demo.py

# Observability: per-task traces, Prometheus metrics, structured logs
python3 examples/observability_demo.py

# Value-stream alignment: goal anchoring, scoring, bottleneck detection
python3 examples/value_stream_demo.py

# Run a task from the CLI (scenario auto-matched)
PYTHONPATH=src python3 -m taiyi.cli run "commit my changes"
# ...or start the HTTP gateway:  PYTHONPATH=src python3 -m taiyi.cli serve

# The gateway over HTTP-agnostic handlers (tasks, OpenAI-compatible chat, auth)
python3 examples/gateway_demo.py

# Scenario matching + the skill quality gate (ungated skills are refused)
python3 examples/skills_demo.py

# Memory: short-term, full-text (FTS5), semantic (vector), Honcho user model
python3 examples/memory_demo.py

# Validation: cheapest-first checks, isolated model judge, bounce-back into PDCA
python3 examples/validation_demo.py

# The founding case, for real: a governed commit in a throwaway git repo
python3 examples/sandbox_demo.py

# A model proposes tool calls; a prompt-injected one is still denied (no tokens)
python3 examples/llm_offline_demo.py

# Whole tasks through the PDCA loop, and the layers individually:
python3 examples/runtime_demo.py
python3 examples/governance_demo.py   # the governance engine on the founding cases
python3 examples/scheduler_demo.py    # planning + the governance boundary

# Run the test suite
pip install -e ".[dev]"
pytest
taiyi verify-skills  # execute the 9 built-in Skill gate cases
taiyi benchmark protocol --output research/benchmark/results/protocol-v1
taiyi benchmark comparative \
  --pi-executable /path/to/pinned/pi \
  --openclaw-executable /path/to/pinned/openclaw \
  --output research/benchmark/results/comparative-smoke-v2
taiyi benchmark faults \
  --pi-executable /path/to/pinned/pi \
  --openclaw-executable /path/to/pinned/openclaw \
  --output research/benchmark/results/comparative-faults-v1
taiyi benchmark tool-faults \
  --output research/benchmark/results/tool-faults-v1
```

Expected from the example:

```
Identity override (founding incident)      -> DENY          authorship.git_identity.no_override
rm -rf /                                   -> DENY          safety.recursive_delete.no_critical_path
git push                                   -> NEEDS_REVIEW  dev.git.push_needs_review
Refund 200                                 -> NEEDS_REVIEW  customer_service.refund.amount_over_threshold
```

## Rules are data, not prose

Red lines and scenario constraints live in `src/taiyi/rules/*.yaml`, so they are
reviewable via `git diff`, testable as fixtures, and loadable without re-parsing
a prompt. Adding a rule is a reviewed file change, never a runtime call from the
scheduler:

```yaml
id: authorship.git_identity.no_override
domain: authorship
severity: red_line
applies_to: ["shell:git*"]
trigger: pre_execution
check:
  type: deterministic
  match: args_any
  patterns: ["-c user.name=", "-c user.email=", "--author="]
on_fail:
  action: block
  message: "Overriding the git committer/author identity is forbidden."
precedence: 90
owner: platform-security
```

## Credits

Theory and original engineering reflection by **zachshi** (the
[@zachshi-ai](https://github.com/zachshi-ai) article under `research/`). AI
collaboration on docs/architecture: Mavis (MiniMax), Doubao (ByteDance), Claude
(Anthropic). Engineering references (implementation only): OpenClaw, NousResearch
(Hermes).

> The one that goes far is always the one that walks steady.
