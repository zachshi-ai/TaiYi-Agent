# 太一 (The One) —— Agent 体系架构设计(完整交付包)

> **代号**:太一(周行不殆——《道德经》"独立而不改,周行而不殆"——Agent 沿 PDCA/OODA 双循环持续运行)
> **定位**:生产级 Agent 操作系统
> **核心创新**:治理权与调度权物理隔离 + 场景工程独立化 + Skill 质量门禁
> **原作者 / Design Lead**:zachshi | 扎克的自留地
> **AI 协作**:Mavis(MiniMax) · 豆包(ByteDance) · Claude(Anthropic)
> **交付日期**:2026-06-20

---

## 快速导航

| 文档 | 路径 | 用途 |
|---|---|---|
| 📘 **设计说明文档** | `docs/00_Design_Document.md` | **从理论到架构**(必读) |
| 📋 **PRD** | `prd/00_PRD.md` | **产品需求**与版本规划 |
| 🏗️ **技术架构文档** | `tech/00_Technical_Architecture.md` | **实现细节**与接口规约 |
| ✅ **可行性验证报告** | `docs/01_Feasibility_Report.md` | **Demo 跑通证据** |
| 🔬 **借鉴分析** | `research/01_borrowed_patterns.md` | OpenClaw/Hermes 模式提炼 |
| 🧪 **Demo 源码** | `demo/src/*.py` | ~1100 行 Python 核心实现 |
| 📚 **原始仓库** | `research/Agent_Architecture/` | zachshi-ai 文章原文 |

---

## 一、太一是什么?

太一是一套**生产级 Agent 体系架构**,适用于确定性生产任务(代码、事务、合规、流程执行)。它不追求"Agent 什么都能做",而追求"在它能做的领域里——边界清晰、风险可控、持续进化"。

### 三句话总结

1. **理论**:"五层骨架 + 周行不殆"——五层架构(输入/能力/调度/验证/迭代)+ PDCA 内部循环 + OODA 外部循环。命名取《道德经》"独立而不改,周行而不殆"——太一居中不动,Agent 围之运行
2. **差异化**:**治理权与调度权物理隔离**(铁律)+ 场景工程独立化 + Skill 质量门禁
3. **价值**:相比 OpenClaw/Hermes,太一在"工程治理"上有本质提升——Demo 验证 zachshi-ai 文章中"OpenClaw 改 committer 身份"的事故,在太一下会被**100% 拦截**

---

## 二、目录结构

```
agent-arch/
├── README.md                                    ← 本文件
│
├── docs/                                        ← 设计文档
│   ├── 00_Design_Document.md                    ← 设计说明(理论+架构)
│   └── 01_Feasibility_Report.md                 ← 可行性验证报告
│
├── prd/                                         ← 产品需求
│   └── 00_PRD.md                                ← PRD(用户/功能/版本)
│
├── tech/                                        ← 技术架构
│   └── 00_Technical_Architecture.md             ← 技术架构(组件/接口/部署)
│
├── research/                                    ← 借鉴分析
│   ├── 01_borrowed_patterns.md                  ← OpenClaw/Hermes 模式提炼
│   └── Agent_Architecture/                      ← zachshi-ai 原始仓库
│       ├── en/Agent_Architecture_Thoughts.md
│       └── cn/Agent架构思考.md
│
├── demo/                                        ← 最小可行性 Demo
│   ├── src/                                     ← 核心代码
│   │   ├── memory.py                            ← 5 层记忆
│   │   ├── governance.py                        ← L3.1 治理层(中立裁判)
│   │   ├── scheduler.py                         ← L3.2 调度层(决策者)
│   │   ├── validation.py                        ← L4 输出验证
│   │   ├── llm.py                               ← 模拟 LLM
│   │   ├── runtime.py                           ← PDCA 主循环
│   │   └── main.py                              ← 入口 + 8 个测试用例
│   ├── skills/                                  ← 3 个 Skill(含 quality_gate.md)
│   │   ├── git_safe_commit/
│   │   ├── weekly_report/
│   │   └── refund_request/
│   └── scenarios/                               ← 3 个场景
│       ├── dev.git.md
│       ├── ops.report.md
│       └── customer_service.refund.md
│
└── assets/                                      ← 资源(架构图等)
```

---

## 三、跑通 Demo

```bash
# 进入 demo 目录
cd demo

# 跑全部测试用例(~2 秒)
python3 src/main.py

# 或者:一键脚本
./run_demo.sh

# 输出包含 8 个测试用例的执行轨迹 + 可行性验证总结
```

**预期输出**(部分):

```
======================================================================
  Test 1: Git 安全提交(正常路径)
======================================================================
state: COMPLETED  ✓ 正常路径完整跑通

======================================================================
  Test 2: Git 覆盖身份(zachshi-ai 文章场景)
======================================================================
state: REJECTED  ✓ 红线拦截
DENY shell:git commit 触发红线规则: git_identity_override

======================================================================
  Test 3: 危险 rm -rf
======================================================================
state: REJECTED  ✓ 红线拦截
DENY shell:rm -rf /

======================================================================
  Test 4: Git Push
======================================================================
state: NEEDS_REVIEW  ✓ 场景约束触发

======================================================================
  Test 5: 周报自动生成
======================================================================
state: NEEDS_REVIEW  ✓ 推送需人审

======================================================================
  Test 6: 大额退款
======================================================================
state: NEEDS_REVIEW  ✓ 场景约束触发
```

