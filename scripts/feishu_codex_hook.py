from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import hmac
import json
import locale
import os
import re
import shlex
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MANAGED_MARKER = "managed-by=vibe_feishu_hook"
DEFAULT_ENABLED_EVENTS = [
    "pre_tool_use",
    "permission_request",
    "stop",
]
DEFAULT_TOOL_WHITELIST = ["Bash", "apply_patch", "Edit", "Write", "web.search*", "mcp__*"]
TEXT_TAG_COLORS = {
    "neutral",
    "blue",
    "turquoise",
    "lime",
    "orange",
    "violet",
    "indigo",
    "wathet",
    "green",
    "yellow",
    "red",
    "purple",
    "carmine",
}
CANONICAL_EVENT_BY_HOOK = {
    "SessionStart": "session_start",
    "SubagentStart": "subagent_start",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "PermissionRequest": "permission_request",
    "Stop": "stop",
    "SubagentStop": "subagent_stop",
    "UserPromptSubmit": "user_prompt_submit",
}
HOOK_EVENT_BY_CANONICAL = {value: key for key, value in CANONICAL_EVENT_BY_HOOK.items()}
DISPLAY_EVENT_NAME = {
    "session_start": "SessionStart",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "permission_request": "PermissionRequest",
    "stop": "Stop",
    "user_prompt_submit": "UserPromptSubmit",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
}
SENSITIVE_WORDS = ("token", "secret", "password", "authorization")
TOOL_EVENT_NAMES = {"pre_tool_use", "post_tool_use"}
HIGH_FREQUENCY_WINDOW_SECONDS = 15
HIGH_FREQUENCY_MAX_EVENTS = 6
QUIET_AFTER_USER_SECONDS = 15
STOP_FINAL_WAIT_SECONDS = 4.0
STOP_FINAL_POLL_INTERVAL_SECONDS = 0.25
MAX_FEISHU_PAYLOAD_BYTES = 20 * 1024
MAX_TEXT_MESSAGE_CHARS = 3500
HOOK_STATUS_MESSAGE = "飞书过程通知"


@dataclass
class HookConfig:
    webhook: str
    process_webhook: str
    codex_alias: str
    codex_alias_tag_color: str
    secret: str
    keyword: str
    enabled_events: list[str]
    allowed_roots: list[Path]
    tool_whitelist: list[str]
    request_timeout_seconds: int
    dedupe_window_seconds: int
    tool_event_min_interval_seconds: int
    max_summary_length: int
    send_subagent_events: bool
    log_path: Path
    state_dir: Path
    config_path: Path


def project_root_from_script() -> Path:
    return Path(__file__).resolve().parent.parent


def default_config_path(repo_root: Path) -> Path:
    return repo_root / "config" / "feishu.local.json"


def normalize_event_name(event_name: str | None) -> str:
    if not event_name:
        return ""
    event_name = event_name.strip()
    if event_name in CANONICAL_EVENT_BY_HOOK:
        return CANONICAL_EVENT_BY_HOOK[event_name]
    return event_name.lower()


def is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def first_non_empty_line(text: str) -> str:
    text = normalize_summary_source(text)
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if stripped:
            return stripped
    return ""


def truncate_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return f"{text[: max_length - 3]}..."


def contains_sensitive_marker(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in SENSITIVE_WORDS)


def sanitize_summary(text: str, max_length: int) -> str:
    line = first_non_empty_line(text)
    if not line:
        return ""
    if contains_sensitive_marker(line):
        return "内容已脱敏"
    return truncate_text(line, max_length)


def normalize_summary_source(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for _ in range(2):
        normalized = repair_gbk_utf8_mojibake(normalized)
        normalized = (
            normalized
            .replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\r", "\n")
            .replace("\\t", "\t")
        )
    return normalized


def repair_gbk_utf8_mojibake(text: str) -> str:
    if not text:
        return ""

    pattern = re.compile(r"[\u3400-\u9fff]{2,8}[nrt]?")

    def replace_match(match: re.Match[str]) -> str:
        segment = match.group(0)
        try:
            repaired = segment.encode("gb18030").decode("utf-8")
        except UnicodeError:
            return segment
        if repaired == segment:
            return segment
        if any(token in repaired for token in ("\\n", "\\r", "\\t", "。", "，", "：", "；", "！", "？")):
            return repaired
        return segment

    return pattern.sub(replace_match, text)


def safe_markdown_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "'")


def normalize_text_tag_color(color: str | None) -> str:
    if not color:
        return "orange"
    normalized = str(color).strip().lower()
    return normalized if normalized in TEXT_TAG_COLORS else "orange"


def build_text_tag(text: str, color: str) -> str:
    safe_text = html.escape(text, quote=False)
    return f"<text_tag color='{normalize_text_tag_color(color)}'>{safe_text}</text_tag>"


def short_session_id(session_id: str) -> str:
    return session_id[:8] if session_id else ""


