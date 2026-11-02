"""Interface language: English by default, Chinese / Japanese by preference or locale, English fallback per key."""
import json
import os
import re
import string
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mixhub import i18n

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC = REPO_ROOT / "mixhub" / "web" / "static"


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class LanguageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefs = Path(self.tmp.name) / "Preferences"
        self.prefs.write_text("{}")
        keep = {k: v for k, v in os.environ.items()
                if k not in ("LC_ALL", "LC_MESSAGES", "LANG", "MIXHUB_LANGUAGE", "HUB_LANGUAGE")}
        patcher = mock.patch.dict(os.environ, dict(keep, MIXHUB_PREFERENCES_PATH=str(self.prefs)), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_english_without_a_preference_or_locale(self):
        self.assertEqual(i18n.language(), "en")
        os.environ["LANG"] = "C.UTF-8"
        self.assertEqual(i18n.language(), "en")

    def test_locale_picks_a_supported_language(self):
        os.environ["LANG"] = "ja_JP.UTF-8"
        self.assertEqual(i18n.language(), "ja")
        os.environ["LC_ALL"] = "zh_CN.UTF-8"  # LC_ALL wins over LANG
        self.assertEqual(i18n.language(), "zh")
        os.environ["LC_ALL"] = "fr_FR.UTF-8"
        self.assertEqual(i18n.language(), "en")

    def test_preference_beats_the_locale(self):
        os.environ["LANG"] = "ja_JP.UTF-8"
        self.prefs.write_text(json.dumps({"language": "zh"}))
        self.assertEqual(i18n.preference(), "zh")
        self.assertEqual(i18n.language(), "zh")
        self.prefs.write_text(json.dumps({"language": "auto"}))
        self.assertEqual(i18n.language(), "ja")

    def test_a_rewrite_within_the_same_mtime_tick_is_seen(self):
        # Linux/WSL file times can be coarse enough that two quick writes share an mtime.
        self.prefs.write_text(json.dumps({"language": "zh"}))
        self.assertEqual(i18n.preference(), "zh")
        st = self.prefs.stat()
        self.prefs.write_text(json.dumps({"language": "auto"}))
        os.utime(self.prefs, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(i18n.preference(), "auto")

    def test_env_override_beats_the_preference_and_locale(self):
        os.environ["LANG"] = "zh_CN.UTF-8"
        self.prefs.write_text(json.dumps({"language": "zh"}))
        os.environ["MIXHUB_LANGUAGE"] = "ja"
        self.assertEqual(i18n.language(), "ja")

    def test_text_falls_back_to_english_then_the_key(self):
        with mock.patch.object(i18n, "table", side_effect=lambda lang: {"en": {"a": "Hi {name}"}, "ja": {}}[lang]):
            self.assertEqual(i18n.t("a", "ja", name="X"), "Hi X")
            self.assertEqual(i18n.t("missing", "ja"), "missing")

    def test_web_script_carries_web_strings_and_the_preference(self):
        self.prefs.write_text(json.dumps({"language": "ja"}))
        script = i18n.web_script()
        self.assertTrue(script.startswith("window.MIXHUB_I18N = "))
        doc = json.loads(script[len("window.MIXHUB_I18N = "):].rstrip().rstrip(";"))
        self.assertEqual(doc["preference"], "ja")
        self.assertEqual(doc["strings"]["ja"]["web.send"], "送信")
        self.assertTrue(all(k.startswith("web.") for lang in doc["strings"].values() for k in lang))


class TranslationFilesTest(unittest.TestCase):
    def test_every_language_matches_english(self):
        en = i18n.table("en")
        self.assertGreater(len(en), 100)
        for lang in i18n.LANGUAGES:
            table = i18n.table(lang)
            self.assertTrue(table, lang)
            self.assertEqual(sorted(set(table) - set(en)), [], f"{lang}.json has keys English lacks")
            for key, text in table.items():
                self.assertEqual(placeholders(text), placeholders(en[key]), f"{lang}:{key}")

    def test_web_markup_and_script_keys_exist(self):
        en = i18n.table("en")
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        used = set(re.findall(r'data-i18n(?:-\w+)?="([^"]+)"', html)) | set(re.findall(r"\bt\('([\w.]+)'", js))
        self.assertGreater(len(used), 100)
        self.assertEqual(sorted(used - set(en)), [])

    def test_web_ui_source_is_english(self):
        # Translations live in mixhub/i18n; only the language picker names languages in their own script
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace(">中文<", "").replace(">日本語<", "")
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        for name, text in (("index.html", html), ("app.js", js)):
            self.assertIsNone(re.search(r"[぀-ヿ一-鿿]", text), name)


class TerminalAndHandoffTextTest(unittest.TestCase):
    """Text MixHub writes outside the browser follows `language()`; English for most users."""

    def lang(self, code):
        patcher = mock.patch.dict(os.environ, {"MIXHUB_LANGUAGE": code})
        patcher.start()
        self.addCleanup(patcher.stop)


    def test_resume_directive_and_bundle_headings(self):
        from mixhub.handoff import RESUME_STATE_ACTIONS
        from mixhub.handoff.resume import RESUME_STATES

        self.lang("en")
        self.assertEqual(list(RESUME_STATE_ACTIONS), list(RESUME_STATES))
        self.assertTrue(RESUME_STATE_ACTIONS["user_pending"].startswith("Last turn is an unanswered user request"))
        self.assertEqual(i18n.t("handoff.section.immediate"), "## 0 Immediate Action")
        self.lang("zh")
        self.assertTrue(RESUME_STATE_ACTIONS["user_pending"].startswith("最后一条是用户提问"))
        with self.assertRaises(KeyError):
            RESUME_STATE_ACTIONS["nope"]

    def test_preference_change_is_picked_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "Preferences"
            env = {k: v for k, v in os.environ.items() if k not in ("MIXHUB_LANGUAGE", "HUB_LANGUAGE")}
            with mock.patch.dict(os.environ, dict(env, MIXHUB_PREFERENCES_PATH=str(prefs)), clear=True):
                prefs.write_text(json.dumps({"language": "ja"}))
                self.assertEqual(i18n.preference(), "ja")
                prefs.write_text(json.dumps({"language": "zh"}))
                os.utime(prefs, ns=(1, 2_000_000_000_000_000_000))  # a different mtime, however fast the write
                self.assertEqual(i18n.preference(), "zh")

    def test_tmux_strings_fit_inside_a_tmux_format(self):
        for lang in i18n.LANGUAGES:
            for key, text in i18n.table(lang).items():
                if key.startswith("tmux."):
                    self.assertIsNone(re.search(r"[,{}#]", text.replace("{n}", "")), f"{lang}:{key}")


class RouteTest(unittest.TestCase):
    def test_page_loads_the_strings_before_the_app(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('src="/i18n.js"'), html.index('src="/app.js"'))

    def test_handler_serves_i18n_js(self):
        import http.server
        import threading
        import urllib.request

        from mixhub.web.handler import Handler

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.hub = mock.Mock()
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/i18n.js") as resp:
            self.assertIn("javascript", resp.headers["Content-Type"])
            self.assertIn("window.MIXHUB_I18N", resp.read().decode())


if __name__ == "__main__":
    unittest.main()
