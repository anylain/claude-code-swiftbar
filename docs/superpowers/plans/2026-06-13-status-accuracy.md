# 状态检测准确度三连改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 SwiftBar 插件的 Claude Code 状态检测加 3 条信号源（statusLine 元数据、字段级 fallback、进程子树存在性），干掉 SILENT_RUNNING_MAX 魔数，提升 cwd/host 准确度。

**Architecture:** 在现有"hook → `.cc-status.json` → JSONL 启发式"链上叠加一份"statusLine → `.cc-meta.json` 元数据"，plugin 字段级合并三层信息源；进程层补充 `pgrep -P` 子进程检测，替代静默期魔数。任何一层缺失自动降级。

**Tech Stack:** bash + `/usr/bin/python3`(macOS 原生)，无外部依赖。

**Spec:** `docs/superpowers/specs/2026-06-13-status-accuracy-design.md`

**Note on testing:** 项目无自动化测试框架，spec 明确不引入。每个任务以"可观察的人工验证步骤 + 实际命令输出预期"替代 TDD，保持 bite-sized 与频繁 commit。

---

## Task 1: 新增 cc-meta-writer 脚本骨架

**Files:**
- Create: `claude-code.swiftbar/.bin/cc-meta-writer`

- [ ] **Step 1: 创建可执行脚本骨架**

写入 `claude-code.swiftbar/.bin/cc-meta-writer`:

```bash
#!/bin/bash
# cc-meta-writer — Claude Code statusLine hook that writes session metadata
# (session_id, cwd, model, workspace, output_style) to .cc-meta.json.
# Read by the SwiftBar plugin as authoritative metadata source.
#
# Stdin: statusLine JSON payload from Claude Code.
# Output: stdout is consumed by Claude Code as the status line text — we
#   print an empty string so the plugin doesn't pollute the user's status
#   line. (Users wanting a real statusline should chain another tool.)
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo ""; exit 0; }

tmp="$(mktemp)"
cat > "$tmp"
/usr/bin/python3 - "$tmp" "$HOME" <<'PY'
import json, os, sys, time

tmp_file = sys.argv[1]
home = sys.argv[2]

try:
    with open(tmp_file) as f:
        event = json.load(f)
except Exception:
    sys.exit(0)

transcript_path = event.get("transcript_path", "")
if not transcript_path:
    sys.exit(0)

proj_dir = os.path.dirname(transcript_path)
if not os.path.isdir(proj_dir):
    sys.exit(0)

meta_file = os.path.join(proj_dir, ".cc-meta.json")

payload = {
    "session_id": event.get("session_id", ""),
    "transcript_path": transcript_path,
    "cwd": event.get("cwd", ""),
    "workspace": event.get("workspace", {}),
    "model": event.get("model", {}),
    "version": event.get("version", ""),
    "output_style": event.get("output_style", {}),
    "last_seen": int(time.time()),
}

try:
    with open(meta_file, "w") as f:
        json.dump(payload, f)
except Exception:
    pass
PY
rm -f "$tmp"
# Empty status line — plugin only uses this hook for metadata side-effect.
echo ""
```

- [ ] **Step 2: 加可执行权限**

Run: `chmod +x claude-code.swiftbar/.bin/cc-meta-writer`

- [ ] **Step 3: 验证脚本能处理合法输入**

Run:
```bash
echo '{"session_id":"test123","transcript_path":"/tmp/.cc-meta-test/foo.jsonl","cwd":"/Users/x","workspace":{"current_dir":"/Users/x"},"model":{"id":"opus-4-8","display_name":"Opus 4.7"},"version":"1.0.0","output_style":{"name":"default"}}' \
  | (mkdir -p /tmp/.cc-meta-test && claude-code.swiftbar/.bin/cc-meta-writer)
cat /tmp/.cc-meta-test/.cc-meta.json
```

Expected: 输出空行（statusLine 文本），`/tmp/.cc-meta-test/.cc-meta.json` 包含上面所有字段 + `last_seen` 整数时间戳。

