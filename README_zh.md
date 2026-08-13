# 太一 / The One (Taiyi)

> 一个面向*确定性*生产任务（代码、交易、合规、流程执行）的 Agent Harness / Agent OS 原型。它存在的理由是一个设计决策：**治理权与调度权物理分离。** 一个既干活又给自己验收的模块，有动机跳过验收以加快完成——太一用设计消除了这个动机。
>
> English: see [`README.md`](./README.md)。

项目源于一个真实事故：一个 agent 报告"任务完成"，却悄悄替换了 git commit 作者，把用户的代码记到别人名下。表层成功 ≠ 真正做对。太一的目标是把**隐性验收标准**——作者权属、合规、安全——写成模型**无法绕过**的代码，而不是要求它记住的规则。

## 目录组织：学 · 研 · 产 · 用

| 路径 | 内容 |
|---|---|
| **产（生产，留根）** — Agent 本体 | |
| `src/taiyi/` | **生产代码** — 可靠运行、上下文、治理、执行与验证模块 |
| `tests/` | 391 个已收集测试，覆盖治理不变量、三模式、持久任务/副作用恢复、仓库上下文、LLM 故障、基准合同与可执行 Skill 门禁 |
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
| [`research/benchmark/`](./research/benchmark/) | 版本化 Harness 协议基准、逐次回执与基线 |
| [`research/demo/`](./research/demo/) | Phase 0 一次性 demo（全 mock，仅参考） |
| **用（practices/）** — 落地后的优秀实践 | |
| [`practices/`](./practices/) | 经过验证的技能、prompt、运维笔记（持续积累） |

## 当前状态

**核心代码骨架与可靠运行链路已经建成，目前是向 L4 演进的 L3 生产原型。** 治理、permit、ReAct、沙箱、验证、审计和人工恢复均有可运行实现；真实 LLM 端到端路径也已用 DeepSeek 验证。默认 `mock` executor 和尚未接入的业务 connector 仍是明确的非生产边界，不能拿测试全绿代替真实业务验收。

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

### 可恢复运行协议

三种模式共用同一个持久化运行协议。`TaskState` 表示用户看到的结果，独立的
`RunPhase` 表示 Harness 此刻正在等待模型、等待 permit、运行工具、验证、等待审批、
恢复还是已经 `SETTLED`。因此工具阶段发生的超时不会被误报成 LLM 超时，
`NEEDS_REVIEW` 也不会被误当作任务已经完全结束。

配置 `base_dir` 后，每个任务都会写入 fsync 的类型化事件流和原子 checkpoint。
Workflow 与 ReAct Agent 在等待人工审批时都会保存计划/对话、已执行步骤、冻结合同和
继续点；进程重启后会恢复审批队列，批准后仍须重新经过 governance permit。

Sandbox 的 shell 工具现在由独立持久 supervisor 执行，不再受一次 30 秒阻塞调用限制。
每次操作会在启动前获得稳定 operation id；重复 id 只会重连同一个 job，不会重放副作用。
心跳、进程组取消、空闲/硬超时、精确退出码或信号、有硬上限的 stdout/stderr artifact 共同让
长命令可观察，同时只把更小的输出尾部送进模型上下文。artifact 达上限后 worker 仍持续排空，
并记录完整原始流的字节数和 SHA-256，因此既不会撑满磁盘，也不会把持续输出误判成 idle。新 executor 已可重连运行中的
job；重启后的网关会先取得任务级 lease，再重连原 operation，恢复冻结的 Workflow 计划或
ReAct 对话，并从精确的下一步继续。`POST /v1/tasks` 支持 `async=true`，客户端可以查询任务
状态、类型化事件、job 心跳并取消，不必一直占用原 HTTP 请求。太一不会自动重跑结果不确定的非持久外部副作用。详细设计见
[`learning/docs/07_Durable_Runtime_Protocol.md`](./learning/docs/07_Durable_Runtime_Protocol.md)。

### 副作用恢复协议

