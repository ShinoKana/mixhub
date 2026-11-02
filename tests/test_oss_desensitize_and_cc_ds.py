#!/usr/bin/env python3
"""
Unit tests for:
  1. GitHub Open-Source Desensitization (`channels.py`, `providers.py`, `rules_sync.py`, `.gitignore`,
     `README.md`) with the private-term blocklist derived from the local, gitignored `Preferences`
     (never spelled out in this tracked file), plus the `Preferences` lookup chain and
     `TelegramPollerLock` / `default_bot` parameterization.
  2. `agy_claude_filter.py` & `rules_sync.ensure_workspace_trusted` respecting `Preferences`
     (`agy_mouse_shim`, `trust_all_workspaces`, `trust_roots`).
  3. `CC·DS` (DeepSeek inside Claude Code harness sharing `~/.claude`) detection & launch command
     (`detect_claude_subprovider`, `build_cc_ds_launch_cmd`).
  4. `handoff.py` Relay title inheritance (`↳ <source_title>` in `session_names.json` without
     polluting native CLI files, retaining source session).
"""
from __future__ import annotations

import json
import subprocess
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agy_claude_filter
import channels
import handoff
import mixhub.channels.config
import mixhub.channels.telegram
import mixhub.handoff
import mixhub.providers
import mixhub.providers.preferences
import providers
import rules_sync

LOCAL_PREFERENCES_PATH = ROOT / "Preferences"
HOME_PATH_PLACEHOLDERS = {"you", "user", "username", "alice", "bob", "other", "developer", "dev", "me", "runner", "example"}
HOME_PATH_RE = re.compile(r"(?:/Users/|/home/|C:\\Users\\)([A-Za-z0-9_][A-Za-z0-9_.-]*)")


