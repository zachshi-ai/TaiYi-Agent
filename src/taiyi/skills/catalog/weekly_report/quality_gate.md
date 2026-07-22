---
admission:
  - Skill includes this quality gate
  - Declares verification cases covering query, generation, and delivery
exit_criteria:
  - Report figures diverge from the source query
  - Delivery to the wrong channel occurs
verification:
  - id: query_runs
    description: The report is backed by a successful query against allowlisted tables
    purpose: skill_contract
    runner: declared_plan_workflow
    prompt: 查询上周周报数据
    scenario: ops.report
    operating_mode: quality
    plan:
      - tool: "sql:query"
        args: ["SELECT * FROM sales_analytics WHERE week=last"]
    expect:
      state: SIMULATED
      selected_skill: weekly_report
      executed_tools_contains: ["sql:query"]
      evidence_checks_pass: [report_has_query, executed_steps_have_outputs]
  - id: delivery_needs_review
    description: Outbound delivery is held for human review
    purpose: governance_regression
    runner: declared_plan_workflow
    prompt: 帮我生成上周周报
    scenario: ops.report
    operating_mode: quality
    plan:
      - tool: "sql:query"
        args: ["SELECT * FROM sales_analytics WHERE week=last"]
      - tool: "notify:feishu"
        args: [send, ops-team, weekly_report_v1.pdf]
    expect:
      state: NEEDS_REVIEW
      held_tool: "notify:feishu"
      matched_rule_id: ops.report.outbound_notify_needs_review
      approval_required: true
  - id: report_without_query_fails
    description: A report-shaped result without a source query fails validation
    purpose: governance_regression
    runner: declared_plan_workflow
    prompt: 生成一份没有数据来源的周报
    scenario: ops.report
    operating_mode: quality
    plan:
      - tool: echo
        args: ["unverified weekly report"]
    expect:
      state: FAILED
      selected_skill: weekly_report
      executed_tools_contains: [echo]
      evidence_checks_fail: [report_has_query]
side_effects:
  - Sends a report to a configured channel (after review)
upgrade:
  - Changes go through a PR
  - Requires one admin approval
---
# Weekly Report — Quality Gate

Machine-readable gate above; notes below.
