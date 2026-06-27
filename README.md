# vibeCoding-notify

[English](./README.en.md) | 简体中文

Codex 到飞书的实时过程通知 Hook。它使用一个纯 Python 脚本接入 Codex hooks，把 Codex 在本地运行时的过程说明、授权请求、最终答复和异常信息发送到飞书自定义机器人。

项目目标是把通知做得可读、可控、低噪音：过程消息单独发送到过程机器人，授权、完成和异常等关键信息保留在主机器人；默认使用飞书卡片展示，并尽量避免泄露完整命令、工具输出或敏感字段。

## 特性

- 纯 Python 标准库实现，不依赖第三方包。
- 支持飞书自定义机器人 `interactive` 卡片，长正文使用折叠面板展示。
- 支持飞书 webhook 签名密钥 `secret` 和关键词校验 `keyword`。
- 支持主 webhook 和过程 webhook 分流。
- 默认全局生效，`allowed_roots: []` 会处理所有 Codex 工作目录。
- 可选限制项目目录，填入 `allowed_roots` 后只处理指定目录下的会话。
- 过程通知从 Codex session 日志读取 assistant 已输出的过程说明。
- 完成通知优先读取最终答复全文。
- 可识别 `stream disconnected before completion`、`error decoding response body` 等 session 错误并发送异常卡片。
- 内置去重、节流、静默窗口和工具白名单，减少刷屏。
- 一键部署到用户级 `~/.codex/hooks.json`，不会写入项目级 Codex 配置。

## 工作方式

部署后，脚本会把以下 Codex hook 写入用户级 `~/.codex/hooks.json`：

| Codex 事件 | 用途 | 是否发送飞书 |
| --- | --- | --- |
| `PreToolUse` | 工具调用前，读取最新过程说明 | 是，发送到 `process_webhook`，未配置时回退到 `webhook` |
| `PermissionRequest` | Codex 请求授权时通知 | 是，发送到 `webhook` |
| `Stop` | 当前回合结束时通知 | 是，发送到 `webhook` |
| `UserPromptSubmit` | 标记用户已回到终端，用于短时间静默 | 否 |

当前默认不发送 `PostToolUse` 的“工具已完成”“工具调用结束”提示，因为这类消息噪音较高，且通常没有真正有价值的过程内容。

## 目录结构

```text
.
├── config/
│   └── feishu.example.json
├── scripts/
│   └── feishu_codex_hook.py
├── tests/
│   └── test_feishu_codex_hook.py
├── .gitignore
└── README.md
```

运行时会按配置自动创建 `logs/` 和 `state/`，这两个目录默认不会提交到 Git。

## 环境要求

- Windows、macOS 或 Linux。
- Python 3.10 或更高版本。
- 已安装并可使用 Codex。
- 一个飞书自定义机器人 webhook。

Windows 推荐使用：

```powershell
py -3 --version
```

## 快速开始

1. 克隆或下载仓库。

```powershell
git clone <your-repo-url>
cd vibeCoding-notify
```

2. 创建本地配置文件。

```powershell
Copy-Item .\config\feishu.example.json .\config\feishu.local.json
```

3. 编辑 `config/feishu.local.json`，至少填入 `webhook`。

```json
{
  "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/your-token",
  "process_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/your-process-token",
  "codex_alias": "Codex",
  "codex_alias_tag_color": "orange",
  "secret": "",
  "keyword": "",
  "enabled_events": [
    "pre_tool_use",
    "permission_request",
    "stop"
  ],
  "allowed_roots": []
}
```

4. 渲染一次测试 payload，确认 JSON 正常。

```powershell
py -3 .\scripts\feishu_codex_hook.py render --event stop --config .\config\feishu.local.json
```

5. 发送一条测试消息到飞书。

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```

6. 部署 hook。

```powershell
py -3 .\scripts\feishu_codex_hook.py deploy --config .\config\feishu.local.json
```

7. 在 Codex 中执行 `/hooks`，信任新增 hook。

## 配置说明

完整配置见 [config/feishu.example.json](./config/feishu.example.json)。

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `webhook` | 示例地址 | 主飞书机器人 webhook，用于授权、完成和异常通知 |
| `process_webhook` | 示例地址 | 过程通知 webhook；留空时使用 `webhook` |
| `codex_alias` | `Codex` | 卡片中显示的 Codex 别名，例如 `4080s codex` |
| `codex_alias_tag_color` | `orange` | 飞书 `text_tag` 标签颜色 |
| `secret` | 空字符串 | 飞书机器人签名密钥，未启用签名时留空 |
| `keyword` | 空字符串 | 飞书机器人关键词校验；配置后会自动注入标题 |
| `enabled_events` | `pre_tool_use`、`permission_request`、`stop` | 允许发送通知的事件 |
| `allowed_roots` | `[]` | 允许通知的工作目录；空数组表示全局生效 |
| `tool_whitelist` | `Bash`、`apply_patch`、`Edit`、`Write`、`web.search*`、`mcp__*` | 允许触发过程通知的工具名 |
| `request_timeout_seconds` | `10` | 发送飞书请求超时时间 |
| `dedupe_window_seconds` | `5` | 相同事件去重窗口 |
| `tool_event_min_interval_seconds` | `3` | 同一 session 工具事件最小间隔 |
| `max_summary_length` | `100` | 摘要最大长度 |
| `send_subagent_events` | `false` | 是否发送子代理事件 |
| `log_path` | `logs/feishu-codex-hook.log` | 本地日志路径 |
| `state_dir` | `state` | 去重、静默和 session 游标状态目录 |

如果你只想让通知在某几个项目里生效，可以这样配置：

```json
{
  "allowed_roots": [
    "D:\\WorkDic\\Program\\project-a",
    "D:\\WorkDic\\Program\\project-b"
  ]
}
```

## 命令说明

### 部署

```powershell
py -3 .\scripts\feishu_codex_hook.py deploy --config .\config\feishu.local.json
```

部署动作：

- 备份现有 `~/.codex/hooks.json`。
- 移除已识别的旧通知 hook，例如 `cc-notify-hooks` 和 `codex_alert_notify.py`。
- 保留其他无关 hooks。
- 写入当前方案管理的 `PreToolUse`、`PermissionRequest`、`Stop` 和 `UserPromptSubmit` hooks。
- 不改写 `allowed_roots`，默认空数组保持全局模式。

### 卸载

```powershell
py -3 .\scripts\feishu_codex_hook.py undeploy
```

卸载只会移除本方案注入的 hook，不会自动恢复旧备份。

### 渲染 payload

```powershell
py -3 .\scripts\feishu_codex_hook.py render --event permission_request --config .\config\feishu.local.json
py -3 .\scripts\feishu_codex_hook.py render --event stop --config .\config\feishu.local.json
```

也可以直接传入模拟事件：

```powershell
py -3 .\scripts\feishu_codex_hook.py render --event stop --config .\config\feishu.local.json --payload-json '{"cwd":"D:\\WorkDic\\Program\\demo","session_id":"demo-session","last_assistant_message":"已完成。"}'
```

### 发送测试消息

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```

