---
name: ops.report
description: Operations reporting and scheduled reports
triggers: [周报, weekly, report, 报表, 报告]
---
# Scenario: ops.report (operations reporting)

## Constraints
1. Data sources are restricted to the approved analytics tables.
2. Publishing a report to an external channel requires human review.
3. Reports must be backed by a query, not fabricated.

## Tool permissions
- ✅ sql:query (allowlisted tables)
- ⚠️ notify:* send — needs human review
- ❌ arbitrary outbound HTTP

## Credentials
- Read-only analytics credentials, isolated per the credential policy.
