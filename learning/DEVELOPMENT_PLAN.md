# 太一 / The One — Development Plan

> How this project gets built: one self-contained module at a time, lowest risk
> and highest leverage first. Each module is a "move forward" unit — when you say
> *go*, the next module gets built, tested, and pushed. Nothing here is built
> speculatively ahead of need.

This plan turns the architecture package (`docs/`, `prd/`, `tech/`) and the
validated Phase 0 demo (`demo/`) into a production system. It is deliberately
sequenced to respect a **limited budget**: we build a thin vertical slice through
the highest-risk path before we build breadth.

---

## Guiding principles

1. **Vertical slice, not framework.** Quoting the design doc (§5.4): *"Don't
   build a framework. Build a vertical slice."* We harden one path end-to-end
   before adding channels, providers, or multi-agent breadth.
2. **Risk-reduction per unit effort.** Order modules by how much unreliability
   they remove per unit of work. Irreversible / high-blast-radius operations
   (writes, payments, identity, outbound publish, deletes) get gated first.
3. **Each module ships green.** Every module lands with tests and a runnable
   artifact, and CI must pass before it's considered done.
4. **The two core differentiators come first.** Governance/scheduling separation
   and rules-as-data are the project's reason to exist — so they are Module 1.
5. **Budget discipline.** Anything that costs money to run (real LLM tokens,
   hosted channels, vector DBs) is isolated into its own module so it can be
   scoped, deferred, or swapped without touching the core.

Maturity targets use the design's L0–L4 engineering scale (Design Doc §5.5).
Phase 0 left us at **L1→L2**; this plan drives toward **L4 (closed loop)**.

---

## Module roadmap

| # | Module | Status | Maturity after | Costs money? |
|---|--------|--------|----------------|--------------|
| **M1** | **Governance Core (rules-as-data)** | ✅ **Done** | L2 | No |
| **M2** | **Scheduler + governance boundary** | ✅ **Done** | L2 | No |
| **M3** | **Task Runtime (PDCA loop + state machine)** | ✅ **Done** | L2 | No |
| **M4** | **LLM provider layer (offline-first)** | ✅ **Done** | L2 | No (live = opt-in) |
| **M5** | **Tool Runtime (sandbox, credential isolation, SSRF)** | ✅ **Done** | L2 | No |
| **M6** | **Validation Engine (L4, per-task checklists)** | ✅ **Done** | **L3** | No (offline) |
| **M7** | **Memory (5-layer: SQLite/FTS5/vector/Honcho)** | ✅ **Done** | L3 | No (offline) |
| **M8** | **Scenario + Skill engine (quality gates)** | ✅ **Done** | L3 | No |
| **M9** | **Gateway + channels (CLI + HTTP, OpenAI-compatible)** | ✅ **Done** | L3 | No |
| **M10** | **Value Stream (H4: goal anchoring, scoring)** | ✅ **Done** | L3 | No (offline) |
| **M11** | **Observability (H3: traces, metrics, logs)** | ✅ **Done** | L3 | No |
| **M12** | **Iteration / OODA (L5) + Skill auto-generation** | ✅ **Done** | **L4** | No (offline) |
| **M13** | **Multi-agent (expert matrix + arbitration)** | ✅ **Done** | L4 | No (offline) |
| **M14** | **MCP server + channel adapter + Skill market** | ✅ **Done** | L4 | No (live channels = opt-in) |
| **M15** | **Configuration & deployment (taiyi.yaml + Docker)** | ✅ **Done** | L4 | No |
| **M16** | **Iterative agent loop (reason → act → observe)** | ✅ **Done** | L4 | No (live LLM = opt-in) |
| **M17** | **Human approval & resume (HITL)** | ✅ **Done** | L4 | No |

> Rough phase mapping: **M1–M5 = Phase 1** (trustworthy single-task vertical
> slice with a real model), **M6–M9 = Phase 2**, **M10–M12 = Phase 3**,
> **M13–M14 = Phase 4**. The PRD's calendar phases still hold; this table is the
> build order underneath them.

