# 太一 (The One) 技术架构文档 (Technical Architecture Document)

> **版本**:v1.0 / 2026-06-20
> **配套文档**:Design Document / PRD
> **理论原创 / Design Lead**:zachshi | 扎克的自留地
> **AI 协作**:Mavis(MiniMax) · 豆包(ByteDance) · Claude(Anthropic)
> **目标读者**:架构师、后端工程师、DevOps、SRE
> **本文档回答**:太一 (The One) 由哪些组件构成?它们怎么通信?数据怎么流转?怎么部署?

---

## 0. 文档信息

| 项目 | 内容 |
|---|---|
| 系统名称 | 太一 (The One) |
| 文档类型 | 技术架构(Logical + Process + Deployment Views) |
| 关键决策 | 治理-调度物理隔离、Markdown 优先、SQLite 单机基线、MCP 双向 |
| 部署目标 | 单机(2C4G)起步,水平扩展到多节点集群 |
| 编程语言 | 核心 Python 3.11+ / TypeScript 5+ |

---

## 1. 架构视图

### 1.1 4+1 视图(简版)

| 视图 | 文档章节 |
|---|---|
| 逻辑视图 | §2 组件与职责 |
| 进程视图 | §3 运行时主循环 + 数据流 |
| 部署视图 | §4 部署架构 |
| 开发视图 | §5 技术选型与代码组织 |
| 场景视图(4+1 的 +1) | §6 关键场景走读 |

---

## 2. 组件与职责(逻辑视图)

### 2.1 顶层组件图

```
┌──────────────────────────────────────────────────────────────────────┐
│                              Gateway (P0 核心)                         │
│   - WebSocket / HTTP 入口                                             │
│   - 鉴权 / 限流 / 会话管理 / 状态广播                                  │
│   - 单一真相源(运行时)                                                 │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
       ┌───────────────────────┼───────────────────────┐
       │                       │                       │
       ▼                       ▼                       ▼
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│ Channel      │       │ Task Worker  │       │ Cron         │
│ Adapter      │       │ Pool         │       │ Scheduler    │
│              │       │              │       │              │
│ - 飞书/钉钉  │       │ - PDCA 循环  │       │ - Heartbeat  │
│ - 微信/TG    │       │ - 多 Agent  │       │ - at/every   │
│ - Discord    │       │ - 模型调用   │       │ - 自然语言    │
│ - Email      │       │              │       │              │
└──────┬───────┘       └──────┬───────┘       └──────┬───────┘
       │                      │                      │
       └──────────────────────┼──────────────────────┘
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
       ▼                      ▼                      ▼
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│ Memory       │       │ Skill        │       │ Knowledge    │
│ Engine       │       │ Registry     │       │ Service      │
│              │       │              │       │              │
│ 5 层记忆     │       │ 4 类技能     │       │ 规则/文档    │
│ Honcho 建模  │       │ 质量门禁     │       │ 向量+全文    │
└──────────────┘       └──────────────┘       └──────────────┘

       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
       ▼                      ▼                      ▼
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│ Governance   │       │ Scheduler    │       │ Validation   │
│ Engine       │       │ Engine       │       │ Engine       │
│              │       │              │       │              │
│ 红线规则     │       │ 模型路由     │       │ 客观验证     │
│ 凭证隔离     │       │ 工具选择     │       │ 同行复评     │
│ SSRF 防护    │       │ 多 Agent     │       │ 人审调度     │
│ 审批流       │       │ 委派         │       │              │
└──────────────┘       └──────────────┘       └──────────────┘

       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
       ▼                      ▼                      ▼
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│ Tool         │       │ Model        │       │ MCP          │
│ Runtime      │       │ Router       │       │ Gateway      │
│              │       │              │       │              │
│ 6 种后端     │       │ 25+ Provider │       │ Client/Server│
│ Local/Docker │       │ 智能路由     │       │ 双向暴露     │
│ SSH/Modal    │       │ Token 经济   │       │              │
└──────────────┘       └──────────────┘       └──────────────┘
```

### 2.2 关键组件职责矩阵

