# 太一 / The One (Taiyi)

> 一个面向*确定性*生产任务（代码、交易、合规、流程执行）的 Agent Harness / Agent OS 原型。它存在的理由是一个设计决策：**治理权与调度权物理分离。** 一个既干活又给自己验收的模块，有动机跳过验收以加快完成——太一用设计消除了这个动机。
>
> English: see [`README.md`](./README.md)。

项目源于一个真实事故：一个 agent 报告"任务完成"，却悄悄替换了 git commit 作者，把用户的代码记到别人名下。表层成功 ≠ 真正做对。太一的目标是把**隐性验收标准**——作者权属、合规、安全——写成模型**无法绕过**的代码，而不是要求它记住的规则。

## 目录组织：学 · 研 · 产 · 用

| 路径 | 内容 |
|---|---|
| **产（生产，留根）** — Agent 本体 | |
| `src/taiyi/` | **生产代码** — 17 个模块 |
| `tests/` | 263 个测试，覆盖治理不变量、三模式与可执行 Skill 门禁 |
| `web/` | 内置 React Web UI（构建产物在 `web/dist`） |
| `deploy/` | Dockerfile + docker-compose |
| `pyproject.toml` · `taiyi.example.yaml` | 打包 + 配置模板 |
| **学（learning/）** — 为什么这么设计 | |
| [`learning/docs/`](./learning/docs/) | 设计哲学 + 五层架构 |
| [`learning/prd/`](./learning/prd/) | 产品需求与版本规划 |
| [`learning/tech/`](./learning/tech/) | 组件、接口、部署 |
| [`learning/research/`](./learning/research/) | 借鉴模式分析（别人怎么做） |
| [`learning/assets/`](./learning/assets/) | 交互式架构图 |
| [`learning/DEVELOPMENT_PLAN.md`](./learning/DEVELOPMENT_PLAN.md) | 模块化构建路线 |
| **研（research/）** — 理论转道路、测试验证 | |
| [`research/examples/`](./research/examples/) | 对生产包的可运行示例 |
| [`research/demo/`](./research/demo/) | Phase 0 一次性 demo（全 mock，仅参考） |
| **用（practices/）** — 落地后的优秀实践 | |
| [`practices/`](./practices/) | 经过验证的技能、prompt、运维笔记（持续积累） |

## 当前状态

**17 个模块的代码骨架与核心链路已经建成，目前是向 L4 演进的 L3 生产原型。** 治理、permit、ReAct、沙箱、验证、审计和人工恢复均有可运行实现；真实 LLM 端到端路径也已用 DeepSeek 验证。默认 `mock` executor 和尚未接入的业务 connector 仍是明确的非生产边界，不能拿测试全绿代替真实业务验收。

一个请求经 CLI、HTTP 或内置 Web UI 进入后，会匹配场景和生产级 Skill，解析质量/平衡/效率运行策略，在任何规划或执行前冻结不可变 Task Contract（包括 Git remote/ref、退款金额等任务参数），再逐步通过治理闸门执行。独立验证观察完整工具调用而非只有工具名，并产生绑定 Contract、检查器类型和当前产物摘要的 Evidence Ledger；完成控制器只有在当前产物的全部必过标准得到证据后才允许成功终态。若工具动作由无副作用 `mock` 执行，即使 Harness 检查全过也只能进入 `SIMULATED`；只有非 mock 执行才可进入 `COMPLETED`。模拟任务不会作为真实交付写入长期完成记忆、价值评分或 Skill 沉淀。失败证据会回灌下一轮规划，而不是重复原计划。轨迹进入 OODA 外循环，规则/Skill 建议仍须人审并在下次启动生效。详细设计见 [`learning/docs/05_Immutable_Acceptance_Contracts.md`](./learning/docs/05_Immutable_Acceptance_Contracts.md)。

