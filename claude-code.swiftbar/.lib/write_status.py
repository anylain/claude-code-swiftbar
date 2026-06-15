#!/usr/bin/env python3
# Reads hook event JSON from argv[1], writes status to
# <project_dir>/.cc-status.json based on hook_event_name.
# argv: <event-json-path> <home-dir>
import json
import os
import sys
import time

tmp_file = sys.argv[1]
home = sys.argv[2]
projects_dir = os.path.join(home, ".claude", "projects")

try:
    with open(tmp_file) as f:
        event = json.load(f)
except Exception:
    sys.exit(0)

event_name = event.get("hook_event_name", "")
transcript_path = event.get("transcript_path", "")
session_id = event.get("session_id", "")

if not transcript_path:
    sys.exit(0)

proj_dir = os.path.dirname(transcript_path)
status_file = os.path.join(proj_dir, ".cc-status.json")


def tool_detail(tool_name, tool_input):
    if not tool_input:
        return tool_name
    inp = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Bash":
        return inp.get("description") or (inp.get("command") or "")[:60]
    if tool_name in ("Edit", "Write", "Read", "NotebookEdit"):
        return inp.get("file_path") or inp.get("notebook_path") or tool_name
    if tool_name == "TaskCreate":
        return inp.get("subject") or tool_name
    if tool_name in ("Grep", "Glob"):
        return inp.get("pattern") or tool_name
    if tool_name == "AskUserQuestion":
        # input.questions[0].question is the human-readable prompt
        qs = inp.get("questions") or []
        if qs and isinstance(qs[0], dict):
            return (qs[0].get("question") or "")[:80] or tool_name
        return tool_name
    if tool_name == "ExitPlanMode":
        return "review plan"
    return (inp.get("description") or inp.get("query") or tool_name)[:60]


# Tools whose "tool_use" semantically means "waiting on user to decide", not
# "waiting on user to authorize a side effect". Bash/Edit/Write etc. trigger
# Claude Code's permission dialog → needs-permission. AskUserQuestion and
# ExitPlanMode block the turn waiting for a user reply / plan approval — that
# is a decision, not a security gate.
DECISION_TOOLS = {"AskUserQuestion", "ExitPlanMode"}

HISTORY_LOG = "/tmp/cc-status-history.log"
HISTORY_MAX_BYTES = 1_000_000  # ~1MB cap; truncate when exceeded
# Opt-in: only write history log if env var set OR sentinel file exists.
# Avoids wasting disk on regular users — diagnostic-only feature.
HISTORY_ENABLED = os.environ.get("CC_STATUS_HISTORY") == "1" or os.path.exists("/tmp/cc-status-history.enabled")


def _append_history(ev_name, state, detail):
    # DEBUG (TEMPORARY): timeline of every status write/clear so we can find
    # which event overwrites needs-input back to running. Format:
    #   <ts>  <iso-time>  <event_name>  <sid8>  <state>  <detail>
    if not HISTORY_ENABLED:
        return
    try:
        if os.path.getsize(HISTORY_LOG) > HISTORY_MAX_BYTES:
            os.remove(HISTORY_LOG)
    except (FileNotFoundError, OSError):
        pass
    try:
        ts = int(time.time())
        iso = time.strftime("%FT%T", time.localtime(ts))
        sid8 = (session_id or "")[:8]
        line = f"{ts}\t{iso}\t{ev_name or '-'}\t{sid8}\t{state}\t{(detail or '')[:60]}\n"
        with open(HISTORY_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


def write_status(state, detail="", ev_name=""):
    ts = int(time.time())
    payload = json.dumps({"state": state, "detail": detail, "ts": ts, "sid": session_id})
    try:
        with open(status_file, "w") as f:
            f.write(payload)
    except Exception:
        pass
    _append_history(ev_name, state, detail)


def clear_status(ev_name=""):
    try:
        os.remove(status_file)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    _append_history(ev_name, "<cleared>", "")


if event_name == "SessionEnd":
    # Write a tombstone instead of deleting. The plugin treats `state=ended`
    # as "session over, drop from UI immediately" — without it, the plugin's
    # JSONL heuristics keep the session marked alive for ALIVE_SECS=120s after
    # the last write, which makes `claude` exit not reflect in the menu bar.
    # The tombstone has no TTL on the writer side; the plugin enforces its own.
    write_status("ended", "session ended", ev_name=event_name)

elif event_name == "SessionStart":
    write_status("needs-input", "awaiting your input", ev_name=event_name)

elif event_name == "UserPromptSubmit":
    write_status("running", "Claude is responding…", ev_name=event_name)

elif event_name == "PreToolUse":
    name = event.get("tool_name", "?")
    detail = tool_detail(name, event.get("tool_input", {}))
    if name in DECISION_TOOLS:
        # Decision tools block the turn the moment they're invoked, no
        # PermissionRequest fires. Mark needs-decision immediately so the
        # menu bar reflects "waiting on you" without 1s+ of "running".
        write_status("needs-decision", detail, ev_name=event_name)
    else:
        write_status("running", f"using {name}: {detail}", ev_name=event_name)

elif event_name == "PostToolUse":
    name = event.get("tool_name", "?")
    write_status("running", f"finished {name}", ev_name=event_name)

elif event_name == "PostToolBatch":
    write_status("running", "processing results…", ev_name=event_name)

elif event_name == "PermissionRequest":
    name = event.get("tool_name", "?")
    detail = tool_detail(name, event.get("tool_input", {}))
    if name in DECISION_TOOLS:
        write_status("needs-decision", detail, ev_name=event_name)
    else:
        write_status("needs-permission", f"approve {name}: {detail}", ev_name=event_name)

elif event_name == "PermissionDenied":
    write_status("running", "permission denied, continuing…", ev_name=event_name)

elif event_name == "Stop":
    # Note: Claude Code's Stop hook payload does NOT carry `stop_reason` —
    # the field exists on assistant jsonl entries but isn't propagated to
    # the hook event. So we infer turn end from `tool_calls` alone:
    # empty list → turn fully ended (needs your input);
    # non-empty → tools queued, still running.
    tool_calls = event.get("tool_calls", []) or []
    if not tool_calls:
        write_status("needs-input", "awaiting your input", ev_name=event_name)
    else:
        tc_names = [tc.get("name", "?") for tc in tool_calls[:3]]
        write_status("running", f"executing {', '.join(tc_names)}…", ev_name=event_name)

elif event_name == "StopFailure":
    err_type = event.get("error_type", "")
    err_msg = (event.get("error_message", "") or "")[:80]
    write_status("error", f"{err_type}: {err_msg}" if err_type else err_msg or "unknown error", ev_name=event_name)

elif event_name == "PreCompact":
    write_status("running", "compacting context…", ev_name=event_name)

elif event_name == "PostCompact":
    write_status("running", "processing after compaction…", ev_name=event_name)

# Other events are intentionally ignored.
