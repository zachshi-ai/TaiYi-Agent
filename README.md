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
| `src/taiyi/` | **Production code** — 17 modules, built module by module |
| `tests/` | 263 tests covering governance, operating modes, and executable Skill gates |
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