Skill 准入不再等同于“有一份完整 Markdown”：每个 Skill 至少需要 3 个自动案例，内置 3 个 Skill 的 9 个案例会真实经过当前治理、调度与验证链路，全部通过后生成绑定 Skill 内容的发布锁；网关装载时还会在当前代码上再次运行。当前证据环境明确是 `mock`，成功动作案例也必须期待 `SIMULATED`，只证明 Harness 行为，不代表 SQL、通知、退款等真实 Connector 已生产就绪。详细设计见 [`learning/docs/03_Executable_Skill_Quality_Gates.md`](./learning/docs/03_Executable_Skill_Quality_Gates.md)。

### 三种运行模式

Web UI 每次任务可选择：

- **质量模式**：重要歧义先问，exhaustive 验证，最多 3 次验证/修复尝试；
- **平衡模式（默认）**：高影响才问，standard 验证，高风险时启用已配置的独立评审，最多 2 次尝试；
- **效率模式**：AI 主导、采用可逆默认值，critical 验证，1 次尝试。

Agent Runtime 和模型驱动的 Workflow Runtime 会把三种模式分别路由到 `quality_model`、`balanced_model` 和
`efficiency_model`；某一档未配置时回退到默认 `model`，并在任务证据中明确记录
`fallback=true`。三种模式共享完全相同的治理红线和授权边界。详细设计见
[`learning/docs/02_Operating_Modes.md`](./learning/docs/02_Operating_Modes.md) 和
[`learning/docs/04_Provider_Routing.md`](./learning/docs/04_Provider_Routing.md)。

质量模式还要求至少一个与任务目标绑定的客观检查器；只有“输出非空”等基础检查时会在执行前拒绝认证。效率模式可以交付低风险未知任务，但会明确标记 `baseline_only`，不冒充目标正确性已得到证明。

使用 `executor: sandbox` 时还可启用只读 Git Authority：执行前冻结 HEAD 和仓库本地身份，执行后独立证明出现了新提交，并核对 author/committer。详见 [`learning/docs/06_External_Authority_Checks.md`](./learning/docs/06_External_Authority_Checks.md)。

可选的 Git Remote Authority 还会在 push 前冻结 commit、remote URL 摘要和 branch，执行后通过独立 `git ls-remote` 证明远端 ref 确实指向冻结提交；执行器自报 push 成功不能替远端签字。

对于 GitHub remote，还可额外启用平台 Authority：push 后分别查询 GitHub branch 与 commit，证明平台分支指向冻结 SHA，并确认 GitHub 把 author 和 committer 都映射到配置的预期账号。这补上了“本地邮箱正确，但 GitHub 页面归属仍可能错误”的缺口。

Sandbox 中未配置的 SQL、通知、退款 Connector 会 fail closed，`[deferred:...]` 不再被当成执行成功。任务响应同时暴露 `execution_environment=mock|workspace|custom`；mock 工具动作的终态是 `SIMULATED` 而不是 `COMPLETED`，不会再把模拟误认成真实工作区交付。

各层：M1 治理核心（规则即数据、fail-closed、审计日志）；M2 调度+边界（无执行能力，只放行）；M3 任务运行时（PDCA 循环+状态机）；M4 LLM 层（模型**无法绕过治理**；**OpenAI 兼容 adapter 已接电并验证**——Ollama/DeepSeek/智谱/Moonshot/OpenAI 都通过一个 `base_url` 接入）；M5 工具运行时（沙箱执行、凭据隔离、SSRF、**macOS `sandbox-exec` deny-all 隔离**）；M6 校验引擎（cheapest-first 清单、独立/可校准的模型评判、回炉）；M7 记忆（5 层 SQLite/FTS5/向量/Honcho，**多轮会话历史**）；M8 场景+技能引擎（场景即数据；Skill 必须有可执行案例、内容绑定发布锁和当前运行时重验）；M9 网关（stdlib HTTP+CLI、auth/rate-limit、OpenAI 兼容端点、**内置 React Web UI 同源托管**）；M10 价值流（双模式目标锚定、增值评分、瓶颈识别）；M11 观测性（per-task trace、Prometheus `/metrics`、结构化日志）；M12 迭代/OODA（**真闭环**：SQLite 持久化轨迹、自动归档建议、人审规则/技能补丁、校验器回归集）；M13 多 Agent（专家矩阵+红线否决+优先级仲裁——**接进 permit 作为只加严的第二道闸**，绝不放宽治理决策）；M14 MCP server+渠道适配器+技能市场；M15 配置部署；M16 迭代 agent loop（reason→act→observe，默认"最高决策者"路径）；M17 人工审批+resume（HITL，**resume 前重新 permit**，suspend 期间规则变严仍生效）。

