"""Session records and the in-memory session index (by key, id prefix, path and tool)."""
from __future__ import annotations

import json
import threading
import zlib


class SessionRecord(dict):
    """Fast in-memory session record with compatible commit() hook."""
    def __init__(self, owner=None, key=None, value=None):
        super().__init__(value or {})
        self.owner, self.key = owner, key

    def commit(self):
        if self.owner is not None and self.key is not None:
            self.owner.data[self.key] = self


class SessionIndex:
    """In-memory session metadata index (eliminates 38,000+ per-field zlib/tempfile writes)."""
    def __init__(self):
        self.data = {}
        self.lock = threading.RLock()

    def close(self):
        pass

    def write(self, key, payload):
        with self.lock:
            if isinstance(payload, (bytes, bytearray)):
                self.data[key] = SessionRecord(self, key, json.loads(zlib.decompress(payload)))
            elif isinstance(payload, SessionRecord):
                self.data[key] = payload
            else:
                self.data[key] = SessionRecord(self, key, payload)

    def __contains__(self, key):
        return key in self.data

    def __len__(self):
        return len(self.data)

    def __getitem__(self, key):
        with self.lock:
            return self.data[key]

    def get(self, key, default=None):
        with self.lock:
            rec = self.data.get(key)
            return rec if rec is not None else default

    def setdefault(self, key, value):
        with self.lock:
            rec = self.data.get(key)
            if rec is None:
                rec = SessionRecord(self, key, value)
                self.data[key] = rec
            return rec

    def pop(self, key, default=None):
        with self.lock:
            return self.data.pop(key, default)

    def items(self):
        with self.lock:
            return list(self.data.items())

    def values(self):
        with self.lock:
            return list(self.data.values())
