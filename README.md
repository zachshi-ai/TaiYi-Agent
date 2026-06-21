# 太一 / The One (Taiyi)

> A production-grade Agent OS for *deterministic* production tasks (code,
> transactions, compliance, process execution). Its reason to exist is one design
> decision: **governance authority and scheduling authority are physically
> separated.** A module that both does the work and signs off on the work has an
> incentive to skip the sign-off — Taiyi removes that incentive by design.
>
> 中文说明见 [`README_cn.md`](./README_cn.md)。

The project started from a real incident: an agent reported "task complete" while
silently swapping the git commit author, recording the user's code under someone
else's name. Surface success ≠ actually correct. Taiyi's goal is to make the
*implicit* acceptance criteria — authorship, compliance, safety — into code the
model **cannot bypass**, rather than rules it is merely asked to remember.

## Where things are

| Path | What |
|---|---|
| [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md) | **The build order** — modular roadmap, one module per "move forward" |
| [`docs/00_Design_Document.md`](./docs/00_Design_Document.md) | Design philosophy + the five-layer architecture |
| [`docs/01_Feasibility_Report.md`](./docs/01_Feasibility_Report.md) | Phase 0 demo evidence |
| [`prd/00_PRD.md`](./prd/00_PRD.md) | Product requirements & version plan |
| [`tech/00_Technical_Architecture.md`](./tech/00_Technical_Architecture.md) | Components, interfaces, deployment |
| [`research/`](./research/) | The originating article + borrowed-pattern analysis |
| `src/taiyi/` | **Production code** (built module by module) |
| `demo/` | Phase 0 throwaway demo (mock everything; kept as reference) |
| `examples/` | Runnable examples against the production package |

## Current status

**Modules 1–10 are built — a governed, validated agent with memory, scenarios,
gated skills, a gateway (CLI + HTTP), and value-stream alignment, at maturity level
L3.** A request enters via the CLI or HTTP, is anchored to a business goal, matched
to a scenario, planned (rule- or LLM-driven), gated step-by-step by governance,
executed for real but sandboxed only when cleared, independently validated (a
failed check bounces it back), scored for value contribution, and remembered. M1
Governance Core (rules-as-data, fail-closed, audit log); M2 Scheduler + boundary
(no execution capability; permits only); M3 Task Runtime (PDCA loop + state
machine); M4 LLM layer offline-first (a model **cannot bypass governance**; live
providers are an opt-in); M5 Tool Runtime (sandboxed execution, credential
isolation, SSRF); M6 Validation Engine (cheapest-first checklists, isolated/
calibrated model judge, bounce-back); M7 Memory (5-layer SQLite/FTS5/vector/Honcho);
M8 Scenario + Skill engine (scenarios as data; **no skill enters production without
a passing quality gate**); M9 Gateway (stdlib HTTP + CLI, auth/rate-limit,
OpenAI-compatible endpoint); M10 Value Stream (dual-mode goal anchoring, value-
contribution scoring, bottleneck detection). See the roadmap for what's next.
(Phase 0's demo remains under `demo/` as reference.)

```bash
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
