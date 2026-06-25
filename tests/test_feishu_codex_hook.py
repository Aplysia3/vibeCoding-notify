from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "feishu_codex_hook.py"
SPEC = importlib.util.spec_from_file_location("feishu_codex_hook", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FeishuCodexHookTests(unittest.TestCase):
    def create_config(self, root: Path) -> MODULE.HookConfig:
        config_path = root / "config.json"
        config_data = {
            "webhook": "https://example.invalid/hook",
            "secret": "",
            "keyword": "",
            "enabled_events": MODULE.DEFAULT_ENABLED_EVENTS,
            "allowed_roots": [str(root)],
            "tool_whitelist": MODULE.DEFAULT_TOOL_WHITELIST,
            "request_timeout_seconds": 10,
            "dedupe_window_seconds": 5,
            "tool_event_min_interval_seconds": 3,
            "max_summary_length": 100,
            "send_subagent_events": False,
            "log_path": str(root / "logs" / "hook.log"),
            "state_dir": str(root / "state"),
        }
        config_path.write_text(json.dumps(config_data, ensure_ascii=False), encoding="utf-8")
        return MODULE.load_config(config_path)

    def test_normalize_event_name(self) -> None:
        self.assertEqual(MODULE.normalize_event_name("SessionStart"), "session_start")
        self.assertEqual(MODULE.normalize_event_name("PermissionRequest"), "permission_request")
        self.assertEqual(MODULE.normalize_event_name("stop"), "stop")

    def test_sanitize_summary_masks_sensitive_text(self) -> None:
        text = "Authorization: Bearer abcdef"
        self.assertEqual(MODULE.sanitize_summary(text, 100), "内容已脱敏")

    def test_sanitize_summary_repairs_mojibake_newline(self) -> None:
        text = "2" + chr(0x9286) + chr(0x4FD3) + "n项目: vibeCoding-notify"
        self.assertEqual(MODULE.sanitize_summary(text, 100), "2。")

    def test_extract_event_context_for_permission_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root)
            payload = {
                "hook_event_name": "PermissionRequest",
                "cwd": str(root),
                "session_id": "permission-session",
                "model": "gpt-5.4",
                "tool_name": "Bash",
                "prompt": "请求执行 Bash 命令：git push origin main\n\n后续内容不应出现在摘要。",
            }
            context = MODULE.extract_event_context("permission_request", payload, config)
            self.assertEqual(context["title"], "Codex 需要授权")
            self.assertEqual(context["template"], "orange")
            self.assertEqual(context["summary"], "请求执行 Bash 命令：git push origin main")

    def test_extract_event_context_for_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root)
            payload = {
                "hook_event_name": "Stop",
                "cwd": str(root),
                "session_id": "stop-session",
                "model": "gpt-5.4",
                "last_assistant_message": "已完成训练状态检查。\n\n不会发送第二段。",
            }
            context = MODULE.extract_event_context("stop", payload, config)
            self.assertEqual(context["title"], "Codex 任务完成")
            self.assertEqual(context["template"], "green")
            self.assertEqual(context["summary"], "已完成训练状态检查。")

    def test_build_final_payload_prefers_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root)
            context = {
                "event_name": "session_start",
                "event_display_name": "SessionStart",
                "title": "Codex 开始处理",
                "template": "blue",
                "summary": "已开始处理当前请求",
                "project": "demo",
                "cwd": str(root),
                "session_id": "abc123456",
                "session_short": "abc12345",
                "tool_name": "",
                "model": "gpt-5.4",
                "hostname": "host",
                "meta_line": f"gpt-5.4 | {root} | host",
                "timestamp_text": "2026-06-25 21:00:00",
            }
            payload = MODULE.build_final_payload(context, config)
            self.assertEqual(payload["msg_type"], "interactive")
            self.assertEqual(payload["card"]["header"]["title"]["content"], "Codex 开始处理")

    def test_apply_feishu_signature(self) -> None:
        payload = {"msg_type": "text", "content": {"text": "hello"}}
        with mock.patch.object(MODULE.time, "time", return_value=1599360473):
            MODULE.apply_feishu_signature(payload, "demo")
        self.assertEqual(payload["timestamp"], "1599360473")
        self.assertEqual(payload["sign"], "l1N0gAcBjdwBvGm1xMjOF0XSyaLRpR7tuO5dHfhAYc8=")

    def test_run_hook_dedupes_recent_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root)
            payload = {
                "hook_event_name": "SessionStart",
                "cwd": str(root),
                "session_id": "same-session",
                "model": "gpt-5.4",
            }
            with mock.patch.object(MODULE, "send_to_feishu", return_value={"code": 0}) as sender:
                self.assertEqual(MODULE.run_hook("session_start", payload, config), 0)
                self.assertEqual(MODULE.run_hook("session_start", payload, config), 0)
            self.assertEqual(sender.call_count, 1)

    def test_read_stdin_json_prefers_utf8_bytes(self) -> None:
        payload = {"last_assistant_message": "2。", "hook_event_name": "Stop"}

        class FakeStdin:
            def __init__(self, raw: bytes) -> None:
                self.buffer = io.BytesIO(raw)
                self.encoding = "gbk"

            def isatty(self) -> bool:
                return False

            def read(self) -> str:
                raise AssertionError("不应退回到文本 read()")

        fake_stdin = FakeStdin(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        with mock.patch.object(MODULE.sys, "stdin", fake_stdin):
            loaded = MODULE.read_stdin_json()
        self.assertEqual(loaded["last_assistant_message"], "2。")

    def test_deploy_removes_old_notification_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root)
            codex_home = root / ".codex"
            hooks_path = codex_home / "hooks.json"
            hooks_path.parent.mkdir(parents=True, exist_ok=True)
            hooks_data = {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "python old/codex_alert_notify.py"},
                                {"type": "command", "command": "python keep_me.py"},
                            ],
                        }
                    ],
                    "PermissionRequest": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "bash cc-notify-hooks/run.sh"},
                            ],
                        }
                    ],
                }
            }
            hooks_path.write_text(json.dumps(hooks_data, ensure_ascii=False), encoding="utf-8")
            result = MODULE.deploy_hooks(config.config_path, codex_home)
            deployed = json.loads(hooks_path.read_text(encoding="utf-8"))
            stop_groups = deployed["hooks"]["Stop"]
            stop_handlers = [
                handler
                for group in stop_groups
                for handler in group.get("hooks", [])
                if isinstance(handler, dict)
            ]
            self.assertTrue(any("keep_me.py" in handler.get("command", "") for handler in stop_handlers))
            self.assertTrue(any(MODULE.MANAGED_MARKER in handler.get("commandWindows", "") for handler in stop_handlers))
            self.assertTrue(result["backup_path"])


if __name__ == "__main__":
    unittest.main()