def get_project_name(cwd: str) -> str:
    if not cwd:
        return "unknown"
    path = Path(cwd)
    return path.name or str(path)


def hostname_short() -> str:
    name = socket.gethostname()
    return name.split(".", 1)[0]


def codex_home_dir() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home).resolve(strict=False)
    return Path.home() / ".codex"


def find_session_file(session_id: str) -> Path | None:
    if not session_id:
        return None
    sessions_root = codex_home_dir() / "sessions"
    if not sessions_root.exists():
        return None
    matches = list(sessions_root.rglob(f"*{session_id}.jsonl"))
    if not matches:
        return None
    matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0]


def iter_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                items.append(data)
    return items


def collect_session_messages(items: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    commentary_messages: list[str] = []
    final_messages: list[str] = []
    error_messages: list[str] = []
    for item in items:
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        content_type = payload.get("type")
        if item.get("type") == "event_msg" and content_type == "agent_message":
            message = payload.get("message")
            phase = payload.get("phase")
            if isinstance(message, str) and message.strip():
                if phase == "commentary":
                    commentary_messages.append(message)
                elif phase == "final_answer":
                    final_messages.append(message)
        if item.get("type") == "event_msg" and content_type == "task_complete":
            message = payload.get("last_agent_message")
            if isinstance(message, str) and message.strip():
                final_messages.append(message)
        if item.get("type") == "event_msg" and content_type == "error":
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                error_messages.append(message)
    return commentary_messages, final_messages, error_messages


def extract_latest_session_text(session_id: str) -> dict[str, str]:
    session_file = find_session_file(session_id)
    if session_file is None:
        return {}
    commentary_messages, final_messages, error_messages = collect_session_messages(iter_jsonl_objects(session_file))
    result: dict[str, str] = {}
    if commentary_messages:
        result["commentary"] = commentary_messages[-1]
    if final_messages:
        result["final_answer"] = final_messages[-1]
    if error_messages:
        result["error"] = error_messages[-1]
    return result


def extract_session_text_update(session_id: str, state: HookState) -> dict[str, str]:
    session_file = find_session_file(session_id)
    if session_file is None:
        return {}
    all_items = iter_jsonl_objects(session_file)
    cursor_key = f"session_cursor:{session_id}"
    last_cursor_raw = state.get_kv(cursor_key)
    try:
        last_cursor = int(last_cursor_raw) if last_cursor_raw else 0
    except ValueError:
        last_cursor = 0
    new_items = all_items[last_cursor:]
    commentary_messages, final_messages, error_messages = collect_session_messages(new_items)
    state.set_kv(cursor_key, str(len(all_items)))
    result: dict[str, str] = {}
    if commentary_messages:
        result["commentary"] = commentary_messages[-1]
    if final_messages:
        result["final_answer"] = final_messages[-1]
    if error_messages:
        result["error"] = error_messages[-1]
    return result


def wait_for_final_session_text(session_id: str, timeout_seconds: float) -> str:
    if not session_id:
        return ""
    deadline = time.time() + timeout_seconds
    while True:
        final_message = extract_latest_session_text(session_id).get("final_answer", "")
        if final_message:
            return final_message
        if time.time() >= deadline:
            return ""
        time.sleep(STOP_FINAL_POLL_INTERVAL_SECONDS)


def wait_for_terminal_session_text(session_id: str, timeout_seconds: float) -> dict[str, str]:
    if not session_id:
        return {}
    deadline = time.time() + timeout_seconds
    while True:
        session_state = extract_latest_session_text(session_id)
        if session_state.get("final_answer") or session_state.get("error"):
            return session_state
        if time.time() >= deadline:
            return session_state
        time.sleep(STOP_FINAL_POLL_INTERVAL_SECONDS)


def load_json_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点必须是对象: {path}")
    return data


def write_json_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_config(path: Path) -> HookConfig:
    raw = load_json_file(path)
    repo_root = project_root_from_script()
    allowed_root_values = raw.get("allowed_roots") or []
    allowed_roots = [Path(value).resolve(strict=False) for value in allowed_root_values]
    log_path = Path(raw.get("log_path") or "logs/feishu-codex-hook.log")
    if not log_path.is_absolute():
        log_path = (repo_root / log_path).resolve(strict=False)
    state_dir = Path(raw.get("state_dir") or "state")
    if not state_dir.is_absolute():
        state_dir = (repo_root / state_dir).resolve(strict=False)
    return HookConfig(
        webhook=str(raw.get("webhook") or "").strip(),
        process_webhook=str(raw.get("process_webhook") or "").strip(),
        codex_alias=str(raw.get("codex_alias") or "Codex").strip() or "Codex",
        codex_alias_tag_color=normalize_text_tag_color(raw.get("codex_alias_tag_color")),
        secret=str(raw.get("secret") or "").strip(),
        keyword=str(raw.get("keyword") or "").strip(),
        enabled_events=[str(item).strip() for item in (raw.get("enabled_events") or DEFAULT_ENABLED_EVENTS)],
        allowed_roots=allowed_roots,
        tool_whitelist=[str(item).strip() for item in (raw.get("tool_whitelist") or DEFAULT_TOOL_WHITELIST)],
        request_timeout_seconds=int(raw.get("request_timeout_seconds") or 10),
        dedupe_window_seconds=int(raw.get("dedupe_window_seconds") or 5),
        tool_event_min_interval_seconds=int(raw.get("tool_event_min_interval_seconds") or 3),
        max_summary_length=int(raw.get("max_summary_length") or 100),
        send_subagent_events=bool(raw.get("send_subagent_events", False)),
        log_path=log_path,
        state_dir=state_dir,
        config_path=path.resolve(strict=False),
    )


def write_log(config: HookConfig | None, message: str) -> None:
    if config is None:
        return
    timestamp = datetime.now().isoformat(timespec="seconds")
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    with config.log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"[{timestamp}] {message}\n")


