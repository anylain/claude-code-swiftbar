#!/usr/bin/env python3
# Renders the SwiftBar menu output for plugin.10s.sh.
# argv: PROJECTS_DIR JUMP_BIN ICON_DIR
import os
import re
import sys
import json
import time
import subprocess
import ctypes
import ctypes.util
from datetime import datetime

projects_dir = sys.argv[1]
jump_bin = sys.argv[2]
icon_dir = sys.argv[3]
now = time.time()

# Display-sleep fast-path: when all screens are off (lid closed, display sleep,
# or system sleep), skip all I/O and subprocess work — the menu bar is invisible.
# CGDisplayIsAsleep is a CoreGraphics public API present since macOS 10.0.
_CG = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
_CG.CGGetActiveDisplayList.restype = ctypes.c_int
_CG.CGGetActiveDisplayList.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
_CG.CGDisplayIsAsleep.restype = ctypes.c_int
_CG.CGDisplayIsAsleep.argtypes = [ctypes.c_uint32]


def _displays_asleep():
    n = ctypes.c_uint32(0)
    if _CG.CGGetActiveDisplayList(0, None, ctypes.byref(n)) != 0:
        return False
    if n.value == 0:
        return True
    displays = (ctypes.c_uint32 * n.value)()
    if _CG.CGGetActiveDisplayList(n.value, displays, ctypes.byref(n)) != 0:
        return False
    return all(_CG.CGDisplayIsAsleep(d) for d in displays)


def load_icon(name):
    p = os.path.join(icon_dir, f"{name}.b64")
    try:
        with open(p) as f:
            return f.read().strip()
    except Exception:
        return ""


CC_APP_B64 = load_icon("cc-app")

if _displays_asleep():
    if CC_APP_B64:
        print(f"🌑 | image={CC_APP_B64} width=21 height=24 color=gray")
    else:
        print("🌑")
    print("---")
    print("Display asleep | color=gray")
    raise SystemExit(0)


HOST_TAG = {
    "iterm": "  [iTerm]",
    "terminal": "  [Terminal]",
    "warp": "  [Warp]",
    "ghostty": "  [Ghostty]",
    "alacritty": "  [Alacritty]",
    "kitty": "  [kitty]",
    "wezterm": "  [WezTerm]",
    "hyper": "  [Hyper]",
    "tabby": "  [Tabby]",
    "vscode": "  [VSCode]",
    "cursor": "  [Cursor]",
    "windsurf": "  [Windsurf]",
    "zed": "  [Zed]",
    "sublime": "  [Sublime]",
    "nova": "  [Nova]",
    "intellij": "  [IntelliJ]",
    "pycharm": "  [PyCharm]",
    "webstorm": "  [WebStorm]",
    "goland": "  [GoLand]",
    "rubymine": "  [RubyMine]",
    "clion": "  [CLion]",
    "phpstorm": "  [PhpStorm]",
    "rider": "  [Rider]",
    "datagrip": "  [DataGrip]",
    "rustrover": "  [RustRover]",
    "aqua": "  [Aqua]",
    "appcode": "  [AppCode]",
    "datasphere": "  [DataSpell]",
    "androidstudio": "  [AndroidStudio]",
    "fleet": "  [Fleet]",
    "jetbrains": "  [JetBrains]",
    "tmux": "  [tmux]",
}

RUNNING_SECS = 30
IDLE_SECS = 5
ALIVE_SECS = 120  # jsonl modified within 2 min => session is "alive"
THINK_GRACE = 180  # alive proc + no child + jsonl silent < THINK_GRACE => still thinking
HOOK_STATUS_TTL = 60  # hook-written .cc-status.json valid for 60s before fallback to classify

# DEBUG (TEMPORARY): per-refresh decision timeline. Opt-in via env var or
# sentinel file. Pairs with cc-status-writer's /tmp/cc-status-history.log to
# answer "what did writer last say vs. what did plugin decide?".
PLUGIN_DEBUG_LOG = "/tmp/cc-plugin-debug.log"
PLUGIN_DEBUG_MAX_BYTES = 1_000_000
PLUGIN_DEBUG_ENABLED = os.environ.get("CC_PLUGIN_DEBUG") == "1" or os.path.exists("/tmp/cc-plugin-debug.enabled")


