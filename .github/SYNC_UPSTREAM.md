# 上游同步 PR

同步目标是 `Milesians/sub2api` 的 `milesians`。定时器只创建 PR，合并和发布由
独立的 **Process upstream PR** 工作流处理：

- **Sync upstream release**：每小时第 17、47 分钟检查 `Wei-Shaw/sub2api` 最新
  正式 GitHub Release。为新版本创建 `upstream-sync`、`upstream-release` 标记的
  PR，正文记录上游地址、版本、提交以及 Release 日志。
- **Sync upstream main**：每周一北京时间 03:23 创建 `upstream-sync`、
  `upstream-main` 标记的 PR。这类 PR 合并后不发布镜像。
- **Process upstream PR**：验证 PR 来源，无冲突且 CI 通过后自动合并。
  Release PR 合并后，在 `milesians` 的合并提交上创建上游同名版本 tag，触发
  现有 Release 工作流。镜像版本沿用上游版本，GitHub Release 正文复制上游日志。

fork 不会直接收到上游 Release 事件，因此使用轮询，调度可能延迟。只有正式
发布的 `vX.Y.Z` 版本才会处理，裸 tag、草稿和预发布不触发发布。每次检查最新
Release，不补发历史版本、不覆盖已有 tag。重复运行复用同一个 PR；手工关闭且
未合并的 PR 不会被重新创建。

Release PR 只引入对应 tag 的代码，不额外同步上游最新 main。已有的 fork 定制
和之前每周合入的代码保留。每个版本附带 `.github/upstream-releases/<tag>.json`
来源记录，保证版本代码即使已被每周同步带入，也有独立 PR 驱动该版本发布。

## 冲突与 Codex

发生冲突时，PR 标记为 `codex-review-required`，停止自动合并，并由官方
`openai/codex-action` 尝试解决一轮。Codex 的修改和检查报告提交到同一个 PR。
不论是否解决成功，最终由用户审查后手工合并；Release PR 手工合并后仍会自动
发布镜像。`codex-attempted` 标记避免定时器重复调用模型。

配置位于仓库 Settings → Secrets and variables → Actions：

| 类型 | 名称 | 用途 |
| --- | --- | --- |
| Variable | `CODEX_BASE_URL` | API Base URL，例如 `https://example.com/v1` |
| Variable | `CODEX_MODEL` | 模型名称 |
| Variable | `CODEX_REASONING_EFFORT` | 模型支持的思考等级 |
| Secret | `CODEX_API_KEY` | 模型 API Key |
| Secret | `UPSTREAM_SYNC_SSH_KEY` | 仅授权此 fork 的可写 Deploy key |

模型任务没有仓库写入凭据；提出的补丁由另一个干净的 job 推送。PR 在模型运行
期间被修改时，工作流不会覆盖后来的提交。

需要再次尝试时，可手动运行 **Codex upstream conflict** 并填写 PR 编号；编号
留空则在临时仓库构造一个小冲突，验证 API、模型、思考等级和修改能力，不创建
PR、不修改项目、不发布镜像。

## 发布与权限

当前 `SIMPLE_RELEASE=true`，发布 `ghcr.io/milesians/sub2api:<版本>` 和 `latest`
的 Linux amd64 镜像。Release 构建来源仍为 `milesians`，所有构建任务共用
prepare 阶段解析出的一个提交。

Actions 已允许创建 PR。工作流用 `GITHUB_TOKEN` 管理 PR，Deploy key 推送分支
和 tag（包括上游对工作流文件的修改）。密钥推送会触发已有 CI 和发布工作流。
发现任务显式调度 PR 处理工作流，避免依赖机器人创建 PR 是否自动触发其他工作流。
处理 PR 的代码总是从可信的 `milesians` 读取，不执行 PR 中的自动化脚本。

两条定时工作流都支持手动运行。公开仓库连续 60 天无活动时，GitHub 可能停用
定时任务，需要在 Actions 页面重新启用。