| 组件 | 职责 | 不做 | 接口 |
|---|---|---|---|
| Gateway | 入口路由/鉴权/限流/会话管理 | 业务逻辑 | WS / HTTP / 内部 gRPC |
| Channel Adapter | 消息平台协议适配(收发) | 内容理解 | 平台 SDK + 内部标准化事件 |
| Task Worker | 跑 PDCA 主循环 | 模型选择 | 内部 task API |
| Cron Scheduler | 主动调度 | 任务执行 | croniter + 内部 task API |
| Memory Engine | 5 层记忆读写 | 业务理解 | MemoryProvider ABC |
| Skill Registry | 技能注册/检索/版本管理 | 技能执行 | SkillSpec + 内部 task API |
| Knowledge Service | 文档/规则/合规 | LLM 推理 | KnowledgeQuery + 检索 API |
| Governance Engine | 红线/审批/凭证 | 调度决策 | IssuePermit API |
| Scheduler Engine | 选模型/工具/技能 | 自我豁免 | Schedule + 申请许可证 |
| Validation Engine | 客观/主观/同行验证 | 业务实现 | Validate API |
| Tool Runtime | 工具执行沙箱 | 业务逻辑 | ToolExecutor ABC |
| Model Router | Provider 路由 + 限流 + 重试 | 业务实现 | Provider ABC |
| MCP Gateway | MCP 协议接入/暴露 | 业务逻辑 | MCP stdio / HTTP |

### 2.3 治理-调度物理隔离(关键设计)

**这是 太一 (The One) 最核心的架构决策**。具体落地:

```
┌──────────────────────────────────────────────────────────┐
│                     Process 1: gateway                     │
│  - 入口路由 / 鉴权 / 限流 / 会话                          │
│  - 调度层 (Scheduler Engine)                              │
└──────────────┬───────────────────────────────────────────┘
               │ IPC (gRPC / 共享内存)
┌──────────────▼───────────────────────────────────────────┐
│                     Process 2: governance                  │
│  - 治理层 (Governance Engine)                             │
│  - 红线规则 / 审批 / 凭证隔离 / SSRF 防护                 │
│  - 物理上不可被调度进程修改                                │
└──────────────────────────────────────────────────────────┘
```

**约束**:
1. 治理进程以**只读**方式加载规则(规则更新需走人审 + 重启)
2. 调度进程**不持有**任何高危操作执行能力,必须通过治理进程申请
3. 即使调度进程被攻击者拿下,所有高危操作仍需治理进程盖章
4. 治理进程是 single-tenant 进程——一个 太一 (The One) 实例对应一个治理进程

**接口定义**(简化):

```protobuf
// 调度 → 治理:申请执行许可证
message ExecutionRequest {
  string task_id = 1;
  string actor = 2;             // 哪个 Agent
  string tool = 3;              // 用什么工具
  map<string,string> args = 4;  // 工具参数
  ScenarioContext scenario = 5; // 当前场景
  UserContext user = 6;         // 当前用户
}

message ExecutionPermit {
  enum Verdict {
    ALLOW = 0;       // 放行
    DENY = 1;        // 拒绝
    NEEDS_REVIEW = 2; // 转人审
  }
  Verdict verdict = 1;
  string reason = 2;
  string evidence = 3;        // 决策依据
  int64 expires_at = 4;       // 许可证过期时间
  string approval_id = 5;     // 若转人审,审批单 ID
}
```

---

## 3. 运行时主循环(进程视图)

### 3.1 单任务 PDCA 时序图

```
用户/通道         Gateway       Scheduler     Governance      LLM Provider        Tools/MCP        Validation      L5 Iteration
   │                │              │              │                │                  │                │                │
   │─── 输入 ─────→│              │              │                │                  │                │                │
   │                │── 解析 ────→│              │                │                  │                │                │
   │                │              │ 选模型/工具  │                │                  │                │                │
   │                │              │ 拆子任务     │                │                  │                │                │
   │                │              │              │                │                  │                │                │
   │                │              │── 申请许可证 ─→│              │                  │                │                │
   │                │              │←─ 放行/拒绝 ─│              │                  │                │                │
   │                │              │              │                │                  │                │                │
   │                │              │── 调用 LLM ─────────────────→│                  │                │                │
   │                │              │←─ 思考/工具调用 ──────────────│                  │                │                │
   │                │              │── 调用工具 ───────────────────────────────→│                │                │
   │                │              │←─ 工具结果 ──────────────────────────────│                │                │
   │                │              │              │                │                  │                │                │
   │                │              │ (循环:工具调用 + LLM 思考)                │                │                │
   │                │              │              │                │                  │                │                │
   │                │              │── 提交结果 ─────────────────────────────────────────────→│                │
   │                │              │              │                │                  │                │                │
   │                │              │              │                │                  │                │── 客观验证 ──→│
   │                │              │              │                │                  │                │←─ Pass/Fail ──│
   │                │              │              │                │                  │                │                │
   │                │              │ (若 Fail:回弹 L3 修正,进入下一轮 PDCA)      │                │                │
   │                │              │              │                │                  │                │                │
   │                │              │── 归档轨迹 ────────────────────────────────────────────────────────→│
   │                │              │              │                │                  │                │                │
   │                │←─ 响应 ─────│              │                │                  │                │                │
   │←─ 推送 ──────│              │              │                │                  │                │                │
   │                │              │              │                │                  │                │                │
```

