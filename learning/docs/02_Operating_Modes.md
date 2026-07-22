# 太一三模式运行策略设计

> 状态：已进入生产代码。对应实现：`src/taiyi/policy/`。

## 1. 为什么需要独立的运行策略层

太一原有的核心铁律是“治理权与调度权分离”：调度器决定做什么，治理层独立决定某个动作能不能发生。这解决了越权、安全、合规和权属问题，但它没有完整回答另外一个问题：

> 在合法可执行的范围内，Agent 应该多问还是少问、走多深、验证到什么程度、什么时候才可以宣布完成？

这个问题不能继续塞进 Prompt，也不能用 `agent / workflow` 代替。前者不可验证，后者只是执行形态。因此增加第三个彼此独立的控制面：

| 控制面 | 回答的问题 | 权威边界 |
|---|---|---|
| 治理 Governance | 这个动作允许发生吗？ | 红线、授权、风险、人审；模式不可修改 |
| 调度 Scheduling | 下一步做什么？ | 计划、工具、Skill、模型选择 |
| 运行策略 Operating Policy | 应该多问、多快、多深，何时算完成？ | 交互、预算、验证、修复、停止条件 |

## 2. 不变量

三种模式共享以下不变量：

1. 每个有副作用的动作仍须取得治理 permit。
2. 效率模式不能降低红线、权限、权属、凭证隔离或不可逆操作确认。
3. 场景风险只能加严模式，不能放宽模式。
4. 成功终态由独立完成控制器依据验收证据和执行环境决定，不由产出结果的模型自我宣布。
5. 质量不是“模型觉得不错”，而是每个必过验收标准都有可检查的证据。
6. 模式不能篡改事实：mock 工具动作只能是 `SIMULATED`，非 mock 真实执行才可能是 `COMPLETED`。

## 3. 三种模式

### 3.1 质量模式 `quality`

目标：最大化可证明的正确性，允许增加时间和成本。

- 重要歧义可能改变正确性或验收标准时，先问一个简洁问题。
- 选择 `quality_model`（最强胜任模型）并使用更高步骤预算；未配置专用模型时显式回退默认模型。
- 运行 exhaustive 级验证，保留完整 Evidence Ledger。
- 最多进行 3 次验证/修复尝试。
- 所有必过验收标准均有 PASS 证据后才完成。
- 若只有“非空、无拒绝话术”等基础检查，没有目标绑定检查器，则在任何动作前返回 `CAPABILITY_UNAVAILABLE`，不伪造准确性保证。

### 3.2 平衡模式 `balanced`

目标：错误代价高时问，低风险可逆工作直接推进。

- 只有“错误假设的预期代价”高于“打断用户的代价”时才询问。
- 选择 `balanced_model`（自适应模型），工具和步骤预算按任务选择；未配置时回退默认模型。
- 运行 standard 级验证；高风险任务在配置独立 judge 时自动复核。
- 最多进行 2 次验证/修复尝试。
- 核心标准通过且重大缺口已经解决后完成。

### 3.3 效率模式 `efficiency`

目标：AI 主导，采用最短有效路径交付可用结果。

- 主动采用合理且可逆的默认值。
- 仅在缺少权限、凭证、阻塞信息或面临不可逆选择时询问。
- 选择 `efficiency_model`（最快胜任模型）和较小步骤预算；未配置时回退默认模型。
- 运行 critical 级验证；高风险场景至少提升到 standard。
- 只进行 1 次验证尝试。
- 可用交付物存在且全部关键标准通过后完成。
- 低风险未知任务允许以基础检查交付，但 Contract/API/UI 必须明确标记 `baseline_only`，不能显示成目标正确性已认证。

## 4. Task Contract 与 Evidence Ledger

每个任务在模型规划或工具执行前冻结一份不可变 `TaskContract`：

- 用户目标；
- 目标场景；
- 选中的生产级 Skill；
- 运行模式；
- 交付物定义；
- 必过验收标准；
- 治理约束；
- 本模式允许采用的假设策略。