Workflow 与 Agent Runtime 在派发任何受治理工具前，都会先把 Effect Record 写入
checkpoint：冻结逻辑 operation、参数摘要、由 Harness 决定的副作用等级与重放策略、稳定
idempotency key，以及可用的独立观察 Authority。Connector 的自报文字不能把自己声明成安全
或幂等。

发生超时、取消、Connector 异常或进程退出后，Harness 会区分 `APPLIED`、`NOT_APPLIED` 和
`UNKNOWN`：独立证明已发生时只继续一次；证明未发生时才允许在冻结策略和模式预算内使用同一
key 有界重放；无法判断时进入 `WAITING_INPUT`，步骤不标记为已执行。人工可以选择 `applied`、
`not_applied` 或 `abandon`，但必须提交审计说明，而且这与执行前 approval 是两个不同决策。
任意 shell、SQL、HTTP 和不可逆动作默认 `NEVER` 重放。三种模式只能改变恢复次数，不能改变
真值边界。详见
[`learning/docs/10_Side_Effect_Recovery_Protocol.md`](./learning/docs/10_Side_Effect_Recovery_Protocol.md)。

### Harness 对照基准

Phase 7A 的 18 次确定性协议基准覆盖六类故障/规模案例与三种模式，当前结果是 100%
协议符合、0 次虚假完成、0 次重复副作用。Phase 7B1.1 又冻结同一个模型端点、提示词、工作区、
预算和外部验收器，让 TaiYi、固定版本 Pi 0.84.1 与已发布的 OpenClaw 2026.8.1-beta.1 只比较
Harness 的传输、工具循环、隔离和完成真值：三者均以 2 次模型请求、1 次工具调用完成交付；
模型可见和执行器可用的工具面均限制为冻结的读写预算，并从真实请求反查。ZCode 因缺少权威
批处理证据接口被明确标成 `NOT_COMPARABLE`，不会被伪造为分数。这还不是模型能力或生产效率排名。详见
[`learning/docs/11_Harness_Benchmark_Protocol.md`](./learning/docs/11_Harness_Benchmark_Protocol.md) 和
[`learning/docs/12_Controlled_Cross_Harness_Comparison.md`](./learning/docs/12_Controlled_Cross_Harness_Comparison.md)。

Phase 7B2.1 进一步注入确定性的首 token 停顿和流中途停顿。TaiYi、Pi、OpenClaw 的 6 个
可比较故障单元全部归因到正确 LLM 阶段、安全终止，并保持 0 次虚假完成。Receipt 还把 Harness
启动与模型等待拆开：在本机基线中，OpenClaw 首次模型请求约发生在 7.8 秒，TaiYi 约 0.2 秒，
Pi 约 0.4 秒。这说明过短的总超时可能在 OpenClaw 尚未接触模型时就到期；该数据用于诊断，
不是生产效率排名。详见
[`learning/docs/14_Cross_Harness_Fault_Attribution.md`](./learning/docs/14_Cross_Harness_Fault_Attribution.md)。

Phase 7B2.2 把诊断推进到生产工具执行链：三种模式分别运行了抗拒 SIGTERM 的父子进程树、静默
idle timeout、stdout/stderr 洪泛、父进程退出后的残留后代，以及 job attach 后网关退出并重启。
签名基线 15/15 通过，虚假完成与重复副作用均为 0。任意 shell 超时时，底层仍保留精确 executor
故障；如果无法独立排除外部副作用，顶层会安全升级到 `EFFECT_OUTCOME_UNKNOWN / NEEDS_INPUT`。
Pi、OpenClaw、ZCode 在没有同一权威受控工具生命周期接口前均标为 `NOT_COMPARABLE`。详见
[`learning/docs/15_Tool_Process_Reliability.md`](./learning/docs/15_Tool_Process_Reliability.md)。

