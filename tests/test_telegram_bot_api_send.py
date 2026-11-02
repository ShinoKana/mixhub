"""Telegram notifications work with only a bot token and chat id; `tgnotify` is an optional command."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mixhub.channels.telegram import TelegramChannel


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TelegramBotApiSendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.config = base / "config.json"
        self.config.write_text(json.dumps({"bot_token": "123:abc", "chat_id": -1001, "topics": {"builds": 42}}))
        self.prefs = base / "Preferences"
        self.prefs.write_text("{}")
        self.requests = []
        env = {k: v for k, v in os.environ.items() if k not in ("MIXHUB_TGNOTIFY_BIN", "HUB_DISABLE_REAL_NOTIFY")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # These tests lift the no-real-notify guard to exercise the send path, so no real `tgnotify` may ever be
        # found (one did once, and posted the test messages to a live group), and every HTTP call must be faked
        which = mock.patch("shutil.which", return_value=None)
        which.start()
        self.addCleanup(which.stop)
        net = mock.patch("urllib.request.urlopen", side_effect=AssertionError("real network call in a test"))
        net.start()
        self.addCleanup(net.stop)

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(json.dumps({"ok": True, "result": {"message_id": 7}}).encode())

    def channel(self):
        return TelegramChannel(config_path=str(self.config), preferences_path=str(self.prefs))

    def test_without_tgnotify_messages_go_to_the_bot_api(self):
        tg = self.channel()
        self.assertTrue(tg.is_configured())
        with mock.patch("urllib.request.urlopen", self.urlopen):
            res = tg.send("Build finished", topic="builds")
        self.assertTrue(res["ok"])
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertTrue(req.full_url.endswith("/bot123:abc/sendMessage"))
        body = json.loads(req.data)
        self.assertEqual(body["chat_id"], -1001)
        self.assertEqual(body["message_thread_id"], 42)
        self.assertEqual(body["text"], "Build finished")
        self.assertTrue(body["disable_notification"])

    def test_alert_rings_and_a_file_is_sent_as_a_document(self):
        tg = self.channel()
        report = Path(self.tmp.name) / "report.txt"
        report.write_text("details")
        with mock.patch("urllib.request.urlopen", self.urlopen):
            res = tg.send("Tests failed", topic="builds", alert=True, file_path=str(report))
        self.assertTrue(res["ok"])
        methods = [r.full_url.rsplit("/", 1)[1] for r in self.requests]
        self.assertEqual(methods, ["sendMessage", "sendDocument"])
        self.assertFalse(json.loads(self.requests[0].data)["disable_notification"])
        doc = self.requests[1]
        self.assertIn(b'filename="report.txt"', doc.data)
        self.assertIn(b"details", doc.data)
        self.assertIn(b"-1001", doc.data)

    def test_tgnotify_is_still_used_when_configured(self):
        script = Path(self.tmp.name) / "tgnotify"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
        self.prefs.write_text(json.dumps({"tgnotify_bin": str(script)}))
        calls = []
        tg = TelegramChannel(config_path=str(self.config), preferences_path=str(self.prefs),
                             runner=lambda argv: calls.append(argv) or mock.Mock(returncode=0))
        with mock.patch("urllib.request.urlopen", self.urlopen):
            res = tg.send("hello", topic="builds")
        self.assertTrue(res["ok"])
        self.assertEqual(calls[0][:3], [str(script), "-t", "builds"])
        self.assertEqual(self.requests, [])

    def test_unconfigured_without_token_or_tgnotify(self):
        self.config.unlink()
        self.assertFalse(self.channel().is_configured())


if __name__ == "__main__":
    unittest.main()
