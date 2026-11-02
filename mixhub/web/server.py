"""HTTP daemon entry point: bind `127.0.0.1:$MIXHUB_PORT`, detect an already-running hub, serve `Handler`."""
from __future__ import annotations

import errno
import http.server
import json
import os
import sys
import urllib.request

from mixhub.core.env import env
from mixhub.hub.hub import Hub
from mixhub.web.handler import Handler


def _port_owner_is_hub(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
            return json.load(response).get("service") in ("agent-session-hub", "mixhub")
    except (OSError, ValueError, AttributeError):
        return False


def main():
    port = int(env("PORT", "8765"))
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        if _port_owner_is_hub(port):
            print(f"MixHub is already running at http://127.0.0.1:{port}", file=sys.stderr)
            sys.exit(0)
        print(f"Port {port} is used by another program. Set MIXHUB_PORT to a free port.", file=sys.stderr)
        sys.exit(2)
    server.daemon_threads = True
    server.hub = Hub(home=env("AGENT_HOME"))
    print("MixHub (Agent Session Hub) http://127.0.0.1:%d" % server.server_port, flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
