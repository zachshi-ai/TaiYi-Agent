# 不可变验收合同

> 状态：已进入 Agent Runtime、Workflow Runtime、HTTP API、审计日志和 Web UI。

## 1. 修复的问题

旧链路虽然有 `TaskContract` 和 `EvidenceLedger`，但验收标准是在产出完成后由 `_validate()` 选择并写回 Contract。逻辑上等价于：

```text
先做结果 → 看见结果 → 再定义什么叫合格
```

这违反太一“选择而不是生成”的设计原则，也让“任务开始时冻结 Contract”的说法不成立。一个质量系统不能允许生产者在看见答案后移动终点线。

## 2. 新证据链

```text
task type + scenario + resolved policy
                 ↓
ValidationEngine.prepare（执行前只调用一次）
                 ↓
ValidationChecklist
  - 确定性 / 外部 / 模型检查器类型
  - 验证深度
  - 独立 judge 版本
  - checklist_id（SHA-256）
                 ↓
TaskContract（frozen dataclass）
  - 目标、假设、约束
  - task type + 规范化任务参数
  - 完整验收标准
  - contract_id（SHA-256）
                 ↓
task_start 审计 → Plan / Act
                 ↓
ValidationResult.subject_digest
  - 完整工具调用（tool + args）
                 ↓
EvidenceRecord
  - criterion_id
  - checker kind
  - authority + environment + checker configuration digest
  - contract_id
  - subject_digest
  - repair attempt
                 ↓
CompletionController
```

成功验收现在要求同一条证据同时满足：标准 ID 相同、检查器类型相同、Contract 相同、当前产物摘要相同、结果为 `PASS`。旧修复轮次、其他任务或低可信检查器的 PASS 不能替当前产物签字。证据全过后还要检查执行环境：mock 工具动作进入 `SIMULATED`，只有非 mock 执行才可进入 `COMPLETED`。

## 3. 清单如何产生

遵循原设计的“选择，不是生成”：

1. 根据 `(task_type, scenario)` 从受版本控制的检查库选择候选项；
2. 从目标中确定性提取并冻结参数（如 push remote/ref、refund amount）；
3. 用冻结参数实例化检查器描述与配置摘要；
4. 根据 Operating Mode 的验证深度过滤；
5. 根据已解析风险决定是否加入已配置的独立 judge；
6. 在任何模型规划或工具执行前冻结；
7. 后续修复轮次复用同一份清单，不重新选择。

模型会看到 Contract 中的验收标准，但无权修改它。验证器不接受与冻结清单的 task type 或 scenario 不一致的上下文。

验证上下文同时记录 `executed_tools`（兼容与汇总）和结构化 `executed_calls`（tool + args）。例如，合同冻结 `git push origin main` 后，即使执行器成功调用了 `git push origin feature/wrong`，`git_push_executed` 仍会 FAIL；退款金额同理。

## 4. 可观察性

- `task_start` 审计事件保存完整 Contract；
- API 返回 `contract_id`、`checklist_id`、`immutable=true`；
- 每条 Evidence 返回 `contract_id`、`subject_digest`、`authority`、`environment` 和检查器配置摘要；
- Web UI 按“当前 Contract + 当前产物”计算通过数量，不再把多个修复轮次的历史记录混成一个分数；
- Skill Gate Runner v5 使用同一条冻结合同链路，校验冻结参数与完整工具调用，并要求 mock 成功动作明确为 `SIMULATED`。

## 5. 目标覆盖不是基础卫生

验收项分为两类：

- `baseline`：输出非空、没有用拒绝话术冒充结果、执行步骤有可检查输出；
- `objective`：确实发生了 Git commit、报告有数据查询、退款动作有执行证据，或独立 judge 按任务目标完成评审。

质量模式和高风险任务要求至少一个 `objective` 标准。只有 baseline 时，Runtime 在模型规划和工具执行前返回 `CAPABILITY_UNAVAILABLE`。这避免“写了一段非空文字”被包装成准确性认证。

效率模式可以对低风险未知任务采用 baseline-only 路径，但 Contract、API 和 UI 会明确显示 `coverage=baseline_only`。它表示“交付物通过基础卫生检查”，不表示“目标正确性已证明”。

Skill Gate 的 case 声明本身会编译为执行前的 objective criterion；Gate 外层仍独立比较终态、工具、规则和证据，Skill 不能靠这个 criterion 自我放行。

## 6. 当前边界

当前客观检查库仍然很小，只覆盖通用输出、执行证据、Git commit、数据查询和退款动作。不可变合同解决的是“标准何时确定、证据签给谁”，不自动创造缺失的行业检查器。

Git 工作区权威检查已经作为第一条真实 `external` 垂直切片接入，详见 [`06_External_Authority_Checks.md`](./06_External_Authority_Checks.md)。下一阶段仍需把其他 Connector 的权威回执、代码测试/类型检查、数据质量规则和业务状态查询注册成 `external` 检查器。只有这些真实证据接入后，客观质量覆盖率才会从 Harness 正确性走向业务正确性。
