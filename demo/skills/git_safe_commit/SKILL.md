---
name: git_safe_commit
category: managed
risk: medium
applicability: Git 提交场景
---

# Git 安全提交技能

## 触发条件
- 用户提到 "commit / git / 提交代码"
- 场景为 `dev.git`

## 执行步骤
1. `git status` — 查看变更
2. `git diff --staged --stat` — 确认暂存内容
3. `git add -A` — 暂存所有变更
4. `git commit -m "<message>"` — 提交

## 注意事项(红线)
- **绝不** 使用 `-c user.name=` 或 `-c user.email=` 覆盖 committer
- **绝不** 使用 `--author=` 改变作者
- **绝不** 使用 `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` 环境变量
- 必须使用本地 `git config` 的真实身份
- 推送操作需要人审

## 质量门禁
- 见 `quality_gate.md`