class HookState:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "state.sqlite3"
        self.connection = sqlite3.connect(str(self.db_path), timeout=5)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT,
              event_name TEXT NOT NULL,
              tool_name TEXT,
              summary TEXT,
              created_at REAL NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at REAL NOT NULL
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def cleanup(self, now: float) -> None:
        expiry = now - 86400
        self.connection.execute("DELETE FROM event_log WHERE created_at < ?", (expiry,))
        self.connection.execute("DELETE FROM kv WHERE updated_at < ?", (expiry,))
        self.connection.commit()

    def get_quiet_until(self, session_id: str) -> float:
        if not session_id:
            return 0.0
        row = self.connection.execute(
            "SELECT value FROM kv WHERE key = ?",
            (f"quiet_until:{session_id}",),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def set_quiet_until(self, session_id: str, quiet_until: float) -> None:
        if not session_id:
            return
        now = time.time()
        self.connection.execute(
            """
            INSERT INTO kv(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (f"quiet_until:{session_id}", str(quiet_until), now),
        )
        self.connection.commit()

    def recently_sent(self, session_id: str, event_name: str, tool_name: str, summary: str, window_seconds: int) -> bool:
        since = time.time() - window_seconds
        row = self.connection.execute(
            """
            SELECT 1
            FROM event_log
            WHERE session_id = ? AND event_name = ? AND IFNULL(tool_name, '') = ? AND IFNULL(summary, '') = ? AND created_at >= ?
            LIMIT 1
            """,
            (session_id, event_name, tool_name, summary, since),
        ).fetchone()
        return row is not None

    def recent_tool_event_count(self, session_id: str, window_seconds: int) -> int:
        since = time.time() - window_seconds
        row = self.connection.execute(
            """
            SELECT COUNT(1)
            FROM event_log
            WHERE session_id = ? AND event_name IN ('pre_tool_use', 'post_tool_use') AND created_at >= ?
            """,
            (session_id, since),
        ).fetchone()
        return int(row[0]) if row else 0

    def last_tool_event_at(self, session_id: str, event_name: str, tool_name: str) -> float:
        row = self.connection.execute(
            """
            SELECT created_at
            FROM event_log
            WHERE session_id = ? AND event_name = ? AND IFNULL(tool_name, '') = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id, event_name, tool_name),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def record_event(self, session_id: str, event_name: str, tool_name: str, summary: str) -> None:
        self.connection.execute(
            """
            INSERT INTO event_log(session_id, event_name, tool_name, summary, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (session_id, event_name, tool_name, summary, time.time()),
        )
        self.connection.commit()

    def get_kv(self, key: str) -> str:
        row = self.connection.execute(
            "SELECT value FROM kv WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row[0]) if row else ""

    def set_kv(self, key: str, value: str) -> None:
        now = time.time()
        self.connection.execute(
            """
            INSERT INTO kv(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        self.connection.commit()


def tool_matches_whitelist(tool_name: str, whitelist: list[str]) -> bool:
    if not tool_name:
        return False
    for pattern in whitelist:
        if not pattern:
            continue
        if pattern.endswith("*") and tool_name.startswith(pattern[:-1]):
            return True
        if tool_name == pattern:
            return True
    return False


def get_payload_value(payload: dict[str, Any], *keys: str) -> str:
    current: Any = payload
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return ""
    return current if isinstance(current, str) else ""


def extract_tool_name(payload: dict[str, Any]) -> str:
    tool_name = payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name.strip():
        return tool_name.strip()
    tool = payload.get("tool")
    if isinstance(tool, str) and tool.strip():
        return tool.strip()
    if isinstance(tool, dict):
        name = tool.get("name")
        if isinstance(name, str):
            return name.strip()
    return ""


def extract_success_flag(payload: dict[str, Any]) -> bool | None:
    for key in ("success", "ok"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, int):
        return exit_code == 0
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return False
    return None


def extract_event_context(event_name: str, payload: dict[str, Any], config: HookConfig) -> dict[str, Any]:
    cwd = get_payload_value(payload, "cwd")
    project = get_project_name(cwd)
    session_id = get_payload_value(payload, "session_id")
    tool_name = extract_tool_name(payload)
    model = get_payload_value(payload, "model")
    host = hostname_short()
    success = extract_success_flag(payload)
    summary = ""
    title = ""
    template = "blue"

    if event_name == "session_start":
        title = "Codex 开始处理"
        summary = "已开始处理当前请求"
    elif event_name == "pre_tool_use":
        title = "Codex 正在执行"
        summary = f"准备调用工具 {tool_name or 'unknown'}"
    elif event_name == "post_tool_use":
        title = "Codex 工具已完成"
        if success is True:
            summary = f"工具 {tool_name or 'unknown'} 调用成功"
        elif success is False:
            summary = f"工具 {tool_name or 'unknown'} 调用失败"
        else:
            summary = f"工具 {tool_name or 'unknown'} 调用结束"
    elif event_name == "permission_request":
        title = "Codex 需要授权"
        template = "orange"
        prompt = get_payload_value(payload, "prompt") or get_payload_value(payload, "message")
        summary = sanitize_summary(prompt, config.max_summary_length) or "Codex 请求执行受限操作"
    elif event_name == "stop":
        title = "Codex 任务完成"
        template = "green"
        message = get_payload_value(payload, "last_assistant_message")
        summary = sanitize_summary(message, config.max_summary_length) or "本回合已完成"
    else:
        title = "Codex 状态更新"
        summary = "有新的运行状态"

    if event_name in {"session_start", "pre_tool_use", "post_tool_use"}:
        summary = sanitize_summary(summary, config.max_summary_length) or "有新的运行状态"

    if config.keyword and config.keyword not in summary and config.keyword not in title:
        summary = f"{config.keyword} {summary}"

    meta_parts = [item for item in [model, cwd, host] if item]
    return {
        "event_name": event_name,
        "event_display_name": DISPLAY_EVENT_NAME.get(event_name, event_name),
        "title": title,
        "template": template,
        "summary": summary,
        "body_text": summary,
        "delivery_mode": "card",
        "codex_alias": config.codex_alias,
        "codex_alias_tag_color": config.codex_alias_tag_color,
        "project": project,
        "cwd": cwd,
        "session_id": session_id,
        "session_short": short_session_id(session_id),
        "tool_name": tool_name,
        "model": model,
        "hostname": host,
        "meta_line": " | ".join(meta_parts),
        "timestamp_text": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_context_from_session_message(
    event_name: str,
    payload: dict[str, Any],
    config: HookConfig,
    message: str,
    phase: str,
) -> dict[str, Any]:
    context = extract_event_context(event_name, payload, config)
    body_text = normalize_summary_source(message).strip()
    summary = sanitize_summary(body_text, config.max_summary_length) or context["summary"]
    context["summary"] = summary
    context["body_text"] = body_text
    context["delivery_mode"] = "card"
    if phase == "commentary":
        context["title"] = "Codex 过程更新"
        context["template"] = "blue"
        context["panel_title"] = "查看完整过程"
    elif phase == "final_answer":
        context["title"] = "Codex 任务完成"
        context["template"] = "green"
        context["panel_title"] = "查看完整结果"
    elif phase == "error":
        context["event_name"] = "session_error"
        context["event_display_name"] = "SessionError"
        context["title"] = "Codex 运行异常"
        context["template"] = "red"
        context["panel_title"] = "查看异常详情"
    if config.keyword and config.keyword not in body_text and config.keyword not in context["title"]:
        context["title"] = f"{config.keyword} {context['title']}"
    return context


def build_text_payload(context: dict[str, Any]) -> dict[str, Any]:
    body_text = truncate_text(
        normalize_summary_source(context.get("body_text") or context["summary"]).strip(),
        MAX_TEXT_MESSAGE_CHARS,
    )
    lines = [context["title"]]
    if body_text:
        lines.extend(["", body_text])
    return {
        "msg_type": "text",
        "content": {
            "text": "\n".join(lines)
        },
    }


def build_card_subtitle(context: dict[str, Any]) -> str:
    parts = []
    if context.get("project"):
        parts.append(str(context["project"]))
    return " · ".join(parts)


def build_card_details(context: dict[str, Any]) -> str:
    lines = []

    def append_detail(label: str, value: Any) -> None:
        if value in (None, ""):
            return
        safe_value = safe_markdown_text(str(value))
        lines.append(f"**{label}：** {safe_value}")

    codex_alias = str(context.get("codex_alias") or "Codex")
    codex_alias_color = normalize_text_tag_color(context.get("codex_alias_tag_color"))
    lines.append(f"**Codex：** {build_text_tag(codex_alias, codex_alias_color)}")
    append_detail("项目", context.get("project"))
    append_detail("事件", context.get("event_display_name"))
    append_detail("Session", context.get("session_short"))
    append_detail("模型", context.get("model"))
    append_detail("路径", context.get("cwd"))
    append_detail("时间", context.get("timestamp_text"))
    return "\n".join(lines)


def build_collapsible_panel(context: dict[str, Any], body_text: str) -> dict[str, Any]:
    return {
        "tag": "collapsible_panel",
        "expanded": False,
        "padding": "8px 8px 8px 8px",
        "margin": "8px 0px 0px 0px",
        "vertical_spacing": "8px",
        "border": {
            "color": "grey",
            "corner_radius": "8px",
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": context.get("panel_title") or "查看完整内容",
            },
            "width": "fill",
            "vertical_align": "center",
            "icon": {
                "tag": "standard_icon",
                "token": "down-small-ccm_outlined",
                "size": "16px 16px",
            },
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "elements": [
            {
                "tag": "markdown",
                "content": body_text,
                "text_align": "left",
                "margin": "0px 0px 0px 0px",
            }
        ],
    }


def build_card_payload(context: dict[str, Any]) -> dict[str, Any]:
    summary_text = normalize_summary_source(context["summary"]).strip()
    body_text = normalize_summary_source(context.get("body_text") or "").strip()
    details_text = build_card_details(context)
    elements = []
    if details_text:
        elements.append(
            {
                "tag": "markdown",
                "content": details_text,
                "text_align": "left",
                "margin": "0px 0px 8px 0px",
            }
        )
        if summary_text or body_text:
            elements.append(
                {
                    "tag": "hr",
                    "margin": "8px 0px 8px 0px",
                }
            )
    if summary_text:
        elements.append(
            {
                "tag": "markdown",
                "content": summary_text,
                "text_align": "left",
                "margin": "0px 0px 8px 0px",
            }
        )
    if body_text and body_text != summary_text:
        elements.append(build_collapsible_panel(context, body_text))
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "update_multi": True,
                "style": {
                    "text_size": {
                        "normal_v2": {
                            "default": "normal",
                            "pc": "normal",
                            "mobile": "normal",
                        }
                    }
                },
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": context["title"],
                },
                "subtitle": {
                    "tag": "plain_text",
                    "content": build_card_subtitle(context),
                },
                "template": context["template"],
                "padding": "12px 12px 12px 12px",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": elements,
            },
        },
    }


