---
admission:
  - Skill includes this quality gate
  - Declares at least three verification cases (normal / identity override / push)
exit_criteria:
  - A real committer-identity mismatch incident occurs
  - 5-run success rate falls below 95%
  - Three or more substantiated user complaints
verification:
  - id: normal_commit_completes
    description: A clean commit runs and records the local git identity
  - id: identity_override_denied
    description: An attempt to override committer identity is denied with no commit
  - id: push_needs_review
    description: A push is held for human review
side_effects:
  - Creates a commit in the user's repository (expected)
  - Does not affect other branches (confirmed)
upgrade:
  - Changes go through a PR
  - Requires one admin approval
  - Must pass five runs in a staging repo first
---
# Git Safe Commit — Quality Gate

Human notes accompany the machine-readable gate above. The author-matches-local
verification is the founding guarantee of this skill.