### 3.2 数据流(关键数据结构)

```python
# Task Context - 跨模块流转的"任务上下文"
@dataclass
class TaskContext:
    task_id: str                  # 任务唯一 ID
    session_id: str               # 所属会话
    user_id: str                  # 用户标识
    channel: str                  # 触达通道
    scenario: ScenarioContext     # 场景(从 L1 加载)
    prompt: str                   # 原始输入
    task_info: dict               # 任务级一次性信息
    knowledge_hits: list          # 知识检索结果
    plan: ExecutionPlan           # L3.2 制定的执行计划
    permits: list[ExecutionPermit] # 已获得的许可证
    tool_results: list            # 工具执行历史
    validation_history: list      # 验证历史
    state: TaskState              # 当前状态机
    # === H4 价值流层字段 (zachshi 提出,Phase 1) ===
    goal: TaskGoal | None         # 三层目标锰定
    value_contribution: ValueContribution | None  # 完成后填入
    goal_anchoring_mode: GoalAnchoringMode        # 锰定模式 A/B
    created_at: datetime
    updated_at: datetime

# Goal Stack - 三层目标锰定 (借鉴 APQC + OKR)
@dataclass
class TaskGoal:
    task_layer: GoalRef            # 任务层目标 (必填)
    tactical_layer: GoalRef | None # 战术层目标 (可选)
    strategic_layer: GoalRef | None # 战略层目标 (可选)
    value_stream_id: str | None    # 所属价值流模板 ID
    anchored_at: datetime          # 锰定时间
    anchoring_source: str          # "user_explicit" / "llm_inferred" / "preset"

@dataclass
class GoalRef:
    goal_id: str                   # 目标 ID
    title: str                     # 目标标题
    kpi_id: str | None             # 关联 KPI
    target_value: float | None     # 目标值
    owner: str | None              # 责任人

class GoalAnchoringMode(str, Enum):
    AI_INFER_CONFIRM = "A"         # AI 推断 + 用户确认
    PRESET_DEFAULT = "B"           # 预设分级

# Value Contribution - 任务对目标的贡献评分
@dataclass
class ValueContribution:
    task_layer_completion: float    # 0-1,任务层完成度
    tactical_alignment: float       # 0-1,对战术目标贡献
    strategic_alignment: float      # 0-1,对战略目标贡献
    wasted_steps: list[str]         # 价值流上的浪费环节
    bottleneck_nodes: list[str]     # 造成延迆/失败的环节
    notes: str                      # 人工/Agent 补充说明

# Memory Write - 记忆写入的统一格式
@dataclass
class MemoryWrite:
    layer: MemoryLayer            # L1-L5
    content: str                  # Markdown 内容
    tags: list[str]               # 标签
    source_task_id: str           # 来源任务
    expires_at: datetime | None   # 过期时间(可选)
    importance: int               # 1-10,影响检索排序
```

### 3.3 状态机(单任务)

