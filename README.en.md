# vibeCoding-notify

English | [Simplified Chinese](./README.md)

Real-time Codex hook notifications for Feishu custom bots. This project provides a single pure-Python script that connects to Codex hooks and sends local Codex progress updates, permission requests, final answers, and error alerts to Feishu.

The goal is to keep notifications readable, controlled, and low-noise. Progress updates can be routed to a dedicated process bot, while permission requests, final results, and errors stay on the main bot. Messages are sent as Feishu cards by default, with care taken to avoid exposing full commands, tool outputs, or sensitive fields.

## Features

- Pure Python standard library implementation with no third-party dependencies.
- Feishu custom bot `interactive` cards with collapsible panels for long content.
- Optional Feishu webhook signature `secret` and keyword validation `keyword`.
- Main webhook and process webhook routing.
- Global mode by default: `allowed_roots: []` handles all Codex working directories.
- Optional project-level filtering through `allowed_roots`.
- Progress updates are read from assistant messages already written to the Codex session log.
- Final notifications prefer the full final answer from the session log.
- Detects session errors such as `stream disconnected before completion` and `error decoding response body`.
- Built-in deduplication, throttling, quiet windows, and tool whitelisting.
- One-command deployment to the user-level `~/.codex/hooks.json`; no project-level Codex config is written.

## How It Works

After deployment, the script writes these Codex hooks to the user-level `~/.codex/hooks.json`:

| Codex event | Purpose | Sends Feishu message |
| --- | --- | --- |
| `PreToolUse` | Reads the latest progress message before a tool call | Yes, to `process_webhook`, or `webhook` when no process webhook is configured |
| `PermissionRequest` | Notifies when Codex asks for approval | Yes, to `webhook` |
| `Stop` | Notifies when the current turn finishes | Yes, to `webhook` |
| `UserPromptSubmit` | Marks that the user returned to the terminal, used for a short quiet window | No |

`PostToolUse` messages such as "tool completed" are intentionally not sent by default because they are noisy and usually do not contain useful process context.

## Repository Layout

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

The runtime `logs/` and `state/` directories are created automatically when needed and are ignored by Git.

## Requirements

- Windows, macOS, or Linux.
- Python 3.10 or later.
- Codex installed and available.
- A Feishu custom bot webhook.

On Windows, verify Python with:

```powershell
py -3 --version
```

## Quick Start

1. Clone or download the repository.

```powershell
git clone https://github.com/Aplysia3/vibeCoding-notify.git
cd vibeCoding-notify
```

2. Create a local config file.

```powershell
Copy-Item .\config\feishu.example.json .\config\feishu.local.json
```

3. Edit `config/feishu.local.json` and set at least `webhook`.

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

4. Render a test payload to verify the JSON output.

```powershell
py -3 .\scripts\feishu_codex_hook.py render --event stop --config .\config\feishu.local.json
```

5. Send a test message to Feishu.

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```

6. Deploy the hooks.

```powershell
py -3 .\scripts\feishu_codex_hook.py deploy --config .\config\feishu.local.json
```

7. Run `/hooks` in Codex and trust the newly added hook.

## Configuration

See [config/feishu.example.json](./config/feishu.example.json) for the full configuration.

| Field | Default | Description |
| --- | --- | --- |
| `webhook` | Example URL | Main Feishu bot webhook for permission, final, and error notifications |
| `process_webhook` | Example URL | Process notification webhook; falls back to `webhook` when empty |
| `codex_alias` | `Codex` | Codex label displayed in cards, for example `4080s codex` |
| `codex_alias_tag_color` | `orange` | Feishu `text_tag` color |
| `secret` | Empty string | Feishu bot signature secret; leave empty when signature verification is disabled |
| `keyword` | Empty string | Feishu bot keyword validation; configured keywords are injected into card titles |
| `enabled_events` | `pre_tool_use`, `permission_request`, `stop` | Events allowed to send notifications |
| `allowed_roots` | `[]` | Allowed working directories; an empty array means global mode |
| `tool_whitelist` | `Bash`, `apply_patch`, `Edit`, `Write`, `web.search*`, `mcp__*` | Tool names allowed to trigger process notifications |
| `request_timeout_seconds` | `10` | Feishu request timeout |
| `dedupe_window_seconds` | `5` | Deduplication window for identical events |
| `tool_event_min_interval_seconds` | `3` | Minimum interval for tool events in the same session |
| `max_summary_length` | `100` | Maximum summary length |
| `send_subagent_events` | `false` | Whether to send subagent events |
| `log_path` | `logs/feishu-codex-hook.log` | Local log path |
| `state_dir` | `state` | State directory for deduplication, quiet windows, and session cursors |

To restrict notifications to specific projects:

```json
{
  "allowed_roots": [
    "D:\\WorkDic\\Program\\project-a",
    "D:\\WorkDic\\Program\\project-b"
  ]
}
```

## Commands

### Deploy

```powershell
py -3 .\scripts\feishu_codex_hook.py deploy --config .\config\feishu.local.json
```

Deployment will:

- Back up the existing `~/.codex/hooks.json`.
- Remove recognized old notification hooks, such as `cc-notify-hooks` and `codex_alert_notify.py`.
- Keep unrelated hooks.
- Write the managed `PreToolUse`, `PermissionRequest`, `Stop`, and `UserPromptSubmit` hooks.
- Keep `allowed_roots` unchanged; an empty array remains global mode.

### Undeploy

```powershell
py -3 .\scripts\feishu_codex_hook.py undeploy
```

This removes only the hooks managed by this project. It does not automatically restore old backups.

### Render Payload

```powershell
py -3 .\scripts\feishu_codex_hook.py render --event permission_request --config .\config\feishu.local.json
py -3 .\scripts\feishu_codex_hook.py render --event stop --config .\config\feishu.local.json
```

You can also pass a simulated event directly:

```powershell
py -3 .\scripts\feishu_codex_hook.py render --event stop --config .\config\feishu.local.json --payload-json '{"cwd":"D:\\WorkDic\\Program\\demo","session_id":"demo-session","last_assistant_message":"Done."}'
```

### Send Test Message

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```

