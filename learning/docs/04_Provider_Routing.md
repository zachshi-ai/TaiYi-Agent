# 三模式 Provider Router

## 1. 设计边界

Operating Mode 可以选择“由哪个模型负责推理”，但不能选择“动作是否被允许”。Provider Router 属于调度/运行策略层；其输出仍必须经过完全相同的治理、Skill Gate 和完成判定。

```text
TaskPolicy.model_strategy
        ↓
Provider Router
  strongest_capable → quality route
  adaptive          → balanced route
  fastest_capable   → efficiency route
        ↓
Agent ReAct loop / model-backed Workflow planner
        ↓
Governance permit（不可被路由结果修改）
```

## 2. 配置

同一个 OpenAI-compatible endpoint 可以配置三个模型：

```yaml
provider: openai_compat
base_url: https://example.com/v1
model: default-model
quality_model: strongest-model
balanced_model: null
efficiency_model: fastest-model
```

`null` 或空值表示使用 `model`。每个任务的 `provider_route` 会记录请求策略、Provider、实际配置模型和 `fallback`，所以“平衡模式走了默认模型”是可观察事实，不会被显示成已配置专用路由。

程序化调用可以直接构造 `ProviderRouter`，为三条策略传入不同 Provider，从而跨 endpoint 或厂商路由；YAML v1 先覆盖最常见的同 endpoint 多模型部署。

## 3. 恢复与审计

Agent 任务进入 `NEEDS_REVIEW` 后恢复时，Router 会按冻结的 `TaskPolicy` 重新选择同一策略，并记录 `resumed=true`。Workflow 的每一轮修复规划同样按冻结策略选择 Provider。路由选择进入 `task_start`/`plan_created` 审计和任务返回；模型的最后响应模型也记录为 `last_response_model`。

## 4. Fail-safe 行为

- 未配置专用模型：回退默认 Provider，记录 `fallback=true`；
- 默认 Provider 不可用：沿用现有离线降级，Agent Runtime 不启动，改走 workflow；
- 模型调用失败：任务进入 `FAILED`，不伪造响应；
- 任何模型提出红线动作：治理结果与其他模式完全相同。

## 5. 当前边界

离线 Workflow 继续使用确定性的 Keyword Planner，因此没有模型可路由。Model-backed Workflow 已按单次任务切换 Provider。执行保持顺序语义；在计划显式声明依赖关系之前，不会为了追求速度而猜测两个副作用动作可以并发。