---

## Module details

### M1 — Governance Core (rules-as-data) ✅ Done
**Goal.** The neutral referee, with rules expressed as versionable data, that the
rest of the system asks for permission. This is the crown jewel and the first
vertical slice (Design Doc §5.4, steps 1–4).

**Delivered.**
- `taiyi.core` — the permit contract (`PermitRequest`/`PermitResponse`/`Verdict`)
  and a **tamper-evident, hash-chained audit log** (`AuditLog`).
- `taiyi.governance` — a `GovernanceEngine` that loads rules read-only and issues
  `ALLOW` / `DENY` / `NEEDS_REVIEW` verdicts, **fail-closed** (a red line is a
  one-vote veto; it always beats a scenario review).
- `taiyi/rules/*.yaml` — 7 rules as data: 4 red lines (git identity override,
  recursive delete, SSH key read, credential leak) + 3 scenario constraints
  (git push review, outbound report review, refund-over-threshold review).
- 17 passing tests reproducing all 6 feasibility-report cases plus conflict
  resolution and audit-tampering detection; `examples/governance_demo.py`; CI.

**Acceptance (met).** Red-line interception 100% on the test set; scenario
constraints scoped per scenario; every decision audited; audit chain detects
edits and deletions; rules load read-only and reject malformed/duplicate sets.

**Maturity reached.** L2 — separated governance, a real rule library, red lines
fail closed.

---

### M2 — Scheduler + governance boundary ✅ Done
**Goal.** A scheduler (the decision-maker) that plans a tool sequence for a task
and **must obtain a permit** for each step. Cements the request/permit contract
so governance and scheduling can later run as separate processes unchanged.

**Delivered.**
- `taiyi.governance.client` — the `PermitClient` boundary (a Protocol) plus
  `LocalPermitClient`, the in-process implementation. The scheduler depends only
  on this one method; swapping in an IPC/gRPC client later changes nothing for
  the scheduler. `LocalPermitClient` exposes only `issue_permit`, so the
  scheduler gets no handle on rules or the audit log — it cannot self-grant.
- `taiyi.scheduler.planner` — a pluggable `Planner` interface with `KeywordPlanner`
  (a faithful port of the Phase 0 router), `ExecutionPlan`, and `PlanStep`.
- `taiyi.scheduler.engine` — `SchedulerEngine` plans and `clear_plan`s steps
  through the boundary, returning a `PlanClearance`. It has **no execute method**.
- 9 tests + `examples/scheduler_demo.py`.

**Acceptance (met).** No step clears without an `ALLOW`; a DENY halts the plan
immediately; a NEEDS_REVIEW suspends it while preserving already-cleared steps
(e.g. the weekly-report SQL query survives when the outbound notify is held);
every step provably routes through the `PermitClient`; the scheduler exposes no
execution capability.
**Depends on.** M1.

### M3 — Task Runtime (PDCA loop + state machine) ✅ Done
**Goal.** Wire scheduler + governance into the PDCA main loop with the documented
state machine (PENDING→…→COMPLETED/REJECTED/NEEDS_REVIEW), producing a
`TaskContext` and archiving a replayable trajectory. Executor still mocked.

**Delivered.**
- `taiyi.runtime.state` — the `TaskState` machine from Technical Architecture §3.3.
- `taiyi.runtime.context` — `TaskContext` / `StepResult` flowing through the loop.
- `taiyi.runtime.executor` — `Executor` interface + side-effect-free `MockExecutor`
  (the real sandboxed executor is M5). Only cleared steps are ever executed.
- `taiyi.runtime.engine` — `TaskRuntime` drives interleaved permit→execute PDCA;
  shares one `AuditLog` with governance, so permit decisions and execution events
  land in a single hash-chained trajectory. `replay_task` reconstructs any task.
