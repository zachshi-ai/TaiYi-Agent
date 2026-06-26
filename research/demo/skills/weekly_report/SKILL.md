---
name: weekly_report
category: managed
risk: low
applicability: 运营周报场景
---

# 周报自动生成技能

## 触发条件
- 用户提到 "周报 / weekly / report"
- 场景为 `ops.report`

## 执行步骤
1. 查询 `sales_analytics` 表,过滤 last week
2. 生成 Markdown 周报
3. 转 PDF
4. 推送到飞书群

## 注意事项
- 推送需要场景约束人审(场景: ops.report)
- 失败重试 3 次
- PDF 模板由 `templates/standard.md` 控制
