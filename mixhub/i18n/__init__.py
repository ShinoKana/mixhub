"""Interface language: English by default, Chinese and Japanese included.

Each language is one flat JSON file next to this module (`en.json`, `zh.json`, `ja.json`): `"key": "text"`, with
`{name}` placeholders. A key missing from a language falls back to English, so a new language can start small. To
add one, copy `en.json` to `<code>.json` (a two-letter code), translate the values, and add the code to `LANGUAGES`.

The language comes from `MIXHUB_LANGUAGE`, else the `language` preference (`"auto"`, `"en"`, `"zh"`, `"ja"`), else
the locale (`LC_ALL`, `LC_MESSAGES`, `LANG`). The web UI follows the browser language when the preference is `auto`.
Keys starting with `web.` are the web UI's (served as `/i18n.js`).
"""
from __future__ import annotations

import functools
import json
import os

from mixhub.core.env import env

LANGUAGES = ("en", "zh", "ja")
DEFAULT = "en"
_DIR = os.path.dirname(os.path.abspath(__file__))


@functools.lru_cache(maxsize=None)
def table(lang: str) -> dict[str, str]:
    try:
        with open(os.path.join(_DIR, f"{lang}.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _code(value: object) -> str:
    """`zh_CN.UTF-8` / `ja-JP` / `en` -> the supported two-letter code, or ""."""
    code = str(value or "").strip().lower().replace("-", "_").split(".")[0].split("_")[0]
    return code if code in LANGUAGES else ""


_pref_cache: tuple = (None, "auto")  # ((path, mtime), value): `t()` runs per transcript entry, so read on change only


def preference() -> str:
    """The `language` preference: one of `LANGUAGES`, or "auto"."""
    global _pref_cache
    from mixhub.core.prefs import load_preferences, preferences_path

    path = preferences_path()
    try:
        st = os.stat(path)
        sig = (path, st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        sig = (path, None, None, None)
    if _pref_cache[0] != sig:
        _pref_cache = (sig, _code(load_preferences(path).get("language")) or "auto")
    return _pref_cache[1]


def language() -> str:
    """The language for text MixHub writes outside the browser (terminal messages, handoff bundles, notifications)."""
    forced = _code(env("LANGUAGE"))
    if forced:
        return forced
    pref = preference()
    if pref != "auto":
        return pref
    for value in (os.environ.get("LC_ALL"), os.environ.get("LC_MESSAGES"), os.environ.get("LANG")):
        if value:
            return _code(value) or DEFAULT
    return DEFAULT


def t(key: str, lang: str | None = None, **values: object) -> str:
    """The text for `key` in `lang` (default: `language()`), English when that language lacks it."""
    text = table(lang or language()).get(key) or table(DEFAULT).get(key) or key
    return text.format(**values) if values else text


def web_script() -> str:
    """`/i18n.js`: the web UI's strings for every language plus the preference; the page picks the language."""
    strings = {lang: {k: v for k, v in table(lang).items() if k.startswith("web.")} for lang in LANGUAGES}
    doc = {"preference": preference(), "default": DEFAULT, "strings": strings}
    return "window.MIXHUB_I18N = " + json.dumps(doc, ensure_ascii=False) + ";\n"


__all__ = ["DEFAULT", "LANGUAGES", "language", "preference", "t", "table", "web_script"]
