#!/usr/bin/env python3
"""Seed a throwaway HOME with made-up agent sessions, for README screenshots and manual testing.

    python3 scripts/demo_seed.py ~/.cache/mixhub-demo             # write the sessions
    python3 scripts/demo_seed.py ~/.cache/mixhub-demo --web 8799  # ... and serve the web UI on them

Everything written is fictional: no real paths, names or transcripts. The directory must not be under /tmp (sessions
there are hidden as temporary). `--web` also hides the processes running on this machine, so live sessions of your
own don't show up next to the demo ones.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

NOW = time.time()
WORK = "/home/dev/work/"  # session working directories; they don't need to exist


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def demo_id(*parts):
    """The same id on every run, so seeding again replaces the demo sessions instead of adding copies."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "mixhub-demo/" + "/".join(parts)))


def write_rows(path, rows, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.utime(path, (mtime, mtime))


def claude_session(home, project, title, turns, age):
    """`turns`: [(prompt, [(tool, input, output), ...], reply)]."""
    sid = demo_id("claude", project, title)
    cwd = WORK + project
    start = NOW - age - 60 * len(turns)
    rows, ts = [], start

    def base(kind):
        nonlocal ts
        ts += 7
        return {"type": kind, "sessionId": sid, "cwd": cwd, "timestamp": iso(ts), "uuid": str(uuid.uuid4())}

    for prompt, tools, reply in turns:
        rows.append({**base("user"), "message": {"role": "user", "content": prompt}})
        for i, (name, args, output) in enumerate(tools):
            call_id = f"toolu_{uuid.uuid4().hex[:20]}"
            rows.append({**base("assistant"), "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [
                {"type": "tool_use", "id": call_id, "name": name, "input": args}]}})
            rows.append({**base("user"), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": output}]}})
        rows.append({**base("assistant"), "message": {"role": "assistant", "model": "claude-opus-5-5",
                                                      "content": [{"type": "text", "text": reply}]}})
    rows.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
    write_rows(home / ".claude/projects" / ("-work-" + project) / f"{sid}.jsonl", rows, NOW - age)
    return sid


def codex_session(home, project, title, prompt, reply, age):
    sid = demo_id("codex", project, title)
    cwd = WORK + project
    day = time.strftime("%Y/%m/%d", time.localtime(NOW - age))
    rows = [
        {"timestamp": iso(NOW - age - 300), "type": "session_meta",
         "payload": {"id": sid, "cwd": cwd, "timestamp": iso(NOW - age - 300)}},
        {"timestamp": iso(NOW - age - 290), "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}},
        {"timestamp": iso(NOW - age - 200), "type": "response_item",
         "payload": {"type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["rg", "-n", "TODO"]}),
                     "call_id": "call1"}},
        {"timestamp": iso(NOW - age - 190), "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "call1", "output": "src/cache.rs:41: // TODO evict"}},
        {"timestamp": iso(NOW - age), "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": reply}]}},
        {"timestamp": iso(NOW - age), "type": "event_msg",
         "payload": {"type": "token_count", "rate_limits": {
             "primary": {"used_percent": 31.0, "window_minutes": 300, "resets_at": int(NOW + 2.4 * 3600)},
             "secondary": {"used_percent": 22.0, "window_minutes": 10080, "resets_at": int(NOW + 4.2 * 86400)}}}},
    ]
    for old in (home / ".codex/sessions").rglob(f"rollout-*-{sid}.jsonl"):  # its date in the name moves with NOW
        old.unlink()
    write_rows(home / ".codex/sessions" / day / f"rollout-{time.strftime('%Y-%m-%dT%H-%M-%S', time.localtime(NOW - age))}-{sid}.jsonl", rows,
               NOW - age)
    db = sqlite3.connect(home / ".codex/state_5.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS threads(id TEXT, cwd TEXT, title TEXT, first_user_message TEXT)")
    db.execute("DELETE FROM threads WHERE id = ?", (sid,))
    db.execute("INSERT INTO threads VALUES(?,?,?,?)", (sid, cwd, title, prompt))
    db.commit()
    db.close()
    return sid


def opencode_session(home, project, title, prompt, reply, age):
    path = home / ".local/share/opencode/opencode.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS session_v2 (id text PRIMARY KEY, project_id text NOT NULL, parent_id text,
          slug text NOT NULL, directory text NOT NULL, title text, version text NOT NULL, agent text, model text,
          time_created integer NOT NULL, time_updated integer NOT NULL, time_idle integer, time_viewed integer,
          time_archived integer);
        CREATE TABLE IF NOT EXISTS session_message (id text PRIMARY KEY, session_id text NOT NULL, type text NOT NULL,
          seq integer NOT NULL, time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL);
        """
    )
    sid = "ses_" + demo_id("opencode", project, title).replace("-", "")[:24]
    db.execute("DELETE FROM session_message WHERE session_id = ?", (sid,))
    created, updated = int((NOW - age - 600) * 1000), int((NOW - age) * 1000)
    db.execute("INSERT OR REPLACE INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, "p", None, "demo", WORK + project, title, "2", "build", None, created, updated,
                updated, updated, None))
    for seq, (kind, data) in enumerate([("user", {"text": prompt}),
                                        ("assistant", {"content": [{"type": "text", "text": reply}]}),
                                        ("idle", {"outcome": "succeeded"})]):
        data = dict(data, time={"created": created + seq})
        db.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                   (f"{sid}-{seq}", sid, kind, seq, created + seq, created + seq, json.dumps(data)))
    db.commit()
    db.close()
    return sid


def agy_session(home, project, title, turns, age):
    """A Gemini (AGY) conversation: its summary row plus the step transcript. `turns` as in `claude_session`."""
    sid = demo_id("agy", project, title)
    cwd = WORK + project
    rows, step, ts = [], 0, NOW - age - 60 * len(turns)

    def at(t):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    for prompt, tools, reply in turns:
        rows.append({"step_index": step, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
                     "created_at": at(ts), "content": f"<USER_REQUEST>{prompt}</USER_REQUEST>"})
        step += 1
        ts += 10
        if tools:
            rows.append({"step_index": step, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": at(ts),
                         "tool_calls": [{"id": f"c{step}-{n}", "name": name, "args": args}
                                        for n, (name, args, _) in enumerate(tools)]})
            step += 1
            for _, _, output in tools:
                rows.append({"step_index": step, "type": "GENERIC", "status": "DONE", "created_at": at(ts),
                             "content": output})
                step += 1
        ts += 20
        rows.append({"step_index": step, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": at(ts),
                     "content": reply})
        step += 1
    write_rows(home / ".gemini/antigravity-cli/brain" / sid / ".system_generated/logs/transcript.jsonl", rows,
               NOW - age)
    db = sqlite3.connect(home / ".gemini/antigravity-cli/conversation_summaries.db")
    db.execute("CREATE TABLE IF NOT EXISTS conversation_summaries(conversation_id TEXT, title TEXT, preview TEXT, "
               "step_count INTEGER, last_modified_time TEXT, workspace_uris TEXT, status TEXT, not_fully_idle INTEGER, "
               "killed INTEGER, agent_name TEXT, parent_conversation_id TEXT, nesting_depth INTEGER, "
               "last_user_input_time TEXT, last_user_input_step_index INTEGER)")
    db.execute("DELETE FROM conversation_summaries WHERE conversation_id = ?", (sid,))
    db.execute("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, title, turns[-1][0], step, time.strftime("%Y-%m-%d %H:%M:%S+00:00", time.gmtime(NOW - age)),
                json.dumps([f"file://{cwd}"]), "CASCADE_RUN_STATUS_IDLE", 0, 0, "", "", 0,
                at(NOW - age - 30), max(0, step - 3)))
    db.commit()
    db.close()
    return sid


def write_quota(home):
    """Made-up quota in the files `mixhub statusline` writes, so the footer doesn't show this machine's real numbers."""
    data = home / "state/data"
    data.mkdir(parents=True, exist_ok=True)

    def window(remaining, reset):
        return {"remaining_fraction": remaining, "reset_time": reset, "observed_at": NOW, "started": True}

    (data / "claude-statusline.json").write_text(json.dumps({"quota": {
        "claude-5h": window(0.64, int(NOW + 3.1 * 3600)), "claude-weekly": window(0.71, int(NOW + 5.3 * 86400)),
    }, "updated_at": NOW}))
    (data / "agy-statusline.json").write_text(json.dumps({"quota": {
        "gemini-5h": window(0.88, iso(NOW + 1.6 * 3600)), "gemini-weekly": window(0.57, iso(NOW + 2.5 * 86400)),
    }}))


def demo_env(home):
    """`--web` environment: the demo HOME and state, and only the demo's quota files (no `/tmp` fallbacks)."""
    return {"HOME": str(home), "MIXHUB_HOME": str(home / "state"),
            "MIXHUB_STATUSLINE_JSON": str(home / "state/data/agy-statusline.json")}