```
        ┌─────────┐
   入站 │ PENDING │
        └────┬────┘
             │ Gateway 接收
             ▼
        ┌─────────┐
        │PARSING  │ L1 输入解析
        └────┬────┘
             │
             ▼
        ┌─────────┐
        │PLANNING │ L3.2 调度层制定计划
        └────┬────┘
             │
             ▼
   ┌─────────────────┐
   │AWAITING_PERMIT  │ 等待治理许可证
   └────┬──────┬─────┘
        │      │
   DENY │      │ ALLOW
        │      │
        ▼      ▼
   ┌─────────┐  ┌──────────┐
   │REJECTED │  │EXECUTING │ LLM + 工具循环
   └────┬────┘  └────┬─────┘
        │            │
        │            ▼
        │      ┌──────────┐
        │      │VALIDATING│ L4 输出验证
        │      └────┬─────┘
        │            │
        │            │ (失败 → 回弹 PLANNING / EXECUTING)
        │            │
        │            ▼
        │      ┌─────────┐
        │      │COMPLETED│ 通过验证
        │      └────┬────┘
        │           │
        │           ▼
        │      L5 迭代层归档
        │
        └─────────────→ (若验证失败,或人审拒绝,转 FAILED / REJECTED)

   ┌────────────┐
   │NEEDS_REVIEW│ 转人审(治理 NEEDS_REVIEW 决策触发)
   └────┬───────┘
        │ 人审通过 → 继续 EXECUTING
        │ 人审拒绝 → REJECTED
        ▼
     人审结果反馈 L5
```

---

## 4. 部署架构(部署视图)

### 4.1 单机部署(MVP / Demo)

```
┌──────────────────────────────────────────────────────────────┐
│  Host (2C4G 起步)                                           │
│                                                              │
│  ┌──────────────────── 太一 (The One) Pod (Docker Compose) ─────────┐ │
│  │                                                          │ │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────────┐  │ │
│  │  │Gateway  │ │Worker   │ │Governance│ │  Cron      │  │ │
│  │  │:8080    │ │(1+)     │ │:7070     │ │  (进程内)  │  │ │
│  │  └────┬────┘ └────┬────┘ └────┬─────┘ └─────┬──────┘  │ │
│  │       │           │           │             │          │ │
│  │       └───────────┴─────┬─────┴─────────────┘          │ │
│  │                         │                              │ │
│  │       ┌─────────────────┼─────────────────┐            │ │
│  │       │                 │                 │            │ │
│  │  ┌────▼────┐      ┌─────▼─────┐    ┌──────▼──────┐   │ │
│  │  │  SQLite │      │ Markdown  │    │  Skill/     │   │ │
│  │  │  +FTS5  │      │  Memory   │    │  Scenario   │   │ │
│  │  │ +vec    │      │  ~/helix/ │    │  (本地目录) │   │ │
│  │  └─────────┘      └───────────┘    └─────────────┘   │ │
│  │                                                          │ │
│  │  (Skill/Scenario 在 MVP 阶段用本地目录;               │ │
│  │   Phase 3 扩展为 Git 协议分发的市场)                  │ │
│  │                                                          │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─────────────────────────┐                                │
│  │  Ollama / 远端 LLM API  │                                │
│  │  (本机或可达)             │                                │
│  └─────────────────────────┘                                │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 中等规模部署(团队 / 小企业)

```
                         ┌──────────────┐
                         │   LB / CDN   │
                         │  (Nginx)     │
                         └──────┬───────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
   ┌────▼─────┐           ┌─────▼─────┐           ┌─────▼─────┐
   │ Gateway1 │           │ Gateway2  │           │ Gateway3  │
   └────┬─────┘           └─────┬─────┘           └─────┬─────┘
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                │
   ┌────────────────────────────┼────────────────────────────┐
   │                            │                            │
┌──▼──────┐              ┌──────▼──────┐              ┌──────▼──────┐
│Worker   │              │Worker       │              │Governance   │
│Pool     │              │Pool         │              │(HA: 2 实例) │
│(4-8)    │              │(4-8)        │              │             │
└────┬────┘              └──────┬──────┘              └──────┬──────┘
     │                          │                           │
     └──────────────┬───────────┴───────────────────────────┘
                    │
   ┌────────────────┼────────────────────┐
   │                │                    │
