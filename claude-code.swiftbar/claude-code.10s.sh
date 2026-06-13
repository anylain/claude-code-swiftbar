#!/bin/bash
# <bitbar.title>Claude Code Status</bitbar.title>
# <bitbar.version>v2.4</bitbar.version>
# <bitbar.author>PanYing</bitbar.author>
# <bitbar.author.github>anylain</bitbar.author.github>
# <bitbar.desc>Realtime Claude Code task/session status with click-to-jump</bitbar.desc>
# <bitbar.dependencies>bash,python3</bitbar.dependencies>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
# <swiftbar.useTrailingStreamSeparator>true</swiftbar.useTrailingStreamSeparator>

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

PROJECTS_DIR="$HOME/.claude/projects"
# Resource dirs are dot-prefixed so SwiftBar 2.0.1 (no packaged-plugin support yet)
# skips them during plugin discovery.
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
JUMP_BIN="$PKG_DIR/.bin/cc-jump"
ICON_DIR="$PKG_DIR/.assets/icons"

/usr/bin/python3 - "$PROJECTS_DIR" "$JUMP_BIN" "$ICON_DIR" <<'PY'
import os, sys, json, time, glob, subprocess
from datetime import datetime

projects_dir = sys.argv[1]
jump_bin = sys.argv[2]
icon_dir = sys.argv[3]
now = time.time()

def load_icon(name):
    p = os.path.join(icon_dir, f"{name}.b64")
    try:
        with open(p) as f:
            return f.read().strip()
    except Exception:
        return ""

ICON_B64 = {
    "iterm": load_icon("iterm"),
    "idea": load_icon("idea"),
    "vscode": load_icon("vscode"),
}

CC_APP_B64 = load_icon("cc-app")

RUNNING_SECS = 30
IDLE_SECS = 5
ALIVE_SECS = 120  # jsonl modified within 2 min => session is "alive"
THINK_GRACE = 180  # alive proc + no child + jsonl silent < THINK_GRACE => still thinking
HOOK_STATUS_TTL = 60  # hook-written .cc-status.json valid for 60s before fallback to classify
# claude only forks ONE kind of child synchronously when running a tool: a shell
# for the Bash tool. Every other tool (Read/Edit/Write/Grep/Glob/...) runs in-process.
# So a "real tool is running" signal = direct child whose comm is the user's shell.
# Everything else (MCP servers, LSP servers, caffeinate, telemetry watchdogs) is a
# long-lived helper that may restart mid-session — those must NOT count as "running".
# Whitelist is more robust than blacklisting helper patterns: LSP servers spawn as
# bare `node` and would slip through any blacklist.
TOOL_CHILD_COMMS = {"/bin/zsh", "/bin/bash", "/bin/sh", "zsh", "bash", "sh"}

def read_last_entries(path, n=20):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 8192
            data = b""
            pos = size
            while pos > 0 and data.count(b"\n") < n + 1:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
        lines = [l for l in data.splitlines() if l.strip()]
        out = []
        for l in lines[-n:]:
            try:
                out.append(json.loads(l))
            except Exception:
                pass
        return out
    except Exception:
        return []

def read_first_cwd(path):
    """Read jsonl entries until we find a 'cwd' field — that's the session's true cwd."""
    try:
        with open(path, "r") as f:
            for _ in range(50):
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                    if d.get("cwd"):
                        return d["cwd"]
                except Exception:
                    pass
    except Exception:
        pass
    return None

def read_entrypoint(path):
    """Read jsonl until we find the entrypoint field — authoritative IDE marker.
    Known values: 'cli' (terminal), 'claude-vscode', 'claude-jetbrains' (likely)."""
    try:
        with open(path, "r") as f:
            for _ in range(200):
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                    if d.get("entrypoint"):
                        return d["entrypoint"]
                except Exception:
                    pass
    except Exception:
        pass
    return None

