"""Persistent hub state: the compressed session-index disk cache, `hub_state.json`, and the Active working set."""
from __future__ import annotations

import json
import os
import time
import zlib

try:
    import fcntl
except ImportError:  # Windows native (win32) fallback
    fcntl = None

from mixhub.hub.constants import INDEX_CACHE_SCHEMA_VERSION
from mixhub.hub.text import snippet
from mixhub.hub.util import read_json


class WorkingSetMixin:
    """Disk cache load / save, hub state file, and Active working-set bookkeeping."""

    def _load_disk_cache(self):
        if self.readonly_cache:
            return
        try:
            if os.path.exists(self.disk_cache_path):
                with open(self.disk_cache_path, "rb") as f:
                    raw = zlib.decompress(f.read())
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("schema_version") == INDEX_CACHE_SCHEMA_VERSION:
                    for k, v in (data.get("entries") or {}).items():
                        if isinstance(v, list) and len(v) == 3 and isinstance(v[2], dict):
                            sig = (int(v[0]), int(v[1]))
                            self.cache[k] = (sig, v[2])
                    if data.get("subagent_links_v") == 1:
                        for k, v in (data.get("subagent_links") or {}).items():
                            if isinstance(k, str) and isinstance(v, list) and len(v) == 3 and isinstance(v[2], list):
                                try:
                                    sig = (int(v[0]), int(v[1]))
                                except (TypeError, ValueError, OverflowError):
                                    continue
                                self._subagent_links_cache[k] = (sig, [x for x in v[2] if isinstance(x, str) and x])
        except Exception:
            pass

    def _save_disk_cache(self):
        if self.readonly_cache or not self.cache_dirty:
            return
        try:
            os.makedirs(os.path.dirname(self.disk_cache_path), exist_ok=True)
            entries = {}
            for k, (sig, val) in self.cache.items():
                if isinstance(val, dict):
                    entries[k] = [sig[0], sig[1], val]
            subagent_links = {
                k: [sig[0], sig[1], list(child_ids)]
                for k, (sig, child_ids) in self._subagent_links_cache.items()
                if os.path.exists(k)
            }
            payload = zlib.compress(json.dumps({
                "schema_version": INDEX_CACHE_SCHEMA_VERSION, "entries": entries,
                "subagent_links_v": 1, "subagent_links": subagent_links,
            }, ensure_ascii=False).encode(), 1)
            tmp = self.disk_cache_path + ".tmp." + str(os.getpid())
            with open(tmp, "wb") as f:
                f.write(payload)
            os.replace(tmp, self.disk_cache_path)
            self.cache_dirty = False
        except Exception:
            pass

    def path(self, suffix):
        return os.path.join(self.home, suffix)

    def state(self):
        value = read_json(self.state_path, {"sessions": {}})
        if not isinstance(value, dict) or not isinstance(value.get("sessions"), dict):
            raise ValueError("hub_state.json must contain a sessions object")
        return value

    def working_set(self) -> set[str]:
        data = read_json(self.working_set_path, {"sessions": []})
        if isinstance(data, dict) and isinstance(data.get("sessions"), list):
            return {str(x) for x in data["sessions"] if x}
        if isinstance(data, list):
            return {str(x) for x in data if x}
        return set()

    def _save_working_set(self, keys: set[str]):
        try:
            os.makedirs(os.path.dirname(self.working_set_path) or ".", exist_ok=True)
            lock_path = f"{self.working_set_path}.lock"
            with open(lock_path, "a+", encoding="utf-8") as lock_f:
                if fcntl is not None:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
                try:
                    tmp = f"{self.working_set_path}.tmp.{os.getpid()}"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump({"sessions": sorted(keys), "updatedAt": time.time()}, f, ensure_ascii=False, indent=2)
                        f.write("\n")
                    os.replace(tmp, self.working_set_path)
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    def _remove_from_working_set(self, tool: str, sid: str):
        with self.lock:
            wset = self.working_set()
            before = len(wset)
            wset.discard(f"{tool}:{sid}")
            wset.discard(sid)
            if len(wset) != before:
                self._save_working_set(wset)
            key = f"{tool}:{sid}"
            if key in self.sessions:
                self.sessions[key]["inWorkingSet"] = False

    def save(self, key, updates):
        with self.lock:
            # Never overwrite a malformed state file with an empty fallback.
            if os.path.exists(self.state_path):
                with open(self.state_path, encoding="utf-8") as f:
                    value = json.load(f)
            else:
                value = {"sessions": {}}
            value.setdefault("sessions", {}).setdefault(key, {}).update(updates)
            temp = self.state_path + ".tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, self.state_path)
            if key in self.sessions:
                s = self.sessions[key]
                if "pinned" in updates:
                    s["pinned"] = bool(updates["pinned"])
                if "title" in updates:
                    if updates["title"]:
                        s["title"] = snippet(updates["title"], 200)
                    else:
                        self.scanned = 0
                for extra_k in (
                    "taskId",
                    "handoffTaskId",
                    "handoffChain",
                    "relayTools",
                    "handoffFrom",
                    "handoffTo",
                    "handoffMode",
                    "handoffProvider",
                    "handoffBundlePath",
                    "handoffYamlPath",
                    "controlMode",
                    "hibernatedByHub",
                ):
                    if extra_k in updates:
                        s[extra_k] = updates[extra_k]
                if "hibernatedAt" in updates:
                    self.scanned = 0
            else:
                self.scanned = 0