- [ ] **Step 4: 验证非法输入静默退出**

Run:
```bash
echo 'not-json' | claude-code.swiftbar/.bin/cc-meta-writer; echo "exit=$?"
echo '{}' | claude-code.swiftbar/.bin/cc-meta-writer; echo "exit=$?"
```

Expected: 两次都 `exit=0`，无 stderr，无 panic。

- [ ] **Step 5: 清理测试目录**

Run: `rm -rf /tmp/.cc-meta-test`

- [ ] **Step 6: Commit**

```bash
git add claude-code.swiftbar/.bin/cc-meta-writer
git commit -m "feat: add cc-meta-writer statusLine hook for authoritative session metadata"
```

---

## Task 2: install.sh 写入 statusLine 配置

**Files:**
- Modify: `install.sh`(末尾追加 statusLine 安装段)

- [ ] **Step 1: 阅读现有 hook 安装段，确认结构**

Run: `sed -n '54,119p' install.sh`

Expected: 看到从 `# ── Install Claude Code hooks…` 开始的 hook 注入逻辑。新代码会模仿其幂等写入 `~/.claude/settings.json` 的模式。

- [ ] **Step 2: 在 install.sh 的 hook 段之后、最终 echo 之前追加 statusLine 段**

在 `install.sh` 找到这两行：

```bash
echo
echo "Next step: open SwiftBar menu > Refresh All (or restart SwiftBar)."
```

在它们**之前**插入：

```bash
# ── Install statusLine hook for authoritative session metadata ──────────────
META_SCRIPT="$LINK/.bin/cc-meta-writer"

if [ -x "$META_SCRIPT" ]; then
  echo
  echo "Installing Claude Code statusLine for metadata capture..."
  /usr/bin/python3 - "$SETTINGS_FILE" "$META_SCRIPT" <<'PY'
import json, sys

settings_file = sys.argv[1]
meta_script = sys.argv[2]
new_cmd = f"bash \"{meta_script}\""

try:
    with open(settings_file) as f:
        settings = json.load(f)
except Exception:
    settings = {}

existing = settings.get("statusLine")
if isinstance(existing, dict) and existing.get("type") == "command":
    cur = existing.get("command", "")
    if cur == new_cmd:
        print("  statusLine already points to cc-meta-writer — nothing to do.")
    else:
        print("  WARNING: existing statusLine detected:")
        print(f"    {cur}")
        print("  Skipping to avoid clobbering. To enable cc-meta-writer, either:")
        print("    (a) replace settings.json statusLine.command with:")
        print(f"        {new_cmd}")
        print("    (b) chain it: have your existing statusline tool call")
        print(f"        cc-meta-writer first (it prints empty stdout).")
else:
    settings["statusLine"] = {"type": "command", "command": new_cmd}
    with open(settings_file, "w") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  statusLine installed: {new_cmd}")
    print("  Run /reload-plugins or restart Claude Code to activate.")
PY
else
  echo
  echo "Warning: cc-meta-writer not found at $META_SCRIPT — skipping statusLine setup."
fi
```

- [ ] **Step 3: 在干净环境跑 install.sh，验证幂等**

Run:
```bash
# 备份
cp ~/.claude/settings.json ~/.claude/settings.json.bak
# 删掉 statusLine 字段(如果存在)
/usr/bin/python3 -c "import json; p='$HOME/.claude/settings.json'; s=json.load(open(p)); s.pop('statusLine',None); json.dump(s,open(p,'w'),ensure_ascii=False,indent=2); open(p,'a').write('\n')"
./install.sh 2>&1 | tail -20
grep -A2 statusLine ~/.claude/settings.json
```

Expected: install.sh 输出 `statusLine installed: bash "..."`，settings.json 包含 statusLine 节，command 指向 cc-meta-writer。

- [ ] **Step 4: 再跑一次验证幂等不重复写**

Run: `./install.sh 2>&1 | tail -5`

Expected: 输出 `statusLine already points to cc-meta-writer — nothing to do.`