def apply_feishu_signature(payload: dict[str, Any], secret: str) -> None:
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    payload["timestamp"] = timestamp
    payload["sign"] = base64.b64encode(digest).decode("utf-8")


def build_final_payload(context: dict[str, Any], config: HookConfig) -> dict[str, Any]:
    payload = build_card_payload(context)
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_FEISHU_PAYLOAD_BYTES:
        context = copy.deepcopy(context)
        original_body = normalize_summary_source(context.get("body_text") or "").strip()
        if original_body:
            suffix = "\n\n（内容过长，已按飞书机器人请求体 20 KB 限制截断）"
            low = 0
            high = len(original_body)
            best_body = ""
            while low <= high:
                mid = (low + high) // 2
                candidate_body = original_body[:mid].rstrip()
                if mid < len(original_body):
                    candidate_body = f"{candidate_body}{suffix}"
                context["body_text"] = candidate_body
                candidate_payload = build_card_payload(context)
                size = len(json.dumps(candidate_payload, ensure_ascii=False).encode("utf-8"))
                if size <= MAX_FEISHU_PAYLOAD_BYTES:
                    best_body = candidate_body
                    low = mid + 1
                else:
                    high = mid - 1
            context["body_text"] = best_body
        payload = build_card_payload(context)
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_FEISHU_PAYLOAD_BYTES:
        context = copy.deepcopy(context)
        context["summary"] = truncate_text(context["summary"], 60)
        context["body_text"] = ""
        payload = build_card_payload(context)
    if config.secret:
        apply_feishu_signature(payload, config.secret)
    return payload


