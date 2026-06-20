# 太一 (The One) · Agent 体系架构设计 — 完整交付包

> **版本**:v1.0 / 2026-06-20
> **理论原创 / Design Lead**:zachshi | 扎克的自留地
> **AI 协作**:Mavis(MiniMax) · 豆包(ByteDance) · Claude(Anthropic)
> **打包日期**:见文件名

---

## 🎯 一句话定位

太一 = "万物未分之前的那个统一体"(The One / 普罗提诺·Monad / 中国·太一)
= 生产级 Agent 操作系统,核心是"治理权与调度权物理隔离"

## 📂 包内文件清单(30 个文件,~112KB 压缩)

```
agent-arch/
├── README.md                                     ← 项目入口
│
├── docs/                                         ← 设计文档(理论+架构)
│   ├── 00_Design_Document.md                    ← 设计说明(44KB,核心)
│   └── 01_Feasibility_Report.md                 ← 可行性验证报告
│
├── prd/                                          ← 产品需求
│   └── 00_PRD.md                                 ← PRD(19KB)
│
├── tech/                                         ← 技术架构
│   └── 00_Technical_Architecture.md             ← 技术架构(40KB)
│
├── research/                                     ← 借鉴分析
│   ├── 01_borrowed_patterns.md                  ← OpenClaw/Hermes 模式提炼
│   └── Agent_Architecture/                      ← zachshi-ai 原始仓库(理论来源)
│       ├── README.md
│       ├── README_cn.md
│       ├── en/Agent_Architecture_Thoughts.md   ← 英文原文
│       └── cn/Agent架构思考.md                  ← 中文原文
│
├── demo/                                         ← 最小可行性 Demo
│   ├── src/                                     ← 7 个核心模块(~1100 行 Python)
│   │   ├── memory.py                            ← 5 层记忆
│   │   ├── governance.py                        ← L3.1 治理层(中立裁判)
│   │   ├── scheduler.py                         ← L3.2 调度层(决策者)
│   │   ├── validation.py                        ← L4 输出验证
│   │   ├── llm.py                               ← 模拟 LLM
│   │   ├── runtime.py                           ← PDCA 主循环
│   │   ├── value_stream.py                      ← H4 价值流层(Phase 1 雏形)
│   │   └── main.py                              ← 入口 + 9 个测试用例
│   ├── skills/                                  ← 3 个 Skill(含 quality_gate.md)
│   │   ├── git_safe_commit/
│   │   ├── weekly_report/
│   │   └── refund_request/
│   ├── scenarios/                               ← 3 个场景
│   │   ├── dev.git.md
│   │   ├── ops.report.md
│   │   └── customer_service.refund.md
│   ├── run_demo.sh                              ← 一键运行脚本
│   └── LAST_RUN.log                             ← 最近一次运行日志(9/9 全过)
│
└── assets/
    └── visual-pages/
        └── taiyi-architecture/
            └── index.html                        ← 🌟 交互式架构图(9 章节)
```

## 🚀 5 分钟跑通

```bash
# 1. 解压
unzip taiyi-agent-arch-20260620.zip
cd agent-arch/demo

# 2. 跑 demo(约 2 秒,8 个核心测试 + 1 个价值流测试)
python3 src/main.py

# 3. 在浏览器看架构图
open ../assets/visual-pages/taiyi-architecture/index.html
```

## 📖 阅读顺序建议

**架构师视角**:
1. `docs/00_Design_Document.md`(理论 + 架构)
2. `tech/00_Technical_Architecture.md`(实现细节)
3. `assets/visual-pages/taiyi-architecture/index.html`(可视化)

**产品视角**:
1. `docs/00_Design_Document.md` §1-2(为什么做)
2. `prd/00_PRD.md`(做什么、什么时候做)
3. `assets/visual-pages/taiyi-architecture/index.html`(全貌)

**开发者视角**:
1. `demo/src/runtime.py`(主循环)
2. `demo/src/governance.py`(治理层)
3. `demo/src/value_stream.py`(价值流层)
4. `docs/01_Feasibility_Report.md`(跑通证据)

## 🎯 核心创新(对比 OpenClaw/Hermes/LangGraph)

1. **治理权与调度权物理隔离**(zachshi-ai 铁律)
2. **场景工程独立化**(场景 = 环境变量集)
3. **Skill 质量门禁**(每个 Skill 必带 quality_gate.md)
4. **多 Agent 红线一票否决** + 优先级裁决表
5. **PDCA + OODA 周行不殆**(双循环,太一居中不动,Agent 围之运行)
6. **H4 价值流层**(zachshi 提议:三层目标锰定 + 增值评分)
7. **嵌套成熟度模型**(Task/Skill/Subsystem/System/ValueStream 各自评估 + 累积性约束)

## 📊 当前成熟度状态(2026-06-20)

| 层级 | 当前 | Phase 1 目标 |
|---|---|---|
| Task | L3 ✓ | L3 |
| Skill | L2-L3 | L3-L4 |
| Subsystem | L2 | L3 |
| System | L1 | L2 |
| Value Stream | L1 | L2 |

## 🌟 灵感溯源

- **中文**:"道生一,一生二,二生三,三生万物"《道德经》+ "独立而不改,周行而不殆"《道德经》
- **西方**:Plotinus 的 The One / Monad,《黑客帝国》The One
- **管理学**:PDCA (Deming) + OODA (Boyd)
- **系统工程**:SoS / INCOSE
- **流程**:APQC + 华为 IPD-CMMI + DORA

## 📜 License

由 zachshi 决定(可选 MIT / Apache 2.0 / 商业)
