# Codex → 飞书实时过程 Hook

这个仓库提供一个纯 Python 的 Codex Hook 方案，把当前仓库里的运行状态实时发送到飞书自定义机器人。

支持的默认事件：

- `SessionStart`：开始处理
- `PreToolUse`：准备调用工具
- `PostToolUse`：工具调用完成
- `PermissionRequest`：请求授权
- `Stop`：本回合完成
- `UserPromptSubmit`：仅用于静默后续过程通知，不发消息

说明：

- 不转发 Codex 内部逐字思考内容。
- “过程信息”定义为可观测的工具级过程流。
- 默认只对白名单工具发运行中卡片：`Bash`、`apply_patch`、`Edit`、`Write`、`web.search*`、`mcp__*`
- 默认只对当前仓库生效。

## 文件说明

- [scripts/feishu_codex_hook.py](./scripts/feishu_codex_hook.py)：主脚本，负责 hook、部署、渲染、测试发送
- [config/feishu.example.json](./config/feishu.example.json)：示例配置
- `config/feishu.local.json`：本机配置，已加入 `.gitignore`

## 飞书机器人准备

参考飞书官方文档：

- 自定义机器人使用指南：<https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot>

建议至少配置一种安全策略：

- 自定义关键词
- IP 白名单
- 签名校验

如果启用了：

- 关键词：把关键词填到 `config/feishu.local.json` 的 `keyword`
- 签名：把密钥填到 `config/feishu.local.json` 的 `secret`

## 本地配置

首次使用时，检查 `config/feishu.local.json`：

```json
{
  "webhook": "你的飞书 webhook",
  "secret": "",
  "keyword": "",
  "enabled_events": [
    "session_start",
    "pre_tool_use",
    "post_tool_use",
    "permission_request",
    "stop"
  ]
}
```

重要字段：

- `allowed_roots`：允许发送通知的仓库根路径；部署时会自动把当前仓库根路径写入
- `tool_whitelist`：运行中过程通知的工具白名单
- `state_dir`：去重、静默状态缓存目录
- `log_path`：本地日志

## 一键部署

在仓库根目录执行：

```powershell
py -3 .\scripts\feishu_codex_hook.py deploy --config .\config\feishu.local.json
```

部署动作：

- 备份 `~/.codex/hooks.json`
- 移除已识别的旧通知 hook：
  - `cc-notify-hooks`
  - `codex_alert_notify.py`
- 保留其他无关 hook
- 写入新的用户级 Codex hooks
- 把当前仓库根路径写入本地配置的 `allowed_roots`

部署完成后，在 Codex 里执行：

```text
/hooks
```

然后信任新加入的 hook。

## 卸载

```powershell
py -3 .\scripts\feishu_codex_hook.py undeploy
```

这会移除当前方案写入的 hook，不会自动恢复旧备份。需要恢复时，可手动把备份文件改回 `hooks.json`。

## 调试命令

只渲染，不发送：

```powershell
py -3 .\scripts\feishu_codex_hook.py render --event session_start --config .\config\feishu.local.json
py -3 .\scripts\feishu_codex_hook.py render --event permission_request --config .\config\feishu.local.json
```

发送测试消息：

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event session_start --config .\config\feishu.local.json
py -3 .\scripts\feishu_codex_hook.py test-send --event permission_request --config .\config\feishu.local.json
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```

运行单元测试：

```powershell
py -3 -m unittest discover -s .\tests -p "test_*.py" -v
```

如果 PowerShell 控制台不是 UTF-8，终端里看到的中文可能乱码，但飞书收到的消息仍是正常 UTF-8 内容。

## 通知行为

运行中过程卡片只发送安全摘要，不主动发送：

- 完整 Bash 命令
- 完整搜索词
- 大段工具参数
- 工具输出内容

摘要规则：

- `PermissionRequest`：取 `prompt` 首行
- `Stop`：取 `last_assistant_message` 首行
- 其他过程事件：使用固定短语
- 如果检测到 `token`、`secret`、`password`、`authorization` 等敏感字段，摘要直接替换为 `内容已脱敏`

降噪规则：

- 同一 `session_id + event + tool + summary`，默认 5 秒内只发一次
- 同一工具事件默认最小间隔 3 秒
- 高频工具事件会被压缩，避免刷屏
- `UserPromptSubmit` 后会短暂静默运行中过程通知

## 当前仓库默认命令

部署：

```powershell
py -3 .\scripts\feishu_codex_hook.py deploy --config .\config\feishu.local.json
```

测试发送：

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```