def resolve_target_webhook(context: dict[str, Any], config: HookConfig) -> str:
    if context.get("event_name") == "pre_tool_use" and config.process_webhook:
        return config.process_webhook
    return config.webhook


def send_to_feishu(payload: dict[str, Any], webhook: str, config: HookConfig) -> dict[str, Any]:
    if not webhook:
        raise ValueError("缺少 webhook 配置")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as response:
        raw = response.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw.strip() else {}
    if isinstance(data, dict):
        return data
    raise ValueError("飞书返回了非对象 JSON")


def read_stdin_json() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    content = read_stdin_text()
    if not content.strip():
        return {}
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("stdin JSON 必须是对象")
    return payload


def read_stdin_text() -> str:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        raw = buffer.read()
        if not raw:
            return ""
        encodings = ["utf-8-sig"]
        if getattr(sys.stdin, "encoding", None):
            encodings.append(sys.stdin.encoding)
        preferred = locale.getpreferredencoding(False)
        if preferred:
            encodings.append(preferred)
        tried: set[str] = set()
        for encoding in encodings:
            normalized = (encoding or "").strip().lower()
            if not normalized or normalized in tried:
                continue
            tried.add(normalized)
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    return sys.stdin.read()


def should_process_event(event_name: str, payload: dict[str, Any], config: HookConfig, state: HookState) -> tuple[bool, str]:
    if event_name == "user_prompt_submit":
        return False, "user prompt event only updates quiet state"
    if event_name not in config.enabled_events:
        return False, "event disabled"
    if payload.get("agent_id") and not config.send_subagent_events:
        return False, "skip subagent event"
    if event_name == "stop" and payload.get("stop_hook_active") is True:
        return False, "stop hook active"
    cwd = get_payload_value(payload, "cwd")
    if not cwd:
        return False, "missing cwd"
    cwd_path = Path(cwd).resolve(strict=False)
    if config.allowed_roots and not any(is_path_within(cwd_path, root) for root in config.allowed_roots):
        return False, "cwd not in allowed roots"
    session_id = get_payload_value(payload, "session_id")
    if event_name in {"pre_tool_use"} and state.get_quiet_until(session_id) > time.time():
        return False, "quiet window active"
    if event_name == "post_tool_use":
        return False, "post tool use disabled"
    if event_name in TOOL_EVENT_NAMES:
        tool_name = extract_tool_name(payload)
        if not tool_matches_whitelist(tool_name, config.tool_whitelist):
            return False, "tool not in whitelist"
    return True, ""