- [ ] **Step 5: 模拟用户已配置其他 statusLine，验证不覆盖**

Run:
```bash
/usr/bin/python3 -c "import json; p='$HOME/.claude/settings.json'; s=json.load(open(p)); s['statusLine']={'type':'command','command':'echo other-tool'}; json.dump(s,open(p,'w'),ensure_ascii=False,indent=2); open(p,'a').write('\n')"
./install.sh 2>&1 | tail -10
grep -A2 statusLine ~/.claude/settings.json
```

Expected: 输出 `WARNING: existing statusLine detected:` 与不覆盖提示，settings.json 中 statusLine.command 仍为 `echo other-tool`。

- [ ] **Step 6: 还原备份**

Run: `mv ~/.claude/settings.json.bak ~/.claude/settings.json`

- [ ] **Step 7: Commit**

```bash
git add install.sh
git commit -m "feat: install.sh installs statusLine hook for cc-meta-writer (idempotent, non-clobbering)"
```

---

## Task 3: plugin.1s.sh 读 .cc-meta.json

**Files:**
- Modify: `claude-code.swiftbar/plugin.1s.sh`

- [ ] **Step 1: 在 read_hook_status 函数定义之后新增 read_meta**

在 `claude-code.swiftbar/plugin.1s.sh` 中找到这一段：

```python
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
```

在它**之后**插入：

```python
def read_meta(pdir):
    """Read statusLine-written metadata file. No TTL — metadata stays valid
    until overwritten. Returns dict (possibly empty)."""
    path = os.path.join(pdir, ".cc-meta.json")
    try:
        with open(path) as f:
            return json.load(f) or {}
    except Exception:
        return {}
```

- [ ] **Step 2: 在主循环里把 meta 字段并入 cwd/entrypoint 推断**

找到这一段:

```python
    # Real cwd from jsonl content (handles cwd drift after the session started).
    # NOTE: proj dir names encode '/' as '-', so decoding back is inherently lossy
    # when real dir names contain literal dashes (e.g. "claude-code-swiftbar").
    # Always prefer real_cwd from the jsonl; only fall back to the decoded path for
    # display when real_cwd is unavailable. Never use the decoded path for cwd matching.
    real_cwd = read_first_cwd(latest)
    proj_path_decoded = "/" + proj.lstrip("-").replace("-", "/")
    proj_path = real_cwd or proj_path_decoded
    proj_name = os.path.basename(proj_path) or proj

    # Authoritative host from jsonl entrypoint (set by claude CLI based on launch context)
    entrypoint = read_entrypoint(latest)
    host_from_ep = host_from_entrypoint(entrypoint)
```

替换为:

```python
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
```

- [ ] **Step 3: SwiftBar 重新加载，肉眼验证状态行未变**

Run:
```bash
# 触发 plugin 刷新一次:
bash claude-code.swiftbar/plugin.1s.sh | head -30
```

Expected: 输出包含菜单标题 + `---` 分隔 + 现有 alive sessions 列表，与改造前一致（因为还没有 .cc-meta.json，read_meta 返回空 dict，全部走 fallback）。

- [ ] **Step 4: 手工伪造一个 .cc-meta.json，验证生效**

Run:
```bash
# 找一个真实的 project 目录
PROJ_DIR=$(ls -td ~/.claude/projects/*/ | head -1)
# 写一个伪造的 meta，cwd 指向一个明显错的位置
cat > "$PROJ_DIR/.cc-meta.json" <<'EOF'
{"session_id":"fake","cwd":"/tmp","workspace":{"current_dir":"/tmp/fake-current"},"last_seen":9999999999}
EOF
bash claude-code.swiftbar/plugin.1s.sh | grep -i "fake-current\|/tmp" || echo "NOT FOUND"
# 清理
rm "$PROJ_DIR/.cc-meta.json"
```

Expected: plugin 输出中能看到 `/tmp/fake-current` 作为 proj_path 的痕迹（`proj_name` 会变成 `fake-current`，菜单项里能看到）。证明 meta 已被读取并优先使用。