def load_local_preferences() -> dict:
    """Read the developer's local (gitignored) `Preferences`; `{}` on a fresh clone."""
    if not LOCAL_PREFERENCES_PATH.is_file():
        return {}
    try:
        data = json.loads(LOCAL_PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def derive_private_terms(prefs: dict) -> set[str]:
    """
    Build the private-term blocklist from local `Preferences` only, so the tracked test file
    never has to spell out anyone's private topics, bot names, or inbox directories.
    Covers: `private_terms`, non-ASCII `tg_topics` names, `tg_default_topic`, `default_bot`,
    `host_label`, the `inbox_jsonl_path` bridge directory, and `/Users/<user>` / `<user>-mac`.
    """
    terms: set[str] = set()
    for term in prefs.get("private_terms") or []:
        if isinstance(term, str) and term.strip():
            terms.add(term.strip())
    for topic in (prefs.get("tg_topics") or {}).keys():
        if isinstance(topic, str) and topic.strip() and not topic.isascii():
            terms.add(topic.strip())
    for key in ("tg_default_topic", "default_bot", "host_label"):
        val = prefs.get(key)
        if isinstance(val, str) and val.strip():
            terms.add(val.strip())
    inbox = str(prefs.get("inbox_jsonl_path") or "").strip()
    if inbox:
        parts = [p for p in Path(inbox.replace("~", "", 1)).parts if p not in ("/", "")]
        if parts:
            terms.add(parts[0])
    user = str(prefs.get("user_name") or "").strip().lower()
    if user:
        terms.add(f"/Users/{user}")
        terms.add(f"{user}-mac")
    return terms


def scanned_source_files() -> list[Path]:
    """Every tracked text file (what a public clone would contain); binary fixtures are skipped."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    files = []
    for rel in out.splitlines():
        path = ROOT / rel
        if not path.is_file() or path.suffix in (".bin", ".png", ".jpg", ".gif", ".ttf", ".otf", ".woff", ".woff2", ".ico"):
            continue
        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        files.append(path)
    return files


def public_terms(prefs: dict) -> list[str]:
    """Names that are meant to be public (e.g. the GitHub owner in repository URLs), from local Preferences."""
    return [t.lower() for t in prefs.get("public_terms") or [] if isinstance(t, str) and t.strip()]


def strip_public(lowered: str, allowed: list[str]) -> str:
    for term in allowed:
        lowered = lowered.replace(term, "")
    return lowered


class OpenSourceDesensitizeAndCCDSTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_source_files_and_docs_contain_no_personal_paths_or_private_topics(self) -> None:
        prefs = load_local_preferences()
        private_terms = derive_private_terms(prefs)
        allowed = public_terms(prefs)
        files = scanned_source_files()
        self.assertGreater(len(files), 50)

        for fpath in files:
            content = fpath.read_text(encoding="utf-8")
            lowered = strip_public(content.lower(), allowed)
            # 1. Generic: no real home-directory paths (placeholders like /Users/you are allowed).
            for m in HOME_PATH_RE.finditer(content):
                self.assertTrue(
                    m.group(1).lower() in HOME_PATH_PLACEHOLDERS,
                    f"Real home path {m.group(0)!r} must not appear in tracked file {fpath.name}",
                )
            # 2. Preferences-derived private terms (case-insensitive).
            for term in sorted(private_terms):
                # assertFalse (not assertNotIn) so a failure never dumps private file content to logs.
                self.assertFalse(
                    term.lower() in lowered,
                    f"Private term {term!r} from local Preferences must not appear in tracked file {fpath.name}",
                )

    def test_core_modules_have_no_user_name_references(self) -> None:
        prefs = load_local_preferences()
        handles = {str(prefs.get("user_name") or "").strip().lower()} - {""}
        allowed = public_terms(prefs)
        for fpath in scanned_source_files():
            lowered = strip_public(fpath.read_text(encoding="utf-8").lower(), allowed)
            for handle in sorted(handles):
                # assertFalse (not assertNotIn) so a failure never dumps file content to logs.
                self.assertFalse(handle in lowered, f"{fpath.name} must not reference a personal user name")

    def test_gitignore_and_local_preferences_file(self) -> None:
        gitignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        gitignore_lines = {ln.strip() for ln in gitignore_text.splitlines()}
        for entry in (
            "Preferences",
            "/Preferences",
            "/Preferences.*",
            "!/Preferences.example",
            "active_working_set.json",
            "unsent_drafts.json",
            "session_names.json",
            "data/",
            "docs/",
            "/docs/internal/",
            "/docs/*_20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]*.md",
            "bench_out.json",
            ".DS_Store",
            "*.pid",
            "*.lock",
            "*.local.*",
            "*.orig",
            "*.bak",
            "*.log",
            ".hub.pid",
            ".hub.lock",
            "hub_state.json",
            "ACCEPTANCE.md",
            "/index.html",
            "screenshots/",
            "__pycache__/",
        ):
            self.assertIn(entry, gitignore_lines, f".gitignore must contain {entry!r}")

    def test_channels_tgnotify_bin_and_topics_open_source_fallback(self) -> None:
        missing_pref = str(self.base / "nonexistent_Preferences")
        missing_cfg = str(self.base / "nonexistent_tg_config.json")

        with patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": missing_pref}, clear=False):
            os.environ.pop("MIXHUB_TGNOTIFY_BIN", None)
            os.environ.pop("MIXHUB_TG_TOPICS", None)
            # A `tgnotify` on PATH is never picked up on its own: the sender command is opt-in.
            with patch("shutil.which", return_value="/usr/local/bin/tgnotify"):
                self.assertEqual(channels.resolve_tgnotify_bin(preferences_path=missing_pref), "")
            topics = channels.resolve_tg_topics(
                config_path=missing_cfg,
                preferences_path=missing_pref,
            )
            self.assertEqual(topics, {"general": 1})

            tg = channels.TelegramChannel(
                config_path=missing_cfg,
                preferences_path=missing_pref,
            )
            self.assertEqual(tg.topics(), {"general": 1})
            self.assertEqual(tg.route_topic({"cwd": "/tmp/demo", "title": "hello"}), ("general", 1))
            # No built-in keyword topics: a release-ish message still goes to the chat itself.
            self.assertEqual(channels.resolve_topic_routing_rules(missing_cfg, missing_pref), ())
            self.assertEqual(tg.route_topic({"cwd": "/src/mixhub"}, message="github release"), ("general", 1))
            self.assertFalse(tg.is_configured())

        # Environment override takes top priority
        with patch.dict(os.environ, {"MIXHUB_TGNOTIFY_BIN": "/opt/custom/tgnotify"}):
            self.assertEqual(
                channels.resolve_tgnotify_bin(preferences_path=missing_pref),
                "/opt/custom/tgnotify",
            )

    def test_agy_claude_filter_preferences_and_mouse_shim_switches(self) -> None:
        local_pref = str(self.base / "Preferences")
        Path(local_pref).write_text(json.dumps({}), encoding="utf-8")

        # Mouse shim switch (`"agy_mouse_shim": "auto"` vs `false` / `MIXHUB_NO_AGY_MOUSE=1`)
        self.assertTrue(agy_claude_filter.is_mouse_shim_enabled(preferences_path=local_pref))
        disabled_pref_file = self.base / "prefs_no_mouse.json"
        disabled_pref_file.write_text(json.dumps({"agy_mouse_shim": False}), encoding="utf-8")
        self.assertFalse(agy_claude_filter.is_mouse_shim_enabled(preferences_path=str(disabled_pref_file)))
        with patch.dict(os.environ, {"MIXHUB_NO_AGY_MOUSE": "1"}):
            self.assertFalse(agy_claude_filter.is_mouse_shim_enabled(preferences_path=local_pref))

        # 4. Missing `/tmp/agy-subagent-live.json` & `/tmp/agy-active-ext-sl.json` never raise errors
        from mixhub.filters.agy_claude import stream as agy_stream  # owner of the badge paths

        orig_sub = agy_stream.SUB_LIVE_PATH
        orig_ext = agy_stream.ACTIVE_EXT_PATH
        orig_ext_sl = agy_stream.ACTIVE_EXT_SL_PATH
        try:
            agy_stream.SUB_LIVE_PATH = str(self.base / "missing_sub.json")
            agy_stream.ACTIVE_EXT_PATH = str(self.base / "missing_ext.json")
            agy_stream.ACTIVE_EXT_SL_PATH = str(self.base / "missing_ext_sl.json")
            agy_claude_filter.SUB_LIVE_CACHE["ts"] = 0.0
            badge = agy_claude_filter._get_subagent_badge()
            self.assertEqual(badge[0], "Argon")
        finally:
            agy_stream.SUB_LIVE_PATH = orig_sub
            agy_stream.ACTIVE_EXT_PATH = orig_ext
            agy_stream.ACTIVE_EXT_SL_PATH = orig_ext_sl

    def test_rules_sync_workspace_trust_respects_trust_all_and_trust_roots(self) -> None:
        home = self.base / "oss_home"
        home.mkdir(parents=True, exist_ok=True)
        allowed_root = home / "trusted_projects"
        allowed_sub = allowed_root / "repo_a"
        allowed_sub.mkdir(parents=True, exist_ok=True)
        untrusted_dir = home / "random_downloads" / "untrusted_repo"
        untrusted_dir.mkdir(parents=True, exist_ok=True)

        oss_prefs = self.base / "oss_Preferences.json"
        oss_prefs.write_text(
            json.dumps(
                {
                    "trust_all_workspaces": False,
                    "trust_roots": [str(allowed_root)],
                }
            ),
            encoding="utf-8",
        )

        # 1. Directory outside `trust_roots` is NOT auto-trusted when `trust_all_workspaces=False`
        res_blocked = rules_sync.ensure_workspace_trusted(
            str(untrusted_dir),
            home_dir=str(home),
            preferences_path=str(oss_prefs),
        )
        self.assertFalse(res_blocked["ok"])
        self.assertFalse(res_blocked["trusted"])
        self.assertEqual(res_blocked.get("reason"), "untrusted_root")

        # 2. Directory inside `trust_roots` IS trusted
        res_allowed = rules_sync.ensure_workspace_trusted(
            str(allowed_sub),
            home_dir=str(home),
            preferences_path=str(oss_prefs),
        )
        self.assertTrue(res_allowed["ok"])
        self.assertTrue(res_allowed["trusted"])

        # 3. Subdirectory of an already-trusted CLI workspace is also trusted
        nested_sub = allowed_sub / "packages" / "core"
        nested_sub.mkdir(parents=True, exist_ok=True)
        empty_prefs = self.base / "empty_Preferences.json"
        empty_prefs.write_text(json.dumps({"trust_all_workspaces": False}), encoding="utf-8")
        res_nested = rules_sync.ensure_workspace_trusted(
            str(nested_sub),
            home_dir=str(home),
            preferences_path=str(empty_prefs),
        )
        self.assertTrue(res_nested["ok"])
        self.assertTrue(res_nested["trusted"])

    def test_detect_claude_subprovider_and_build_cc_ds_launch_cmd(self) -> None:
        # 1. Detect from JSONL file with `message.model = "deepseek-chat"` / `"deepseek-reasoner"`
        ds_jsonl = self.base / "cc_ds_session.jsonl"
        ds_jsonl.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "deepseek-reasoner",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "DeepSeek reasoning inside Claude Code"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual(providers.detect_claude_subprovider(ds_jsonl), "CC·DS")
        self.assertEqual(mixhub.providers.detect_claude_subprovider({"path": str(ds_jsonl)}), "CC·DS")

        # 2. Detect from `ANTHROPIC_BASE_URL` environment or metadata dict
        self.assertEqual(
            providers.detect_claude_subprovider(
                {"tool": "claude"},
                env={"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"},
            ),
            "CC·DS",
        )

        # 3. Standard Anthropic Claude session returns `"Claude"`
        std_jsonl = self.base / "std_claude.jsonl"
        std_jsonl.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-opus-5-5",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Standard Claude"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual(providers.detect_claude_subprovider(std_jsonl), "Claude")

        # 4. `build_cc_ds_launch_cmd` shares `~/.claude` and supports `--resume <id> --fork-session`
        cmd = providers.build_cc_ds_launch_cmd(
            "sess-1234",
            cwd="/tmp/work",
            model="deepseek-chat",
            fork_session=True,
            prompt="Continue implementation",
        )
        self.assertIn("ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic", cmd)
        self.assertIn("ANTHROPIC_MODEL=deepseek-chat", cmd)
        self.assertIn("claude --resume sess-1234 --fork-session", cmd)
        self.assertNotIn("CLAUDE_CONFIG_DIR=", cmd)

    def test_handoff_relay_title_inheritance(self) -> None:
        # Verify Relay title inheritance (`↳ <源会话标题>`) in `session_names.json` & source session retention
        src_sid = "11111111-2222-3333-4444-555555555555"
        dst_sid = "22222222-3333-4444-5555-666666666666"
        dst_jsonl = self.base / f"{dst_sid}.jsonl"
        dst_jsonl_initial = json.dumps({"type": "session_meta", "payload": {"id": dst_sid}}) + "\n"
        dst_jsonl.write_text(dst_jsonl_initial, encoding="utf-8")

        class FakeHub:
            def __init__(self, root_dir: Path, home_dir: Path):
                self.root = str(root_dir)
                self.home = str(home_dir)
                self.saved: dict[str, dict] = {}
                self.sessions = {
                    f"claude:{src_sid}": {
                        "id": src_sid,
                        "tool": "claude",
                        "title": "OAuth2 登录流重构",
                        "cwd": str(home_dir),
                        "isLive": False,
                        "updatedAt": 500.0,
                    },
                    f"codex:{dst_sid}": {
                        "id": dst_sid,
                        "tool": "codex",
                        "title": "Raw Codex Session",
                        "path": str(dst_jsonl),
                        "cwd": str(home_dir),
                        "isLive": True,
                        "updatedAt": 510.0,
                    },
                }

            def discover(self, force: bool = False):
                return self.sessions

            def save(self, key: str, updates: dict):
                self.saved.setdefault(key, {}).update(updates)

        fake_hub = FakeHub(self.base / "hub_root", self.base / "home")
        os.makedirs(fake_hub.home, exist_ok=True)
        h_res = handoff.create_handoff(
            fake_hub,
            src_sid,
            "codex",
            target_session_id=dst_sid,
        )
        self.assertTrue(h_res["ok"])
        self.assertEqual(h_res["relay_title"], "↳ OAuth2 登录流重构")
        self.assertTrue(h_res["source_retained"])
        self.assertIn(f"claude:{src_sid}", fake_hub.sessions)
        self.assertEqual(fake_hub.sessions[f"claude:{src_sid}"]["title"], "OAuth2 登录流重构")

        sn_file = Path(h_res["session_names_path"])
        self.assertTrue(sn_file.is_file())
        sn_data = json.loads(sn_file.read_text(encoding="utf-8"))
        self.assertEqual(sn_data.get(f"codex:{dst_sid}"), "↳ OAuth2 登录流重构")
        # Native CLI transcript file of target session is untouched
        self.assertEqual(dst_jsonl.read_text(encoding="utf-8"), dst_jsonl_initial)

    def test_readme_open_source_quality_and_desensitized(self) -> None:
        readme_path = ROOT / "README.md"
        self.assertTrue(readme_path.is_file())
        content = readme_path.read_text(encoding="utf-8")
        self.assertIn("install.sh | sh", content)
        self.assertIn("pipx install mixhub", content)
        for agent in ("Claude Code", "AGY", "Codex", "OpenCode"):
            self.assertIn(agent, content)
        self.assertIn("`$MIXHUB_PREFERENCES_PATH`, then `~/.config/mixhub/Preferences`, then the state directory", content)
        for m in HOME_PATH_RE.finditer(content):
            self.assertIn(m.group(1).lower(), HOME_PATH_PLACEHOLDERS)


    def test_readme_only_documents_commands_that_exist(self) -> None:
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        cli_sources = [ROOT / "bin" / "hub", *sorted((ROOT / "mixhub" / "cli").glob("*.py"))]
        cli_text = "\n".join(p.read_text(encoding="utf-8") for p in cli_sources if p.is_file())
        documented = set(re.findall(r"`mixhub ([a-z][a-z-]+)", content)) | set(re.findall(r"^mixhub ([a-z][a-z-]+)", content, re.M))
        self.assertIn("handoff", documented)
        for command in sorted(documented):
            self.assertTrue(f'"{command}"' in cli_text, f"README documents `mixhub {command}`, which the CLI does not handle")

    def test_preferences_lookup_chain_prefers_user_config_then_repo_default(self) -> None:
        user_cfg = self.base / "config_mixhub_Preferences"
        repo_default = self.base / "repo_Preferences"
        user_cfg.write_text(json.dumps({"source": "user_config"}), encoding="utf-8")
        repo_default.write_text(json.dumps({"source": "repo_default"}), encoding="utf-8")
        missing = str(self.base / "missing_Preferences")

        # The channels read Preferences through the one lookup in `mixhub.core.prefs`.
        self.assertEqual(channels.load_hub_preferences(missing), {})
        self.assertEqual(channels.load_hub_preferences(str(repo_default)).get("source"), "repo_default")
        with patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(user_cfg)}):
            self.assertEqual(channels.load_hub_preferences().get("source"), "user_config")

        import mixhub.core.prefs
        for mod, loader, default_name in (
            (mixhub.core.prefs, providers.load_provider_preferences, "PREFERENCES_PATH"),
            (mixhub.core.prefs, channels.load_hub_preferences, "PREFERENCES_PATH"),
        ):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MIXHUB_PREFERENCES_PATH", None)
                with patch.object(mod, "USER_CONFIG_PREFERENCES_PATH", str(user_cfg)), \
                        patch.object(mod, default_name, str(repo_default)):
                    self.assertEqual(loader().get("source"), "user_config")
                    # Explicit path wins and is the ONLY candidate (missing -> {}).
                    self.assertEqual(loader(missing), {})
                    self.assertEqual(loader(str(repo_default)).get("source"), "repo_default")
                with patch.object(mod, "USER_CONFIG_PREFERENCES_PATH", missing), \
                        patch.object(mod, default_name, str(repo_default)):
                    self.assertEqual(loader().get("source"), "repo_default")
            with patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": missing}), \
                    patch.object(mod, "USER_CONFIG_PREFERENCES_PATH", str(user_cfg)):
                self.assertEqual(loader(), {})

        # rules_sync: explicit path is exclusive; otherwise home -> ~/.config/mixhub -> repo default.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MIXHUB_PREFERENCES_PATH", None)
            with patch("mixhub.core.prefs.USER_CONFIG_PREFERENCES_PATH", str(user_cfg)), \
                    patch("mixhub.core.prefs.PREFERENCES_PATH", str(repo_default)):
                self.assertEqual(rules_sync._load_trust_preferences(preferences_path=missing), {})
                self.assertEqual(rules_sync._load_trust_preferences().get("source"), "user_config")
            with patch("mixhub.core.prefs.USER_CONFIG_PREFERENCES_PATH", missing), \
                    patch("mixhub.core.prefs.PREFERENCES_PATH", str(repo_default)):
                self.assertEqual(rules_sync._load_trust_preferences().get("source"), "repo_default")
        with patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": missing}), \
                patch("mixhub.core.prefs.PREFERENCES_PATH", str(repo_default)):
            self.assertEqual(rules_sync._load_trust_preferences(), {})


    def test_call_bot_api_default_bot_resolution_has_no_hardcoded_fallback(self) -> None:
        missing_pref = str(self.base / "missing_Preferences")
        env_a = self.base / "bot_a.env"
        env_b = self.base / "bot_b.env"
        env_a.write_text("export TG_TOKEN_A=token-alpha\n", encoding="utf-8")
        env_b.write_text("TG_TOKEN_B='token-beta'\n", encoding="utf-8")
        bots = {
            "alpha": {"env_file": str(env_a), "token_key": "TG_TOKEN_A"},
            "beta": {"env_file": str(env_b), "token_key": "TG_TOKEN_B"},
        }

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"ok": true, "result": {}}'

        def run(cfg: dict, prefs_path: str, extra_env: dict | None = None) -> str:
            cfg_path = self.base / "tg_cfg.json"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            tg = channels.TelegramChannel(config_path=str(cfg_path), preferences_path=prefs_path)
            seen: list[str] = []

            def fake_urlopen(request, timeout=15):
                seen.append(request.full_url)
                return _Resp()

            env = {k: v for k, v in os.environ.items() if k not in ("TELEGRAM_BOT_TOKEN", "MIXHUB_DEFAULT_BOT")}
            env.update(extra_env or {})
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(mixhub.channels.telegram.urllib.request, "urlopen", fake_urlopen):
                tg.call_bot_api("getMe", {})
            return seen[0]

        # 1. Single configured bot -> that bot, no private fallback name needed.
        self.assertIn("/bottoken-alpha/", run({"bots": {"alpha": bots["alpha"]}}, missing_pref))
        # 2. Explicit `default_bot` in channel config.
        self.assertIn("/bottoken-beta/", run({"bots": bots, "default_bot": "beta"}, missing_pref))
        # 3. `MIXHUB_DEFAULT_BOT` environment variable.
        self.assertIn("/bottoken-alpha/", run({"bots": bots}, missing_pref, {"MIXHUB_DEFAULT_BOT": "alpha"}))
        # 4. Local `Preferences["default_bot"]`.
        pref_file = self.base / "bot_Preferences"
        pref_file.write_text(json.dumps({"default_bot": "beta"}), encoding="utf-8")
        self.assertIn("/bottoken-beta/", run({"bots": bots}, str(pref_file)))
        # 5. Multiple bots and nothing configured -> no token, no hidden personal default.
        with self.assertRaises(ValueError):
            run({"bots": bots}, missing_pref)


if __name__ == "__main__":
    unittest.main()
