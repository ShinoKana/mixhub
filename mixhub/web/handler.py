"""Loopback HTTP API: same-origin checks, JSON responses, static files, and the `/api/*` routes over `Hub`."""
from __future__ import annotations

import http.server
import json
import os
import re
import subprocess
import urllib.parse

from mixhub.core import paths
from mixhub.hub.constants import TOOLS
from mixhub.hub.shell import run
from mixhub.hub.util import clean_hostname


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "AgentSessionHub/1"

    def log_message(self, fmt, *args):
        pass  # URLs and session names stay out of logs.

    def allowed(self, write=False):
        host = self.headers.get("Host", "")
        expected = {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}
        if host not in expected:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in expected}:
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            # A link from another site (chat, notification) may open the page itself; it can't read the response,
            # and everything the page then loads is same-origin.
            page = urllib.parse.urlsplit(getattr(self, "path", "")).path in ("/", "/index.html")
            return page and not write and self.headers.get("Sec-Fetch-Mode") == "navigate"
        return not write or self.headers.get("Content-Type", "").split(";")[0] == "application/json"

    def response_headers(self, status=200, content_type="application/json; charset=utf-8", length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def send_json(self, value, status=200):
        self.response_headers(status)
        # Avoid a second multi-megabyte string/bytes copy for the session index.
        parts, size = [], 0
        for part in json.JSONEncoder(ensure_ascii=False).iterencode(value):
            parts.append(part)
            size += len(part)
            if size > 16384:
                self.wfile.write("".join(parts).encode())
                parts, size = [], 0
        if parts:
            self.wfile.write("".join(parts).encode())

    def do_GET(self):
        try:
            if not self.allowed():
                return self.send_json({"error": "Local same-origin access required"}, 403)
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/api/health":
                try:
                    rss_kb = int(run(["ps", "-o", "rss=", "-p", str(os.getpid())], timeout=1).strip() or 0)
                    rss_mb = round(rss_kb / 1024, 1)
                except Exception:
                    rss_mb = 0.0
                return self.send_json({"ok": True, "service": "agent-session-hub", "brand": "MixHub", "hostname": clean_hostname(), "pid": os.getpid(), "rss_mb": rss_mb})
            if url.path == "/api/channels/status":
                return self.send_json(self.server.hub.channel_manager().status())
            if url.path == "/api/channels/hooks-config":
                return self.send_json(self.server.hub.channels_hooks_config())
            if url.path == "/api/quota":
                from mixhub.tui.quota import get_cli_quota_metrics
                return self.send_json({"items": get_cli_quota_metrics()})
            if url.path == "/api/doctor":
                return self.send_json(self.server.hub.doctor())
            handoff_get = re.fullmatch(r"/api/handoff/([^/]+)", url.path)
            if handoff_get:
                return self.send_json(self.server.hub.get_handoff(urllib.parse.unquote(handoff_get[1])))
            if url.path == "/api/sessions":
                data = self.server.hub.listing(urllib.parse.parse_qs(url.query))
                records = data.pop("sessions")
                self.response_headers()
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode()[:-1] + b',"sessions":[')
                for n, record in enumerate(records):
                    self.wfile.write((b"," if n else b"") + json.dumps(record, ensure_ascii=False).encode())
                self.wfile.write(b"]}")
                return
            match = re.fullmatch(r"/api/sessions/(" + "|".join(TOOLS) + r")/([^/]+)", url.path)
            if match:
                session = self.server.hub.get(match[1], urllib.parse.unquote(match[2]))
                self.response_headers()
                self.wfile.write(b'{"session":' + json.dumps(self.server.hub.public(session), ensure_ascii=False).encode() + b',"messages":[')
                count = 0
                for item in self.server.hub.transcript(session):
                    data = json.dumps(item, ensure_ascii=False).encode()
                    self.wfile.write((b"," if count else b"") + data)
                    count += 1
                self.wfile.write(b'],"messageCount":' + str(count).encode() + b'}')
                return
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
            if url.path == "/i18n.js":
                from mixhub.i18n import web_script

                data = web_script().encode()
                self.response_headers(content_type="text/javascript; charset=utf-8", length=len(data))
                self.wfile.write(data)
                return
            if url.path == "/favicon.ico":
                self.response_headers(204, length=0)
                return
            if url.path in static:
                name, mime = static[url.path]
                with open(os.path.join(paths.WEB_STATIC_DIR, name), "rb") as f:
                    data = f.read()
                self.response_headers(content_type=mime, length=len(data))
                self.wfile.write(data)
                return
            self.send_json({"error": "Not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except KeyError as exc:
            self.send_json({"error": str(exc)}, 404)
        except (OSError, ValueError, RuntimeError) as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self):
        try:
            if not self.allowed(write=True):
                return self.send_json({"error": "Local JSON requests only"}, 403)
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16384:
                return self.send_json({"error": "Request too large"}, 413)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object")
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/hibernate-idle":
                return self.send_json(self.server.hub.hibernate_idle(body))
            if path == "/api/handoff":
                return self.send_json(self.server.hub.handoff(body))
            if path == "/api/sync-rules":
                return self.send_json(self.server.hub.sync_rules(body))
            if path == "/api/channels/notify":
                return self.send_json(self.server.hub.channels_notify(body))
            match = re.fullmatch(r"/api/sessions/(" + "|".join(TOOLS) + r")/([^/]+)/(focus|hibernate|resume|rename|pin|metadata|handoff|send)", path)
            if not match:
                raise KeyError("Not found")
            self.send_json(self.server.hub.action(match[1], urllib.parse.unquote(match[2]), match[3], body))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except KeyError as exc:
            self.send_json({"error": str(exc)}, 404)
        except (ValueError, TypeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.send_json({"error": str(exc)}, 500)
