"""`mixhub notify --print-hooks` hooks must notify on a fresh install: a configured channel and nothing else."""
import io
import json
import os
import shlex
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mixhub.channels import cli
from mixhub.channels.hooks import generate_official_hook_config

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HookNotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items()
               if k not in ("MIXHUB_TGNOTIFY_BIN", "MIXHUB_TG_TOPICS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                            "MIXHUB_DISABLE_REAL_NOTIFY", "HUB_DISABLE_REAL_NOTIFY")}
        env.update(HOME=str(self.home), MIXHUB_PREFERENCES_PATH=str(self.home / "no_Preferences"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.requests = []
        net = mock.patch("urllib.request.urlopen", self.urlopen)
        net.start()
        self.addCleanup(net.stop)
        # Nothing may scan the real agent sessions to answer a hook.
        hub = mock.patch("mixhub.hub.hub.Hub", side_effect=AssertionError("a hook must not start a Hub scan"))
        hub.start()
        self.addCleanup(hub.stop)

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(b'{"ok": true, "result": {"message_id": 1}}')

    def configure_telegram(self):
        cfg = self.home / ".config" / "mixhub" / "channels.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"telegram": {"bot_token": "1:abc", "chat_id": -100}}))

    def claude_hook_argv(self, event: str) -> list[str]:
        hooks = generate_official_hook_config()["claude_settings_json"]["hooks"]
        argv = shlex.split(hooks[event][0]["hooks"][0]["command"])
        self.assertEqual(argv[1], "notify")
        return argv[2:]

    def run_cli(self, argv: list[str], stdin: str = "") -> int:
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out):
            return cli.main(argv)

    def sent_texts(self) -> list[str]:
        return [json.loads(r.data)["text"] for r in self.requests if r.full_url.endswith("/sendMessage")]

    def test_claude_notification_hook_sends_the_message(self):
        self.configure_telegram()
        payload = {"hook_event_name": "Notification", "session_id": "abc-123", "cwd": "/src/shop",
                   "message": "Claude needs your permission to use Bash", "transcript_path": ""}
        self.assertEqual(self.run_cli(self.claude_hook_argv("Notification"), json.dumps(payload)), 0)
        texts = self.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("Claude needs your permission to use Bash", texts[0])
        self.assertIn("shop", texts[0])

    def test_claude_stop_hook_sends_the_last_reply(self):
        self.configure_telegram()
        transcript = self.home / "t.jsonl"
        rows = [
            {"type": "user", "message": {"role": "user", "content": "fix the build"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Build fixed, 42 tests pass."}]}},
        ]
        transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        payload = {"hook_event_name": "Stop", "session_id": "abc-123", "cwd": "/src/shop",
                   "transcript_path": str(transcript), "stop_hook_active": False}
        self.assertEqual(self.run_cli(self.claude_hook_argv("Stop"), json.dumps(payload)), 0)
        texts = self.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("Build fixed, 42 tests pass.", texts[0])

    @unittest.skipIf(tomllib is None, "needs tomllib (Python 3.11+)")
    def test_codex_notify_sends_the_last_reply(self):
        self.configure_telegram()
        argv = tomllib.loads(generate_official_hook_config()["codex_config_toml"])["notify"]
        self.assertEqual(argv[1], "notify")
        event = {"type": "agent-turn-complete", "thread-id": "t-1", "cwd": "/src/shop",
                 "last-assistant-message": "Refactor done.", "input-messages": ["refactor it"]}
        self.assertEqual(self.run_cli([*argv[2:], json.dumps(event)]), 0)
        texts = self.sent_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("Refactor done.", texts[0])

    def test_hooks_stay_quiet_and_succeed_without_a_channel(self):
        payload = {"hook_event_name": "Notification", "session_id": "abc", "cwd": "/src", "message": "waiting"}
        self.assertEqual(self.run_cli(self.claude_hook_argv("Notification"), json.dumps(payload)), 0)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
