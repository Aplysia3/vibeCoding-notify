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
    def create_config(self, root: Path, allowed_roots: list[str] | None = None) -> MODULE.HookConfig:
        config_path = root / "config.json"
        if allowed_roots is None:
            allowed_roots = [str(root)]
        config_data = {
            "webhook": "https://example.invalid/hook",
            "process_webhook": "https://example.invalid/process-hook",
            "codex_alias": "user_codex",
            "codex_alias_tag_color": "orange",
            "secret": "",
            "keyword": "",
            "enabled_events": MODULE.DEFAULT_ENABLED_EVENTS,
            "allowed_roots": allowed_roots,
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

    def test_inspect_python_runtime_checks_minimum_version(self) -> None:
        old_runtime = MODULE.inspect_python_runtime((3, 9, 18), "python")
        current_runtime = MODULE.inspect_python_runtime((3, 10, 0), "python")
        self.assertFalse(old_runtime["supported"])
        self.assertTrue(current_runtime["supported"])
        self.assertEqual(current_runtime["minimum"], "3.10")

    def test_complete_config_data_fills_setup_defaults(self) -> None:
        data = MODULE.complete_config_data({"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/token"})
        self.assertEqual(data["webhook"], "https://open.feishu.cn/open-apis/bot/v2/hook/token")
        self.assertEqual(data["codex_alias"], "Codex")
        self.assertEqual(data["codex_alias_tag_color"], "orange")
        self.assertEqual(data["enabled_events"], MODULE.DEFAULT_ENABLED_EVENTS)
        self.assertEqual(data["allowed_roots"], [])
        self.assertEqual(data["tool_whitelist"], MODULE.DEFAULT_TOOL_WHITELIST)

    def test_webhook_helpers_identify_placeholders_and_feishu_urls(self) -> None:
        self.assertTrue(MODULE.is_placeholder_webhook("https://open.feishu.cn/open-apis/bot/v2/hook/your-token"))
        self.assertTrue(MODULE.looks_like_feishu_webhook("https://open.feishu.cn/open-apis/bot/v2/hook/abc"))
        self.assertTrue(MODULE.looks_like_feishu_webhook("https://open.larksuite.com/open-apis/bot/v2/hook/abc"))
        self.assertFalse(MODULE.looks_like_feishu_webhook("https://example.invalid/hook"))

    def test_build_hook_handler_uses_current_python_executable(self) -> None:
        with mock.patch.object(MODULE.sys, "executable", r"C:\Python311\python.exe"):
            handler = MODULE.build_hook_handler(Path("hook.py"), Path("config.json"), "stop")
        self.assertIn(r"C:\Python311\python.exe", handler["commandWindows"])
        self.assertIn("managed-by=vibe_feishu_hook", handler["commandWindows"])

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
            self.assertEqual(context["codex_alias"], "user_codex")
            self.assertEqual(context["codex_alias_tag_color"], "orange")

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
                "event_name": "stop",
                "event_display_name": "Stop",
                "title": "Codex 任务完成",
                "template": "green",
                "summary": "已完成训练状态检查。",
                "body_text": "已完成训练状态检查。\n\n不会发送第二段。",
                "delivery_mode": "card",
                "codex_alias": "user_codex",
                "codex_alias_tag_color": "orange",
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
            self.assertEqual(payload["card"]["header"]["title"]["content"], "Codex 任务完成")
            elements = payload["card"]["body"]["elements"]
            self.assertIn("**Codex：** <text_tag color='orange'>user_codex</text_tag>", elements[0]["content"])
            self.assertIn("**项目：** demo", elements[0]["content"])
            self.assertEqual(elements[1]["tag"], "hr")
            self.assertEqual(elements[2]["content"], "已完成训练状态检查。")

    def test_build_context_from_session_message_uses_text_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root)
            payload = {
                "hook_event_name": "PreToolUse",
                "cwd": str(root),
                "session_id": "commentary-session",
                "model": "gpt-5.4",
                "tool_name": "Bash",
            }
            message = "我先检查仓库状态，确认会提交哪些文件。"
            context = MODULE.build_context_from_session_message("pre_tool_use", payload, config, message, "commentary")
            self.assertEqual(context["delivery_mode"], "card")
            self.assertEqual(context["title"], "Codex 过程更新")
            self.assertEqual(context["summary"], "我先检查仓库状态，确认会提交哪些文件。")

    def test_build_text_payload_omits_footer_metadata(self) -> None:
        context = {
            "title": "Codex 任务完成",
            "summary": "已提交。",
            "body_text": "已提交。\n\n提交信息：新增 Codex 到飞书的实时过程 Hook",
        }
        payload = MODULE.build_text_payload(context)
        self.assertEqual(
            payload["content"]["text"],
            "Codex 任务完成\n\n已提交。\n\n提交信息：新增 Codex 到飞书的实时过程 Hook",
        )

    def test_build_card_payload_uses_collapsible_panel_for_long_body(self) -> None:
        context = {
            "event_name": "stop",
            "event_display_name": "Stop",
            "title": "Codex 任务完成",
            "template": "green",
            "summary": "已提交。",
            "body_text": "已提交。\n\n提交信息：新增 Codex 到飞书的实时过程 Hook",
            "delivery_mode": "card",
            "codex_alias": "user_codex",
            "codex_alias_tag_color": "orange",
            "project": "vibeCoding-notify",
            "cwd": "D:\\WorkDic\\Program\\vibeCoding-notify",
            "session_id": "abc123456",
            "session_short": "abc12345",
            "tool_name": "",
            "model": "gpt-5.4",
            "hostname": "host",
            "meta_line": "",
            "timestamp_text": "2026-06-26 01:00:00",
            "panel_title": "查看完整结果",
        }
        payload = MODULE.build_card_payload(context)
        self.assertEqual(payload["msg_type"], "interactive")
        elements = payload["card"]["body"]["elements"]
        self.assertEqual(elements[0]["tag"], "markdown")
        self.assertIn("**Codex：** <text_tag color='orange'>user_codex</text_tag>", elements[0]["content"])
        self.assertIn("**项目：** vibeCoding-notify", elements[0]["content"])
        self.assertIn("**事件：** Stop", elements[0]["content"])
        self.assertIn("**Session：** abc12345", elements[0]["content"])
        self.assertIn("**模型：** gpt-5.4", elements[0]["content"])
        self.assertIn("**路径：** D:\\\\WorkDic\\\\Program\\\\vibeCoding-notify", elements[0]["content"])
        self.assertIn("**时间：** 2026-06-26 01:00:00", elements[0]["content"])
        self.assertEqual(elements[1]["tag"], "hr")
        self.assertEqual(elements[2]["tag"], "markdown")
        self.assertEqual(elements[2]["content"], "已提交。")
        self.assertEqual(elements[3]["tag"], "collapsible_panel")
        self.assertEqual(elements[3]["header"]["title"]["content"], "查看完整结果")
        self.assertEqual(
            elements[3]["elements"][0]["content"],
            "已提交。\n\n提交信息：新增 Codex 到飞书的实时过程 Hook",
        )

    def test_apply_feishu_signature(self) -> None:
        payload = {"msg_type": "text", "content": {"text": "hello"}}
        with mock.patch.object(MODULE.time, "time", return_value=1599360473):
            MODULE.apply_feishu_signature(payload, "demo")
        self.assertEqual(payload["timestamp"], "1599360473")
        self.assertEqual(payload["sign"], "l1N0gAcBjdwBvGm1xMjOF0XSyaLRpR7tuO5dHfhAYc8=")

    def test_resolve_target_webhook_routes_process_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root)
            process_context = {"event_name": "pre_tool_use"}
            stop_context = {"event_name": "stop"}
            error_context = {"event_name": "session_error"}
            self.assertEqual(
                MODULE.resolve_target_webhook(process_context, config),
                "https://example.invalid/process-hook",
            )
            self.assertEqual(MODULE.resolve_target_webhook(stop_context, config), "https://example.invalid/hook")
            self.assertEqual(MODULE.resolve_target_webhook(error_context, config), "https://example.invalid/hook")

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

    def test_extract_session_text_update_reads_commentary_and_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_root = root / ".codex" / "sessions" / "2026" / "06" / "26"
            session_root.mkdir(parents=True, exist_ok=True)
            session_id = "019efeda-e9e5-7133-91a1-841202651a25"
            session_file = session_root / f"rollout-2026-06-26T00-00-00-{session_id}.jsonl"
            lines = [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "phase": "commentary",
                        "message": "我先检查仓库状态，确认会提交哪些文件。",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": "已提交。\n\n提交哈希：`58af5b3`",
                    },
                },
            ]
            session_file.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in lines), encoding="utf-8")
            state = MODULE.HookState(root / "state")
            try:
                with mock.patch.object(MODULE, "codex_home_dir", return_value=root / ".codex"):
                    result = MODULE.extract_session_text_update(session_id, state)
                self.assertEqual(result["commentary"], "我先检查仓库状态，确认会提交哪些文件。")
                self.assertEqual(result["final_answer"], "已提交。\n\n提交哈希：`58af5b3`")
            finally:
                state.close()

    def test_extract_session_text_update_reads_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_root = root / ".codex" / "sessions" / "2026" / "06" / "26"
            session_root.mkdir(parents=True, exist_ok=True)
            session_id = "019efeda-e9e5-7133-91a1-841202651a25"
            session_file = session_root / f"rollout-2026-06-26T00-00-00-{session_id}.jsonl"
            lines = [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "error",
                        "message": "stream disconnected before completion: Transport error: network error: error decoding response body",
                    },
                },
            ]
            session_file.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in lines), encoding="utf-8")
            state = MODULE.HookState(root / "state")
            try:
                with mock.patch.object(MODULE, "codex_home_dir", return_value=root / ".codex"):
                    result = MODULE.extract_session_text_update(session_id, state)
                self.assertEqual(
                    result["error"],
                    "stream disconnected before completion: Transport error: network error: error decoding response body",
                )
            finally:
                state.close()

    def test_wait_for_final_session_text_reads_latest_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_root = root / ".codex" / "sessions" / "2026" / "06" / "26"
            session_root.mkdir(parents=True, exist_ok=True)
            session_id = "019efeda-e9e5-7133-91a1-841202651a25"
            session_file = session_root / f"rollout-2026-06-26T00-00-00-{session_id}.jsonl"
            session_file.write_text("", encoding="utf-8")
            original_extract_latest_session_text = MODULE.extract_latest_session_text

            def delayed_final(session_id_arg: str) -> dict[str, str]:
                if session_id_arg == session_id and not session_file.read_text(encoding="utf-8"):
                    line = {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "last_agent_message": "已提交。\n\n提交哈希：`58af5b3`",
                        },
                    }
                    session_file.write_text(json.dumps(line, ensure_ascii=False), encoding="utf-8")
                with mock.patch.object(MODULE, "codex_home_dir", return_value=root / ".codex"):
                    return original_extract_latest_session_text(session_id_arg)

            with mock.patch.object(MODULE, "extract_latest_session_text", side_effect=delayed_final):
                final_message = MODULE.wait_for_final_session_text(session_id, 0.5)
            self.assertEqual(final_message, "已提交。\n\n提交哈希：`58af5b3`")

    def test_build_context_from_error_message_marks_error_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root)
            payload = {
                "hook_event_name": "Stop",
                "cwd": str(root),
                "session_id": "error-session",
                "model": "gpt-5.4",
            }
            context = MODULE.build_context_from_session_message(
                "stop",
                payload,
                config,
                "stream disconnected before completion: Transport error: network error: error decoding response body",
                "error",
            )
            self.assertEqual(context["event_name"], "session_error")
            self.assertEqual(context["title"], "Codex 运行异常")
            self.assertEqual(context["template"], "red")
            self.assertEqual(context["panel_title"], "查看异常详情")

    def test_should_process_event_allows_any_cwd_when_allowed_roots_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root, allowed_roots=[])
            state = MODULE.HookState(root / "state")
            try:
                allowed, reason = MODULE.should_process_event(
                    "pre_tool_use",
                    {
                        "hook_event_name": "PreToolUse",
                        "cwd": str(root.parent / "other-project"),
                        "session_id": "global-session",
                        "tool_name": "Bash",
                    },
                    config,
                    state,
                )
            finally:
                state.close()
            self.assertTrue(allowed, reason)

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

    def test_deploy_preserves_global_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.create_config(root, allowed_roots=[])
            codex_home = root / ".codex"
            result = MODULE.deploy_hooks(config.config_path, codex_home)
            saved_config = json.loads(config.config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_config["allowed_roots"], [])
            self.assertTrue(result["hooks_path"])


if __name__ == "__main__":
    unittest.main()