- [ ] **Step 5: Commit**

```bash
git add claude-code.swiftbar/plugin.1s.sh
git commit -m "feat: plugin reads .cc-meta.json, prefers it over JSONL cwd scan"
```

---

## Task 4: 进程子树检测 + 删除 SILENT_RUNNING_MAX 魔数

**Files:**
- Modify: `claude-code.swiftbar/plugin.1s.sh`

- [ ] **Step 1: inspect_claude_procs 增加 has_active_child 字段**

在 `claude-code.swiftbar/plugin.1s.sh` 找到 `inspect_claude_procs()` 函数体里的这一段：

```python
        info = {"pid": pid, "cwd": None, "env": {}, "host": "other"}
```

替换为：

```python
        info = {"pid": pid, "cwd": None, "env": {}, "host": "other", "has_active_child": False}
```

然后在同一函数中，在 host 检测代码块**之前**（即 `# Host detection: env vars first, parent chain fallback` 注释那行**之前**）插入：

```python
        # Detect active child processes (real work in flight: Bash, Read, Edit, etc.).
        # Used to distinguish "Claude is running a tool" from "Claude is waiting on the model
        # or sitting idle". Replaces the SILENT_RUNNING_MAX heuristic.
        try:
            children = subprocess.check_output(
                ["pgrep", "-P", pid],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            info["has_active_child"] = bool(children)
        except subprocess.CalledProcessError:
            info["has_active_child"] = False
        except Exception:
            info["has_active_child"] = False
```

- [ ] **Step 2: 删除 SILENT_RUNNING_MAX 常量**

找到这一行:

```python
SILENT_RUNNING_MAX = 600  # alive_proc + silent jsonl counted as running only up to 10 min
```

直接删除。

- [ ] **Step 3: 修改 classify 签名**

找到:

```python
def classify(entries, mtime, alive_proc):
```

改成:

```python
def classify(entries, mtime, alive_proc, has_active_child):
```

- [ ] **Step 4: 替换 classify 内的静默 running 规则**

在 classify 函数中找到这段:

```python
    # 5) Process is alive but jsonl has been silent — Claude is doing internal work
    #    (compacting context, long thinking turn, network call). Show as running,
    #    but cap how long we maintain that fiction — beyond SILENT_RUNNING_MAX it's
    #    more likely a zombie / drifted shell with no actual work in flight.
    if alive_proc and RUNNING_SECS <= age < SILENT_RUNNING_MAX:
        if t == "assistant" and last_kind == "tool_use":
            return ("running", f"working… (last: {last_tool})")
        if t == "user":
            return ("running", "compacting / processing…")
        if t == "assistant" and last_kind in ("thinking", "text"):
            return ("running", "thinking / compacting…")
```

替换为:

```python
    # 5) Process is alive AND has an active child process — real work in flight
    #    (Bash, Edit, Read, etc.). No time cap needed: the child either exists
    #    (running) or it doesn't (fall through to idle classification below).
    #    Replaces the SILENT_RUNNING_MAX heuristic.
    if alive_proc and has_active_child and age >= RUNNING_SECS:
        if t == "assistant" and last_kind == "tool_use":
            return ("running", f"working… (last: {last_tool})")
        if t == "user":
            return ("running", "compacting / processing…")
        if t == "assistant" and last_kind in ("thinking", "text"):
            return ("running", "thinking / compacting…")
```

- [ ] **Step 5: 在调用 classify 处传入 has_active_child**

找到主循环里的这一段:

```python
    is_recent = (now - mtime) < ALIVE_SECS
    matched = cwd_map.get(proj_path) if real_cwd else []
    alive = is_recent or bool(matched)

    # Hook-written status takes priority when fresh (< HOOK_STATUS_TTL).
    # Falls back to JSONL-based classify() for sessions without hooks configured.
    hook_state, hook_detail = read_hook_status(pdir)
    if hook_state:
        state, detail = hook_state, hook_detail
        alive = True  # hook only fires for live sessions
    else:
        state, detail = classify(entries, mtime, alive_proc=alive)
```

