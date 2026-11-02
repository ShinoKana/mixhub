"""channels.json: one file with a section per channel, older locations as fallbacks, one-way sends only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mixhub.channels.config import resolve_channel_config_path
from mixhub.channels.manager import ChannelManager


class ChannelsConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items()
               if k not in ("MIXHUB_TGNOTIFY_BIN", "MIXHUB_TG_TOPICS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        env["MIXHUB_PREFERENCES_PATH"] = str(self.home / "no_Preferences")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        net = mock.patch("urllib.request.urlopen", side_effect=AssertionError("real network call in a test"))
        net.start()
        self.addCleanup(net.stop)

    def write(self, rel: str, data: dict) -> Path:
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def test_nothing_is_configured_on_a_fresh_home(self):
        status = ChannelManager(home=str(self.home)).status()["channels"]
        self.assertEqual({name: s["configured"] for name, s in status.items()},
                         {"telegram": False, "slack": False, "webhook": False})
        self.assertEqual(status["telegram"]["config_path"], str(self.home / ".config/mixhub/channels.json"))

    def test_mixhub_channels_json_holds_every_section(self):
        self.write(".config/mixhub/channels.json", {
            "telegram": {"bot_token": "1:a", "chat_id": -100},
            "slack": {"webhook_url": "https://hooks.example/slack"},
            "webhook": {"url": "https://hooks.example/any"},
        })
        status = ChannelManager(home=str(self.home)).status()["channels"]
        self.assertTrue(all(s["configured"] for s in status.values()))

    def test_older_locations_fill_in_missing_sections(self):
        new = self.write(".config/mixhub/channels.json", {"webhook": {"url": "https://hooks.example/any"}})
        legacy = self.write(".config/session-hub/channels.json", {"slack": {"webhook_url": "https://hooks.example/s"}})
        tg = self.write(".config/tg-notify/config.json", {"bot_token": "1:a", "chat_id": -100, "topics": {"ci": 5}})
        home = str(self.home)
        self.assertEqual(resolve_channel_config_path("webhook", home), str(new))
        self.assertEqual(resolve_channel_config_path("slack", home), str(legacy))
        self.assertEqual(resolve_channel_config_path("telegram", home), str(tg))
        mgr = ChannelManager(home=home)
        self.assertTrue(mgr.telegram.is_configured())
        self.assertEqual(mgr.telegram.topics(), {"ci": 5})

    def test_a_slack_bot_token_alone_is_not_a_webhook(self):
        self.write(".config/session-hub/channels.json", {"slack": {"bot_token": "xoxb-1", "app_token": "xapp-1"}})
        self.assertFalse(ChannelManager(home=str(self.home)).slack.is_configured())

    def test_telegram_token_from_a_bot_env_file_also_sends_documents(self):
        env_file = self.home / "bot.env"
        env_file.write_text("export MY_TOKEN=7:xyz\n")
        self.write(".config/mixhub/channels.json", {"telegram": {
            "chat_id": -100, "bots": {"main": {"env_file": str(env_file), "token_key": "MY_TOKEN"}}}})
        report = self.home / "r.txt"
        report.write_text("x")
        urls = []

        class Resp:
            def __init__(self, url):
                urls.append(url)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *a):
                return b'{"ok": true}'

        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: Resp(req.full_url)):
            res = ChannelManager(home=str(self.home)).notify("done", file_path=str(report))
        self.assertTrue(res["ok"], res)
        self.assertEqual([u.rsplit("/", 2)[1:] for u in urls], [["bot7:xyz", "sendMessage"], ["bot7:xyz", "sendDocument"]])

    def test_webhook_posts_json_with_its_headers(self):
        self.write(".config/mixhub/channels.json", {"webhook": {"url": "https://hooks.example/any",
                                                                 "headers": {"Authorization": "Bearer t"}}})
        sent = []
        mgr = ChannelManager(home=str(self.home), http_sender=lambda url, payload, headers=None: sent.append(
            (url, payload, headers)) or 204)
        res = mgr.notify("needs you", channel="webhook", session={"id": "abc", "tool": "claude", "title": "Fix CI"})
        self.assertTrue(res["ok"])
        url, payload, headers = sent[0]
        self.assertEqual(url, "https://hooks.example/any")
        self.assertEqual(payload["text"], "needs you")
        self.assertEqual((payload["session_id"], payload["tool"], payload["title"]), ("abc", "claude", "Fix CI"))
        self.assertEqual(headers, {"Authorization": "Bearer t"})


if __name__ == "__main__":
    unittest.main()