- `SchedulerEngine.request_permit` added (per-step clearance for the loop).
- 8 tests + `examples/runtime_demo.py`.

**Acceptance (met).** All six scenarios reach the correct terminal state
(COMPLETED / REJECTED / NEEDS_REVIEW); a half-finished task keeps its executed
steps (weekly report runs the query, suspends the notify); the commit denied by
the identity red line is never executed; each task replays from the audit chain
and the chain verifies after runs.
**Depends on.** M2.

> **M1–M3 together** are a runnable single-task engine: a request is planned,
> gated step-by-step, executed (mock) only when cleared, and archived to a
> tamper-evident trajectory. M4 makes the planner/executor real (and starts
> spending money); M5 makes execution real and sandboxed.

### M4 — LLM provider layer (offline-first) ✅ Done
**Goal.** A provider-agnostic LLM interface and an LLM-driven planner, proving the
key Phase 0 open question — **a model cannot bypass governance** — at zero cost.
Built offline-first per the budget decision; live providers are a later opt-in.

**Delivered.**
- `taiyi.llm.base` — `LLMProvider` interface + `LLMMessage` / `LLMResponse` /
  `ToolCall`, and `DEFAULT_LIVE_MODEL` (documentation for the future live seam).
- `taiyi.llm.offline` — `ScriptedProvider` (deterministic replay; used to
  simulate an adversarial/injected model) and `KeywordOfflineProvider`.
- `taiyi.scheduler.LLMPlanner` — implements the same `Planner` interface, so the
  model's proposed tool calls flow through the unchanged governance gate.
- 8 tests + `examples/llm_offline_demo.py`.

**Acceptance (met).** A model proposing an identity override or `rm -rf /` is
**denied** (`REJECTED`, nothing executed); a benign proposed plan completes; the
property holds regardless of provider.

**Remaining live opt-in (needs API key + budget).** The provider seam is wired:
`make_provider(config)` returns the right adapter by config, and a live adapter
is a single class implementing `LLMProvider.complete()` plus one SDK in the
`[live]` optional-dependency group. The iterative agent loop (M16) is real — it
feeds tool results back to the model — so dropping in an adapter makes the whole
ReAct loop run live. Governance behaviour does not change. **Depends on.** M3.

### M5 — Tool Runtime (sandbox + credential isolation + SSRF) ✅ Done
**Goal.** Real, constrained execution: the high-risk layer the gates protect.

**Delivered.**
- `taiyi.tools.credentials` — default-deny `safe_environment`: subprocesses inherit
  only an allowlist of safe vars, and anything matching a sensitive pattern is
  dropped even if explicitly allowlisted.
- `taiyi.tools.ssrf` — `SSRFGuard`: rejects loopback/private/link-local/reserved
  address space, enforces an optional host allowlist, resolves hostnames to catch
  DNS rebinding, and fails closed.
- `taiyi.tools.sandbox` — `SandboxExecutor` (a real `Executor`): runs shell and
  file I/O inside a sandbox dir with a scrubbed environment, screens URL tools
  through the SSRF guard, blocks path traversal, and marks not-yet-connected
  business tools as deferred rather than faking side effects. Two shell backends:
  `local` (direct, default) and `sandbox_exec` (macOS `sandbox-exec` with a
  deny-all profile that whitelists only sandbox-dir writes + system-binary reads
  + TMPDIR and denies all network — kernel-enforced isolation, not a denylist).
  Degrades to `local` off macOS.
- Runtime now treats a failed real execution as terminal `FAILED`.
- 17 tests + `examples/sandbox_demo.py`.

**Acceptance (met).** Secrets never reach tool subprocesses (verified by scrubbing
the env of a real `env` call); loopback/private/rebinding URLs are refused;
**the founding case is real** — a normal commit runs in a genuine git repo and
records the *local* identity, while the override attempt is denied and produces
no commit at all.
**Depends on.** M4.

