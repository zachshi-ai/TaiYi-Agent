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
| M2 | Scheduler + governance boundary | Planned | L2 | No |
| M3 | Task Runtime (PDCA loop + state machine) | Planned | L2 | No |
| M4 | Real LLM provider layer | Planned | L2 | **Yes (API keys)** |
| M5 | Tool Runtime (sandbox, credential isolation, SSRF) | Planned | L2 | No |
| M6 | Validation Engine (L4, per-task checklists) | Planned | L3 | Partly |
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

### M2 — Scheduler + governance boundary
**Goal.** A scheduler (the decision-maker) that plans a tool sequence for a task
and **must obtain a permit** for each step. Cements the request/permit contract
so governance and scheduling can later run as separate processes unchanged.
**Scope.** Pluggable `Planner` interface (port the demo's keyword router as the
first implementation); the scheduler holds no execution capability of its own.
**Acceptance.** A planned step cannot execute without an `ALLOW` permit; a DENY
halts the plan; a NEEDS_REVIEW suspends it without losing completed steps.
**Depends on.** M1.

### M3 — Task Runtime (PDCA loop + state machine)
**Goal.** Wire scheduler + governance into the PDCA main loop with the documented
state machine (PENDING→…→COMPLETED/REJECTED/NEEDS_REVIEW), producing a
`TaskContext` and archiving a trajectory. Executor still mocked.
**Acceptance.** Single tasks run end-to-end across all six scenarios with correct
terminal states; trajectory is replayable from the audit log.
**Depends on.** M2.

### M4 — Real LLM provider layer  💲
**Goal.** Replace `MockLLM` with a real provider abstraction (default: latest
Claude; also OpenAI-compatible and Ollama) and a tool-call loop. **Answers the
key Phase 0 open question:** can a real LLM bypass governance? (It must not.)
**Budget note.** First module that spends tokens. Ships with a recorded/offline
mode so CI and local dev cost nothing; live keys only for integration runs.
**Acceptance.** LLM-proposed tool calls still pass through the permit gate; a
prompt-injection attempt to run a red-line action is denied.
**Depends on.** M3.

### M5 — Tool Runtime (sandbox + credential isolation + SSRF)
**Goal.** Real execution backends (local + Docker), credential filtering (child
processes get only safe env vars), and SSRF protection (URL allowlist, private-IP
deny, fail-closed). The high-risk execution layer the gates were protecting.
**Acceptance.** Secrets never reach tool subprocesses; internal IPs are refused;
the git-identity case is verified end-to-end against a real sandboxed repo.
**Depends on.** M4. *(M1–M5 = a trustworthy single-task vertical slice.)*

### M6 — Validation Engine (L4, per-task checklists)
**Goal.** Objective checks selected by `(task_type, scenario)` from a checklist
library, routed to the cheapest reliable checker (deterministic → external tool →
model judge), kept **separate from the executor**. Adds post-execution gates.
**Acceptance.** Failed validation bounces the task back into PDCA; model-judge
use is isolated and version-tracked. **Maturity → L3.**
**Depends on.** M3 (M5 for functional checks).

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