---

## 四、核心架构图(简版)

```
┌────────────────────────────────────────────────────────────────┐
│  L5 迭代/优化层 (Loop Engineering / OODA)                      │
├────────────────────────────────────────────────────────────────┤
│  L4 输出/验证层 (Output Validation)                            │
├────────────────────────────────────────────────────────────────┤
│  L3 调度/治理层  ← 治理权强制分离                               │
│  ┌─────────────────┐   ┌─────────────────┐                     │
│  │ 执行治理(裁判)   │   │ 任务调度(决策者) │                     │
│  │ 红线/场景/凭证  │   │ 选模型/工具/技能  │                     │
│  └─────────────────┘   └─────────────────┘                     │
├────────────────────────────────────────────────────────────────┤
│  L2 能力/资源层 (Tool + Skill + Knowledge)                     │
├────────────────────────────────────────────────────────────────┤
│  L1 输入/场景层 (Prompt + Scenario + Knowledge)                │
└────────────────────────────────────────────────────────────────┘
       ↑
   5 层记忆贯穿
   3 条横切关注点:记忆/协议/观测
```

---

## 五、核心创新

1. **治理-调度物理隔离**(P8 铁律)
   - 治理层是独立进程,Demo 中是独立类
   - 调度层无权修改红线规则
   - 即便 LLM 被攻陷,红线仍有效

2. **场景工程独立化**
   - 场景 = 一类约束的集合(行业/角色/任务类型)
   - 与 Prompt 解耦,Markdown 管理
   - Demo 中 3 个场景,差异化触发约束

3. **Skill 附质量门禁**
   - 每个 Skill 必带 `quality_gate.md`
   - 声明:准入/退出/验证/副作用/升级流程
   - 无门禁的 Skill 不能进生产路径

4. **PDCA + OODA 双循环**
   - PDCA:单任务内部循环(Demo 跑通)
   - OODA:跨任务系统级演进(框架已实现,长期效果待 Phase 1)

---

## 六、版本规划

| Phase | 周期 | 关键交付 |
|---|---|---|
| **Phase 0** ✅ | 本次会话 | 理论 + Demo + 可行性验证 |
| Phase 1 | 4 周 | 治理进程独立部署 + 真实 LLM + 5 通道 + 5 内置 Skill |
| Phase 2 | 8 周 | 多 Agent 协作 + Skill 自生成 + 完整五层记忆 |
| Phase 3 | 12 周 | Skill 市场 + 企业级特性 + OpenTelemetry |
| Phase 4 | 长期 | OODA 自动化 + RL 数据飞轮 |

---

## 七、阅读建议

**第一次接触?** 按这个顺序看:
1. `docs/00_Design_Document.md` ← 设计哲学 + 五层架构
2. `docs/01_Feasibility_Report.md` ← Demo 跑通证据
3. 跑一遍 `demo/src/main.py` ← 直觉理解
4. `prd/00_PRD.md` ← 产品全貌
5. `tech/00_Technical_Architecture.md` ← 实现细节

**架构师视角?** 重点看:
- `docs/00_Design_Document.md` 的第 2-4 章(理论框架)
- `tech/00_Technical_Architecture.md` 的第 2-3 章(组件+运行时)

**开发者视角?** 重点看:
- `demo/src/` 全部代码(~500 行)
- `tech/00_Technical_Architecture.md` 的第 5 章(技术选型+代码组织)

**产品/业务视角?** 重点看:
- `docs/00_Design_Document.md` 的第 8 章(风险与边界)
- `prd/00_PRD.md` 的第 1、6、8 章(用户/成功指标/竞品)

---

## 八、致谢

- **zachshi-ai** —— 提供了"五层架构"和"治理-调度分离"的理论骨架,以及那个"OpenClaw 改 committer"的真实事故
- **OpenClaw** —— Gateway + 多通道 + Markdown 记忆的工程参考
- **Hermes Agent (NousResearch)** —— 5 层记忆 + Honcho 辩证用户建模 + 闭环学习
- **系统科学(SoS / INCOSE)与管理学(PDCA / OODA)** —— 理论框架

---

## 九、License & Author

- **Project Owner & 原作者**:zachshi | 扎克的自留地(zachshi-ai 原始作者)
- **AI 协作(文案 / 代码 / 架构图 / 借鉴梳理)**:Mavis(MiniMax) · 豆包(ByteDance) · Claude(Anthropic)
- **借鉴工程(只参考实现,不参考理论)**:OpenClaw Team / NousResearch(Hermes)
- **理论基础**:完全源于 zachshi 自己的工程实践和设计思考
- **License**: 由 zachshi 决定(可选 MIT / Apache 2.0 / 商业)

---

> **走得远的,永远是走得稳的。**
