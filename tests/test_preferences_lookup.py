"""Every reader finds `Preferences` in the same place: `$MIXHUB_PREFERENCES_PATH`, else `~/.config/mixhub/Preferences`
when it exists, else the state directory. The README tells people to use `~/.config`; several readers ignored it."""
import json
import os
import tempfile
import unittest
from unittest import mock

from mixhub.core import prefs as core_prefs


class PreferencesLookupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        cfg = os.path.join(self.home, ".config", "mixhub")
        os.makedirs(cfg)
        self.cfg_path = os.path.join(cfg, "Preferences")
        with open(self.cfg_path, "w") as f:
            json.dump({"language": "ja", "dispatch_scripts": ["my-runner"], "update_check": False,
                       "agy_mouse_shim": "off", "wordmark": "pulse"}, f)
        env = {k: v for k, v in os.environ.items() if k != "MIXHUB_PREFERENCES_PATH"}
        env["HOME"] = self.home
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_resolved_path_prefers_the_user_config_file(self):
        self.assertEqual(core_prefs.preferences_path(), self.cfg_path)
        self.assertEqual(core_prefs.load_preferences()["language"], "ja")
        os.remove(self.cfg_path)
        from mixhub.core.paths import PREFERENCES_PATH
        self.assertEqual(core_prefs.preferences_path(), PREFERENCES_PATH)

    def test_the_environment_variable_wins(self):
        other = os.path.join(self.home, "elsewhere.json")
        with mock.patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": other}):
            self.assertEqual(core_prefs.preferences_path(), other)

    def test_language(self):
        from mixhub import i18n
        i18n._pref_cache = (None, "auto")
        self.assertEqual(i18n.preference(), "ja")

    def test_dispatch_scripts(self):
        from mixhub.proc import classify
        classify._DISPATCH_CACHE["key"] = None
        self.assertEqual(classify.dispatch_script_names(), frozenset({"my-runner"}))

    def test_update_check(self):
        from mixhub.cli import update
        with mock.patch.object(update, "install_method", return_value="pip"):
            self.assertTrue(update.check_disabled())

    def test_agy_mouse_shim(self):
        from mixhub.filters.agy_claude import prefs as agy_prefs
        self.assertEqual(agy_prefs.load_filter_preferences().get("agy_mouse_shim"), "off")

    def test_writes_go_back_to_the_same_file(self):
        core_prefs.save_preferences({"wordmark": "off"})
        with open(self.cfg_path) as f:
            self.assertEqual(json.load(f)["wordmark"], "off")


if __name__ == "__main__":
    unittest.main()
