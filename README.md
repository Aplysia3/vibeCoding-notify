# Codex 到飞书过程通知

这个仓库提供一个纯 Python 的 Codex Hook 方案，把当前仓库里的过程说明、授权请求、最终结果和运行异常发送到飞书自定义机器人。

当前默认行为：

- `PreToolUse`
  - 不再发送“工具调用结束”“工具完成”这类噪音提示
  - 改为读取当前 session 日志里的最新 `commentary` 文本
  - 也就是你在 Codex 里看到的过程说明
- `PermissionRequest`
  - 发送授权请求卡片
- `Stop`
  - 优先读取当前 session 里的最终答复正文
  - 如果 session 里出现 `payload.type = error`，则发送异常卡片

通知形式：

- 默认使用飞书 `interactive` 卡片，`schema = 2.0`
- 卡片正文先显示摘要
- 如果正文较长，则放入 `collapsible_panel` 折叠面板，展开后查看完整内容
- 不再默认附带一长串项目、事件、Session、时间尾巴

说明：

- 不转发 Codex 内部逐字思考内容
- 过程通知以 assistant 实际输出给你的过程文本为准
- 最终结果优先取 session 中的 `final_answer` / `task_complete.last_agent_message`
- 异常提示通过读取 session 中的 `error` 事件补发，因为 Codex hooks 没有独立错误事件

## 文件说明

- [scripts/feishu_codex_hook.py](./scripts/feishu_codex_hook.py)：主脚本，负责 hook、部署、渲染、测试发送
- [config/feishu.example.json](./config/feishu.example.json)：示例配置
- [tests/test_feishu_codex_hook.py](./tests/test_feishu_codex_hook.py)：单元测试
- `config/feishu.local.json`：本机配置，已加入 `.gitignore`

## 飞书侧约束

参考飞书官方文档：

- 自定义机器人请求体大小不能超过 20 KB
- 卡片整体数据不能超过 30 KB
- JSON 2.0 卡片支持 `collapsible_panel` 折叠面板，可用于承载长正文

本仓库实现会优先保留摘要和可展开正文；如果内容仍然超限，会自动截断折叠区内容。

## 本地配置

首次使用时，准备 `config/feishu.local.json`：

```json
{
  "webhook": "你的飞书 webhook",
  "secret": "",
  "keyword": "",
  "enabled_events": [
    "pre_tool_use",
    "permission_request",
    "stop"
  ]
}
```

重要字段：

- `allowed_roots`：允许发送通知的仓库根路径；空数组表示全局生效，填入路径后只处理这些目录下的 Codex 会话
- `tool_whitelist`：哪些工具事件可以触发过程消息抓取
- `state_dir`：去重和静默状态缓存目录
- `log_path`：本地日志

## 部署

在仓库根目录执行：

```powershell
py -3 .\scripts\feishu_codex_hook.py deploy --config .\config\feishu.local.json
```

部署动作：

- 备份 `~/.codex/hooks.json`
- 移除已识别的旧通知 hook
  - `cc-notify-hooks`
  - `codex_alert_notify.py`
- 保留其他无关 hooks
- 写入当前方案的用户级 hooks
- 不改写本地配置中的 `allowed_roots`；默认空数组为全局模式

部署完成后，在 Codex 里执行：

```text
/hooks
```

然后信任新增 hook。

## 卸载

```powershell
py -3 .\scripts\feishu_codex_hook.py undeploy
```

这会移除当前方案注入的 hook，不会自动恢复旧备份。

## 调试

只渲染，不发送：

```powershell
py -3 .\scripts\feishu_codex_hook.py render --event permission_request --config .\config\feishu.local.json
py -3 .\scripts\feishu_codex_hook.py render --event stop --config .\config\feishu.local.json
```

发送测试消息：

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event permission_request --config .\config\feishu.local.json
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```

运行单元测试：

```powershell
py -3 -m unittest discover -s .\tests -p "test_*.py" -v
```

## 当前通知策略

- 不再发送 `PostToolUse` 的“工具已完成”“工具调用结束”提示
- `PreToolUse` 改为发送 session 中最新过程说明
- `Stop` 改为优先发送最终答复全文
- 若出现 `stream disconnected before completion`、`error decoding response body` 这类 session 错误，则发送异常卡片
- 为避免敏感信息泄露，如果检测到 `token`、`secret`、`password`、`authorization` 等字段，摘要会替换为 `内容已脱敏`

## 参考文档

- 飞书自定义机器人使用指南：<https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot>
- 飞书卡片常见问题：<https://open.feishu.cn/document/common-capabilities/message-card/message-card>
- 飞书 JSON 2.0 折叠面板：<https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/collapsible-panel>
- Codex Hooks 手册：<https://developers.openai.com/codex/codex-manual>
