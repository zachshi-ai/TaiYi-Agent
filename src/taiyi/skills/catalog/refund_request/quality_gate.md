---
admission:
  - Skill includes this quality gate
  - Declares verification cases for the amount threshold
exit_criteria:
  - A refund over the threshold is processed without approval
  - Refund success rate falls below the agreed baseline
verification:
  - id: large_refund_needs_review
    description: A refund over 100 is held for human approval
  - id: small_refund_auto
    description: A refund of 100 or less proceeds without review
side_effects:
  - Issues a refund to a customer (high impact)
upgrade:
  - Changes go through a PR
  - Requires finance + admin approval
---
# Refund Request — Quality Gate

High-risk skill. The amount-threshold verification is mandatory.