def _plugin_debug(lines):
    if not PLUGIN_DEBUG_ENABLED:
        return
    try:
        if os.path.getsize(PLUGIN_DEBUG_LOG) > PLUGIN_DEBUG_MAX_BYTES:
            os.remove(PLUGIN_DEBUG_LOG)
    except (FileNotFoundError, OSError):
        pass
    try:
        with open(PLUGIN_DEBUG_LOG, "a") as f:
            f.write("".join(lines))
    except Exception:
        pass


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
        lines = [line for line in data.splitlines() if line.strip()]
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
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
        return "jetbrains"
    return None  # cli or unknown — defer to process inspection


def read_hook_status(pdir, session_id=""):
    """Read hook-written status file. Returns (state, detail, sid, ts) or (None, None, None, 0)."""
    path = os.path.join(pdir, ".cc-status.json")
    try:
        with open(path) as f:
            d = json.load(f)
        state = d.get("state")
        sid = d.get("sid", "")
        ts = d.get("ts", 0)
        if state == "ended":
            if session_id and sid and sid != session_id:
                return None, None, None, 0
            return state, d.get("detail", ""), sid, ts
        # Non-ended: if sid differs from the jsonl session, the jsonl is stale.
        # Trust the hook status unconditionally — it's the only signal we have.
        if session_id and sid and sid != session_id:
            return state, d.get("detail", ""), sid, ts
        if now - ts < HOOK_STATUS_TTL:
            return state, d.get("detail", ""), sid, ts
    except Exception:
        pass
    return None, None, None, 0


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

    # Snapshot pid → (ppid, comm) for every process in one ps call. We use comm
    # (kernel-recorded program name) — NOT command/args — to filter non-tool
    # children: command can contain Bash-tool source code that happens to mention
    # "mcp"/"caffeinate" and would cause false positives.
    #
    # We derive the claude pid list from this ps snapshot rather than calling
    # `pgrep -x claude`. Reason: on macOS 26 (Darwin 25.x), `pgrep -x claude`
    # intermittently misses live claude processes that ps clearly lists with
    # comm="claude" (reproduced: two claude procs in ps, pgrep -x returns one).
    # The exact filtering rule pgrep applies is opaque; ps + explicit comm match
    # is reliable across macOS versions.
    pid_ppid = {}
    pid_comm = {}
    try:
        snap = subprocess.check_output(["ps", "-A", "-o", "pid=,ppid=,comm="], text=True, stderr=subprocess.DEVNULL)
        for line in snap.splitlines():
            parts = line.split(None, 2)
            if len(parts) != 3:
                continue
            cpid, cppid, comm = parts
            pid_ppid[cpid] = cppid
            pid_comm[cpid] = comm
    except Exception:
        return procs

    # comm may be a bare name ("claude") or, in rare cases, an absolute path.
    # Match either form.
    pids = [cpid for cpid, comm in pid_comm.items() if comm == "claude" or os.path.basename(comm) == "claude"]
    if not pids:
        return procs

    # Batch lsof + ps -E across ALL claude pids in two forks total (instead of
    # 2N forks). Both tools accept comma-separated -p lists.
    cwd_by_pid = {}
    try:
        res = subprocess.check_output(
            ["/usr/sbin/lsof", "-a", "-p", ",".join(pids), "-d", "cwd", "-Fpn"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        # `-Fpn` emits records like:  p<pid>\nf<fd>\nn<path>\n
        # Walk records, tracking which pid we're inside; first n-line for that pid wins.
        cur = None
        for line in res.splitlines():
            if not line:
                continue
            tag, val = line[0], line[1:]
            if tag == "p":
                cur = val
            elif tag == "n" and cur and cur not in cwd_by_pid:
                cwd_by_pid[cur] = val
    except Exception:
        pass

    env_by_pid = {pid: {} for pid in pids}
    try:
        envblob = subprocess.check_output(
            ["ps", "-E", "-o", "pid=,command=", "-p", ",".join(pids)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in envblob.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            row_pid, cmdline = parts
            if row_pid not in env_by_pid:
                continue
            for tok in cmdline.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    env_by_pid[row_pid][k] = v
    except Exception:
        pass

    # Pre-bucket children by ppid so detection is O(N) total, not O(N×procs).
    children_by_ppid = {}
    for cpid, cppid in pid_ppid.items():
        children_by_ppid.setdefault(cppid, []).append(cpid)

    for pid in pids:
        info = {
            "pid": pid,
            "cwd": cwd_by_pid.get(pid),
            "env": env_by_pid.get(pid, {}),
            "host": "other",
            "has_active_child": False,
        }
        for cpid in children_by_ppid.get(pid, ()):
            if pid_comm.get(cpid, "") in TOOL_CHILD_COMMS:
                info["has_active_child"] = True
                break
        # Host detection: env vars first, parent chain fallback
        env = info["env"]
        term_prog = env.get("TERM_PROGRAM", "")
        if "ITERM_SESSION_ID" in env:
            info["host"] = "iterm"
        elif term_prog == "Apple_Terminal":
            info["host"] = "terminal"
        elif term_prog == "WarpTerminal":
            info["host"] = "warp"
        elif term_prog == "ghostty":
            info["host"] = "ghostty"
        elif term_prog == "WezTerm":
            info["host"] = "wezterm"
        elif term_prog == "Hyper":
            info["host"] = "hyper"
        elif term_prog == "Tabby":
            info["host"] = "tabby"
        elif env.get("ALACRITTY_LOG") or term_prog == "alacritty":
            info["host"] = "alacritty"
        elif env.get("KITTY_WINDOW_ID") or env.get("TERM") == "xterm-kitty":
            info["host"] = "kitty"
        elif (
            "VSCODE_INJECTION" in env
            or "VSCODE_PID" in env
            or term_prog == "vscode"
        ):
            # VSCode fork detection: Cursor / Windsurf set distinct CHANNEL strings.
            ch = (env.get("VSCODE_GIT_ASKPASS_NODE", "") + " " + env.get("VSCODE_IPC_HOOK", "")).lower()
            if "cursor" in ch:
                info["host"] = "cursor"
            elif "windsurf" in ch:
                info["host"] = "windsurf"
            else:
                info["host"] = "vscode"
        elif "TERMINAL_EMULATOR" in env and "JetBrains" in env.get("TERMINAL_EMULATOR", ""):
            # JetBrains terminal emulator is shared across all IDEs; the specific
            # product (PyCharm/WebStorm/...) only shows up in the parent chain.
            info["host"] = host_from_parent(pid, pid_ppid, pid_comm) or "jetbrains"
            if info["host"] == "other":
                info["host"] = "jetbrains"
        elif term_prog == "zed":
            info["host"] = "zed"
        elif "CLAUDE_CODE_SSE_PORT" in env:
            # SSE port set by IDE plugin — fallback to parent chain
            info["host"] = host_from_parent(pid, pid_ppid, pid_comm)
        elif env.get("TMUX"):
            # Running inside tmux. We can't cheaply identify the terminal behind
            # tmux (the tmux server's parent chain doesn't include the terminal
            # emulator — the client does, but it's a sibling, not an ancestor).
            # For accurate terminal detection we'd need to find the tmux client
            # pid via `tmux list-clients` and walk its parent chain.
            info["host"] = "tmux"
        else:
            info["host"] = host_from_parent(pid, pid_ppid, pid_comm)
        procs.append(info)
    return procs


def host_from_parent(pid, pid_ppid, pid_comm):
    # Walk the parent chain using the global ps snapshot — no extra forks.
    p = pid
    seen = 0
    while p and p not in ("0", "1") and seen < 30:
        comm = pid_comm.get(p, "")
        if not comm:
            break
        low = comm.lower()
        if "iterm" in low:
            return "iterm"
        if "warp" in low and "warp.app" in low:
            return "warp"
        if "ghostty" in low:
            return "ghostty"
        if "alacritty" in low:
            return "alacritty"
        if "/kitty" in low or low.endswith("/kitty"):
            return "kitty"
        if "wezterm" in low:
            return "wezterm"
        if "hyper.app" in low:
            return "hyper"
        if "tabby.app" in low:
            return "tabby"
        if "/terminal" in low and "/terminal.app/" in low:
            return "terminal"
        if "cursor" in low and ("cursor.app" in low or "cursor helper" in low):
            return "cursor"
        if "windsurf" in low:
            return "windsurf"
        if "/zed" in low and "zed.app" in low:
            return "zed"
        if "sublime text" in low:
            return "sublime"
        if "nova.app" in low:
            return "nova"
        # JetBrains family — match specific IDE name in the .app bundle path.
        # Order matters: more specific names ("android studio") before catch-alls.
        if "android studio" in low or "androidstudio" in low:
            return "androidstudio"
        if "intellij idea" in low or "/idea " in low or low.endswith("/idea"):
            return "intellij"
        if "pycharm" in low:
            return "pycharm"
        if "webstorm" in low:
            return "webstorm"
        if "goland" in low:
            return "goland"
        if "rubymine" in low:
            return "rubymine"
        if "clion" in low:
            return "clion"
        if "phpstorm" in low:
            return "phpstorm"
        if "rider" in low:
            return "rider"
        if "datagrip" in low:
            return "datagrip"
        if "rustrover" in low:
            return "rustrover"
        if "appcode" in low:
            return "appcode"
        if "dataspell" in low or "datasphere" in low:
            return "datasphere"
        if "/aqua" in low and "aqua.app" in low:
            return "aqua"
        if "fleet" in low and "fleet.app" in low:
            return "fleet"
        if "jetbrains" in low:
            return "jetbrains"
        if comm.endswith("/Code") or "Code Helper" in comm:
            return "vscode"
        p = pid_ppid.get(p)
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
                            err_content = " ".join(x.get("text", "") for x in err_content if isinstance(x, dict))
                        last_error = (err_content or "error")[:120]
                        last_error_pos = idx

    # Last assistant entry's stop_reason
    last_stop_reason = None
    for e in reversed(entries):
        if e.get("type") == "assistant":
            last_stop_reason = (e.get("message", {}) or {}).get("stop_reason")
            break

    t = last.get("type")
    msg = last.get("message", {}) or {}
    content = msg.get("content")

    # `age` should reflect time since the last *conversation* turn, not since
    # any jsonl write. Claude Code occasionally appends system/keepalive entries
    # to jsonl while the session sits idle (e.g. ~3 min after end_turn), which
    # would otherwise reset the file mtime and make a needs-input session look
    # like fresh activity → misclassified as "thinking…" running. Prefer the
    # last user/assistant entry's own timestamp; fall back to mtime if missing.
    last_ts_raw = last.get("timestamp")
    last_ts = None
    if isinstance(last_ts_raw, str):
        try:
            last_ts = datetime.fromisoformat(last_ts_raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            last_ts = None
    age = now - (last_ts if last_ts is not None else mtime)

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
                elif last_tool == "AskUserQuestion":
                    qs = inp.get("questions") or []
                    if qs and isinstance(qs[0], dict):
                        last_text = (qs[0].get("question") or "")[:80]
                    else:
                        last_text = "awaiting your decision"
                elif last_tool == "ExitPlanMode":
                    last_text = "review plan"
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

    # 1) Pending tool_use with no result yet → either waiting on permission
    #    (real side-effect tools) or waiting on a decision (AskUserQuestion,
    #    ExitPlanMode — these block the turn but aren't a security gate).
    DECISION_TOOLS = ("AskUserQuestion", "ExitPlanMode")
    if t == "assistant" and last_kind == "tool_use" and last_pending:
        if age < RUNNING_SECS:
            return ("running", f"using {last_tool}: {last_text or ''}".strip())
        if last_tool in DECISION_TOOLS:
            return ("needs-decision", last_text or last_tool or "awaiting your decision")
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
    #     Skip when last assistant has stop_reason=end_turn: that's an explicit
    #     "turn ended cleanly" signal — the next entry is system metadata, not
    #     thinking. Without this guard, sessions that exited via Ctrl+C / window
    #     close keep showing "running" for THINK_GRACE seconds because alive_proc
    #     stays True via is_recent (jsonl mtime < ALIVE_SECS).
    if alive_proc and not has_active_child and age < THINK_GRACE and last_stop_reason != "end_turn":
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


# PASS 1: cheap project pre-scan. Stat each project dir's *.jsonl mtimes and
# look for the .cc-status.json sentinel. Skip projects that are clearly dead
# (stale jsonl + no hook file) before paying any sub-process cost.
#
# inspect_claude_procs() costs ~96ms (3 sub-process spawns: ps -A, lsof, ps -E)
# and dominates the refresh CPU budget. If this pre-scan finds zero potentially
# active projects, we skip it entirely; drift fallback also degrades gracefully
# (no procs → no drift assignments, identical to the "all sessions matched"
# happy path).
#
# Trade-off: a freshly-launched claude that has not yet written any jsonl
# would not show up for one refresh cycle. The very next 10s tick picks it up
# once the first jsonl line lands.
prescan = []  # (proj, pdir, latest_path, latest_mtime, has_status_file, is_recent)
any_live_signal = False
for proj in sorted(os.listdir(projects_dir)):
    pdir = os.path.join(projects_dir, proj)
    if not os.path.isdir(pdir):
        continue

    latest_path = None
    latest_mtime = 0.0
    has_status_file = False
    try:
        for de in os.scandir(pdir):
            name = de.name
            if name.endswith(".jsonl"):
                try:
                    m = de.stat().st_mtime
                except OSError:
                    continue
                if m > latest_mtime:
                    latest_mtime = m
                    latest_path = de.path
            elif name == ".cc-status.json":
                has_status_file = True
    except OSError:
        continue
    if latest_path is None:
        continue

    is_recent = (now - latest_mtime) < ALIVE_SECS
    if is_recent or has_status_file:
        any_live_signal = True
    prescan.append((proj, pdir, latest_path, latest_mtime, has_status_file, is_recent))

# PASS 2: only inspect procs if at least one project has a live signal.
# Otherwise procs/cwd_map stay empty and the rest of the pipeline naturally
# renders "No active sessions" without paying the 96ms.
if any_live_signal:
    procs = inspect_claude_procs()
else:
    procs = []

cwd_map = {}
for p in procs:
    if p["cwd"]:
        cwd_map.setdefault(p["cwd"], []).append(p)

# Encoding: claude stores `/Users/x/foo` as proj-dir `-Users-x-foo`.
# Claude Code replaces ALL non-alphanumeric characters with '-', not just '/'.
# (e.g. `/Users/x/baize_qa_lc` → `-Users-x-baize-qa-lc`).
proc_proj_keys = set()
for cwd in cwd_map:
    if cwd.startswith("/"):
        proc_proj_keys.add(re.sub(r'[^a-zA-Z0-9]', '-', cwd))


sessions = []
for proj, pdir, latest_path, latest_mtime, has_status_file, is_recent in prescan:
    # Final dead-project skip: stale jsonl + no hook sentinel + no claude proc
    # plausibly rooted here.
    proc_could_match = proj in proc_proj_keys
    if not is_recent and not has_status_file and not proc_could_match:
        continue

    files_latest = latest_path
    mtime = latest_mtime
    entries = read_last_entries(files_latest)

    # Layer 1 (highest priority): statusLine-written .cc-meta.json
    meta = read_meta(pdir)
    meta_workspace = meta.get("workspace") or {}
    # project_dir is the session's launch root — stable across `cd` inside the
    # session. Use it for display name AND process matching: the claude parent
    # proc never chdir's, so its lsof cwd stays at project_dir even after the
    # user `cd`s into a subdir via the Bash tool. Falling back to meta.cwd
    # covers older meta files written before project_dir was tracked.
    meta_project_dir = meta_workspace.get("project_dir") or meta.get("cwd") or ""

    # Layer 2: JSONL first-cwd scan (fallback)
    # NOTE: proj dir names encode '/' as '-', so decoding back is inherently lossy
    # when real dir names contain literal dashes (e.g. "claude-code-swiftbar").
    # Always prefer real_cwd from the jsonl; only fall back to the decoded path for
    # display when real_cwd is unavailable. Never use the decoded path for cwd matching.
    real_cwd = meta_project_dir or read_first_cwd(files_latest)
    proj_path_decoded = "/" + proj.lstrip("-").replace("-", "/")
    proj_path = real_cwd or proj_path_decoded
    proj_name = os.path.basename(proj_path) or proj

    # Authoritative host from jsonl entrypoint (set by claude CLI based on launch context).
    # write_meta.py caches it on first session statusLine; we only re-scan jsonl
    # when meta has no entrypoint (e.g. very old meta written before this field
    # existed, or sessions whose statusLine never fired).
    entrypoint = meta.get("entrypoint") or read_entrypoint(files_latest)
    host_from_ep = host_from_entrypoint(entrypoint)

    # Alive judgement happens in a 2nd pass below. Here we just stage the
    # candidate proc match for this session.
    # Match procs only by proj_path (== meta.workspace.project_dir, the launch
    # root). Do NOT also lookup by current_dir: claude's parent proc never
    # chdir's, so any proc cwd that matches current_dir but not project_dir
    # belongs to a *different* session whose project_dir happens to equal this
    # session's current_dir (e.g. parent-dir session at /Users/x/git would
    # otherwise steal the proc of a child session at /Users/x/git/foo).
    #
    # Multiple claude procs can share the same project_dir (e.g. same project
    # opened in iTerm and VSCode at once). Sort candidates so the one whose
    # CLAUDE_CODE_ENTRYPOINT env matches this session's recorded entrypoint
    # comes first. CLI-launched procs have no CLAUDE_CODE_ENTRYPOINT in env,
    # so for entrypoint=="cli" we prefer procs with the var unset.
    candidate_matched = cwd_map.get(proj_path, []) if real_cwd else []
    if len(candidate_matched) > 1 and entrypoint:
        def _ep_match_score(p):
            proc_ep = p.get("env", {}).get("CLAUDE_CODE_ENTRYPOINT", "")
            if entrypoint == "cli":
                return 0 if proc_ep == "" else 1
            return 0 if proc_ep == entrypoint else 1
        candidate_matched = sorted(candidate_matched, key=_ep_match_score)

    # Hook-written status takes priority when fresh (< HOOK_STATUS_TTL).
    session_id = os.path.basename(files_latest).replace(".jsonl", "")
    hook_state, hook_detail, hook_sid, hook_ts = read_hook_status(pdir, session_id)

    sessions.append(
        {
            "proj": proj_name,
            "proj_path": proj_path,
            "pdir": pdir,
            "session": session_id,
            "mtime": mtime,
            "age": now - mtime,
            "is_recent": is_recent,
            "candidate_matched": candidate_matched,
            "hook_state": hook_state,
            "hook_detail": hook_detail,
            "hook_sid": hook_sid,
            "hook_ts": hook_ts,
            "host_from_ep": host_from_ep,
            "entries": entries,
        }
    )

# 2nd pass: when multiple sessions resolve to the same proj_path (e.g. parent-dir
# session whose statusLine meta points to a child dir collides with the child
# session itself), only the most-recently-active session may claim the matched
# claude procs. Others lose their proc match and fall back to mtime-only alive.
newest_at_path = {}
for idx, s in enumerate(sessions):
    if not s["candidate_matched"]:
        continue
    cur = newest_at_path.get(s["proj_path"])
    if cur is None or s["mtime"] > sessions[cur]["mtime"]:
        newest_at_path[s["proj_path"]] = idx

for idx, s in enumerate(sessions):
    matched = s["candidate_matched"]
    if matched and newest_at_path.get(s["proj_path"]) != idx:
        matched = []  # an older same-path session — let the newest one own the procs
    alive = s["is_recent"] or bool(matched)
    has_child = any(p.get("has_active_child") for p in matched)

    if s["hook_state"]:
        state, detail = s["hook_state"], s["hook_detail"]
        if s.get("hook_sid") and s["hook_sid"] != s["session"]:
            s["session"] = s["hook_sid"]  # hook knows a newer session — use its id
            if s.get("hook_ts"):
                s["age"] = max(0, now - s["hook_ts"])  # use hook's timestamp for age
        if state == "ended":
            # SessionEnd tombstone — the session has explicitly exited. Don't let
            # JSONL is_recent keep it "alive" for the next 120s; drop it now.
            alive = False
            decision_path = "hook-ended"
        else:
            # Hook only fires for live sessions, so a non-ended state normally
            # means the session is alive. Exception: when the hook's sid differs
            # from the jsonl session AND no claude process matches, the new
            # session may have died before writing a jsonl — fall back to
            # mtime-based liveness instead of forcing alive=True.
            if s.get("hook_sid") and s["hook_sid"] != s["session"] and not matched:
                alive = s["is_recent"] or bool(matched)
            else:
                alive = True
            decision_path = "hook"
    else:
        state, detail = classify(s["entries"], s["mtime"], alive_proc=alive, has_active_child=has_child)
        decision_path = "classify"

    if alive:
        if s["host_from_ep"]:
            host = s["host_from_ep"]
        elif matched:
            host = matched[0]["host"]
        else:
            host = "other"
    else:
        host = s["host_from_ep"] or "other"

    s["state"] = state
    s["detail"] = detail
    s["host"] = host
    s["alive"] = alive
    s["matched"] = matched
    s["_decision_path"] = decision_path  # debug-only, popped below
    # Drop staging-only fields so downstream code sees the same shape as before.
    for k in ("is_recent", "candidate_matched", "hook_state", "hook_detail", "hook_sid", "hook_ts", "host_from_ep", "entries"):
        s.pop(k, None)

# DEBUG (TEMPORARY): dump per-refresh decision snapshot. Pairs with writer's
# /tmp/cc-status-history.log. Compare timelines to find "writer wrote needs-input
# but plugin shows running" or "writer cleared but plugin still alive" bugs.
if PLUGIN_DEBUG_ENABLED:
    lines = []
    iso = time.strftime("%FT%T", time.localtime(now))
    lines.append(f"=== {int(now)}\t{iso}\tprocs={len(procs)}\tsessions={len(sessions)} ===\n")
    for p in procs:
        lines.append(
            f"  proc pid={p['pid']}\tcwd={p.get('cwd') or '-'}\thost={p['host']}\tchild={p.get('has_active_child')}\n"
        )
    for s in sessions:
        # Re-read raw status file to expose what was on disk regardless of TTL.
        raw_state = "-"
        raw_age = "-"
        try:
            with open(os.path.join(s["pdir"], ".cc-status.json")) as f:
                d = json.load(f)
                raw_state = d.get("state", "-")
                raw_ts = d.get("ts", 0)
                raw_age = int(now - raw_ts) if raw_ts else "-"
        except Exception:
            pass
        sid8 = (s.get("session") or "")[:8]
        n_matched = len(s.get("matched") or [])
        lines.append(
            f"  sess {sid8}\tproj={s['proj']}\tpath={s['proj_path']}\t"
            f"mtime_age={int(s['age'])}s\tmatched_procs={n_matched}\t"
            f"raw_status={raw_state}(age={raw_age}s)\t"
            f"path_taken={s.get('_decision_path')}\t"
            f"=> alive={s['alive']}\tstate={s['state']}\thost={s['host']}\n"
        )
    _plugin_debug(lines)
# Pop debug-only field after dump.
for s in sessions:
    s.pop("_decision_path", None)
    s.pop("pdir", None)

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
needs_decision = [s for s in alive_sessions if s["state"] == "needs-decision"]
needs_input = [s for s in alive_sessions if s["state"] == "needs-input"]
errors = [s for s in alive_sessions if s["state"] == "error"]
interrupted = [s for s in alive_sessions if s["state"] == "interrupted"]
attention = needs_perm + needs_decision + errors + interrupted + needs_input  # human action required

ICON = {
    "running": "✨",
    "needs-permission": "🔐",
    "needs-decision": "✋",
    "needs-input": "💬",
    "error": "❌",
    "interrupted": "⛔",
    "idle": "💤",
    "unknown": "❓",
}

# Title shows ONE icon — the highest-priority active state — plus total alive count.
# Detail breakdown lives in the dropdown.
priority_order = [
    ("needs-permission", needs_perm),
    ("needs-decision", needs_decision),
    ("error", errors),
    ("interrupted", interrupted),
    ("needs-input", needs_input),
    ("running", running),
]
title_state = None
title_count = 0
for state_name, group in priority_order:
    if group:
        title_state = state_name
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
        "needs-decision": 1,
        "error": 2,
        "interrupted": 3,
        "needs-input": 4,
        "running": 5,
        "idle": 6,
        "unknown": 7,
    }
    shown = sorted(
        alive_sessions,
        key=lambda s: (state_order.get(s["state"], 9), -s["mtime"]),
    )

    def fmt_age(sec):
        sec = int(sec)
        if sec < 60:
            return "<1m"
        if sec < 3600:
            return f"{sec // 60}m"
        if sec < 86400:
            return f"{sec // 3600}h"
        return f"{sec // 86400}d"

    for s in shown:
        icon = ICON.get(s["state"], "·")
        # Only dim "less important" states; let the system theme drive primary text
        # (so hover highlight & dark mode behave). Color signal lives in the icon.
        dim = s["state"] in ("idle", "unknown")

        params = f"bash='{jump_bin}' param1='{s['session']}' param2='{s['proj_path']}' terminal=false"
        if dim:
            params = "color=gray " + params
        host_tag = HOST_TAG.get(s["host"] if s["alive"] else "other", "")
        line = f"{icon} {s['proj']}{host_tag}  ({fmt_age(s['age'])} ago)"
        print(f"{line} | {params}")
        detail = (s["detail"] or "").replace("|", "/").replace("\n", " ")
        if len(detail) > 90:
            detail = detail[:87] + "…"
        print(f"-- {detail} | color=gray")
        print(f"-- Open project folder | bash='/usr/bin/open' param1='{s['proj_path']}' terminal=false")

print("---")
print("Refresh | refresh=true")
