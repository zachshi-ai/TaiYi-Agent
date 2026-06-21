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
  - id: delivery_needs_review
    description: Outbound delivery is held for human review
side_effects:
  - Sends a report to a configured channel (after review)
upgrade:
  - Changes go through a PR
  - Requires one admin approval
---
# Weekly Report — Quality Gate

Machine-readable gate above; notes below.