def run_hook(event_name: str, payload: dict[str, Any], config: HookConfig) -> int:
    state = HookState(config.state_dir)
    try:
        now = time.time()
        state.cleanup(now)
        session_id = get_payload_value(payload, "session_id")
        if event_name == "user_prompt_submit":
            state.set_quiet_until(session_id, now + QUIET_AFTER_USER_SECONDS)
            write_log(config, f"quiet session set: session={session_id}")
            return 0
        should_process, reason = should_process_event(event_name, payload, config, state)
        if not should_process:
            write_log(config, f"skip event={event_name} reason={reason}")
            return 0
        session_id = get_payload_value(payload, "session_id")
        session_updates = extract_session_text_update(session_id, state)

        if event_name == "pre_tool_use":
            commentary_message = session_updates.get("commentary")
            if not commentary_message:
                write_log(config, f"skip event={event_name} reason=no new commentary session={session_id}")
                return 0
            context = build_context_from_session_message(event_name, payload, config, commentary_message, "commentary")
        elif event_name == "stop":
            terminal_state = session_updates
            if not terminal_state.get("final_answer") and not terminal_state.get("error"):
                terminal_state = wait_for_terminal_session_text(session_id, STOP_FINAL_WAIT_SECONDS)
            final_message = terminal_state.get("final_answer") or session_updates.get("final_answer", "")
            error_message = terminal_state.get("error") or session_updates.get("error", "")
            if final_message:
                context = build_context_from_session_message(event_name, payload, config, final_message, "final_answer")
            elif error_message:
                context = build_context_from_session_message(event_name, payload, config, error_message, "error")
            elif get_payload_value(payload, "last_assistant_message"):
                context = build_context_from_session_message(
                    event_name,
                    payload,
                    config,
                    get_payload_value(payload, "last_assistant_message"),
                    "final_answer",
                )
            else:
                context = extract_event_context(event_name, payload, config)
        else:
            context = extract_event_context(event_name, payload, config)

        if state.recently_sent(
            context["session_id"],
            context["event_name"],
            context["tool_name"],
            context["summary"],
            config.dedupe_window_seconds,
        ):
            write_log(config, f"dedupe skip event={context['event_name']} session={context['session_id']}")
            return 0
        if event_name in TOOL_EVENT_NAMES:
            count = state.recent_tool_event_count(context["session_id"], HIGH_FREQUENCY_WINDOW_SECONDS)
            if count >= HIGH_FREQUENCY_MAX_EVENTS:
                write_log(config, f"high frequency skip event={event_name} session={context['session_id']} count={count}")
                return 0
            last_at = state.last_tool_event_at(context["session_id"], event_name, context["tool_name"])
            if last_at and (time.time() - last_at) < config.tool_event_min_interval_seconds:
                write_log(config, f"tool interval skip event={event_name} session={context['session_id']}")
                return 0
        payload_to_send = build_final_payload(context, config)
        target_webhook = resolve_target_webhook(context, config)
        response = send_to_feishu(payload_to_send, target_webhook, config)
        state.record_event(context["session_id"], context["event_name"], context["tool_name"], context["summary"])
        write_log(
            config,
            "sent "
            f"event={context['event_name']} session={context['session_id']} "
            f"tool={context['tool_name'] or '-'} webhook={'process' if target_webhook == config.process_webhook and config.process_webhook else 'default'} "
            f"response_code={response.get('code')}",
        )
        return 0
    finally:
        state.close()


