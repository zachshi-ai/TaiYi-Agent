# 借鉴分析:OpenClaw / Hermes 模式提炼

> 目的:梳理 OpenClaw / Hermes 中可被 太一 (The One) 复用的工程实现模式。
>
> **重要定位说明**:
> - **zachshi 的 zachshi-ai 仓库是 太一的"原创理论来源"**,不是"借鉴"。
> - 本文档的"借鉴"只针对 OpenClaw 和 Hermes。
> - zachshi-ai 的五层架构 + 治理-调度分离理论是 太一的**理论起点**,被直接作为顶层骨架。
>
> 资料来源:
> - zachshi-ai 原创(理论骨架,GitHub: zachshi-ai/TaiYi-Agent)
> - https://github.com/openclaw/openclaw (工程实现参考:Gateway、多通道、Markdown 记忆、Skills)
> - https://github.com/NousResearch/hermes-agent (工程实现参考:闭环学习、5 层记忆、Honcho、RL 数据飞轮)

---

## 1. zachshi-ai 五层架构(理论骨架 / 原创)

文章标题:*From a GitHub Upload Pitfall*。由 zachshi 本人在使用 OpenClaw 时遇到的一次生产事故反思而来——
OpenClaw 替用户改了 commit 作者身份,导致 commit author 与本地 git config 不一致。

| 层 | 核心问题 | 关键模块 |
|---|---|---|
| L1 输入/场景层 | 在什么环境下、做什么 | Prompt / 场景与约束 / 知识 / 任务信息 |
| L2 能力/资源层 | 用什么能力、什么资源 | 工具工程 / 标准与技能工程 |
| L3 调度/治理层 | 规则如何约束能力调度 | **执行治理**(中立裁判) / 任务适配与调度(决策者) |
| L4 输出/验证层 | 结果是否真的过线 | 输出验证工程(独立校验,失败回弹) |
| L5 迭代/优化层 | 如何持续变好 | 跨全链路的循环工程(Loop Engineering) |

### 三条铁律
1. **治理权与调度权必须分离**:同一模块不能既调度又验证,否则会本能绕过验证。
2. **协作无门等于无协作**:多 Agent 讨论没有 gate,本质就是执行 Agent 的一言堂。
3. **80/20 工程基线 + 20% 人审兜底**:客观标准 100% 自动化,主观偏好留给人。

### 适用边界
- ✅ 适用:代码开发、事务处理、合规审计、流程执行
- ❌ 不适用:纯创意(文学、艺术、战略咨询)

---

## 2. OpenClaw 模式(可借鉴)

| 模式 | 说明 | 借鉴决策 |
|---|---|---|
| 四层架构(Channel/Gateway/LLM/Skills) | 通道、控制面、模型、工具箱 | ✅ 全部采用,作为底座 |
| Gateway(ws://127.0.0.1:18789) | 统一入口,状态/路由/编排的单一真相源 | ✅ 强借鉴 |
| Agent Loop 5 阶段 | Initial → Command → Embedded Runtime → Event Streaming → Completion | ✅ 借鉴为运行时主循环 |
| 双层 Markdown 记忆 | `memory/YYYY-MM-DD.md` 日志 + `MEMORY.md` 长期 | ✅ 借鉴为 L1-L3 记忆层 |
| 25+ 模型 + 20+ 通道 | 全部以 provider/channel 抽象 | ✅ 强借鉴 |
| Cron + Heartbeat | at/every/cron + 心跳 | ✅ 借鉴为主动执行引擎 |
| 技能注册中心(ClawHub) | Bundled/Managed/Workspace 三级 | ✅ 借鉴为技能管理 |
| 10 项安全控制 | 用户隔离/容器化/审批/审计等 | ✅ 借鉴为治理层底线 |

### OpenClaw 不足
- 治理薄弱(主循环没有独立 gate,没有输出验证强制流程)
- 记忆是被动维护(用户告诉它才记)
- 缺乏自进化闭环
- 多 Agent 协作基本没有

---

## 3. Hermes Agent 模式(可借鉴)

| 模式 | 说明 | 借鉴决策 |
|---|---|---|
| **闭环学习** | 任务→技能沉淀→使用→改进→再沉淀 | ✅ 借鉴为 L5 迭代层核心机制 |
| **五层记忆** | L1 上下文 / L2 SKILL.md / L3 向量 / L4 Honcho 用户建模 / L5 FTS5 历史 | ✅ 全部采用,补足 OpenClaw 记忆不足 |
| **Honcho 辩证用户建模** | 正-反-合:持续融合用户偏好与新反馈 | ✅ 借鉴为个性化层 |
| **技能自生成** | 完成任务后自动沉淀 SKILL.md | ✅ 借鉴 |
| **中央调度 + 黑板** | 多 Agent 通过共享黑板协作 | ✅ 借鉴为多 Agent 协作基础 |
| **Atropos RL 引擎** | 工具调用→轨迹→RL 训练 | ✅ 作为可选的飞轮扩展(本期不实现) |
| **6 种执行后端** | Local/Docker/SSH/Daytona/Singularity/Modal | ✅ 借鉴执行后端多形态 |
| **5 层安全防线** | 授权→审批→容器→凭证过滤→注入扫描 | ✅ 借鉴,比 OpenClaw 更强 |
| **12 平台 Gateway** | Telegram/Discord/.../飞书/钉钉 | ✅ 借鉴为多通道 |
| **MCP Client+Server 双向** | 既是客户端也暴露自身 | ✅ 借鉴,Agent 也是 MCP Server |
| **子 Agent 委派** | 上下文隔离、最深 2 层、并发 3 | ✅ 借鉴为多 Agent 调用约束 |

### Hermes 不足
- 多 Agent 仲裁机制弱(中央调度员说算)
- 缺少独立输出验证模块(自评 ≠ 独立验证)
- 业务约束(场景/规则)由 prompt 携带,缺少独立约束管理

---

## 4. 综合提炼:本架构要落地的 12 个核心模式

| 编号 | 模式 | 来源 | 在本架构的位置 |
|---|---|---|---|
| P1 | Gateway 单一真相源 | OpenClaw | 入口网关 |
| P2 | 五层记忆(L1-L5) | Hermes | 记忆层 |
| P3 | 双层 Markdown 存储 | OpenClaw | 记忆层基础 |
| P4 | Skills 注册中心(Bundled/Managed/Workspace) | OpenClaw | 能力层 |
| P5 | Skill 自生成与编辑 | Hermes | 迭代层 |
| P6 | Honcho 辩证用户建模 | Hermes | 个性化层 |
| P7 | 五层架构(输入/能力/调度/验证/迭代) | zachshi-ai | 顶层骨架 |
| P8 | 治理权 / 调度权分离 | zachshi-ai | 治理层铁律 |
| P9 | 80/20 验证 + 20% 人审 | zachshi-ai | 输出验证层 |
| P10 | Cron + Heartbeat 主动执行 | OpenClaw | 主动调度 |
| P11 | MCP 双向(客户端 + Server) | Hermes | 协议层 |
| P12 | 五层安全防线 | Hermes | 治理层底线 |

### 三个新增的"集成创新"
1. **Skill 标准化 + 治理 gate**:每个 Skill 附带"质量门禁",不通过门禁禁止进入生产
2. **多 Agent 仲裁(红线一票否决 + 优化建议)**:把 zachshi-ai 的"协作无门"补足
3. **场景工程独立化**:把"场景/约束"从 prompt 抽到独立模块,做工程级管理
