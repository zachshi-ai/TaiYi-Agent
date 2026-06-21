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
| M7 | Memory (5-layer: SQLite/FTS5/vector/Honcho) | Planned | L3 | Partly |
| M8 | Scenario + Skill engine (quality gates) | Planned | L3 | No |
| M9 | Gateway + channels (CLI + Web, OpenAI-compatible) | Planned | L3 | No |
| M10 | Value Stream (H4: goal anchoring, scoring) | Planned | L3 | Partly |
| M11 | Observability (H3: OpenTelemetry, metrics) | Planned | L3 | No |
| M12 | Iteration / OODA (L5) + Skill auto-generation | Planned | **L4** | Partly |
| M13 | Multi-agent (expert matrix + arbitration) | Deferred | L4 | Yes |
| M14 | Channel breadth + MCP server + Skill market | Deferred | L4 | Yes |

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

**Deferred to a live opt-in (needs API key + budget).** Real Anthropic /
OpenAI-compatible / Ollama providers implementing `LLMProvider`, and the iterative
agent loop that feeds real tool results back to the model (meaningful once M5
makes execution real). Flip this on by supplying a key; governance behaviour does
not change.
**Depends on.** M3.

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
  business tools as deferred rather than faking side effects.
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

### M7 — Memory (5-layer, production)
**Goal.** Production memory: SQLite + FTS5 (history), vector index (semantic),
Honcho dialectical user model, Markdown-first storage. Replaces the demo's
in-memory simplification.
**Depends on.** M3.

### M8 — Scenario + Skill engine (quality gates)
**Goal.** Scenario registry/matcher and a Skill loader that **refuses Skills
without a passing `quality_gate.md`** into the production path. Migrate the three
demo Skills/scenarios into the production format.
**Depends on.** M2, M7.

### M9 — Gateway + channels (CLI + Web)
**Goal.** FastAPI gateway (auth, rate limit, session), a real CLI, a minimal web
console, and an OpenAI-compatible `/v1/chat/completions` endpoint.
**Depends on.** M3.

### M10 — Value Stream (H4)
**Goal.** Productionize the demo's `value_stream.py`: dual-mode goal anchoring
(A: AI-infer+confirm, B: preset), value-contribution scoring at L4, bottleneck
detection at L5.
**Depends on.** M6.

### M11 — Observability (H3)
**Goal.** OpenTelemetry traces (one trace per task, spans per phase), Prometheus
metrics (governance verdicts, latency, token cost), structured logs built on the
M1 audit log.
**Depends on.** M3.

### M12 — Iteration / OODA (L5) + Skill auto-generation
**Goal.** Close the loop: trajectory analysis, rule-patch suggestions (human
approved), and Skill sedimentation after N repeats (sandbox-verified, gated).
**Acceptance.** A new failure class can become a permanent check; the validator
gets a regression set. **Maturity → L4.**
**Depends on.** M6, M8, M11.

### M13 — Multi-agent (expert matrix + arbitration)  *(deferred)*
Expert agents (security/compliance/business/perf/UX) with red-line veto and the
precedence-based arbitration from Design Doc §5.3. Highest complexity; deferred
until the single-agent slice is solid.

### M14 — Channel breadth + MCP server + Skill market  *(deferred)*
P1/P2 channels (Feishu/DingTalk/Telegram/Discord/Slack/…), Taiyi-as-MCP-server,
and the Git-distributed Skill market. Breadth work; deferred for budget.

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