## 通知内容策略

过程通知只发送可读摘要，不发送完整工具参数或工具输出。默认会包含：

- Codex 别名
- 项目名
- 事件名
- Session 短 ID
- 模型
- 工作目录
- 时间
- 过程说明或最终答复

敏感字段会被脱敏。如果摘要中检测到 `token`、`secret`、`password`、`authorization` 等字段名，会替换为 `内容已脱敏`。

## 飞书卡片说明

本项目默认使用飞书 `interactive` 卡片，卡片结构包括：

- Header：通知状态标题和颜色。
- 状态信息：Codex 别名、项目、事件、Session、模型、路径和时间。
- 分割线：区分状态信息和正文内容。
- 正文摘要：优先展示最重要的一段内容。
- 折叠面板：正文较长时放入 `collapsible_panel`，避免卡片直接省略长文本。

飞书对机器人消息体有大小限制，本项目会在接近限制时截断可选内容。

## 开发与测试

运行单元测试：

```powershell
py -3 -m unittest discover -s .\tests -p "test_*.py" -v
```

当前测试覆盖：

- 事件名归一化。
- 飞书签名生成。
- 卡片 payload 渲染。
- 长正文折叠面板。
- 过程 webhook 分流。
- 全局 `allowed_roots` 行为。
- 部署时移除旧通知 hook。
- session 日志中的过程、最终答复和异常读取。

## 排障

### 飞书没有收到消息

先确认 webhook 是否能收到测试消息：

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```

再查看本地日志：

```powershell
Get-Content .\logs\feishu-codex-hook.log -Tail 80
```

### 其他 Codex 窗口没有通知

检查 `allowed_roots`。空数组表示全局生效：

```json
"allowed_roots": []
```

如果填了目录，只有这些目录下的 Codex 会话会发送通知。

### 刚输入新消息后过程通知变少

这是静默窗口的预期行为。`UserPromptSubmit` 到来后，同一 session 的过程通知会短时间静默，避免你刚回到终端时继续刷屏。

### 过程通知没有显示工具命令

这是有意设计。脚本默认不发送完整 Bash 命令、完整搜索词、大段工具参数或工具输出，以降低敏感信息泄露风险。

### 卡片文字被截断

飞书机器人消息有大小限制。项目会优先保留摘要和折叠面板正文；如果仍然超限，会继续截断正文。

## 安全边界

- 不会转发模型内部逐字推理内容。
- 不会主动发送完整工具输出。
- 不会主动发送完整 Bash 命令。
- 不会提交 `config/feishu.local.json`、`logs/`、`state/` 等本地敏感或运行时文件。
- webhook、secret 和 token 应只保存在本地配置或安全的密钥管理系统中。

## 发布包

本地 release 包可以从 Git 标签生成，版本号按实际标签替换：

```powershell
New-Item -ItemType Directory -Force releases | Out-Null
git archive --format=zip --output=releases\vibecoding-notify-v1.1.zip --prefix=vibeCoding-notify-v1.1/ v1.1
```

`releases/` 默认被 `.gitignore` 忽略，不会进入版本库。

## 参考文档

- 飞书自定义机器人使用指南：<https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot>
- 飞书卡片常见问题：<https://open.feishu.cn/document/common-capabilities/message-card/message-card>
- 飞书 JSON 2.0 折叠面板：<https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/collapsible-panel>
- Codex Hooks 手册：<https://developers.openai.com/codex/codex-manual>

## License

本项目使用 [GNU General Public License v3.0](./LICENSE)（`GPL-3.0-only`）。

GPL 允许个人和商业场景使用、复制、修改和分发，但如果你分发本项目或基于本项目修改后的版本，需要按 GPLv3 的要求提供相同许可证下的源码和许可证文本。