┌──▼──────┐    ┌────▼─────┐       ┌──────▼──────┐
│Postgres │    │ Redis    │       │ Object Store│
│(主存)   │    │(缓存/队列)│       │(S3/MinIO)   │
└─────────┘    └──────────┘       └─────────────┘
```

### 4.3 大规模部署(企业级 / Phase 4 之后)

- Kubernetes 编排
- 治理进程独立集群(高安全等级)
- 多 Region 部署
- Worker 池自动扩缩容(KEDA)
- 模型 Provider 故障自动切换
- 全链路 OTel + Grafana + Loki

### 4.4 部署矩阵

| 形态 | 适用 | 资源 | 部署工具 |
|---|---|---|---|
| Demo | 验证 | 1C2G | curl 一行脚本 |
| Personal | 个人开发者 | 2C4G | npm install / pip install |
| Team | 小团队 | 4C8G 起 | Docker Compose |
| Enterprise | 企业 | 多节点 | K8s + Helm |
| Serverless | 偶尔使用 | Serverless | Cloudflare Workers / Modal |

---

## 5. 技术选型与代码组织(开发视图)

### 5.1 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 主语言 | Python 3.11+ | AI 生态最成熟、async/await 完善 |
| Web/API | FastAPI | 性能好、类型安全、自动 OpenAPI |
| 异步运行时 | asyncio | 高并发 IO 密集场景 |
| 持久化(单机) | SQLite + FTS5 + sqlite-vec | 零运维,符合 MVP 定位 |
| 持久化(集群) | PostgreSQL + pgvector + Redis | 可扩展 |
| LLM 路由 | LiteLLM 风格抽象 | 25+ Provider 一致接口 |
| 工具沙箱 | Docker / SSH / Modal | 多形态隔离 |
| 通道 SDK | 平台官方 SDK | 稳定性 + 长期维护 |
| 观测 | OpenTelemetry + Prometheus | 行业标准 |
| 配置 | Pydantic Settings + YAML | 类型安全 |
| 部署 | Docker Compose (单机) / K8s (集群) | 灵活 |
| 文档 | MkDocs + 自动生成 | 与代码同步 |

### 5.2 代码组织(Monorepo)

```
helix/
├── pyproject.toml
├── README.md
├── helix/                          # 主包
│   ├── __init__.py
│   ├── main.py                     # 入口
│   ├── config.py                   # 全局配置
│   │
│   ├── core/                       # 核心(框架无关)
│   │   ├── types.py                # 数据类型
│   │   ├── context.py              # TaskContext
│   │   ├── events.py               # 事件总线
│   │   └── errors.py
│   │
│   ├── gateway/                    # 入口网关
│   │   ├── server.py               # FastAPI 入口
│   │   ├── auth.py                 # 鉴权
│   │   ├── rate_limit.py
│   │   └── session.py
│   │
│   ├── channels/                   # 通道适配器
│   │   ├── base.py
│   │   ├── cli.py
│   │   ├── web.py
│   │   ├── feishu.py
│   │   ├── dingtalk.py
│   │   ├── telegram.py
│   │   ├── discord.py
│   │   └── ...
│   │
│   ├── runtime/                    # 运行时主循环
│   │   ├── worker.py               # PDCA 主循环
│   │   ├── planner.py              # L3.2 任务规划
│   │   ├── executor.py             # 执行
│   │   └── state_machine.py
│   │
│   ├── governance/                 # L3.1 治理(独立进程!)
│   │   ├── server.py               # 治理进程入口
│   │   ├── rules.py                # 红线规则
│   │   ├── approval.py             # 审批流
│   │   ├── credential_isolation.py
│   │   ├── ssrf.py
│   │   └── prompt_injection.py
│   │
│   ├── scheduler/                  # L3.2 调度
│   │   ├── model_router.py
│   │   ├── tool_selector.py
│   │   ├── skill_matcher.py
│   │   ├── multi_agent.py          # 多 Agent 委派
│   │   └── cron.py
│   │
│   ├── validation/                 # L4 输出验证
│   │   ├── objective.py
│   │   ├── subjective.py
│   │   ├── peer_review.py
│   │   └── history_compare.py
│   │
│   ├── memory/                     # 5 层记忆
│   │   ├── l1_short_term.py
│   │   ├── l2_skill.py
│   │   ├── l3_vector.py
│   │   ├── l4_honcho.py            # 用户建模
│   │   └── l5_fts5.py
│   │
│   ├── skills/                     # 技能引擎
│   │   ├── registry.py
│   │   ├── loader.py
│   │   ├── quality_gate.py
│   │   ├── auto_generation.py
│   │   └── catalog/                # 内置技能
│   │       ├── git_safe_commit/
│   │       ├── weekly_report/
│   │       └── code_review/
│   │
│   ├── knowledge/                  # 知识库
│   │   ├── indexer.py
│   │   ├── retriever.py
│   │   └── rules/
│   │
│   ├── scenarios/                  # 场景工程
│   │   ├── registry.py
│   │   ├── matcher.py
│   │   └── catalog/
│   │       ├── dev/
│   │       ├── customer_service/
│   │       └── finance/
│   │
│   ├── tools/                      # 工具
│   │   ├── registry.py
│   │   ├── executor.py
│   │   ├── backends/
│   │   │   ├── local.py
│   │   │   ├── docker.py
│   │   │   └── ssh.py
│   │   └── builtin/
│   │       ├── file.py
│   │       ├── shell.py
│   │       ├── http.py
│   │       └── sql.py
│   │
│   ├── models/                     # 模型 Provider
│   │   ├── base.py
│   │   ├── router.py
│   │   └── providers/
│   │       ├── openai.py
│   │       ├── anthropic.py
│   │       ├── ollama.py
│   │       └── ...
│   │
│   ├── mcp/                        # MCP 双向
│   │   ├── client.py
│   │   ├── server.py
│   │   └── tools/
│   │
│   ├── iteration/                  # L5 迭代层
│   │   ├── trajectory.py
│   │   ├── analyzer.py
│   │   ├── skill_upgrader.py
│   │   └── rule_patcher.py
│   │
│   ├── observability/              # 观测
│   │   ├── tracing.py
│   │   ├── metrics.py
│   │   └── audit.py
│   │
│   └── protocols/                  # 协议
│       ├── openai_compat.py
│       └── acp.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── docs/
│
├── deploy/
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── k8s/
│   └── helm/
│
└── examples/
    └── ...
