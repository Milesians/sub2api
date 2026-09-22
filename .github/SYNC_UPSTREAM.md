# 自动同步上游

同步目标都是 `Milesians/sub2api` 的 `milesians`，分为两条独立流程：

- **Sync upstream release**（`sync-upstream-release.yml`）：每小时的第 17、47 分钟
  查询 `Wei-Shaw/sub2api` 最新的正式 GitHub Release。上游发布新版本后，直接
  合并该 Release 的 tag，再创建指向 fork 合并结果的同名版本 tag，触发镜像发布。
- **Sync upstream main**（`sync-upstream.yml`）：每周一北京时间 03:23 合并上游
  `main`，不创建版本 tag，也不触发镜像发布。

fork 无法直接接收上游仓库的 Release 事件，因此使用每 30 分钟轮询；GitHub
的定时调度可能延迟。两条流程共用并发锁，避免同时更新分支。也可以在 Actions
中选择对应工作流，使用 Run workflow 在 `milesians` 上手动执行。
工作流必须保留在默认分支 `milesians`。

同步使用普通 Git 合并，保留 fork 自有提交。没有更新时不创建提交；合并冲突
时终止，冲突文件列在运行摘要中，需要人工解决。推送不使用 force，若有人同时
更新目标分支，本次推送可能失败，重新执行即可。

Release 同步以 GitHub 已发布的正式 Release 为准，仅有 tag、草稿或预发布均
不会触发。版本格式为 `v主版本.次版本.修订号`，只有版本高于本 fork 已有正式
版本才会处理；每次只处理上游标记为 latest 的正式 Release，不补发历史版本、
不覆盖已有 tag。分支与版本 tag 一起原子推送，推送失败时不会只发布一半。

Release 同步不拉取上游最新 main，即使 Release 来自独立发布分支，也只合并
其 tag 对应的提交。`milesians` 已有的自定义修改及之前每周同步的代码会保留。
每周同步已包含新 Release 的代码时，随后仍会为该 Release 创建版本 tag 并构建。

本 fork 的 Release 固定读取 `milesians`，在 prepare 阶段锁定一个提交供所有
构建任务使用；tag 用于版本命名，不能切换构建源码到上游或旧分支。
当前 `SIMPLE_RELEASE=true`，因此自动发布 GHCR 的 Linux amd64 镜像，包含版本
标签和 `latest`。首次启用时已有的 `v0.2.7` 不会重新发布。

认证使用此仓库专用的可写 Deploy key，私钥保存在 Actions Secret
`UPSTREAM_SYNC_SSH_KEY`，公钥在 Settings → Deploy keys 中管理。
内置 `GITHUB_TOKEN` 只有读取权限；使用部署密钥是为了能够同步上游对
`.github/workflows/` 的修改。密钥推送也会触发仓库原有的 push 工作流。
重新部署本工作流时，需要配置同名 Secret 及对应的可写 Deploy key。

公开仓库连续 60 天没有活动时，GitHub 可能停用定时工作流，需要在 Actions
页面重新启用。
