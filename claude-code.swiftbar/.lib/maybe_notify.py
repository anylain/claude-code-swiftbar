#!/usr/bin/env python3
# Decide whether the latest status write deserves a macOS notification, and
# fire one via swiftbar://notify if so.
#
# Triggered from cc-status-writer right after write_status.py succeeds.
# Argv: maybe_notify.py <event-json-path>
# We read transcript_path from the hook event JSON, derive project_dir from it,
# then read state / detail / sid from <project_dir>/.cc-status.json (which
# write_status.py just wrote). This keeps the two scripts decoupled.
#
# Rules:
#   1. CC_NOTIFY != "0" (env, default on)
#   2. state ∈ {needs-permission, needs-decision}
#   3. last notified (sid, state) is different from current (suppress dupes
#      from clustered hook events)
#   4. session host app is not currently frontmost AND its window/tab does
#      not own the focused process — i.e. user is somewhere else and won't
#      see the in-app prompt anyway
#
# Failures are swallowed with sys.exit(0); this script must never block the
# hook caller.
import json
import os
import subprocess
import sys

NOTIFY_STATES = {"needs-permission", "needs-decision"}
STATUS_FILE_NAME = ".cc-status.json"
DEDUPE_FILE_NAME = ".cc-notify-last"  # stores last "sid|state"
OSASCRIPT_TIMEOUT = 0.6  # seconds; on timeout we treat host as NOT foreground


def main():
    if os.environ.get("CC_NOTIFY", "1") == "0":
        return

    if len(sys.argv) < 2:
        return
    event_path = sys.argv[1]
    try:
        with open(event_path) as f:
            event = json.load(f)
    except (OSError, ValueError):
        return

    transcript_path = event.get("transcript_path", "")
    if not transcript_path:
        return
    proj_dir = os.path.dirname(transcript_path)
    if not proj_dir:
        return

    status_path = os.path.join(proj_dir, STATUS_FILE_NAME)
    try:
        with open(status_path) as f:
            status = json.load(f)
    except (OSError, ValueError):
        return

    state = status.get("state", "")
    detail = status.get("detail", "")
    sid = status.get("sid", "")

    if state not in NOTIFY_STATES:
        return

    # Dedupe: same (sid, state) within this session → silent. We only want
    # the first hook in a cluster (e.g. PreToolUse(AskUserQuestion) +
    # PermissionRequest both write needs-decision back-to-back).
    dedupe_path = os.path.join(proj_dir, DEDUPE_FILE_NAME)
    last_key = ""
    try:
        with open(dedupe_path) as f:
            last_key = f.read().strip()
    except OSError:
        pass
    cur_key = f"{sid}|{state}"
    if cur_key == last_key:
        return
    try:
        with open(dedupe_path, "w") as f:
            f.write(cur_key)
    except OSError:
        pass

    host = _host_from_transcript(transcript_path)

    if _is_host_foreground(host, proj_dir):
        return

    proj_name = os.path.basename(proj_dir.lstrip("-").replace("-", "/")) or "Claude"
    title = "Claude Code"
    if state == "needs-permission":
        subtitle = f"{proj_name} · 等待授权"
    else:
        subtitle = f"{proj_name} · 等待决策"
    body = (detail or "")[:120]

    _fire_notification(title, subtitle, body)


def _host_from_transcript(jsonl_path):
    try:
        with open(jsonl_path) as fh:
            for _ in range(200):
                line = fh.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                    ep = d.get("entrypoint", "")
                    if ep:
                        if "vscode" in ep:
                            return "vscode"
                        if "jetbrains" in ep or "intellij" in ep:
                            return "jetbrains"
                        if ep == "cli":
                            return "iterm"  # best-effort; could be Terminal/Warp
                        return ""
                except Exception:
                    pass
    except OSError:
        pass
    return ""


# AppleScript: returns "1" if the host owns the focused window/tab, "0"
# otherwise. Per host we check that (a) it's the frontmost app AND (b) its
# active document/tab cwd matches the session's project_dir. The "tab"
# match is only attempted for iTerm; VSCode/JetBrains lack a stable AS API
# for the focused project path, so for those we settle for "frontmost app".
ITERM_AS = """
on run argv
    set targetCwd to item 1 of argv
    tell application "System Events"
        if not (exists (process "iTerm2")) then return "0"
        if not (frontmost of process "iTerm2") then return "0"
    end tell
    tell application "iTerm2"
        try
            set tt to current tab of current window
            set ss to current session of tt
            set sCwd to (variable named "session.path") of ss
            if sCwd starts with targetCwd then return "1"
        end try
    end tell
    return "0"
end run
"""

VSCODE_AS = """
tell application "System Events"
    if not (exists (process "Code")) then return "0"
    if frontmost of process "Code" then return "1"
end tell
return "0"
"""

JETBRAINS_AS = """
tell application "System Events"
    repeat with p in processes
        if frontmost of p is true then
            set n to name of p
            if n contains "IntelliJ" or n contains "PyCharm" or n contains "WebStorm" or n contains "GoLand" or n contains "RubyMine" or n contains "CLion" or n contains "PhpStorm" or n contains "Rider" or n contains "DataGrip" or n contains "RustRover" or n contains "AppCode" or n contains "Aqua" or n contains "DataSpell" or n contains "Android Studio" or n contains "Fleet" then
                return "1"
            end if
            return "0"
        end if
    end repeat
end tell
return "0"
"""


def _run_osascript(script, args=None):
    cmd = ["/usr/bin/osascript", "-e", script]
    if args:
        cmd.extend(args)
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=OSASCRIPT_TIMEOUT,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _is_host_foreground(host, proj_dir):
    if host == "iterm":
        return _run_osascript(ITERM_AS, [proj_dir]) == "1"
    if host == "vscode":
        return _run_osascript(VSCODE_AS) == "1"
    if host == "jetbrains":
        return _run_osascript(JETBRAINS_AS) == "1"
    return False


def _fire_notification(title, subtitle, body):
    # SwiftBar URL scheme: swiftbar://notify?plugin=<name>&title=&subtitle=&body=
    # We fire the same dual-name pair as cc-status-writer's refresh URL so it
    # works on both v2.0.1 and post-packaged-plugin SwiftBar.
    from urllib.parse import urlencode

    base_params = {
        "title": title,
        "subtitle": subtitle,
        "body": body,
    }
    for plugin_name in ("plugin.10s.sh", "claude-code"):
        params = dict(base_params, plugin=plugin_name)
        url = "swiftbar://notify?" + urlencode(params)
        try:
            subprocess.Popen(
                ["/usr/bin/open", "-g", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


try:
    main()
except Exception:
    pass
sys.exit(0)
