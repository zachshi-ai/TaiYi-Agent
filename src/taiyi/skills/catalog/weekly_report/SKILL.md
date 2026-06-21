---
name: weekly_report
category: managed
risk: low
applicability: Operations weekly report scenarios
triggers: [周报, weekly, report]
scenario: ops.report
---
# Weekly Report

## Steps
1. `sql:query` — pull last week's metrics from the approved analytics tables
2. Generate a Markdown report from the result
3. `notify:* send` — deliver to the configured channel (held for review)

## Red lines
- Only query allowlisted tables.
- Outbound delivery requires human review.
- Never fabricate figures not backed by the query.
