# Agent Architecture

> 一次 GitHub 上传踩坑，引发的 AI Agent 工程化底层思考。
> A reflection on AI Agent engineering, sparked by a real GitHub upload pitfall.

## 关于 / About

我最近用 [OpenClaw](https://github.com/) 做了一次 GitHub 仓库上传，工具返回了"任务成功"，但提交记录的作者身份被悄悄替换了——把代码记在了别人名下。

这件事让我意识到：**当前 AI Agent 行业的"任务完成"，只覆盖了表层验收标准，过程合规、权属清晰、风险可控这些隐性标准，几乎没有工具主动覆盖。**

顺着这个坑，我往下挖了三层：
- **第一层**：表层成功 ≠ 真的做对——用户和 Agent 的验收标准根本不在一个维度
- **第二层**：行业为什么都在做"半吊子"Agent——大家都在卷模型能力，但硬编码规则、工程实现、设计优先级这些被严重低估
- **第三层**：多智能体不是万能解药——没有门禁的协同，等于没有协同

最终推导出一套**生产级 Agent 的工程化框架**，按五层结构拆解：
1. **输入与场景层**（提示词工程 / 背景环境工程 / 知识工程 / 任务信息工程）
2. **能力与资源层**（工具工程 / 标准技能工程）
3. **调度与管控层**（执行管控工程 / 任务适配调度）
4. **输出与校验层**（输出校验工程）
5. **迭代与优化层**（循环工程）

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

## 目录 / Contents

- [`cn/Agent架构思考.md`](./cn/Agent架构思考.md) — 中文原文（Markdown 版）
- [`en/Agent_Architecture_Thoughts.md`](./en/Agent_Architecture_Thoughts.md) — English Translation

## 核心观点 / Key Takeaways

- **没有门禁的协同，等于没有协同。** Multi-agent 框架如果只让几个 Agent 互相聊天，本质还是执行 Agent 一言堂。
- **80/20 原则**：守住 80 分的客观底线（靠工程化强制校验），剩下 20% 的人类品味留给人来做。
- **管控权与调度权必须分离**：同一个模块既调度又校验，天然有动力为了完成任务绕过校验。
- **当前所有外围工程模块，都是为了补齐大模型内生能力的不足**——AGI 真正实现时，它们绝大多数会被内生能力替代。
- **能走得远的，从来都是走得稳的那个。**

- **Collaboration without gates is no collaboration at all.** If a multi-agent framework just lets Agents chat, it's still a one-voice autocracy by the executor.
- **80/20 rule**: Hold the 80-point objective baseline (via engineering-mandated validation), and leave the remaining 20% of human taste to humans.
- **Governance and scheduling authorities must be separated**: a module that both schedules and validates will naturally have the motivation to bypass validation to complete the task.
- **All current peripheral engineering modules are to fill in the gaps in the inherent capabilities of large models** — when AGI truly arrives, most of them will be replaced by inherent capabilities.
- **The one that goes far is always the one that walks steady.**

## 作者 / Author

- GitHub: [@zachshi-ai](https://github.com/zachshi-ai)
- 公众号：扎克的自留地
- 写作日期：2026-06-19

---

> 写这篇思考的初衷，不是为了否定任何一款工具。相反，我觉得现在的开源项目都做得非常好。只是整个行业走得太快了，所有人都在往前冲效率、堆功能，很少有人停下来抠"靠谱"这件事。
> 
> *The original intention of this reflection is not to deny any tool. On the contrary, I think the current open-source projects are all very well done. It's just that the whole industry is moving too fast — everyone is racing forward on efficiency and piling on features, and very few people pause to obsess over "reliability".*