> **M1–M5 are the trustworthy single-task vertical slice.** A request is planned
> (rule- or LLM-driven), gated step-by-step, executed for real but sandboxed only
> when cleared, and archived to a tamper-evident, replayable trajectory. The only
> remaining money decision is the *live* LLM opt-in (M4's deferred half).

### M6 — Validation Engine (L4, per-task checklists) ✅ Done
**Goal.** Objective checks selected by `(task_type, scenario)`, routed to the
cheapest reliable checker, kept separate from the executor; failed validation
bounces the task back into PDCA. Lifts the system to maturity **L3**.

**Delivered.**
- `taiyi.validation.checks` — a checklist *library* (universal + per-scenario +
  per-task-type) with `select_checks`: **select, don't generate** (§5.2).
- `taiyi.validation.engine` — `ValidationEngine` runs checks **cheapest-first**
  (deterministic → external → model judge) and **short-circuits** on the first
  failure, so a one-line check failing never spends a model-judge call. It judges
  output; it never produces it.
- `taiyi.validation.model_judge` — `ModelJudge` is isolated (its own provider,
  never the executor's), **version-tracked** (rubric_version), and **calibrated**:
  `calibrate()` measures its false-pass / false-block rates against labelled cases.
- Runtime now runs a **bounce-back correction loop** (`max_rounds`): a validation
  FAIL re-enters PDCA; with an LLM planner that can revise, the task can recover.
- 10 tests + `examples/validation_demo.py`.

**Acceptance (met).** Selection by (task_type, scenario); cheapest-first
short-circuit proven (model judge not called when a deterministic check fails);
model judging isolated, versioned, and calibratable; a validation failure bounces
back and then succeeds on a corrected round, or fails after exhausting rounds.
**Depends on.** M3 (M5 for functional checks). External-tool checks (real
linters/test-suites) and live model judging are a later opt-in on the M4 seam.

### M7 — Memory (5-layer, production) ✅ Done
**Goal.** Production memory replacing the demo's in-memory simplification.

**Delivered.**
- `taiyi.memory.engine` — `MemoryEngine`, all five layers over stdlib sqlite3:
  L1 short-term session messages; L2 skill index; L3 semantic search (vector);
  L4 Honcho dialectical user model; L5 FTS5 full-text history (auto-detects FTS5,
  falls back to LIKE). Markdown-first: long-term memories also append to a daily
  human-readable log; SQLite is the index over it. Persists and reopens cleanly.
- `taiyi.memory.embedding` — `HashingEmbedder`, a deterministic, dependency-free
  local embedder (stable md5 feature hashing) + cosine. A real embedding model
  implements the same `Embedder` interface and is a later opt-in.
- Runtime gained optional memory hooks: the prompt is recorded to L1, a completed
  task is archived to L5, and the L4 user model is updated.
- 9 tests + `examples/memory_demo.py`.

**Acceptance (met).** Full-text and semantic retrieval work; the user model merges
dialectically (dedups repeats, appends new); everything persists across reopen
with a Markdown mirror; the runtime records to memory end-to-end.
**Note.** The local embedder is lexical (exact-token); semantic recall improves
when a real embedding model is plugged into the `Embedder` seam (opt-in).
**Depends on.** M3.

### M8 — Scenario + Skill engine (quality gates) ✅ Done
**Goal.** Scenario registry/matcher and a Skill loader that refuses ungated skills
from the production path.

**Delivered.**
- `taiyi.scenarios` — `ScenarioRegistry` (loads Markdown scenarios with structured
  frontmatter) + `ScenarioMatcher` (deterministic trigger matching, default
  fallback). Scenarios are standalone, switchable data, decoupled from prompts.
- `taiyi.skills` — `QualityGate` (structured, with a completeness check),
  `load_skill`, and `SkillRegistry` that splits **production-eligible** skills
  (present + complete + passing gate) from the **sandbox**. `get_production`
  refuses sandbox skills; `index_into` registers production skills into memory L2.
- Migrated the three demo skills + scenarios into the structured catalog under
  `taiyi/skills/catalog/` and `taiyi/scenarios/catalog/` (shipped as package data).
- 13 tests + `examples/skills_demo.py`.

**Acceptance (met).** A skill with no gate, or an incomplete gate, is not
production-eligible and is refused by `get_production`; every shipped catalog skill
passes its gate; scenario matching routes the founding prompts correctly and falls
back to default. Running a gate's verification cases is deferred to M12.
**Depends on.** M2, M7.

### M9 — Gateway + channels (CLI + HTTP) ✅ Done
**Goal.** A single entry point that wires the whole stack, with auth, rate limit,
session, an OpenAI-compatible endpoint, and a real CLI.

**Delivered.**
- `taiyi.gateway.core` — `build_gateway` assembles governance → scheduler →
  runtime with memory, validation, scenarios, and the gated skill catalog over one
  audit chain; `Gateway.submit` matches a scenario when none is given. Defaults are
  safe/offline (keyword planner, mock executor); the live planner/sandbox executor
  swap in per deployment.
- `taiyi.gateway.app` — transport-agnostic `GatewayApp.handle()` (routing, auth,
  rate limit) returning `(status, dict)`; routes `/healthz`, `/v1/tasks`, and an
  OpenAI-compatible `/v1/chat/completions`.
- `taiyi.gateway.auth` — opt-in Bearer-token auth + sliding-window rate limiter.
- `taiyi.gateway.server` — a stdlib `http.server` transport (no framework dep).
- `taiyi.cli` — `taiyi run` / `taiyi serve` (registered as a console script).
- 13 tests (incl. a real HTTP round trip on an ephemeral port) + `examples/gateway_demo.py`.

**Decision.** Built on the **standard library** (`http.server`) rather than
FastAPI/uvicorn, to keep the dependency footprint minimal and CI simple. The
design names FastAPI; a FastAPI transport can wrap the same `GatewayApp` later
without changing the app logic. A **browser web UI is built** — a React SPA
under `web/` (build output committed to `web/dist`, served same-origin by the
gateway, zero runtime node dependency) with panels for chat/tasks, human
approvals, OODA review, memory/metrics, and config. Static files + SPA fallback
live in the transport layer, keeping `GatewayApp.handle`'s JSON contract pure.
**Acceptance (met).** Tasks submit over HTTP and via the CLI; governance still
applies through the gateway (identity override → REJECTED); auth refuses missing
tokens (401) and the rate limiter returns 429; OpenAI clients get a valid
chat-completion shape.
**Depends on.** M3.

### M10 — Value Stream (H4) ✅ Done
**Goal.** Productionize the demo's `value_stream.py`: dual-mode goal anchoring,
value-contribution scoring at L4, bottleneck detection at L5.

**Delivered.**
- `taiyi.value_stream.goals` — `GoalRef` / `TaskGoal` / `ValueContribution` /
  `GoalAnchoringMode` (the H4 data types from Tech Doc §3.2).
- `value_streams.yaml` — three-layer goal templates (task→tactical→strategic) per
  scenario, as data (shipped as package data).
- `taiyi.value_stream.anchoring` — Mode B (preset, zero-interaction) and Mode A
  (AI-infer candidates → user confirms which layers to lock); offline-first, with
  an optional provider seam for live refinement.
- `taiyi.value_stream.scoring` — contribution scoring + `BottleneckDetector` that
  aggregates scores into a value-leak report (avg alignment, waste, worst type).
- `ValueStreamEngine` ties it together; the runtime anchors a goal at L1 and scores
  contribution at L4 on completion (wired into the gateway, surfaced in the task
  summary and the audit trajectory).
- 6 tests + `examples/value_stream_demo.py`.

**Acceptance (met).** Preset anchoring honors each scenario's default stack;
infer→confirm locks the chosen layers; scoring reflects completion and flags
step-count waste; the bottleneck report aggregates and names the worst task type;
runtime tasks carry a goal and a contribution score end-to-end.
**Depends on.** M6.

### M11 — Observability (H3) ✅ Done
**Goal.** Traces, metrics, and structured logs across the system.

**Delivered.**
- `taiyi.observability.tracing` — one `TaskTrace` per task with nested phase spans
  (task → plan / do / validate), timing and attributes. An OTel exporter walks
  these as the opt-in seam.
- `taiyi.observability.metrics` — stdlib Counter / Gauge / Histogram with labels
  and a `render_prometheus()` exposition (no client library).
- `taiyi.observability.logging` — structured JSON logs, correlated by task id,
  with an optional sink.
- `Observability` facade pre-declares task metrics; the runtime records traces,
  task/state counters, governance verdicts, and duration. The gateway exposes
  `/metrics` (Prometheus text), and the HTTP server now serves text payloads.
- 7 tests + `examples/observability_demo.py`.

**Acceptance (met).** Nested spans recorded with correct parentage; Prometheus
render is well-formed (counters with labels, histogram buckets/count/sum); logs
captured and forwarded; a runtime task emits the four phase spans and increments
the verdict/state/duration metrics; the gateway `/metrics` endpoint returns text.
**Decision.** Stdlib metrics/tracing rather than the OpenTelemetry SDK, consistent
with the dependency-light posture; an OTLP/Prometheus-client exporter is an opt-in.
**Depends on.** M3.

### M12 — Iteration / OODA (L5) + Skill auto-generation ✅ Done
**Goal.** Close the loop: trajectory analysis, human-approved rule patches, gated
skill auto-generation, and the validator regression set. Takes the build to L4.

**Delivered.**
- `taiyi.iteration.trajectory` — `TrajectoryStore` (SQLite-backed, survives
  restarts) records each finished task with a signal-rich step trail (tool,
  args, verdict, output) and surfaces failure classes and repeated skill-less
  task shapes.
- `taiyi.iteration.rule_patcher` — turns a recurring failure into a
  `RulePatchSuggestion` (rule-as-data, M1 schema); `approve()` writes the YAML,
  which governance loads read-only. Human-gated — nothing auto-mutates live rules.
- `taiyi.iteration.skill_generator` — drafts an `auto_generated` skill with a
  complete quality gate from a repeated tool sequence; enters the sandbox, needs
  human approval to be promoted to managed.
- `taiyi.iteration.regression` — accumulates labelled validation cases and
  calibrates a model judge against them over time.
- `IterationEngine` (OODA) is fed by the runtime on every finished task **and
  automatically files Orient/Decide suggestions into a persisted `pending_review`
  queue** every task. `approve()`/`reject()` (CLI `taiyi review` + `/v1/review/*`
  HTTP) are the human Act gate; approved suggestions land in `base/rules/auto` /
  `base/skills/auto`, which governance/skills load read-only on the next start.
  This is the closed loop — last task's result changes next task's governance.
- 13 tests + `examples/iteration_demo.py`.

**Acceptance (met).** A recurring failure produces a suggestion (auto-filed, no
human prompting) that, once approved, makes governance return NEEDS_REVIEW for
that tool — a new failure class became a permanent check; a repeated shape
sediments into a gated, production-eligible auto-generated skill; trajectories
persist across process restarts; the validator gets a regression set with
false-pass/false-block tracking. **Maturity → L4 (closed loop, 周行不殆).**
**Depends on.** M6, M8, M11.

### M17 — Human approval & resume (HITL) ✅ Done
**Goal.** Close the human-in-the-loop story: a `NEEDS_REVIEW` task can be approved
and **resumed from where it stopped**, not just abandoned.

**Delivered.**
- `taiyi.approvals` — `ApprovalStore` + `PendingApproval` (in-memory; no runtime
  import, so no cycle). The runtime parks a suspended task here keyed by approval id.
- `TaskRuntime.resume(approval_id, approve=…)` — on approve, executes the held step
  (a human override of the review), then continues gating the remaining steps and
  validates → COMPLETED; on reject, marks REJECTED. Steps already done are kept.
  A downstream step that needs review re-suspends with a fresh approval (chained).
- Gateway `GET /v1/approvals` (list pending) and `POST /v1/approvals/resolve`.
- 4 tests + `examples/approval_demo.py`.

**Acceptance (met).** A weekly-report task suspends at the outbound notify (keeping
the completed query), shows up in the pending list, and resumes to COMPLETED on
approval; rejection marks it REJECTED; an unknown approval id errors; the full
flow works over the gateway endpoints.
**Note.** In-process store; persisting approvals to disk for resume-across-restart
is a small later refinement. **Depends on.** M3, M9.

### M16 — Iterative agent loop ✅ Done
**Goal.** Turn plan-once execution into a real agent: reason → act → observe →
repeat, with every action still gated. The "framework → agent" piece.

**Delivered.**
- `taiyi.agent.AgentRuntime` — a step-by-step loop: the model proposes one tool
  call, **`scheduler.request_permit` gates it**, it executes, the result is fed
  back into the conversation, and the model decides the next step. Stops when the
  model answers with no tool call (validated first) or the step budget runs out.
  Reuses the same governance boundary, executor, validation, memory, value-stream,
  observability, and iteration components as `TaskRuntime`.
- A validation failure on "done" is fed back as an observation so the agent can
  correct itself within budget.
- 5 tests + `examples/agent_demo.py`.

**Acceptance (met).** A multi-step task runs to COMPLETED with results fed back; a
prompt-injected override mid-loop is DENIED and never executes (governance holds
step by step); a `git push` mid-loop suspends as NEEDS_REVIEW; a validation failure
feeds back and the agent recovers; the step budget bounds the loop (FAILED).
**The live LLM is the opt-in** that drives this for real — same control flow.
**Depends on.** M4, M5, M6.

### M15 — Configuration & deployment ✅ Done
**Goal.** Make Taiyi self-operable: configure an instance without editing Python,
and ship a container.

**Delivered.**
- `taiyi.config` — `TaiyiConfig` + `load_config`: one YAML file (`taiyi.yaml`) plus
  `TAIYI_*` env overrides for persistence, host/port, auth tokens, executor
  (`mock`|`sandbox`), `max_rounds`, and custom rule/scenario/skill directories.
- **Merge-friendly loaders** — `load_rule_set` and `ScenarioRegistry.load_dirs` /
  `SkillRegistry.load_dirs`: drop your own YAML/Markdown into a directory and it
  merges with the built-ins (yours can override a built-in by reusing its id/name).
- `build_gateway_from_config` + `--config` on `taiyi run|serve|mcp`; selecting
  `executor: sandbox` runs real, governed, credential-isolated execution.
- `taiyi.example.yaml`, `deploy/Dockerfile`, `deploy/docker-compose.yml`.
- 5 tests + verified end-to-end: a config with `executor: sandbox` runs a real
  governed `git commit` in a target repo, preserving the local identity.

**Acceptance (met).** `pip install` → `taiyi serve --config taiyi.yaml` (or
`docker compose up`) stands up a configured instance; custom rules are enforced
alongside the built-ins; the real executor is selectable by config alone.
**Depends on.** M9.

### M13 — Multi-agent (expert matrix + arbitration) ✅ Done
**Goal.** Expert agents with red-line veto and precedence-based arbitration
(Design Doc §4.5 / §5.3) — collaboration with gates, not free-form agent chat.

**Delivered.**
- `taiyi.multi_agent.experts` — the `Expert` interface, `ExpertOpinion`, and
  deterministic `MarkerExpert` stand-ins; `builtin_experts()` is the five-domain
  matrix (security/compliance/business with veto authority; performance/UX
  advisory-only), with the design's default precedence. A live LLM-backed expert
  implements the same interface — the arbitration math is unchanged.
- `taiyi.multi_agent.arbitration` — `arbitrate()`: no veto → APPROVED (advisories
  non-binding); any veto → highest-precedence veto wins, task paused + escalated;
  same-precedence cross-domain conflict → fail closed → NEEDS_HUMAN.
- `taiyi.multi_agent.committee` — `ExpertCommittee.review` + `reconsider_once`
  (one amendment retry; a persistent veto becomes an L5 system defect, bounded).
- `taiyi.multi_agent.permit_review` — `reconsider_permit()`: the one-way mapping
  that makes the committee a **second permit gate**. It runs after governance
  ALLOWs a step (in both runtimes) and can only tighten — ALLOW → NEEDS_REVIEW on
  a committee veto. It never loosens a governance DENY (governance owns the
  red-line authority; the committee only escalates to human review).
- Gateway `/v1/review` endpoint; the same committee instance is wired into both
  the runtime (as the second gate) and `build_gateway` (for on-demand review).
- 14 tests + `examples/multi_agent_demo.py` (reproduces the design's contract-review
  scenario C).

**Acceptance (met).** A red-line expert's veto is a one-vote pause that escalates;
advisory experts never block; a higher-precedence veto wins a cross-level conflict;
a same-precedence hard conflict fails closed to a human; reconsideration resolves
an amended proposal or, if not, records a system defect. As a permit gate: a
governance-allowed step the committee vetoes is suspended for human review without
executing; a governance DENY is never overridden by a committee approval.
**Decision.** Built offline-first (deterministic marker experts), so it is
zero-cost; the live multi-expert LLM path is the opt-in. **Depends on.** M3.

### M14 — MCP server + channel adapter + Skill market ✅ Done
**Goal.** The breadth layer. Built the zero-cost core; live messaging connectors
remain a documented opt-in (they need platform SDKs and credentials).

**Delivered.**
- `taiyi.mcp` — **Taiyi as an MCP server** (JSON-RPC 2.0: `initialize` / `tools/list`
  / `tools/call`), exposing governed tools (`taiyi_run_task`, `taiyi_list_skills`,
  `taiyi_get_skill`, `taiyi_search_memory`, `taiyi_review`) backed by the gateway.
  `handle()` is transport-agnostic; `serve_stdio()` runs the loop; `taiyi mcp` is a
  CLI subcommand. A call from Claude Code / Cursor flows through the same
  governance — an MCP client cannot bypass a red line.
- `taiyi.channels` — the `ChannelAdapter` interface + `InProcessChannel` reference.
  A real Feishu/Telegram/Discord adapter subclasses it and overrides only transport
  ("a new channel is one file"); those are the live opt-in.
- `taiyi.market` — `SkillMarket`: list/search/install from a registry, **refusing
  any skill that does not pass its quality gate** at install time, so the market
  cannot become a junkyard. (Git distribution is the deferred transport.)
- 18 tests + `examples/mcp_demo.py`.

**Acceptance (met).** MCP `initialize`/`tools/list`/`tools/call` work in-process and
over real stdio; `taiyi_run_task` is governed via MCP (identity override → REJECTED);
the channel adapter runs and reflects governance; the market installs gated skills
and refuses ungated ones.
**Deferred (live opt-in).** Real platform channel connectors and Git-based market
distribution — both need credentials/SDKs/network. **Depends on.** M9.

---

## How "move forward" works
1. You say *move forward* (optionally naming a module).
2. I build the next planned module on branch
   `claude/agent-architecture-design-4r4c9b`: code + tests + a runnable artifact.
3. CI must be green; I commit and push, then report what landed and what's next.
4. I do **not** open a pull request unless you ask.

## Open issues / decisions needed
See the "Issues to flag" section the assistant raised when kicking this off
(naming standardization `helix`→`taiyi`, license choice, and the budget posture
for M4 onward). None block M2; they are worth resolving before M4.
