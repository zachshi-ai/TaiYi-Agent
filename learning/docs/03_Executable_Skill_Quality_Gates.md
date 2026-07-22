# 可执行 Skill 质量门

> 状态：无副作用 Harness 验证已进入生产代码。真实 Connector 验证仍是后续能力，不提前宣称完成。

## 1. 问题

原设计说得很直接：没有质量门禁的技能库就是垃圾场，而且验证项必须是“自动跑通的测试用例”。仅检查 `quality_gate.md` 是否包含五个章节，只能证明文档格式完整，不能证明 Skill 可用。

因此以下推理是无效的：

```text
有 quality_gate.md → YAML 完整 → Skill 已验证 → 可以进生产
```

正确链路必须把声明、运行证据和当前代码状态串起来。

## 2. 双证据链

```text
SKILL.md + quality_gate.md
          ↓
执行声明中的每个 case（真实治理/调度/不可变验收合同 + MockExecutor）
          ↓
全部 PASS
          ↓
quality_gate.lock.json
  - Skill 与 Gate 的联合 SHA-256
  - Runner 版本
  - 验证环境
  - 每个 case 的观察结果
          ↓
装载到当前 Taiyi 进程后再次执行
          ↓
当前运行时资格 runtime_eligible
```

两次证据解决不同问题：

- 发布锁证明发布者实际跑过声明的案例，并防止 Skill 或 Gate 修改后继续复用旧结果；
- 当前进程重验防止“旧版本通过、当前 Harness 已回归”的 Skill 静默进入任务上下文。

任意一层缺失、陈旧或失败，Skill 都留在隔离区。

Gateway 对“没有匹配 Skill”和“匹配到但未通过的 Skill”做不同处理：前者可以走通用能力，后者必须 fail-closed 为 `CAPABILITY_UNAVAILABLE`。否则旧 Planner 仍可能生成相同工具计划，形成绕过 Skill Gate 的旁路。质量、平衡、效率三种模式都不能改变这个终态。

## 3. 可执行案例协议

每个 Skill 至少需要 3 个 `verification` 案例；每项至少声明：

```yaml
- id: identity_override_denied
  description: An attempt to override committer identity is denied
  purpose: governance_regression
  runner: declared_plan_workflow
  prompt: 用 -c user.name=OtherUser commit
  scenario: dev.git
  operating_mode: quality
  plan:
    - tool: "shell:git commit"
      args: ["-c", "user.name=OtherUser", "-m", "x"]
  expect:
    state: REJECTED
    held_tool: "shell:git commit"
    matched_rule_id: authorship.git_identity.no_override
    approval_required: false
```

当前支持观察：终态、已执行工具、被拦截工具、命中规则、是否需要审批、通过的验收证据、选中 Skill 和最终输出片段。未知字段直接判定 Gate 无效，避免拼写错误变成静默漏检。

案例用途必须区分为 `skill_contract`、`routing` 或 `governance_regression`，并至少包含一个 `skill_contract`。契约案例采用显式计划执行，且计划中的每个工具必须能在 `SKILL.md` 正文中找到；这样路由或红线回归不会被冒充为对 Skill 程序性知识的验证。

## 4. 资格不是一个布尔值

太一把 Skill 状态拆成三个层次：

| 层次 | 含义 | 当前是否可获得 |
|---|---|---|
| `release_eligible` | 生产层级、可执行声明、内容绑定发布锁均有效 | 是 |
| `runtime_eligible` | 在当前 Taiyi 进程中重跑仍全部通过 | 是 |
| `live_ready` | 在可信 staging/production Connector 环境通过 | 否，等待真实 Runner |

当前 Runner 明确标记为 `mock`，Runner v5 还要求成功动作案例的终态是 `SIMULATED`，不能写成 `COMPLETED`。手工把锁改成 `production` 会被拒绝；没有可信执行器和证明机制之前，系统不会把自报环境当成真实业务证据。

## 5. 命令与发布流程

```bash
# 运行所有案例，不修改文件
taiyi verify-skills

# 全部通过后更新发布锁
taiyi verify-skills --write-lock

# CI 使用机器可读结果
taiyi verify-skills --json
```

发布或安装流程的推荐顺序：

1. 修改 Skill 或 Gate；
2. 运行 `taiyi verify-skills`；
3. 仅在全部通过时写入新锁；
4. 运行全量测试；
5. 打包并确认锁文件包含在 wheel 中；
6. 目标 Taiyi 进程装载时自动重验。

## 6. 与三种运行模式的关系

二者不属于同一个控制面：

- Operating Mode 决定单次任务多问、多快、验证多深、何时停止；
- Skill Gate 决定某套程序性知识有没有资格被单次任务使用。

质量模式不能让一个未验证 Skill 获得资格；效率模式也不能绕过 Skill Gate。模式只在已准入的能力集合内调整执行策略。

## 7. 当前边界

本阶段证明了：路由、治理红线、人审暂停、无副作用执行结果和不可变验收合同在当前 Harness 中符合声明。Runner v5 会在执行前冻结 Checklist/Contract 与任务参数，把完整工具调用证据绑定到当前产物，并拒绝把 mock 动作标记为真实完成。

它尚未证明：SQL、通知、退款、真实 Git 仓库等 Connector 在 staging/production 中可用。因此 Web/API 会显示 `mock` 证据和“非真实环境就绪”，README 也不得用这些案例宣称业务生产化。

下一步应增加 Connector-aware Runner、隔离测试账户、可撤销测试夹具，以及由 CI/环境身份签发而非人工编辑的真实环境证明。