def main(target):
    home = Path(target).resolve()
    (home / "state").mkdir(parents=True, exist_ok=True)
    (home / "state/Preferences").write_text(json.dumps({"host_label": "dev-laptop", "user_name": "Developer"}))

    webhooks = claude_session(home, "shop-api", "Payment webhook retries", [
        ("Stripe webhooks are processed twice when the worker restarts. Make the handler idempotent.",
         [("Grep", {"pattern": "handle_webhook", "path": "src"}, "src/payments/webhook.py:18"),
          ("Read", {"file_path": "src/payments/webhook.py"}, "def handle_webhook(event): ..."),
          ("Edit", {"file_path": "src/payments/webhook.py"}, "ok"),
          ("Bash", {"command": "pytest -q tests/payments"}, "14 passed in 0.61s")],
         "The handler now records `event.id` in a `processed_events` table inside the same transaction, so a "
         "redelivered event is acknowledged without being applied again. Added two tests for the restart case."),
        ("Does this hold if two workers get the same event at once?",
         [("Read", {"file_path": "migrations/0042_processed_events.sql"}, "PRIMARY KEY (event_id)")],
         "Yes. `event_id` is the primary key, so the second insert fails and that worker rolls back and returns 200."),
    ], age=90)
    claude_session(home, "web-dashboard", "Chart tooltips on mobile", [
        ("Tooltips on the revenue chart get cut off on narrow screens.",
         [("Read", {"file_path": "src/charts/Revenue.tsx"}, "..."),
          ("Edit", {"file_path": "src/charts/Tooltip.tsx"}, "ok"),
          ("Bash", {"command": "npm test -- Tooltip"}, "PASS  src/charts/Tooltip.test.tsx")],
         "Tooltips now flip to the left half when they would overflow, and stay inside the chart on touch."),
    ], age=60 * 25)
    claude_session(home, "infra", "Terraform state lock timeout", [  # seven prompts, for the preview's minimap
        ("`terraform apply` hangs on acquiring the state lock in CI.",
         [("Bash", {"command": "terraform force-unlock -help"}, "Usage: terraform force-unlock LOCK_ID")],
         "A cancelled job left the DynamoDB lock behind. Before touching it: which job held it, and is it still "
         "running? The lock's `Info` field names the run."),
        ("It's from run 4812, which was cancelled an hour ago.",
         [("Bash", {"command": "aws dynamodb get-item --table-name tf-locks --key file://key.json"},
           '{"Item": {"Info": {"S": "run-4812"}}}')],
         "Then it's safe to remove. I'll unlock it by ID rather than deleting the item, so Terraform logs it."),
        ("Go ahead.",
         [("Bash", {"command": "terraform force-unlock -force 6f1c0d2e"}, "Terraform state has been unlocked!")],
         "Unlocked. The next apply in CI should start normally."),
        ("How do we stop this from happening again?", [],
         "Two changes: give the CI step `-lock-timeout=5m` so a short overlap waits instead of failing, and add a "
         "nightly job that unlocks only locks older than an hour whose run is no longer active."),
        ("Write the cleanup job.",
         [("Write", {"file_path": ".github/workflows/tf-lock-cleanup.yml"}, "ok"),
          ("Write", {"file_path": "scripts/stale_locks.py"}, "ok")],
         "Added `tf-lock-cleanup.yml` (nightly at 03:00 UTC) and `stale_locks.py`, which checks each lock's run "
         "through the CI API before unlocking it."),
        ("Can it run in dry-run mode first?",
         [("Edit", {"file_path": "scripts/stale_locks.py"}, "ok"),
          ("Bash", {"command": "python scripts/stale_locks.py --dry-run"}, "would unlock: none (0 stale)")],
         "Yes: `--dry-run` lists what it would unlock and exits. The workflow runs it that way for the first week."),
        ("Add `-lock-timeout` to the apply step too.",
         [("Edit", {"file_path": ".github/workflows/deploy.yml"}, "ok")],
         "Done. Both `plan` and `apply` now wait up to five minutes for the lock."),
    ], age=3600 * 3)
    claude_session(home, "docs-site", "Migrate docs to MDX", [
        ("Plan the move from Markdown to MDX without breaking existing links.", [],
         "Plan: keep slugs, add redirects for the two renamed sections, convert pages in batches of ten."),
    ], age=3600 * 28)
    images = codex_session(home, "mobile-app", "Image cache eviction", "Images in the feed never leave the disk cache.",
                  "Eviction now runs on launch and when the cache passes 200 MB, oldest first.", age=60 * 8)
    codex_session(home, "shop-api", "Review: refund endpoint", "Review the refund endpoint PR for edge cases.",
                  "Two issues: partial refunds can exceed the captured amount, and the currency isn't checked.",
                  age=3600 * 6)
    codex_session(home, "public-api", "Rate limiter for public API", "Add per-key rate limiting to the public API.",
                  "Added a token bucket per API key (100 requests/min, burst 20) with `429` and `Retry-After`.",
                  age=3600 * 9)
    flaky = agy_session(home, "web-dashboard", "Flaky login e2e test", [
        ("The login e2e test fails about one run in ten. Find out why.",
         [("run_command", {"command": "npx playwright test login.spec.ts --repeat-each=20"}, "18 passed, 2 failed"),
          ("view_file", {"path": "tests/login.spec.ts"}, "await page.click('#submit')")],
         "The test clicks Submit before the form's validation script has loaded, so the click is sometimes lost. "
         "Waiting for the button to be enabled fixes it."),
    ], age=60 * 18)
    agy_session(home, "ci", "Bump Node to 22 in CI", [
        ("Move CI from Node 20 to 22.",
         [("replace_file_content", {"path": ".github/workflows/ci.yml"}, "ok")],
         "CI now uses Node 22; `.nvmrc` and the engines field are updated to match."),
    ], age=3600 * 30)
    tokens = opencode_session(home, "web-dashboard", "Dark mode tokens", "Move the dashboard colors to CSS variables.",
                     "Done: 23 colors now come from `--color-*` tokens with a dark set under `[data-theme=dark]`.",
                     age=60 * 40)
    write_quota(home)
    # One session per agent in Active (the rest in Recent), and a workspace shell prompt that names no user or host
    (home / "state/active_working_set.json").write_text(json.dumps(
        {"sessions": [f"claude:{webhooks}", f"codex:{images}", f"agy:{flaky}", f"opencode:{tokens}"], "updatedAt": NOW}))
    (home / ".zshrc").write_text("PROMPT='%F{8}%1~%f %# '\n")
    return home


def serve(home, port):
    os.environ.update(demo_env(home), MIXHUB_PORT=str(port))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mixhub.hub.hub import Hub
    from mixhub.web.server import main as web_main

    Hub.processes = lambda self: []
    Hub.open_files = lambda self, pids: {}
    web_main()


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args.pop(args.index("--web") + 1)) if "--web" in args else 0
    args = [a for a in args if a != "--web"]
    home = main(args[0] if args else os.path.expanduser("~/.cache/mixhub-demo"))
    print(home)
    if port:
        serve(home, port)
