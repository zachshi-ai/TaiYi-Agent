# Agent Architecture

> A reflection on AI Agent engineering, sparked by a real GitHub upload pitfall.
>
> 中文版：[`README_cn.md`](./README_cn.md)

## About

Recently I used [OpenClaw](https://github.com/) to push a GitHub repo. The tool reported "task complete" — but the commit author was silently swapped, recording my code under someone else's name.

This made me realize: **today's "task complete" only covers the surface acceptance criteria. Process compliance, authorship clarity, risk control — these hidden criteria are almost never actively covered by any tool.**

Following this pitfall, I dug down three layers:

- **Layer 1**: Surface success ≠ actually correct — user's and Agent's acceptance criteria aren't even on the same dimension
- **Layer 2**: Why the whole industry builds "half-baked" Agents — everyone's racing on model capability, while hard-coded rules, engineering implementation, and design priorities are severely underestimated
- **Layer 3**: Multi-agent is not a universal cure — collaboration without gates is no collaboration at all

Eventually, I derived a **production-grade Agent engineering framework**, broken into five layers:

1. **Input & Scenario Layer** (Prompt / Scenario & Constraint / Knowledge / Task Information Engineering)
2. **Capability & Resource Layer** (Tool / Standard & Skill Engineering)
3. **Scheduling & Governance Layer** (Execution Governance / Task Adaptation & Scheduling)
4. **Output & Validation Layer** (Output Validation Engineering)
5. **Iteration & Optimization Layer** (Loop Engineering)

## Contents

- [`cn/Agent架构思考.md`](./cn/Agent架构思考.md) — Chinese full article
- [`en/Agent_Architecture_Thoughts.md`](./en/Agent_Architecture_Thoughts.md) — English full article

## Key Takeaways

- **Collaboration without gates is no collaboration at all.** If a multi-agent framework just lets Agents chat, it's still a one-voice autocracy by the executor.
- **80/20 rule**: Hold the 80-point objective baseline (via engineering-mandated validation), and leave the remaining 20% of human taste to humans.
- **Governance and scheduling authorities must be separated**: a module that both schedules and validates will naturally have the motivation to bypass validation to complete the task.
- **All current peripheral engineering modules are to fill in the gaps in the inherent capabilities of large models** — when AGI truly arrives, most of them will be replaced by inherent capabilities.
- **The one that goes far is always the one that walks steady.**

## Author

- GitHub: [@zachshi-ai](https://github.com/zachshi-ai)
- Writing date: 2026-06-19

---

> The original intention of this reflection is not to deny any tool. On the contrary, I think the current open-source projects are all very well done. It's just that the whole industry is moving too fast — everyone is racing forward on efficiency and piling on features, and very few people pause to obsess over "reliability".