验证器把每个检查结果写入 `EvidenceLedger`，记录：标准 ID、结果、检查器类型、Contract ID、当前产物摘要和第几次尝试。完成控制器只消费这些结构化证据，不消费模型置信度。完整绑定规则见 [`05_Immutable_Acceptance_Contracts.md`](./05_Immutable_Acceptance_Contracts.md)。

这使“任务完成”从一句自然语言声明变成可审计事实：

```text
Task Contract
  ├─ criterion A ── PASS evidence
  ├─ criterion B ── PASS evidence
  └─ criterion C ── missing/FAIL
                         ↓
                    REPAIR / HUMAN
```

证据全过后还要经过执行环境判定。无工具动作的纯文本任务可正常
`COMPLETED`；一旦使用无副作用 MockExecutor 执行动作，只能得到
`SIMULATED`。这种模拟会保留对话和审计证据，但不会进入真实完成记忆、
价值贡献或自动 Skill 候选。

## 5. 运行时行为

```text
请求 + operating_mode
        ↓
场景匹配 + 生产 Skill 匹配
        ↓
Policy Resolver（应用场景风险下限）
        ↓
Task Contract
        ↓
Plan / Act（每个动作仍走 governance permit）
        ↓
Validation → Evidence Ledger
        ↓
Completion Controller
        ├─ COMPLETE
        ├─ REPAIR（具体失败证据回灌）
        └─ NEEDS_HUMAN / NEEDS_INPUT
```

workflow 模式发生验证失败时，下一轮规划必须收到具体失败标准和证据，不能再次仅用原始 Prompt 规划。agent 模式同样收到结构化修复反馈。

## 6. 配置与调用

执行形态和运行策略分开配置：

```yaml
runtime_mode: agent          # agent | workflow
operating_mode: balanced     # quality | balanced | efficiency
```

Web UI 在每次任务输入处提供三段式开关。API 支持任务级覆盖：

```json
{
  "prompt": "完成并验证这个功能",
  "operating_mode": "quality"
}
```

任务返回包含 `policy`、`provider_route`、`contract` 和 `evidence`，让用户看到模式实际改变了什么，而不是只显示一个标签。

当前实现边界：Agent Runtime 和 model-backed Workflow Runtime 都已消费 `model_strategy`，支持同一 OpenAI-compatible endpoint 的三档模型，也支持程序化传入跨 Provider 池；缺失专用路由时记录 `fallback=true`。步骤预算在两个 Runtime 中都强制执行。执行仍保持顺序语义；计划尚未声明依赖关系，因此系统不猜测两个副作用动作可以安全并发。Provider Router 详见 [`04_Provider_Routing.md`](./04_Provider_Routing.md)。

## 7. 验收要求

三模式功能必须由行为测试证明：

1. 三种模式具有不同的询问、步骤、验证和修复预算。
2. 同一失败 workflow 在质量/平衡/效率下分别允许 3/2/1 次尝试。
3. 质量模式生成比效率模式更深的验收证据。
4. 三种模式面对同一治理红线都返回相同拒绝结果。
5. 场景正文与生产 Skill 正文真正进入 Agent 上下文。
6. 非法模式在 API 边界返回 400，而不是运行时崩溃。
7. Web UI 构建产物可用，全量 Python 测试通过。

## 8. 反模式

- 把质量模式实现成更长的 Prompt。
- 把效率模式实现成关闭验证或放宽红线。
- 用更多 Agent 数量代替可检查的验收证据。
- 把 `agent / workflow` 当成质量/效率模式。
- 验证失败后重复同一计划，却称其为 PDCA。
- 只展示模式名称，不记录解析后的真实策略。

太一三模式的核心不是“给用户三个按钮”，而是把用户对确定性、自主性和速度的偏好编译成可审计、不可绕过的运行协议。

## 9. 与 Skill 发布质量的边界

三模式只控制已准入能力的单次执行策略，不能给未验证 Skill 放行。Skill 的声明、发布锁和当前运行时重验见 [`03_Executable_Skill_Quality_Gates.md`](./03_Executable_Skill_Quality_Gates.md)。质量模式与 Skill Gate 是正交且累积的：下层 Skill 不合格，上层模式不能制造虚假的高质量。