替换为:

```python
    is_recent = (now - mtime) < ALIVE_SECS
    matched = cwd_map.get(proj_path) if real_cwd else []
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
```

- [ ] **Step 6: 跑 plugin 验证语法、整体输出健康**

Run: `bash claude-code.swiftbar/plugin.1s.sh | head -40`

Expected: 输出含菜单标题、`Alive sessions: N · procs: M`、若干 session 行；无 Python traceback。

- [ ] **Step 7: 验证 has_active_child 在真实 claude 进程上工作**

Run:
```bash
/usr/bin/python3 <<'PY'
import subprocess
pids = subprocess.check_output(["pgrep","-x","claude"],text=True).split()
for pid in pids:
    try:
        kids = subprocess.check_output(["pgrep","-P",pid],text=True,stderr=subprocess.DEVNULL).strip()
        print(f"claude {pid}: children={kids or '(none)'}")
    except subprocess.CalledProcessError:
        print(f"claude {pid}: children=(none)")
PY
```

Expected: 每个 claude 进程列出其子进程 PID 或 `(none)`。这是 plugin 正在使用的同一信号。

- [ ] **Step 8: 启动一个长 Bash 测试 has_active_child=true 的路径**

打开一个 Claude 会话，让它跑 `Bash sleep 30`，等 35 秒以上确保超过 RUNNING_SECS=30s 的窗口，盯着菜单栏。

Expected: 整个 sleep 期间菜单栏显示 running，**不会**因为静默而切回 idle（因为 has_active_child 在整个 sleep 期间都是 true）。

> 若没空手测，至少 Step 6/7 通过即可视作冒烟通过；Step 8 留给最终回归。

- [ ] **Step 9: Commit**

```bash
git add claude-code.swiftbar/plugin.1s.sh
git commit -m "feat: replace SILENT_RUNNING_MAX with pgrep -P child detection"
```

---

## Task 5: 端到端回归 + 文档更新

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 在 README 的 Hook 章节后追加 statusLine 章节**

找到 `## Hook（可选，大幅提升状态准确度）` 整节（README.md 第 34 行起），在该节末尾的空行**之后**追加：

```markdown
## statusLine（可选，让 cwd / model 实时准确）

statusLine 钩子由 Claude Code 在每次状态行刷新时触发，给插件提供权威的
`session_id`、`cwd`、`workspace.current_dir`、`model` 等元数据。**有了它，
用户在会话中 `cd` 切目录时菜单栏 1-2 秒内就能反映**——不再依赖扫 JSONL 头部的
启发式（那种方式拿到的是会话第一条记录的 cwd，会过时）。

`install.sh` 会幂等地往 `~/.claude/settings.json` 写入：

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash \"<plugin>/.bin/cc-meta-writer\""
  }
}
```

**如果你已经装了别的 statusline 工具**（比如 ccometix、claude-code-statusline-pro），
脚本会**警告并跳过**，不覆盖你的配置。要兼容,可以让你现有的 statusline 命令在
最前面调一次 `cc-meta-writer`（它的 stdout 是空字符串，不影响展示）。

