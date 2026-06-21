# 场景:dev.git (开发者 Git 场景)

## 适用
- 开发者日常 Git 操作

## 约束(必执行)
1. committer 身份必须与 git config 一致
2. 任何覆盖身份的操作 → 红线拒绝
3. `git push` 需要人审
4. 提交信息长度不超过 80 字符

## 工具权限
- ✅ git status / diff / log / add / commit
- ⚠️ git push — 需要人审
- ❌ git filter-branch / filter-repo / rebase --interactive — 红线

## 凭证
- 使用本地 git config,不可注入