```

### 5.3 关键接口规约

#### 5.3.1 治理-调度接口(gRPC)

参考 §2.3 的 protobuf 定义。

#### 5.3.2 任务提交接口(HTTP)

```http
POST /v1/tasks
Authorization: Bearer <token>
Content-Type: application/json

{
  "prompt": "帮我跑测试然后 commit",
  "scenario": "dev.git",
  "channel": "cli",
  "async": false
}
```

```json
// 响应
{
  "task_id": "tsk_2026_06_20_001",
  "status": "awaiting_review",
  "permit_id": "prm_...",
  "reason": "敏感操作需要人审",
  "expires_at": "2026-06-20T18:30:00Z"
}
```

#### 5.3.3 MCP Server 暴露

太一 (The One) 自身作为 MCP Server 时,暴露以下工具:

| 工具 | 描述 | 权限 |
|---|---|---|
| `helix_run_task` | 提交一个任务 | 受治理 |
| `helix_search_memory` | 检索记忆 | 受治理 |
| `helix_list_skills` | 列出可用技能 | 公开 |
| `helix_get_skill` | 获取技能详情 | 公开 |
| `helix_apply_rule` | 申请新规则(需审批) | 受治理 |

---

## 6. 关键场景走读(场景视图)

### 6.1 场景 A:Git 安全提交(zachshi-ai 文章原案例)

**目标**:用户让 Agent 跑测试 + commit + push,Agent **绝不**修改 committer 身份。

**走读**:

1. **L1**:用户输入"测试 + commit + push" → 加载 `dev.git` 场景 → 场景约束:必须使用 `git config user.name/email`,禁止覆盖
2. **L3.2**:调度层制定计划
   - 选模型:轻量
   - 选工具:shell_executor(受限)
   - 选技能:无需(可手动跑)
3. **L3.1 治理**:调度申请 shell_executor 执行 `git commit`
   - 治理检查:操作 = `git commit`,参数 = `["-m", "..."]`
   - 规则匹配:`dev.git.commit` 红线 → **必须先预检 committer**
   - 治理执行预检:在 shell 沙箱内先 `git config --get user.name` 与 用户档案匹配 → 通过 → 放行
4. **L2 执行**:沙箱内跑测试 → commit → push
5. **L4 验证**:
   - 客观:拉取 GitHub 端最新 commit,验证 author.email 与本地 config 一致
   - 不一致 → 回弹 L3 修正
   - 一致 → 通过
6. **L5 归档**:写入轨迹,异常事件(若发生过)送入 OODA 优化规则库

**为什么能保证不出问题**:
- 治理层是物理隔离进程,即便 LLM 失控,也无法绕过预检
- 预检是 shell 沙箱内执行,沙箱与用户 `git config` 真实一致
- L4 验证是 GitHub 端独立拉取验证,无法伪造

### 6.2 场景 B:周报自动生成

**目标**:每周一早上 8 点生成周报推到飞书群。

**走读**:

1. **L5/Cron**:周一 8:00 触发,产生 `task_id`
2. **L1**:加载 `ops.weekly_report` 场景,场景约束:数据来源限定 `analytics_db` 表
3. **L3.2**:调度层
   - 选模型:主力模型(报告生成需要推理)
   - 选技能:`weekly_report` 技能(已有)
   - 选工具:SQL 工具 + 飞书推送工具
4. **L3.1 治理**:
   - SQL 工具:库/表白名单通过
   - 飞书推送:群 ID 校验
   - 全部放行
5. **L2 执行**:
   - SQL 工具查数据
   - LLM 生成 Markdown 报告
   - 飞书推送工具发送
6. **L4 验证**:
   - 客观:报告字段完整、PDF 格式、推送到指定群
   - 同行 Agent 复评:数据来源合规性
7. **L5**:轨迹归档,周报 Skill 性能指标更新

### 6.3 场景 C:多 Agent 合同审阅

**目标**:让安全/合规/业务专家 Agent 一起审合同草稿,红线一票否决。

**走读**:

1. **L1**:用户提交合同 PDF
2. **L3.2 调度**:识别为多专家任务
   - 拆分子任务:安全审 / 合规审 / 业务审
   - 委派到对应专家 Agent
3. **L3.1 治理**:为每个专家 Agent 准备
   - 专家 Agent 工具集 = (专家专属工具) ∩ (全局允许工具)
4. **L2 并发执行**:
   - 安全 Agent:扫描数据保护条款
   - 合规 Agent:扫描合规风险
   - 业务 Agent:评估商业价值
   - 通过"黑板"共享初稿
5. **L3.1 仲裁**:
   - 安全 Agent:发现"未约定数据归属" → 红线 → **一票否决**
   - 业务 Agent:建议"修改第 3 条措辞" → 优化维度 → 采纳决策归执行 Agent
   - 合规 Agent:无意见
6. **状态机**:任务标记 `paused` → 转人审,推送"请用户决策"
7. **用户决策**:接受安全意见 → 任务回退到合规 Agent 复评 → 通过 → 完成

### 6.4 场景 D:Skill 自生成(闭环学习)

**目标**:同一类任务(查 Sentry + 生成修复 PR)完成 5 次后,自动生成 Skill 草稿。

**走读**:

1. **L5 迭代层**:检测到 5 次类似任务 → 触发 Skill 自动生成
2. **生成草稿**:
   - 抽取共性步骤
   - 生成 `SKILL.md`(步骤 + 注意事项)
   - 生成 `quality_gate.md`(准入/退出标准)
   - 生成 3 个测试用例
3. **沙箱验证**:
   - 在沙箱中跑通测试用例 10 次
   - 10/10 通过 → 进入"待审批"
4. **L5 通知管理员**:Web Console 显示"新 Skill 草稿待审批"
5. **管理员审批**:通过 → 进入 `workspace` 类(组织私有)/ 推广到 `managed`(社区)
6. **下一次同类任务**:自动匹配该 Skill,任务执行时间大幅缩短

---

## 7. 性能与可扩展性设计

### 7.1 性能优化

| 优化点 | 手段 | 目标收益 |
|---|---|---|
| LLM 成本 | 智能路由(简单→轻量,复杂→主力),prompt cache,批量调用 | Token 成本降低 30-50%(参照 Hermes 的"否定式启发"实测) |
| 记忆检索 | 向量索引 + FTS5 双路召回,MMR 重排序,缓存热点 | P95 < 200ms |
| Skill 匹配 | 倒排索引 + 语义检索 + 缓存 | 匹配耗时 < 50ms |
| 工具执行 | 沙箱预热(连接池),结果缓存,批量调用 | 平均节省 200ms/调用 |
| 通道接收 | 长连接 + 事件驱动,非阻塞 | 并发 100 任务 P95 < 60s |
| 验证开销 | 客观验证并行执行,主观人审串行 | 验证耗时 P95 < 1s |

### 7.2 可扩展性

- **水平扩展**:Worker 池可独立扩缩容(KEDA)
- **存储扩展**:SQLite → Postgres,本地 → MinIO
- **模型扩展**:Provider 抽象,可热插拔
- **技能扩展**:技能市场,Git 协议分发
- **通道扩展**:Adapter 插件化,新通道只需 1 个文件

---

## 8. 安全架构

### 8.1 五层安全防线

```
L1:用户认证 (AuthN)
   ↓
