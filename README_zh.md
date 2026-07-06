# 太一 / The One (Taiyi)

> 一个面向*确定性*生产任务（代码、交易、合规、流程执行）的生产级 Agent 操作系统。它存在的理由是一个设计决策：**治理权与调度权物理分离。** 一个既干活又给自己验收的模块，有动机跳过验收以加快完成——太一用设计消除了这个动机。
>
> English: see [`README.md`](./README.md)。

项目源于一个真实事故：一个 agent 报告"任务完成"，却悄悄替换了 git commit 作者，把用户的代码记到别人名下。表层成功 ≠ 真正做对。太一的目标是把**隐性验收标准**——作者权属、合规、安全——写成模型**无法绕过**的代码，而不是要求它记住的规则。

## 目录组织：学 · 研 · 产 · 用

| 路径 | 内容 |
|---|---|
| **产（生产，留根）** — Agent 本体 | |
| `src/taiyi/` | **生产代码** — 17 个模块 |
| `tests/` | 208 个测试，全绿 |
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

**17 个模块全部建成——完整架构，L4 成熟度的闭环 Agent OS。** 每层都实现并测试（208 测试），且**真实 LLM 端到端跑通**（用 DeepSeek 验证：模型 → 工具调用 → 治理许可 → 沙箱执行 → 结果回灌 → 最终答案，COMPLETED）。

一个请求经 CLI、HTTP 或内置 Web UI 进入，锚定业务目标，匹配场景，规划（规则或 LLM 驱动），逐步经治理闸门，由专家委员会二审，仅在放行后真实执行（沙箱隔离），独立校验（失败回炉），价值评分，追踪/计量，记忆，并喂给 OODA 外循环——把反复出现的失败变成永久治理检查，把重复工作沉淀为带门禁的技能。这个闭环是真的：轨迹跨重启持久化、建议每任务自动归档、人审后写入只读规则/技能集于下次启动生效。

各层：M1 治理核心（规则即数据、fail-closed、审计日志）；M2 调度+边界（无执行能力，只放行）；M3 任务运行时（PDCA 循环+状态机）；M4 LLM 层（模型**无法绕过治理**；**OpenAI 兼容 adapter 已接电并验证**——Ollama/DeepSeek/智谱/Moonshot/OpenAI 都通过一个 `base_url` 接入）；M5 工具运行时（沙箱执行、凭据隔离、SSRF、**macOS `sandbox-exec` deny-all 隔离**）；M6 校验引擎（cheapest-first 清单、独立/可校准的模型评判、回炉）；M7 记忆（5 层 SQLite/FTS5/向量/Honcho，**多轮会话历史**）；M8 场景+技能引擎（场景即数据；**无质量门禁的技能进不了生产**）；M9 网关（stdlib HTTP+CLI、auth/rate-limit、OpenAI 兼容端点、**内置 React Web UI 同源托管**）；M10 价值流（双模式目标锚定、增值评分、瓶颈识别）；M11 观测性（per-task trace、Prometheus `/metrics`、结构化日志）；M12 迭代/OODA（**真闭环**：SQLite 持久化轨迹、自动归档建议、人审规则/技能补丁、校验器回归集）；M13 多 Agent（专家矩阵+红线否决+优先级仲裁——**接进 permit 作为只加严的第二道闸**，绝不放宽治理决策）；M14 MCP server+渠道适配器+技能市场；M15 配置部署；M16 迭代 agent loop（reason→act→observe，默认"最高决策者"路径）；M17 人工审批+resume（HITL，**resume 前重新 permit**，suspend 期间规则变严仍生效）。

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
pip install -e ".[dev]"                     # 核心 + 测试
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
api_key: sk-...
```

### 运行测试与示例

```bash
python -m pytest                            # 208 测试
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
