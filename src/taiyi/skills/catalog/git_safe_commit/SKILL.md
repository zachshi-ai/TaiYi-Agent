---
name: git_safe_commit
category: managed
risk: medium
applicability: Git commit scenarios
triggers: [commit, git, 提交代码]
scenario: dev.git
---
# Git Safe Commit

## Steps
1. `git status` — inspect changes
2. `git diff --staged --stat` — confirm what is staged
3. `git add -A` — stage all changes
4. `git commit -m "<message>"` — commit

## Red lines
- Never use `-c user.name=` / `-c user.email=` to override the committer.
- Never use `--author=` to change the author.
- Never set `GIT_AUTHOR_*` / `GIT_COMMITTER_*` env vars.
- Always use the real identity from the local `git config`.
- Pushing requires human review.