L2:访问控制 (AuthZ, RBAC + ABAC)
   ↓
L3:执行治理 (Governance, 红线 + 审批 + 凭证隔离)
   ↓
L4:行为审计 (Audit Log, 全链路 trace)
   ↓
L5:供应链安全 (Skill 审核, Provider 验证, 依赖扫描)
```

### 8.2 凭证隔离

子进程只拿安全环境变量:
- 保留:`PATH`, `HOME`, `LANG`, `TZ`, `HELIX_HOME`
- 过滤:`*_API_KEY`, `*_TOKEN`, `*_SECRET`
- 自定义白名单

### 8.3 SSRF 防护

- URL 白名单(管理员可配)
- 内网 IP 拒绝(`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
- DNS rebinding 防护
- 默认 fail-closed

### 8.4 Prompt Injection 防护

- 上下文文件注入扫描
- 工具返回结果脱敏
- 高风险来源标记
- 用户指令 vs 工具结果严格分层

### 8.5 审计与可追溯

- 全链路 OpenTelemetry trace
- 治理决策日志(永久保存)
- 高危操作需"双签"(人 + 系统)
- 审计日志可导出、可签名

---

## 9. 可观测性

### 9.1 指标(Metrics)

| 指标 | 类型 | 用途 |
|---|---|---|
| `helix_tasks_total` | Counter | 任务总数 |
| `helix_task_duration_seconds` | Histogram | 任务耗时 |
| `helix_task_status` | Counter | 任务状态分布 |
| `helix_governance_verdict` | Counter | 治理决策分布 |
| `helix_governance_latency_seconds` | Histogram | 治理决策耗时 |
| `helix_llm_tokens_total` | Counter | Token 消耗 |
| `helix_llm_cost_usd` | Counter | 成本 |
| `helix_skill_hit_rate` | Gauge | Skill 命中率 |
| `helix_validation_failure_rate` | Gauge | 验证失败率 |
| `helix_active_workers` | Gauge | Worker 数 |

