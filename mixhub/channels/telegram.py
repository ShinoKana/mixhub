"""Telegram channel: one-way notifications to a chat (or a forum topic) through the Bot API.

`sendMessage` for text, `sendDocument` for an attached file. When the user configures an external sender command
(`tgnotify_bin`, called as `<bin> -t <topic> [--alert] [--file F] <text>`), messages go through it instead.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

from mixhub.core.env import env
from mixhub.channels.config import (
    _coerce_topic_map,
    load_channel_section,
    load_hub_preferences,
    resolve_channel_config_path,
    resolve_tg_topics,
    resolve_tgnotify_bin,
    resolve_topic_routing_rules,
)
from mixhub.channels.text import sanitize_plain_text
from mixhub.channels.base import RemoteChannel


class TelegramChannel(RemoteChannel):
    """Telegram notifications, routed to a forum topic by explicit name or by the user's keyword rules."""

    name = "telegram"

    def __init__(
        self,
        config_path: str | None = None,
        tgnotify_bin: str | None = None,
        runner=None,
        preferences_path: str | None = None,
    ):
        self.config_path = config_path or resolve_channel_config_path("telegram")
        self.preferences_path = preferences_path
        self.tgnotify_bin = tgnotify_bin or resolve_tgnotify_bin(preferences_path, self.load_config())
        self.runner = runner

    def bot_token(self) -> str:
        """`bot_token`, else `$TELEGRAM_BOT_TOKEN`, else the token in the chosen bot's `env_file`; empty when none."""
        cfg = self.load_config()
        token = str(cfg.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            bots = cfg.get("bots") if isinstance(cfg.get("bots"), dict) else {}
            default_bot = (
                cfg.get("default_bot")
                or os.environ.get("MIXHUB_DEFAULT_BOT")
                or load_hub_preferences(self.preferences_path).get("default_bot")
                or (next(iter(bots)) if len(bots) == 1 else "")
            )
            bot_cfg = bots.get(str(default_bot)) or {}
            env_path = os.path.expanduser(str(bot_cfg.get("env_file") or cfg.get("env_file") or ""))
            token_key = str(bot_cfg.get("token_key") or "TELEGRAM_BOT_TOKEN")
            if env_path and os.path.isfile(env_path):
                with contextlib.suppress(OSError), open(env_path, encoding="utf-8") as env_file:
                    for line in env_file:
                        m = re.match(rf"^\s*(?:export\s+)?{re.escape(token_key)}\s*=\s*['\"]?([^'\"\s#]+)", line)
                        if m:
                            token = m.group(1).strip()
                            break
        return token

    def chat_id(self):
        return self.load_config().get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID") or 0

    def call_bot_api(self, method: str, payload: dict) -> dict:
        """Call Telegram Bot API with the configured token; return its JSON response."""
        token = self.bot_token()
        if not token:
            raise ValueError("Telegram bot_token is missing")
        body = {key: value for key, value in payload.items() if key != "method"}
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeError):
                return {"ok": False, "error_code": exc.code, "description": "Telegram API request failed"}
        except urllib.error.URLError as exc:
            return {"ok": False, "description": str(exc.reason)}

    def load_config(self) -> dict:
        """The Telegram section of the channels config (`{}` when there is none)."""
        return load_channel_section(self.config_path, "telegram")

    def topics(self) -> dict[str, int]:
        cfg = self.load_config()
        parsed = _coerce_topic_map(cfg.get("topics"))
        if parsed:
            return parsed
        return resolve_tg_topics(self.config_path, self.preferences_path)

    def default_topic(self, topic_map: dict[str, int] | None = None) -> tuple[str, int]:
        tmap = topic_map if topic_map is not None else self.topics()
        cfg = self.load_config()
        prefs = load_hub_preferences(self.preferences_path)
        for cand in (cfg.get("default_topic"), prefs.get("tg_default_topic")):
            if cand and str(cand) in tmap:
                return str(cand), int(tmap[str(cand)])
        if tmap:
            last_name = next(reversed(tmap))
            return last_name, int(tmap[last_name])
        return "general", 1

    def is_configured(self) -> bool:
        if self.tgnotify_bin:
            return True
        try:
            return bool(self.bot_token() and self.chat_id())
        except (OSError, ValueError):
            return False

    def _send_document(self, chat_id, thread_id: int, file_path: str, silent: bool) -> dict:
        """`sendDocument` as multipart/form-data (the Bot API takes files only that way)."""
        token = self.bot_token()
        if not token:
            return {"ok": False, "description": "Telegram bot_token is missing"}
        boundary = "mixhub" + os.urandom(12).hex()
        fields = {"chat_id": str(chat_id), "disable_notification": "true" if silent else "false"}
        if thread_id and thread_id != 1:
            fields["message_thread_id"] = str(thread_id)
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        with open(file_path, "rb") as f:
            content = f.read()
        filename = os.path.basename(file_path).replace('"', "")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, ValueError) as exc:
            return {"ok": False, "description": str(getattr(exc, "reason", exc))}

    def route_topic(
        self,
        session: dict | None = None,
        explicit_topic: str | None = None,
        message: str = "",
    ) -> tuple[str, int]:
        topic_map = self.topics()
        def_name, def_tid = self.default_topic(topic_map)
        if explicit_topic and explicit_topic in topic_map:
            return explicit_topic, topic_map[explicit_topic]
        if explicit_topic:
            return explicit_topic, topic_map.get(explicit_topic, def_tid)

        haystack = " ".join(
            str(x or "")
            for x in (
                (session or {}).get("cwd"),
                (session or {}).get("title"),
                (session or {}).get("firstPrompt"),
                (session or {}).get("lastPrompt"),
                message,
            )
        ).lower()

        active_rules = resolve_topic_routing_rules(self.config_path, self.preferences_path)
        for topic_name, keywords in active_rules:
            if any(kw.lower() in haystack for kw in keywords):
                if topic_name in topic_map:
                    return topic_name, topic_map[topic_name]
                return topic_name, def_tid

        for topic_name, tid in topic_map.items():
            if topic_name.lower() in haystack:
                return topic_name, tid

        return def_name, def_tid

    def send(
        self,
        message: str,
        session: dict | None = None,
        topic: str | None = None,
        dry_run: bool = False,
        alert: bool = False,
        file_path: str = "",
        **kwargs,
    ) -> dict:
        topic_name, thread_id = self.route_topic(session=session, explicit_topic=topic, message=message)
        plain_text = sanitize_plain_text(message)
        cmd = [self.tgnotify_bin, "-t", topic_name]
        if alert:
            cmd.append("--alert")
        if file_path:
            cmd.extend(["--file", str(file_path)])
        cmd.append(plain_text)

        if dry_run or env("DISABLE_REAL_NOTIFY") == "1":
            return {
                "ok": True,
                "channel": "telegram",
                "dry_run": True,
                "topic": topic_name,
                "message_thread_id": thread_id,
                "text": plain_text,
                "file_path": str(file_path or ""),
                **({"command": cmd} if self.tgnotify_bin else {}),
            }

        if not self.tgnotify_bin:
            chat_id = self.chat_id()
            payload = {"chat_id": chat_id, "text": plain_text, "disable_notification": not alert}
            if thread_id and thread_id != 1:  # 1 is the General topic, which takes no thread id
                payload["message_thread_id"] = thread_id
            try:
                api = self.call_bot_api("sendMessage", payload)
                if api.get("ok") and file_path:
                    api = self._send_document(chat_id, thread_id, str(file_path), silent=not alert)
            except (OSError, ValueError) as exc:
                api = {"ok": False, "description": str(exc)}
            return {
                "ok": bool(api.get("ok")),
                "channel": "telegram",
                "dry_run": False,
                "topic": topic_name,
                "message_thread_id": thread_id,
                "text": plain_text,
                "file_path": str(file_path or ""),
                "error": "" if api.get("ok") else str(api.get("description") or "Telegram API request failed"),
            }

        exec_fn = self.runner or (
            lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=15)
        )
        res = exec_fn(cmd)
        rc = getattr(res, "returncode", 0)
        return {
            "ok": rc == 0,
            "channel": "telegram",
            "dry_run": False,
            "topic": topic_name,
            "message_thread_id": thread_id,
            "text": plain_text,
            "file_path": str(file_path or ""),
            "command": cmd,
            "returncode": rc,
        }
