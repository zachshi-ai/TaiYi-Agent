---
name: dev.git
description: Developer daily git operations
triggers: [commit, git, push, 提交, 代码]
---
# Scenario: dev.git (developer git operations)

## Constraints
1. The committer identity must match the local git config.
2. Any attempt to override the identity is a red line → denied.
3. `git push` requires human review.
4. Commit messages are capped at 80 characters.

## Tool permissions
- ✅ git status / diff / log / add / commit
- ⚠️ git push — needs human review
- ❌ git filter-branch / filter-repo — red line

## Credentials
- Use the local git config; never inject an identity.