### 9.2 追踪(Tracing)

- 一次任务 = 1 个 trace
- 关键阶段(解析/规划/执行/验证/归档)= 5 个 span
- LLM 调用 = 子 span(包含 token 数、成本、模型)
- 工具调用 = 子 span
- 治理决策 = 子 span(关键证据)

### 9.3 日志(Logging)

- 结构化 JSON 日志
- 9 个级别(trace 到 critical)
- 关联 trace_id / task_id / user_id
- 关键事件:**人审请求、治理拒绝、Skill 升级、规则变更**

---

## 10. 迁移与兼容

### 10.1 从 OpenClaw 迁移
- Skill 目录直接复用
- 记忆(MEMORY.md + 日志)可导入
- 配置(`config.yaml`)自动转换
- 通道配置兼容

### 10.2 从 Hermes Agent 迁移
- 提供 `helix migrate` CLI
- 导入 SKILL.md、记忆、配置
- 桥接 5 层记忆到 5 层记忆
- 一键迁移 Skill、User Profile

### 10.3 兼容性
- OpenAI 兼容 API → 任何 OpenAI 客户端可直接用
- MCP Server → Claude Code / Cursor / Codex 可直接调用
- ACP 协议 → 主流编辑器集成

---

## 11. 总结

本技术架构文档是 太一 (The One) 从设计走向实现的关键桥梁。它明确了:

1. **组件边界**——每个模块只管一件事
2. **接口规约**——模块间通过清晰协议通信
3. **数据流转**——TaskContext 贯穿始终
4. **物理隔离**——治理与调度不在同一进程
5. **可扩展**——所有热路径都可独立扩缩容
6. **可观测**——指标/追踪/日志三位一体
7. **可迁移**——兼容 OpenClaw / Hermes 生态

下一步是 Phase 0 Demo,用 ~500 行 Python 验证核心循环可行性。