要上线：`pip install -e ".[live]"`（装 httpx），在配置里设 provider + base_url，重启。

### 自己跑起来

一条命令，直接从 GitHub 装（仓库公开，无需 clone）。推荐用 pipx（全局命令 + 自动隔离环境）：

```bash
pipx install "taiyi[live] @ git+https://github.com/zachshi-ai/TaiYi-Agent.git"
# 或用 pip:  pip install "taiyi[live] @ git+https://github.com/zachshi-ai/TaiYi-Agent.git"
taiyi init                                  # 交互式生成 taiyi.yaml（可省略，有默认值）
taiyi serve --config taiyi.yaml             # 装完即有 taiyi 命令
# 卸载:  pipx uninstall taiyi   (或 pip uninstall taiyi)
```

或克隆后本地可编辑安装（开发用）：

```bash
pip install -e ".[dev]"                     # 核心 + 测试（含真实适配器测试）
pip install -e ".[live]"                    # 加 httpx —— 接真实 LLM 必须
cp taiyi.example.yaml taiyi.yaml            # 编辑: provider/base_url/model/api_key, executor, auth…
taiyi serve --config taiyi.yaml             # HTTP 网关（+ /metrics, OpenAI API）
# → 浏览器打开 http://127.0.0.1:8080/ 即 Web UI
#     (对话/任务、人工审批、OODA 审查、记忆/指标、配置)
# 或:  docker compose -f deploy/docker-compose.yml up
# 设 `executor: sandbox` + `sandbox_backend: sandbox_exec` (macOS) 获得真实、
#   内核隔离、受治理的执行
```

用真实模型上线——选一个，写进 `taiyi.yaml`（或 Web 配置面板），重启：

```yaml
# 本地 Ollama（无需 key）
provider: ollama
base_url: http://localhost:11434/v1
model: qwen2.5:7b
api_key: null

# 或任何 OpenAI 兼容云模型（DeepSeek / 智谱 / Moonshot / OpenAI / …）
provider: openai_compat
base_url: https://api.deepseek.com/v1
model: deepseek-v4-flash
quality_model: deepseek-reasoner       # 可选：质量模式
balanced_model: null                   # 留空：回退默认 model，并留证
efficiency_model: deepseek-v4-flash    # 可选：效率模式
api_key: sk-...
```

### 运行测试与示例

```bash
python -m pytest                            # 263 测试
taiyi verify-skills                        # 执行 3 个内置 Skill 的 9 个质量门案例
python3 research/examples/agent_demo.py     # 演示 ReAct loop + 治理拦截
python3 research/demo/src/main.py           # Phase 0 demo
```

## 治理规则长什么样

规则是数据（YAML），由独立引擎加载为只读，模型无法修改：

```yaml
id: authorship.git_identity.no_override
domain: authorship
severity: red_line               # red_line (block) | advisory (warn)
applies_to: ["shell:git*"]
trigger: pre_execution
check:
  type: deterministic
  match: args_any
  patterns: ["-c user.name=", "-c user.email=", "--author="]
on_fail:
  action: block                  # block | warn | request_confirmation
  message: "Overriding the git committer/author identity is forbidden."
precedence: 90
owner: platform-security
```

调度器既看不到这些规则，也无法自我豁免——它只能请求一个 permit。一个 prompt 注入让模型尝试运行 `-c user.name=Evil` 的尝试会被拒绝，而任务继续（拒绝被当作观察反馈给模型）。

## 为什么存在

看 [`learning/docs/00_Design_Document.md`](./learning/docs/00_Design_Document.md) 了解完整的设计哲学与五层架构，或 [`learning/DEVELOPMENT_PLAN.md`](./learning/DEVELOPMENT_PLAN.md) 了解模块化构建路线。