## Notification Content Strategy

Process notifications send readable summaries rather than full tool parameters or tool outputs. Cards include:

- Codex alias
- Project name
- Event name
- Short session ID
- Model
- Working directory
- Time
- Progress message or final answer

Sensitive summaries are redacted. If a summary contains field names such as `token`, `secret`, `password`, or `authorization`, it is replaced with `内容已脱敏`.

## Feishu Card Format

The project uses Feishu `interactive` cards by default. A card contains:

- Header: notification title and color.
- Status details: Codex alias, project, event, session, model, path, and time.
- Divider: separates status details from message content.
- Summary: the most important line.
- Collapsible panel: long content is placed in `collapsible_panel` to reduce text omission in cards.

Feishu custom bot messages have size limits. This project truncates optional content when the payload approaches the limit.

## Development and Testing

Run unit tests:

```powershell
py -3 -m unittest discover -s .\tests -p "test_*.py" -v
```

Current tests cover:

- Event name normalization.
- Feishu signature generation.
- Card payload rendering.
- Collapsible panel rendering for long content.
- Process webhook routing.
- Global `allowed_roots` behavior.
- Deployment cleanup for old notification hooks.
- Session log reading for progress, final answers, and errors.

## Troubleshooting

### Feishu Does Not Receive Messages

First check whether the webhook can receive a test message:

```powershell
py -3 .\scripts\feishu_codex_hook.py test-send --event stop --config .\config\feishu.local.json
```

Then inspect the local log:

```powershell
Get-Content .\logs\feishu-codex-hook.log -Tail 80
```

### Other Codex Windows Do Not Send Notifications

Check `allowed_roots`. An empty array means global mode:

```json
"allowed_roots": []
```

If directories are configured, only Codex sessions under those directories will send notifications.

### Fewer Progress Notifications After Submitting a New Prompt

This is expected. After `UserPromptSubmit`, the same session enters a short quiet window to avoid message bursts while you are back in the terminal.

### Tool Commands Are Not Shown

This is intentional. The script does not send full Bash commands, full search queries, large tool arguments, or tool outputs by default, reducing the chance of exposing sensitive information.

### Card Text Is Truncated

Feishu bot messages have size limits. The project prioritizes summaries and collapsible body content; if the payload is still too large, body content is truncated.

## Security Notes

- The script does not forward private model reasoning.
- It does not intentionally send full tool outputs.
- It does not intentionally send full Bash commands.
- `config/feishu.local.json`, `logs/`, and `state/` are ignored by Git.
- Webhooks, secrets, and tokens should only be stored in local config files or a secure secret manager.

## Release Package

You can build a local release archive from a Git tag. Replace the version with the actual tag:

```powershell
New-Item -ItemType Directory -Force releases | Out-Null
git archive --format=zip --output=releases\vibecoding-notify-v1.1.zip --prefix=vibeCoding-notify-v1.1/ v1.1
```

`releases/` is ignored by Git.

## References

- Feishu custom bot guide: <https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot>
- Feishu message card FAQ: <https://open.feishu.cn/document/common-capabilities/message-card/message-card>
- Feishu JSON 2.0 collapsible panel: <https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/collapsible-panel>
- Codex Hooks manual: <https://developers.openai.com/codex/codex-manual>

## License

This project is licensed under the [GNU General Public License v3.0](./LICENSE) (`GPL-3.0-only`).

GPL allows personal and commercial use, copying, modification, and distribution. If you distribute this project or a modified version based on it, you must provide the corresponding source code and license text under the GPLv3 terms.