def create_sample_payload(event_name: str, repo_root: Path) -> dict[str, Any]:
    cwd = str(repo_root)
    if event_name == "session_start":
        return {
            "hook_event_name": "SessionStart",
            "cwd": cwd,
            "session_id": "sample-session-start",
            "model": "gpt-5.4",
        }
    if event_name == "pre_tool_use":
        return {
            "hook_event_name": "PreToolUse",
            "cwd": cwd,
            "session_id": "sample-pre-tool",
            "model": "gpt-5.4",
            "tool_name": "Bash",
        }
    if event_name == "post_tool_use":
        return {
            "hook_event_name": "PostToolUse",
            "cwd": cwd,
            "session_id": "sample-post-tool",
            "model": "gpt-5.4",
            "tool_name": "Bash",
            "success": True,
        }
    if event_name == "permission_request":
        return {
            "hook_event_name": "PermissionRequest",
            "cwd": cwd,
            "session_id": "sample-permission",
            "model": "gpt-5.4",
            "tool_name": "Bash",
            "prompt": "请求执行 Bash 命令：git push origin main\n\n该操作会推送远端分支。",
        }
    if event_name == "stop":
        return {
            "hook_event_name": "Stop",
            "cwd": cwd,
            "session_id": "sample-stop",
            "model": "gpt-5.4",
            "last_assistant_message": "已完成训练状态检查。\n\n后续细节不会进入通知。",
        }
    raise ValueError(f"不支持的示例事件: {event_name}")


