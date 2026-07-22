# 外部权威检查器：Git 垂直切片

> 状态：本地 Git、通用远端 ref 与 GitHub 平台账号归属三个只读检查器已进入 sandbox 配置路径；SQL、退款、通知等 Connector 仍待 staging authority。

## 1. 为什么执行器回执不是权威证据

执行器既负责做事，又能输出任意文本。如果验证器把 `[exit 0]`、`commit succeeded` 或工具返回的自然语言直接当成成功证据，执行器仍然在给自己签字。

太一现在区分：

- `deterministic / harness`：检查计划与运行时内部事实；
- `external / workspace`：由执行器之外的只读权威重新观察本地真实状态；
- `external / remote`：通过独立远端查询重新观察交付状态；
- `model_judge / model`：只处理客观工具无法判断的模糊质量。

Evidence 不仅记录 `criterion_id`，还绑定 `authority`、`environment`、检查器配置摘要、Contract 和当前产物摘要。低可信来源不能冒充高可信标准。

## 2. Git Authority

`GitAuthority` 在 Task Contract 准备阶段读取：

- 当前 HEAD（可为空，表示 unborn repository）；
- repository-local `user.name`；
- repository-local `user.email`；
- 仓库路径的不可逆摘要。

这些值进入检查器配置摘要和不可变 Contract。执行结束后，它通过独立、只读的 Git 子进程验证：

1. 对 `git_safe_commit`，`git_external:new_head` 要求 HEAD 存在且不同于合同冻结时的 HEAD；
2. 对 `git_safe_commit`，`git_external:identity_matches_local_config` 检查执行后的新 HEAD；对 `git_push`，它检查合同冻结时准备推送的 SHA。两者都要求 author 与 committer 名称、邮箱匹配冻结时的 repository-local 身份。

Task Contract 在执行前先冻结 `task_type` 以及 push 的 `remote/ref`。因此 push 只要求“获批后实际向冻结目标执行 push”和 HEAD 身份一致，不会因为共享 `dev.git` 场景或 `git_safe_commit` Skill 而错误要求产生新 commit；推错分支也不能通过本地调用验收。

检查器使用参数数组而非 shell，设置超时、关闭终端提示，不修改仓库。它不信任执行器的成功文本。

## 2.1 Git Remote Authority

`GitRemoteAuthority` 只适用于冻结为 `git_push` 的任务。Contract 准备阶段固定：

- 本地 HEAD SHA；
- remote 名称和目标 branch；
- 当时解析出的 remote URL（Contract/API 只保存和暴露摘要）。

人工批准并执行 push 后，检查器不读取执行器回执，而是对冻结 URL 运行只读 `git ls-remote --heads`。只有远端目标 ref 等于冻结 SHA，`git_remote:ref_matches_frozen_head` 才 PASS。执行器改写 remote 配置、只打印成功、推到错误分支或远端未更新都不能通过。

该 Authority 默认关闭，因为它需要网络和远端读取凭据；启用即表示部署方授权这项只读远端观察。命令关闭交互提示、设置超时，不在 Evidence、API 或错误文本中泄露 remote URL。它只接受本地路径以及 `file/git/http/https/ssh` 传输，显式禁止 Git `ext::` helper，并用 `--` 隔离参数边界，避免恶意 remote 配置把验证器变成命令执行器。

`sandbox_exec` 后端按设计禁止网络，因此网络 push 应使用由容器/主机网络策略约束的 `local` 后端，或在部署层提供专用 Connector；不能同时声称内核拒绝网络又要求 push 成功。

## 3. 配置

```yaml
executor: sandbox
sandbox_dir: /path/to/repository
external_git_validation: true
external_git_remote_validation: false  # opt-in remote observation
external_github_validation: false      # opt-in GitHub ref/account observation
github_expected_login: null            # required above, e.g. zachshi-ai
```

本地检查器只在冻结为 `git_safe_commit` 或 `git_push` 的任务中进入 Checklist；远端检查器只进入 `git_push`。其他任务不会因为 sandbox 目录不是 Git 仓库而受到影响。

`GET /v1/config` 返回 `validation_authorities`，但只暴露仓库摘要，不暴露本地路径。Web 配置页显示当前进程实际启用的只读 authority。

### GitHub Platform Authority

通用 Git Remote Authority 只证明 Git ref。GitHub Platform Authority 使用 `gh api`
在执行前冻结 owner/repository、branch、SHA 与 `github_expected_login`，执行后签发两条独立证据：

- `github:ref_matches_frozen_head`：GitHub branch 指向冻结 SHA；
- `github:commit_identity_matches_expected_login`：GitHub 返回的 author.login 与 committer.login 都等于预期账号。

它只接受 `github.com` 的 HTTPS、SSH 或 scp-like remote，不执行 remote URL，也不把 API
错误正文或凭据写入 Evidence。启用时必须显式配置预期账号并安装、登录 GitHub CLI。

## 4. 与三模式的关系

Git 新 HEAD、本地身份、远端 ref、GitHub ref 与平台账号归属都被标记为 critical objective criteria。只要 deployment 配置了相应 Authority，质量、平衡、效率三种模式都会执行；效率模式不能为了更快而退回执行器自报成功。

质量模式仍会增加其他 exhaustive 检查和修复预算，但不会改变这个共同事实下限。

## 5. 已验证的对抗场景

- 正常真实提交：Git Authority 签发两条 external PASS；
- 执行器在计划外临时改写 Git 身份后提交并恢复配置：身份检查 FAIL；
- MockExecutor 声称执行 commit，但仓库 HEAD 没有变化：新 HEAD 检查 FAIL；
- push 经人工审批恢复：本地调用条件不要求新 HEAD，远端 Authority 要求目标 ref 等于冻结 SHA；
- MockExecutor 声称 push 成功但 bare remote 未更新：远端 ref 检查 FAIL；
- push 准备后本地 HEAD 被切换：身份检查仍绑定合同冻结的原始 commit；
- 恶意 `ext::` remote helper：在任何 helper 执行前 FAIL；
- sandbox 配置构建 Gateway：authority 自动接入运行时与 API。

## 6. 当前边界

- GitHub Authority 已证明直接 push 的 SHA 与账号映射，但尚不证明分支保护状态、签名验证、PR 审核或 merge queue 状态。
- 当前 GitHub Authority 只支持 github.com；GitHub Enterprise 需要显式 host allowlist 与独立配置。
- 多进程同时操作同一仓库时，另一个合法提交可能造成归属歧义；生产部署应增加 repository task lock 或提交级 correlation marker。
- Git Authority 是本地工作区证据，不等于 staging/production Connector 证明。
- SQL 查询结果、通知送达、退款交易状态仍需各自系统的只读 API、测试账户和可撤销夹具。

在这些 Connector 接入前，SandboxExecutor 对相应工具返回失败而不是成功；只有显式的 MockExecutor 会产生带 `[mock]` 标签的模拟结果，终态固定为 `SIMULATED` 而不是 `COMPLETED`，API/UI 同时暴露执行环境。
