# 自动同步上游

`sync-upstream.yml` 每 6 小时将 `Wei-Shaw/sub2api` 的 `main` 合并到
`Milesians/sub2api` 的 `milesians`，北京时间为 03:23、09:23、15:23、21:23。
GitHub 的定时调度可能延迟；也可以在 Actions → Sync upstream → Run workflow
选择 `milesians` 手动执行。工作流必须保留在默认分支 `milesians`。

同步使用普通 Git 合并，保留 fork 自有提交。没有更新时不创建提交；合并冲突
时终止，冲突文件列在运行摘要中，需要人工解决。推送不使用 force，若有人同时
更新目标分支，本次推送可能失败，重新执行即可。

同步后还会检查上游最新的正式版本 tag（`v主版本.次版本.修订号`，不含预发布）。
只有版本高于本 fork 已有正式版本、且对应代码已合入 `milesians`，才会在合并后的
fork 提交上创建同名 tag，并与分支一起原子推送。这次 tag 推送触发 Release，
普通分支更新不构建镜像。即使上游 main 没有新提交，也会检查后来补打的 tag。
每次只发布最新版本，不补发历史版本、不覆盖已有 tag。

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