def backup_hooks_file(hooks_path: Path) -> Path | None:
    if not hooks_path.exists():
        return None
    backup_path = hooks_path.with_name(
        f"{hooks_path.name}.bak-feishu-codex-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    )
    backup_path.write_text(hooks_path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return backup_path


def load_hooks_document(hooks_path: Path) -> dict[str, Any]:
    if not hooks_path.exists():
        return {"hooks": {}}
    data = load_json_file(hooks_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        data["hooks"] = {}
    return data


def is_old_notification_handler(handler: dict[str, Any]) -> bool:
    text_values = []
    for key in ("command", "commandWindows"):
        value = handler.get(key)
        if isinstance(value, str):
            text_values.append(value)
    joined = " ".join(text_values)
    return any(marker in joined for marker in ("cc-notify-hooks", "codex_alert_notify.py", MANAGED_MARKER))


def prune_handlers(groups: list[Any]) -> list[Any]:
    pruned_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        new_handlers = [handler for handler in handlers if isinstance(handler, dict) and not is_old_notification_handler(handler)]
        if not new_handlers:
            continue
        new_group = copy.deepcopy(group)
        new_group["hooks"] = new_handlers
        pruned_groups.append(new_group)
    return pruned_groups


def build_command_parts(script_path: Path, config_path: Path, event_name: str) -> list[str]:
    return [
        str(script_path),
        "hook",
        "--event",
        event_name,
        "--config",
        str(config_path),
        "--managed-by",
        MANAGED_MARKER,
    ]


def build_hook_handler(script_path: Path, config_path: Path, event_name: str) -> dict[str, Any]:
    posix_parts = ["python3", *build_command_parts(script_path, config_path, event_name)]
    windows_parts = ["py", "-3", *build_command_parts(script_path, config_path, event_name)]
    return {
        "type": "command",
        "command": shlex.join(posix_parts),
        "commandWindows": subprocess.list2cmdline(windows_parts),
        "timeout": 30,
        "statusMessage": HOOK_STATUS_MESSAGE,
    }


def build_hook_groups(script_path: Path, config_path: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "PreToolUse": [
            {
                "matcher": "*",
                "hooks": [build_hook_handler(script_path, config_path, "pre_tool_use")],
            }
        ],
        "PermissionRequest": [
            {
                "matcher": "*",
                "hooks": [build_hook_handler(script_path, config_path, "permission_request")],
            }
        ],
        "Stop": [
            {
                "matcher": "*",
                "hooks": [build_hook_handler(script_path, config_path, "stop")],
            }
        ],
        "UserPromptSubmit": [
            {
                "matcher": "*",
                "hooks": [build_hook_handler(script_path, config_path, "user_prompt_submit")],
            }
        ],
    }


def merge_hook_groups(existing_hooks: dict[str, Any], managed_groups: dict[str, list[dict[str, Any]]]) -> None:
    for event_name, new_groups in managed_groups.items():
        current_groups = existing_hooks.get(event_name, [])
        if not isinstance(current_groups, list):
            current_groups = []
        current_groups = prune_handlers(current_groups)
        existing_hooks[event_name] = [*current_groups, *new_groups]


def deploy_hooks(config_path: Path, codex_home: Path) -> dict[str, Any]:
    script_path = Path(__file__).resolve()
    hooks_path = codex_home / "hooks.json"
    backup_path = backup_hooks_file(hooks_path)
    hooks_document = load_hooks_document(hooks_path)
    existing_hooks = hooks_document.get("hooks")
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}
        hooks_document["hooks"] = existing_hooks
    for event_name, groups in list(existing_hooks.items()):
        if isinstance(groups, list):
            new_groups = prune_handlers(groups)
            if new_groups:
                existing_hooks[event_name] = new_groups
            else:
                existing_hooks.pop(event_name, None)
    merge_hook_groups(existing_hooks, build_hook_groups(script_path, config_path.resolve(strict=False)))
    write_json_file(hooks_path, hooks_document)
    return {
        "hooks_path": str(hooks_path),
        "backup_path": str(backup_path) if backup_path else "",
        "config_path": str(config_path),
    }


def undeploy_hooks(codex_home: Path) -> dict[str, Any]:
    hooks_path = codex_home / "hooks.json"
    if not hooks_path.exists():
        return {"hooks_path": str(hooks_path), "removed": 0}
    hooks_document = load_hooks_document(hooks_path)
    existing_hooks = hooks_document.get("hooks")
    removed = 0
    if isinstance(existing_hooks, dict):
        for event_name, groups in list(existing_hooks.items()):
            if isinstance(groups, list):
                original_count = sum(
                    1
                    for group in groups
                    if isinstance(group, dict)
                    for handler in (group.get("hooks") or [])
                    if isinstance(handler, dict)
                )
                new_groups = prune_handlers(groups)
                new_count = sum(
                    1
                    for group in new_groups
                    if isinstance(group, dict)
                    for handler in (group.get("hooks") or [])
                    if isinstance(handler, dict)
                )
                removed += original_count - new_count
                if new_groups:
                    existing_hooks[event_name] = new_groups
                else:
                    existing_hooks.pop(event_name, None)
    write_json_file(hooks_path, hooks_document)
    return {"hooks_path": str(hooks_path), "removed": removed}


def parse_args() -> argparse.Namespace:
    repo_root = project_root_from_script()
    parser = argparse.ArgumentParser(description="Codex -> 飞书实时过程 Hook")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hook_parser = subparsers.add_parser("hook", help="由 Codex hooks 调用")
    hook_parser.add_argument("--event", required=True, help="规范化事件名")
    hook_parser.add_argument("--config", default=str(default_config_path(repo_root)), help="配置文件路径")
    hook_parser.add_argument("--managed-by", default="", help=argparse.SUPPRESS)

    deploy_parser = subparsers.add_parser("deploy", help="部署到 ~/.codex/hooks.json")
    deploy_parser.add_argument("--config", default=str(default_config_path(repo_root)), help="配置文件路径")
    deploy_parser.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Codex 用户目录")

    undeploy_parser = subparsers.add_parser("undeploy", help="移除当前方案注入的 hook")
    undeploy_parser.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Codex 用户目录")

    render_parser = subparsers.add_parser("render", help="仅渲染飞书 payload")
    render_parser.add_argument("--event", required=True, help="规范化事件名")
    render_parser.add_argument("--config", default=str(default_config_path(repo_root)), help="配置文件路径")
    render_parser.add_argument("--payload-json", default="", help="直接传入事件 JSON")

    test_parser = subparsers.add_parser("test-send", help="发送测试消息到飞书")
    test_parser.add_argument("--event", required=True, help="规范化事件名")
    test_parser.add_argument("--config", default=str(default_config_path(repo_root)), help="配置文件路径")
    test_parser.add_argument("--payload-json", default="", help="直接传入事件 JSON")

    return parser.parse_args()


def load_payload_from_argument_or_stdin(payload_json: str) -> dict[str, Any]:
    if payload_json.strip():
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("payload_json 必须是对象 JSON")
        return payload
    payload = read_stdin_json()
    if payload:
        return payload
    return {}


def ensure_payload(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload:
        return payload
    return create_sample_payload(event_name, project_root_from_script())


def main() -> int:
    args = parse_args()
    if args.command == "deploy":
        result = deploy_hooks(
            config_path=Path(args.config).resolve(strict=False),
            codex_home=Path(args.codex_home).resolve(strict=False),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "undeploy":
        result = undeploy_hooks(Path(args.codex_home).resolve(strict=False))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    event_name = normalize_event_name(args.event)
    if not event_name:
        raise SystemExit("缺少事件名")
    config = load_config(Path(args.config).resolve(strict=False))
    payload = ensure_payload(event_name, load_payload_from_argument_or_stdin(getattr(args, "payload_json", "")))
    if not payload.get("hook_event_name") and event_name in HOOK_EVENT_BY_CANONICAL:
        payload["hook_event_name"] = HOOK_EVENT_BY_CANONICAL[event_name]

    if args.command == "hook":
        return run_hook(event_name, payload, config)

    if event_name == "stop" and get_payload_value(payload, "last_assistant_message"):
        context = build_context_from_session_message(
            event_name,
            payload,
            config,
            get_payload_value(payload, "last_assistant_message"),
            "final_answer",
        )
    else:
        context = extract_event_context(event_name, payload, config)
    final_payload = build_final_payload(context, config)
    if args.command == "render":
        print(json.dumps(final_payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "test-send":
        response = send_to_feishu(final_payload, resolve_target_webhook(context, config), config)
        print(json.dumps({"payload": final_payload, "response": response}, ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"未知命令: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