def host_from_entrypoint(ep):
    """Authoritative ONLY for IDE-plugin entrypoints.
    'cli' is ambiguous — could be iTerm, Terminal, IDEA built-in terminal,
    VSCode built-in terminal, etc. Return None for cli so caller falls back
    to process parent-chain inspection."""
    if not ep:
        return None
    ep = ep.lower()
    if "vscode" in ep:
        return "vscode"
    if "jetbrains" in ep or "intellij" in ep or "idea" in ep:
        return "idea"
    return None  # cli or unknown — defer to process inspection

def read_hook_status(pdir):
    """Read hook-written status file. Returns (state, detail) or (None, None)."""
    path = os.path.join(pdir, ".cc-status.json")
    try:
        with open(path) as f:
            d = json.load(f)
        if now - d.get("ts", 0) < HOOK_STATUS_TTL:
            return d.get("state"), d.get("detail", "")
    except Exception:
        pass
    return None, None

def read_meta(pdir):
    """Read statusLine-written metadata file. No TTL — metadata stays valid
    until overwritten. Returns dict (possibly empty)."""
    path = os.path.join(pdir, ".cc-meta.json")
    try:
        with open(path) as f:
            return json.load(f) or {}
    except Exception:
        return {}

# Inspect every live claude process: PID, env vars, cwd
def inspect_claude_procs():
    procs = []
    try:
        pids = subprocess.check_output(["pgrep", "-x", "claude"], text=True).split()
    except subprocess.CalledProcessError:
        return procs

    # Snapshot pid → (ppid, comm) for every process in one ps call. We use comm
    # (kernel-recorded program name) — NOT command/args — to filter non-tool
    # children: command can contain Bash-tool source code that happens to mention
    # "mcp"/"caffeinate" and would cause false positives.
    pid_ppid = {}
    pid_comm = {}
    try:
        snap = subprocess.check_output(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in snap.splitlines():
            parts = line.split(None, 2)
            if len(parts) != 3:
                continue
            cpid, cppid, comm = parts
            pid_ppid[cpid] = cppid
            pid_comm[cpid] = comm
    except Exception:
        pass

    for pid in pids:
        info = {"pid": pid, "cwd": None, "env": {}, "host": "other", "has_active_child": False}
        try:
            res = subprocess.check_output(
                ["/usr/sbin/lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                text=True, stderr=subprocess.DEVNULL
            )
            for line in res.splitlines():
                if line.startswith("n"):
                    info["cwd"] = line[1:]
                    break
        except Exception:
            pass
        try:
            envline = subprocess.check_output(
                ["ps", "-E", "-p", pid, "-o", "command="],
                text=True, stderr=subprocess.DEVNULL
            )
            for tok in envline.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    info["env"][k] = v
        except Exception:
            pass
        # Detect active *tool* children. claude only forks a shell (Bash tool);
        # other helpers (MCP/LSP/caffeinate/watchdog) get filtered out.
        for cpid, cppid in pid_ppid.items():
            if cppid != pid:
                continue
            if pid_comm.get(cpid, "") in TOOL_CHILD_COMMS:
                info["has_active_child"] = True
                break
        # Host detection: env vars first, parent chain fallback
        env = info["env"]
        if "ITERM_SESSION_ID" in env:
            info["host"] = "iterm"
        elif "VSCODE_INJECTION" in env or "VSCODE_PID" in env or "TERM_PROGRAM" in env and env.get("TERM_PROGRAM") == "vscode":
            info["host"] = "vscode"
        elif "TERMINAL_EMULATOR" in env and "JetBrains" in env.get("TERMINAL_EMULATOR", ""):
            info["host"] = "idea"
        elif "CLAUDE_CODE_SSE_PORT" in env:
            # SSE port set by IDE plugin — fallback to parent chain
            info["host"] = host_from_parent(pid)
        else:
            info["host"] = host_from_parent(pid)
        procs.append(info)
    return procs

def host_from_parent(pid):
    p = pid
    seen = 0
    while p and p not in ("0", "1") and seen < 30:
        try:
            comm = subprocess.check_output(
                ["ps", "-o", "comm=", "-p", p],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            break
        low = comm.lower()
        if "iterm" in low:
            return "iterm"
        if "idea" in low or "intellij" in low or "pycharm" in low or "webstorm" in low or "goland" in low:
            return "idea"
        if comm.endswith("/Code") or "Code Helper" in comm:
            return "vscode"
        try:
            p = subprocess.check_output(
                ["ps", "-o", "ppid=", "-p", p],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            break
        seen += 1
    return "other"

def classify(entries, mtime, alive_proc, has_active_child):
    if not entries:
        return ("unknown", "empty")

    # Find last conversation turn (skip system/meta)
    last = None
    for e in reversed(entries):
        if e.get("type") in ("user", "assistant"):
            last = e
            break
    if last is None:
        return ("unknown", "system-only")

    # Index tool_use → tool_result; collect last error
    tool_use_ids = {}
    tool_use_text = {}
    tool_result_ids = set()
    last_error = None
    last_error_pos = -1
    for idx, e in enumerate(entries):
        msg = e.get("message", {}) or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if e.get("type") == "assistant":
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tid = c.get("id")
                    if tid:
                        tool_use_ids[tid] = c.get("name")
                        inp = c.get("input") or {}
                        name = c.get("name")
                        if name == "Bash":
                            tool_use_text[tid] = inp.get("description") or (inp.get("command") or "")[:60]
                        elif name in ("Edit", "Write", "Read", "NotebookEdit"):
                            tool_use_text[tid] = inp.get("file_path") or inp.get("notebook_path")
                        elif name == "TaskCreate":
                            tool_use_text[tid] = inp.get("subject")
                        elif name in ("Grep", "Glob"):
                            tool_use_text[tid] = inp.get("pattern") or inp.get("command") or ""
                        else:
                            tool_use_text[tid] = (inp.get("description") or inp.get("query") or "")[:60]
        elif e.get("type") == "user":
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    rid = c.get("tool_use_id")
                    if rid:
                        tool_result_ids.add(rid)
                    if c.get("is_error"):
                        err_content = c.get("content")
                        if isinstance(err_content, list):
                            err_content = " ".join(
                                x.get("text", "") for x in err_content if isinstance(x, dict)
                            )
                        last_error = (err_content or "error")[:120]
                        last_error_pos = idx

    pending_tool_uses = [tid for tid in tool_use_ids if tid not in tool_result_ids]

    # Last assistant entry's stop_reason
    last_stop_reason = None
    for e in reversed(entries):
        if e.get("type") == "assistant":
            last_stop_reason = (e.get("message", {}) or {}).get("stop_reason")
            break

    t = last.get("type")
    msg = last.get("message", {}) or {}
    content = msg.get("content")
    age = now - mtime

    last_kind = None
    last_tool = None
    last_text = None
    if isinstance(content, list):
        for c in content:
            if not isinstance(c, dict):
                continue
            ct = c.get("type")
            if ct == "tool_use":
                last_kind = "tool_use"
                last_tool = c.get("name")
                inp = c.get("input") or {}
                if last_tool == "Bash":
                    last_text = inp.get("description") or (inp.get("command") or "")[:60]
                elif last_tool in ("Edit", "Write", "Read", "NotebookEdit"):
                    last_text = inp.get("file_path") or inp.get("notebook_path")
                elif last_tool == "TaskCreate":
                    last_text = inp.get("subject")
                elif last_tool in ("Grep", "Glob"):
                    last_text = inp.get("pattern") or inp.get("command") or ""
                else:
                    last_text = (inp.get("description") or inp.get("query") or "")[:60]
            elif ct == "thinking":
                last_kind = last_kind or "thinking"
            elif ct == "text":
                last_kind = last_kind or "text"
                last_text = (c.get("text") or "")[:80]
    elif isinstance(content, str):
        last_kind = "text"
        last_text = content[:80]

    # Pending permission only counts if the LAST assistant message has unresolved tool_use.
    last_assistant_tool_ids = []
    if t == "assistant" and isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                tid = c.get("id")
                if tid:
                    last_assistant_tool_ids.append(tid)
    last_pending = [tid for tid in last_assistant_tool_ids if tid not in tool_result_ids]

    # 1) Pending permission: latest assistant is tool_use, no result yet
    if t == "assistant" and last_kind == "tool_use" and last_pending:
        if age < RUNNING_SECS:
            return ("running", f"using {last_tool}: {last_text or ''}".strip())
        return ("needs-permission", f"approve {last_tool}: {last_text or ''}".strip())

    # 2) max_tokens — output was truncated, almost always needs intervention
    if last_stop_reason == "max_tokens" and age >= IDLE_SECS:
        return ("error", "output truncated (max_tokens)")

    # 3) Process gone but session never ended cleanly — interrupted
    if not alive_proc and last_stop_reason not in ("end_turn", None) and t == "assistant":
        return ("interrupted", f"interrupted (stop={last_stop_reason})")

    # 4) Tool error in the latest exchange (within last 3 entries) and now idle
    if last_error is not None and last_error_pos >= len(entries) - 3 and age >= IDLE_SECS:
        return ("error", f"tool failed: {last_error[:80]}")

    # 5) Process is alive AND has an active child process — real work in flight
    #    (Bash, Edit, Read, etc.). No time cap needed: the child either exists
    #    (running) or it doesn't (fall through below).
    if alive_proc and has_active_child and age >= RUNNING_SECS:
        if t == "assistant" and last_kind == "tool_use":
            return ("running", f"working… (last: {last_tool})")
        if t == "user":
            return ("running", "compacting / processing…")
        if t == "assistant" and last_kind in ("thinking", "text"):
            return ("running", "thinking / compacting…")

    # 5b) Alive proc, no child, but jsonl silent under THINK_GRACE — assume the
    #     model is thinking / streaming a long reply with no tool call yet. Without
    #     this, a long pure-reasoning turn would flip to idle within RUNNING_SECS.
    if alive_proc and not has_active_child and age < THINK_GRACE:
        if t == "assistant" and last_kind in ("thinking", "text"):
            return ("running", "thinking…")
        if t == "user":
            return ("running", "Claude is responding…")

    if t == "user":
        if age < RUNNING_SECS:
            return ("running", "Claude is responding…")
        return ("idle", "user-last")

    if t == "assistant":
        if last_kind == "tool_use":
            # Tool finished, awaiting next assistant turn
            if age < RUNNING_SECS:
                return ("running", f"finished {last_tool}")
            return ("idle", f"after {last_tool}")
        if last_kind == "thinking":
            return ("running", "thinking…") if age < RUNNING_SECS else ("idle", "thinking-last")
        if last_kind == "text":
            if age < IDLE_SECS:
                return ("running", "writing reply…")
            return ("needs-input", last_text or "awaiting your input")

    return ("unknown", t or "?")

procs = inspect_claude_procs()

# Build cwd → procs and host pool
cwd_map = {}
for p in procs:
    if p["cwd"]:
        cwd_map.setdefault(p["cwd"], []).append(p)

sessions = []
for proj in sorted(os.listdir(projects_dir)):
    pdir = os.path.join(projects_dir, proj)
    if not os.path.isdir(pdir):
        continue
    files = glob.glob(os.path.join(pdir, "*.jsonl"))
    if not files:
        continue
    latest = max(files, key=os.path.getmtime)
    mtime = os.path.getmtime(latest)
    entries = read_last_entries(latest)

    # Layer 1 (highest priority): statusLine-written .cc-meta.json
    meta = read_meta(pdir)
    meta_workspace = meta.get("workspace") or {}
    meta_cwd = meta_workspace.get("current_dir") or meta.get("cwd") or ""

    # Layer 2: JSONL first-cwd scan (fallback)
    # NOTE: proj dir names encode '/' as '-', so decoding back is inherently lossy
    # when real dir names contain literal dashes (e.g. "claude-code-swiftbar").
    # Always prefer real_cwd from the jsonl; only fall back to the decoded path for
    # display when real_cwd is unavailable. Never use the decoded path for cwd matching.
    real_cwd = meta_cwd or read_first_cwd(latest)
    proj_path_decoded = "/" + proj.lstrip("-").replace("-", "/")
    proj_path = real_cwd or proj_path_decoded
    proj_name = os.path.basename(proj_path) or proj

    # Authoritative host from jsonl entrypoint (set by claude CLI based on launch context).
    # statusLine doesn't expose this signal, so we still scan jsonl.
    entrypoint = read_entrypoint(latest)
    host_from_ep = host_from_entrypoint(entrypoint)

    # Alive if EITHER:
    #   (a) jsonl was written recently (< ALIVE_SECS), OR
    #   (b) a claude process has cwd matching this project (running but idle session,
    #       OR a forgotten/zombie claude — listed so the user can jump in and close it)
    is_recent = (now - mtime) < ALIVE_SECS
    # claude parent proc doesn't chdir when the user `cd`s in a Bash tool, so its
    # lsof cwd may still be the session-start dir while meta.workspace.current_dir
    # has moved to a subdir. Fall back to meta.cwd if proj_path lookup misses.
    matched = cwd_map.get(proj_path, []) if real_cwd else []
    if not matched and meta.get("cwd") and meta["cwd"] != proj_path:
        matched = cwd_map.get(meta["cwd"], [])
    alive = is_recent or bool(matched)

    # Aggregate has_active_child across all matched procs (any one with a child = working).
    has_child = any(p.get("has_active_child") for p in (matched or []))

    # Hook-written status takes priority when fresh (< HOOK_STATUS_TTL).
    # Falls back to JSONL-based classify() for sessions without hooks configured.
    hook_state, hook_detail = read_hook_status(pdir)
    if hook_state:
        state, detail = hook_state, hook_detail
        alive = True  # hook only fires for live sessions
    else:
        state, detail = classify(entries, mtime, alive_proc=alive, has_active_child=has_child)
    if alive:
        if host_from_ep:
            host = host_from_ep
        elif matched:
            host = matched[0]["host"]
        else:
            host = "other"
    else:
        host = host_from_ep or "other"

    sessions.append({
        "proj": proj_name,
        "proj_path": proj_path,
        "session": os.path.basename(latest).replace(".jsonl", ""),
        "mtime": mtime,
        "age": now - mtime,
        "state": state,
        "detail": detail,
        "host": host,
        "alive": alive,
    })

# Drift fallback: any alive session still without a host (no entrypoint, no cwd-match)
# gets assigned a stale claude proc — the one whose home project was modified longest ago.
proj_path_to_mtime = {s["proj_path"]: s["mtime"] for s in sessions}
matched_pids = set()
for s in sessions:
    if s["alive"] and s["host"] != "other":
        for p in cwd_map.get(s["proj_path"], []):
            matched_pids.add(p["pid"])
            break
unmatched_procs = [p for p in procs if p["pid"] not in matched_pids]

drifted = [s for s in sessions if s["alive"] and s["host"] == "other"]
drifted.sort(key=lambda s: s["mtime"], reverse=True)

def proc_parked_age(p):
    cwd = p.get("cwd") or ""
    if cwd not in proj_path_to_mtime:
        return float("inf")
    return now - proj_path_to_mtime[cwd]

unmatched_procs.sort(key=proc_parked_age, reverse=True)
for s, p in zip(drifted, unmatched_procs):
    s["host"] = p["host"]

sessions.sort(key=lambda s: s["mtime"], reverse=True)

alive_sessions = [s for s in sessions if s["alive"]]
running = [s for s in alive_sessions if s["state"] == "running"]
needs_perm = [s for s in alive_sessions if s["state"] == "needs-permission"]
needs_input = [s for s in alive_sessions if s["state"] == "needs-input"]
errors = [s for s in alive_sessions if s["state"] == "error"]
interrupted = [s for s in alive_sessions if s["state"] == "interrupted"]
attention = needs_perm + errors + interrupted + needs_input  # human action required

ICON = {
    "running": "✨",
    "needs-permission": "🔐",
    "needs-input": "💬",
    "error": "❌",
    "interrupted": "⛔",
    "idle": "💤",
    "unknown": "❓",
}
HOST_ICON_FALLBACK = {
    "iterm": "⌨",
    "idea": "I",
    "vscode": "V",
    "other": "·",
}

# Title shows ONE icon — the highest-priority active state — plus total alive count.
# Detail breakdown lives in the dropdown.
priority_order = [
    ("needs-permission", needs_perm),
    ("error",            errors),
    ("interrupted",      interrupted),
    ("needs-input",      needs_input),
    ("running",          running),
]
title_state = None
title_count = 0
for s, group in priority_order:
    if group:
        title_state = s
        title_count = len(group)
        break

n_total = len(alive_sessions)
# Menu-bar icon size: SwiftBar honors width/height params to NSImage.resizedCopy.
# 18pt matches the system menubar icon convention (verified from SwiftBar source:
# AppIcon is resized to 21x21; system tray glyphs are ~16-18pt). Using a hi-res
# 88px source PNG so retina displays stay crisp after the resize.
ICON_SIZE = "width=21 height=24"
if title_state:
    label = f"{ICON.get(title_state, '·')}{title_count}/{n_total}"
    if CC_APP_B64:
        print(f"{label} | image={CC_APP_B64} {ICON_SIZE}")
    else:
        print(label)
elif alive_sessions:
    label = f"{ICON['idle']}{n_total}"
    if CC_APP_B64:
        print(f"{label} | image={CC_APP_B64} {ICON_SIZE}")
    else:
        print(label)
else:
    if CC_APP_B64:
        print(f"· | image={CC_APP_B64} {ICON_SIZE} color=gray")
    else:
        print("·cc | color=gray")
print("---")

if not alive_sessions:
    print("No active Claude Code sessions | color=gray")
else:
    print(f"Alive sessions: {len(alive_sessions)}  ·  procs: {len(procs)} | color=gray")
    print("---")

    # Sort: attention-required first, then running, then idle. Within group: most recent first.
    state_order = {
        "needs-permission": 0,
        "error": 1,
        "interrupted": 2,
        "needs-input": 3,
        "running": 4,
        "idle": 5,
        "unknown": 6,
    }
    shown = sorted(
        alive_sessions,
        key=lambda s: (state_order.get(s["state"], 9), -s["mtime"]),
    )

    def fmt_age(sec):
        sec = int(sec)
        if sec < 60: return f"{sec}s"
        if sec < 3600: return f"{sec//60}m"
        if sec < 86400: return f"{sec//3600}h"
        return f"{sec//86400}d"

    for s in shown:
        icon = ICON.get(s["state"], "·")
        # Only dim "less important" states; let the system theme drive primary text
        # (so hover highlight & dark mode behave). Color signal lives in the icon.
        dim = s["state"] in ("idle", "unknown")

        host = s["host"] if s["alive"] else "other"
        host_b64 = ICON_B64.get(host, "") or CC_APP_B64

        params = f"bash='{jump_bin}' param1='{s['session']}' param2='{s['proj_path']}' terminal=false"
        if dim:
            params = "color=gray " + params
        if host_b64:
            params += f" image={host_b64} width=16 height=16"
        line = f"{icon} {s['proj']}  ({fmt_age(s['age'])} ago)"
        print(f"{line} | {params}")
        detail = (s["detail"] or "").replace("|", "/").replace("\n", " ")
        if len(detail) > 90:
            detail = detail[:87] + "…"
        print(f"-- {detail} | color=gray")
        print(f"-- Open project folder | bash='/usr/bin/open' param1='{s['proj_path']}' terminal=false")

print("---")
print("Refresh | refresh=true")
print(f"Updated {datetime.now().strftime('%H:%M:%S')} | color=gray")
PY
