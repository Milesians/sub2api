# 自动同步上游

`sync-upstream.yml` 每 6 小时将 `Wei-Shaw/sub2api` 的 `main` 合并到
`Milesians/sub2api` 的 `milesians`，北京时间为 03:23、09:23、15:23、21:23。
GitHub 的定时调度可能延迟；也可以在 Actions → Sync upstream → Run workflow
选择 `milesians` 手动执行。工作流必须保留在默认分支 `milesians`。

同步使用普通 Git 合并，保留 fork 自有提交。没有更新时不创建提交；合并冲突
时终止，冲突文件列在运行摘要中，需要人工解决。推送不使用 force，若有人同时
更新目标分支，本次推送可能失败，重新执行即可。

认证使用此仓库专用的可写 Deploy key，私钥保存在 Actions Secret
`UPSTREAM_SYNC_SSH_KEY`，公钥在 Settings → Deploy keys 中管理。
内置 `GITHUB_TOKEN` 只有读取权限；使用部署密钥是为了能够同步上游对
`.github/workflows/` 的修改。密钥推送也会触发仓库原有的 push 工作流。
重新部署本工作流时，需要配置同名 Secret 及对应的可写 Deploy key。

公开仓库连续 60 天没有活动时，GitHub 可能停用定时工作流，需要在 Actions
页面重新启用。