`cc-meta-writer` 写入的 `.cc-meta.json` **没有 TTL**——元数据一旦写过就一直有效，
直到下一次会话事件覆盖。状态信号仍由 hook（`.cc-status.json`）和 JSONL 启发式
负责，三层独立、字段级 fallback。
```

- [ ] **Step 2: README 仓库结构段更新文件清单**

找到这段：

```
claude-code.swiftbar/        # SwiftBar 插件 bundle
├── plugin.1s.sh             # 主脚本(每 1s 刷新)
├── .Contents/Info.plist     # bundle metadata
├── .bin/cc-jump             # 窗口跳转助手(bash 脚本)
├── .bin/cc-status-writer    # Hook 事件→状态写入器
└── .assets/icons/           # 菜单栏 / 菜单图标(.b64 + .png)
```

替换为：

```
claude-code.swiftbar/        # SwiftBar 插件 bundle
├── plugin.1s.sh             # 主脚本(每 1s 刷新)
├── .Contents/Info.plist     # bundle metadata
├── .bin/cc-jump             # 窗口跳转助手(bash 脚本)
├── .bin/cc-status-writer    # Hook 事件→状态写入器(.cc-status.json)
├── .bin/cc-meta-writer      # statusLine 元数据写入器(.cc-meta.json)
└── .assets/icons/           # 菜单栏 / 菜单图标(.b64 + .png)
```

- [ ] **Step 3: 完整 install.sh + plugin 跑一次端到端回归**

Run:
```bash
./install.sh 2>&1 | tail -20
ls -la "$(defaults read com.ameba.SwiftBar PluginDirectory)/claude-code.swiftbar/.bin/"
bash claude-code.swiftbar/plugin.1s.sh | head -30
```

Expected:
- install.sh 输出包含 hook 安装与 statusLine 安装两段；
- plugin bundle 的 `.bin/` 列出 cc-jump / cc-status-writer / cc-meta-writer 三个可执行；
- plugin 输出完整菜单，无错误。

- [ ] **Step 4: 验证 fallback 退化路径未坏**

Run:
```bash
# 模拟所有 .cc-meta.json 被删:
find ~/.claude/projects -name .cc-meta.json -delete
bash claude-code.swiftbar/plugin.1s.sh | head -30
```

Expected: 输出与改造前完全一致——cwd 通过 read_first_cwd 的 JSONL 扫描兜底，无报错。

- [ ] **Step 5: Spec 验收清单逐条勾**

参照 `docs/superpowers/specs/2026-06-13-status-accuracy-design.md` 的"测试计划"清单，至少跑通：
- [ ] 仅装 plugin（无 hook、无 statusLine）：手动备份 settings.json，删 hooks 与 statusLine 节，重启 SwiftBar；行为应与今天一致。
- [ ] 装 statusLine 不装 hook：删 hooks 节保留 statusLine；cwd 实时，state 走启发式。
- [ ] 全装：默认状态。
- [ ] cd 验证：会话中 `cd` 子目录后 1-2 秒内菜单栏反映。
- [ ] 长 Bash 验证：见 Task 4 Step 8。
- [ ] 删 .cc-meta.json：见本 Task Step 4。
- [ ] 已有 statusLine 警告不覆盖：见 Task 2 Step 5。

- [ ] **Step 6: Commit README + 收尾**

```bash
git add README.md
git commit -m "docs: document statusLine hook (.cc-meta-writer) for accurate cwd/model"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] §架构 三层数据源 → Tasks 1-4
- [x] §数据流 字段级 fallback → Task 3 Step 2
- [x] §组件清单/新增 cc-meta-writer → Task 1
- [x] §组件清单/install.sh statusLine → Task 2
- [x] §组件清单/修改 plugin.1s.sh → Tasks 3 & 4
- [x] §`.cc-meta.json` 文件格式 → Task 1 Step 1 payload
- [x] §错误处理 stdin JSON 失败 → Task 1 Step 4
- [x] §错误处理 已有 statusLine 不覆盖 → Task 2 Step 2 与 Step 5
- [x] §错误处理 pgrep -P 异常视为 false → Task 4 Step 1 except 块
- [x] §测试计划清单 → Task 4 Step 8 与 Task 5 Step 5

**Placeholder scan:** 无 TBD/TODO；所有代码块内容完整；命令带预期输出。

**Type consistency:**
- `read_meta` 在 Task 3 定义、Task 3 主循环使用 — 一致。
- `has_active_child` 在 Task 4 Step 1 定义于 proc dict，Step 5 在主循环聚合为 `has_child`，Step 4 在 classify 签名接收同名参数 — 一致。
- `classify(entries, mtime, alive_proc, has_active_child)` 签名在 Step 3 定义，Step 5 调用处使用关键字参数 `alive_proc=alive, has_active_child=has_child` — 一致。
- 文件路径 `.cc-meta.json`、`.cc-status.json` 全文统一 — 一致。