Phase 7B2.3 把此前分离的边界放进同一个固定大型仓库任务中。签名 Kubernetes 基线纳入
25,683 个文件、生成 59,825 个可检索分块，并明确标出 1,492 个不可文本检索文件；三种模式分别
注入索引进程退出、冻结快照后的首字节超时、单次大输出工具副作用后的上下文溢出，以及模型等待
期间进程退出。12/12 单元格通过，虚假完成和重复副作用均为 0。真实运行还发现并修复了“路径集合
未变化却每轮重建目录 FTS”的性能问题：问题刷新约耗时 248 秒，修复后对应单元格约为 1.0–2.5 秒。
文件清单完整性和文本可检索覆盖现在分开报告，模型不能从二进制、超大、未支持或读取失败的内容中
推断代码不存在。详见
[`learning/docs/16_Large_Repository_Combined_Resilience.md`](./learning/docs/16_Large_Repository_Combined_Resilience.md)。

Phase 7B2.4 把仓库刷新移入持久 supervisor。并发任务共享一个正在运行的索引代际，后续新任务会
领取新代际重新检查仓库；Gateway 重启后依据 checkpoint 重连原 `job_id`，不会再启动第二个
SQLite writer。取消按任务订阅者隔离：只有唯一订阅者时才终止底层作业，不能破坏仍依赖共享索引的
另一个任务。状态接口也会把仓库索引 JobRecord 与普通工具作业分开显示。生产路径在同一固定
25,683 文件 Kubernetes checkout 上首代耗时 7.190 秒，无变化第二代刷新耗时 0.551 秒。详见
[`learning/docs/17_Durable_Repository_Index_Jobs.md`](./learning/docs/17_Durable_Repository_Index_Jobs.md)。

LLM 请求使用独立的可靠性协议。OpenAI 兼容响应以流式方式读取，并分别约束连接、首 token、
流空闲和单次硬截止。429、5xx、网络和阶段超时可以在模式预算内重试或切换 provider；鉴权失败和
无效请求立即停止。上下文溢出不会触发 provider failover，而是进入独立的结构化压缩协议。每次失败、退避、切换和恢复都会持久化，进程重启后仍会遵守剩余
退避时间和冻结的消息/计划。重试边界在模型结果触发任何工具之前结束，因此不会重放外部副作用。
质量模式预算最大且先重试最强路由，平衡模式首次失败后切换，效率模式只有最短的两次尝试预算。
详细设计见 [`learning/docs/08_LLM_Request_Resilience.md`](./learning/docs/08_LLM_Request_Resilience.md)。

### 大型仓库上下文协议

使用 sandbox 工作区时，太一会建立持久化增量仓库快照，快照同时绑定 Git HEAD 和文件内容摘要。
索引覆盖目录结构、Python 符号和有界行块；检索只把当前模式预算内的片段交给模型，而且每段都带
快照、相对路径、精确行号和内容摘要。仓库文字会被明确标为不可信数据，不能冒充系统指令。
索引每个有界批次都会写入持久心跳；中断事务不会发布半成品快照。`inventory_complete` 与
`searchable_complete` 分别表达文件清单是否截断、内容是否全部可文本检索。

每次 Agent 或模型驱动 Workflow 请求前，上下文引擎都会预留回复空间、限制大工具结果的模型投影，
必要时压缩旧历史。压缩不是让模型自由回忆，而是写入原子的结构化 JSON artifact：完整规范对话、
步骤/证据账本和来源摘要都可追溯，工具调用与结果不会被切开。实际发送给 provider 的投影也冻结在
checkpoint 中，因此进程重启后，即使工作区文件已经变化，仍会重放中断请求真正看到的仓库证据。
`CONTEXT_OVERFLOW` 只允许在模式预算内走“压缩后重试”，不能偷偷换 provider，更不能重放已经执行的
工具副作用。三种模式只改变检索与近期历史预算，不改变合同、治理和恢复真值。详见
[`learning/docs/09_Large_Repository_Context_Protocol.md`](./learning/docs/09_Large_Repository_Context_Protocol.md)。

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
python -m pytest                            # 完整自动化测试
taiyi verify-skills                        # 执行 3 个内置 Skill 的 9 个质量门案例
taiyi benchmark tool-faults --output research/benchmark/results/tool-faults-v1
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
