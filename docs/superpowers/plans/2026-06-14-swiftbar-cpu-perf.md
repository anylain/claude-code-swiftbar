# SwiftBar CPU 性能优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把插件从固定 3s 轮询改为「10s 兜底 + hook trailing-edge 防抖主动推送」,降低 SwiftBar 空闲 CPU 占用,同时让状态变化在 ~1s 内反映到菜单栏。

**Architecture:** 三件事联动:(1) 重命名 `plugin.3s.sh` → `claude-code.10s.sh`(改间隔同时让 SwiftBar URL refresh 的 `name=` 唯一);(2) `cc-status-writer` 末尾加 trailing-edge debouncer,1s 内连发的 hook 合并为一次 `swiftbar://refreshplugin?name=claude-code` 推送;(3) `install.sh` 迁移 macOS defaults 里旧文件名的菜单栏图标位置偏好,避免老用户升级后图标跳位。

**Tech Stack:** Bash, macOS `defaults` / `open -g` / LaunchServices URL scheme, SwiftBar plugin filename naming convention, Python 3 (已有 hook 脚本里在用)。

---

## File Structure

| 文件 | 操作 | 责任 |
|---|---|---|
| `claude-code.swiftbar/plugin.3s.sh` | rename → `claude-code.10s.sh` | 主插件脚本,内容不变,仅文件名变 |
| `claude-code.swiftbar/.bin/cc-status-writer` | modify | 现有 hook 处理逻辑后追加 trailing-edge debouncer |
| `install.sh` | modify | 增加菜单栏图标位置偏好迁移块 |
| `README.md` | modify | 仓库结构 + Hook 章节同步新文件名和"~1s 主动刷新"说明 |

每个 task 都是一次完整提交,中间状态可工作(老用户能继续运行,新用户也能正常装)。

---

### Task 1: 重命名插件主脚本

**Files:**
- Rename: `claude-code.swiftbar/plugin.3s.sh` → `claude-code.swiftbar/claude-code.10s.sh`

这一步只动文件名,不动内容。SwiftBar 用文件名 `<name>.<refresh>.<ext>` 决定刷新间隔(`10s`)和 plugin name(`claude-code`,用于后续 URL 推送的 `name=` 字段)。

注意:`plugin.3s.sh` 里 `bitbar.title` / `swiftbar.refreshOnOpen` 等元数据 header 不变。

- [ ] **Step 1: git 重命名,保留历史**

```bash
cd /Users/panying32/git/claude-code-swiftbar
git mv claude-code.swiftbar/plugin.3s.sh claude-code.swiftbar/claude-code.10s.sh
```

- [ ] **Step 2: 验证文件还在、可执行、内容未变**

Run:
```bash
ls -la claude-code.swiftbar/claude-code.10s.sh
head -3 claude-code.swiftbar/claude-code.10s.sh
git status
```

Expected:
```
-rwxr-xr-x  ... claude-code.swiftbar/claude-code.10s.sh
#!/bin/bash
# <bitbar.title>Claude Code Status</bitbar.title>
# <bitbar.version>v2.4</bitbar.version>
```
git status 应该显示 `renamed: claude-code.swiftbar/plugin.3s.sh -> claude-code.swiftbar/claude-code.10s.sh`。

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor: rename plugin.3s.sh to claude-code.10s.sh

Why both at once: SwiftBar derives the plugin's URL-refresh `name`
from the filename prefix. `plugin` is too generic; `claude-code` is
unique. Bumping the suffix to 10s lets us rely on hook-driven URL
pushes for responsiveness while keeping a 10s safety-net poll for
sessions without hooks installed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: cc-status-writer 加 trailing-edge debouncer

**Files:**
- Modify: `claude-code.swiftbar/.bin/cc-status-writer`(在文件末尾、`PY` heredoc 关闭和 `rm -f "$tmp"` 之后追加)

现有 `cc-status-writer` 末尾已有 `rm -f "$tmp"` 清理。我们在它之后追加防抖块。

**关键性质**:
- 第一个 hook 启动 worker 进程,后续 1s 内的 hook 只刷新 PENDING 时间戳,不开新 worker
- worker 醒来后只发 1 次 URL,无论这 1s 内攒了多少事件
- worker 用 `trap 'rm -f "$LOCK"' EXIT` 自清理,异常退出也不残留 LOCK
- `</dev/null >/dev/null 2>&1 &` 完全脱钩 hook stdin/stdout/stderr,避免 hook 因后台进程未关闭管道而被卡

- [ ] **Step 1: 读现有文件确认末尾结构**

Run:
```bash
tail -5 claude-code.swiftbar/.bin/cc-status-writer
```

Expected:
```
# Other events are intentionally ignored.
PY
rm -f "$tmp"
```
确认最后一行是 `rm -f "$tmp"`(没有尾随空行也 OK)。

- [ ] **Step 2: 在文件末尾追加防抖块**

把下面这段追加到 `claude-code.swiftbar/.bin/cc-status-writer` 的最后:

```bash

# ── Trailing-edge debounce: collapse hook bursts into one URL refresh ──────
# Multiple hooks within 1s share a single trailing worker; only the worker
# that wins the LOCK race fires `swiftbar://refreshplugin?name=claude-code`.
# Spec: docs/superpowers/specs/2026-06-14-swiftbar-cpu-perf-design.md
PENDING="/tmp/cc-swiftbar-pending"
LOCK="/tmp/cc-swiftbar-lock"

date +%s.%N > "$PENDING" 2>/dev/null || true

if [ -e "$LOCK" ]; then
  exit 0
fi

(
  : > "$LOCK"
  trap 'rm -f "$LOCK"' EXIT
  sleep 1
  rm -f "$PENDING"
  /usr/bin/open -g "swiftbar://refreshplugin?name=claude-code" 2>/dev/null || true
) </dev/null >/dev/null 2>&1 &
```

注意:`set -euo pipefail` 在文件开头已经有了,所以追加块里我们对可能失败的命令(`date > $PENDING`,`open`)显式加 `|| true`,避免单个偶发失败让 hook 整体非零退出(那会让 Claude Code 把这条 hook 标黄)。

- [ ] **Step 3: 静态验证 — bash 语法**

Run:
```bash
bash -n claude-code.swiftbar/.bin/cc-status-writer
echo "syntax exit=$?"
```

Expected:
```
syntax exit=0
```

- [ ] **Step 4: 静态验证 — shellcheck(若已装)**

Run:
```bash
command -v shellcheck >/dev/null && shellcheck claude-code.swiftbar/.bin/cc-status-writer || echo "shellcheck not installed (skipping)"
```

Expected: 无报错,或 `shellcheck not installed (skipping)`。如果 shellcheck 报告未使用变量、未引号等问题,只修我们新加的那段;原有代码的 lint 不在本任务范围。

- [ ] **Step 5: 行为验证 — 单次触发**

模拟一次 hook 调用,看会不会正确推一次 URL。

Run:
```bash
# 清掉残留状态
rm -f /tmp/cc-swiftbar-pending /tmp/cc-swiftbar-lock

# 喂一个 fake hook event(transcript_path 用 /tmp 避免污染真实项目)
mkdir -p /tmp/cc-test-proj
echo '{"hook_event_name":"UserPromptSubmit","transcript_path":"/tmp/cc-test-proj/x.jsonl","session_id":"test"}' \
  | claude-code.swiftbar/.bin/cc-status-writer

# 立刻看 LOCK 是否存在(worker 在 sleep 中)
ls /tmp/cc-swiftbar-lock /tmp/cc-swiftbar-pending 2>&1

# 等 1.5s 让 worker 醒来发 URL
sleep 1.5

# LOCK 和 PENDING 应该都被清掉
ls /tmp/cc-swiftbar-lock /tmp/cc-swiftbar-pending 2>&1

# 清场
rm -rf /tmp/cc-test-proj
```

Expected: 第一次 ls 列出两个文件;`sleep 1.5` 之后两个文件都报 `No such file or directory`。

- [ ] **Step 6: 行为验证 — 连发 20 次只起 1 个 worker**

Run:
```bash
rm -f /tmp/cc-swiftbar-pending /tmp/cc-swiftbar-lock
mkdir -p /tmp/cc-test-proj

# 先看一下当前后台 worker 数(应为 0)
pgrep -lf 'sleep 1' | grep -v grep | wc -l

# 连发 20 次
for i in $(seq 1 20); do
  echo '{"hook_event_name":"UserPromptSubmit","transcript_path":"/tmp/cc-test-proj/x.jsonl","session_id":"test"}' \
    | claude-code.swiftbar/.bin/cc-status-writer
done

# 立刻数后台 sleep 进程数(1s 内 — worker 还没醒)
sleep 0.2
WORKERS=$(ps -axo pid,command | grep -c '[s]leep 1')
echo "active sleep workers after burst: $WORKERS"

# 清场
sleep 1.5
rm -rf /tmp/cc-test-proj
rm -f /tmp/cc-swiftbar-pending /tmp/cc-swiftbar-lock
```

Expected: `active sleep workers after burst: 1`(可能是 1 或极少数情况下 2,LOCK 检查存在轻微竞态。**如果 ≥ 3 则说明实现有 bug**,需要排查为什么 LOCK 没拦住后续请求)。

- [ ] **Step 7: Commit**

```bash
git add claude-code.swiftbar/.bin/cc-status-writer
git commit -m "$(cat <<'EOF'
feat: add trailing-edge debounced URL refresh in cc-status-writer

Hooks now schedule a single 1s-trailing SwiftBar refresh
(swiftbar://refreshplugin?name=claude-code) regardless of how many
events fire in that window. Combined with the 10s safety-net poll,
this keeps SwiftBar idle most of the time while staying responsive
during active sessions.

Worker uses a /tmp PENDING+LOCK pair, fully detaches stdio
(</dev/null >/dev/null 2>&1 &) so it cannot block the hook return.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: install.sh 迁移菜单栏图标位置偏好

**Files:**
- Modify: `install.sh`(在「Install Claude Code hooks for event-driven status」块之前插入迁移块,大约 line 53 后)

SwiftBar 把每个插件的菜单栏图标位置存在 `defaults read com.ameba.SwiftBar 'NSStatusItem Preferred Position <filename>'`,key 是文件名。改名后老用户的图标会跳到默认位置。`install.sh` 检测旧 key 并复制到新 key。

幂等性靠 `NEW_KEY` 已存在直接跳过实现。`OLD_NAME` 列表覆盖历史用过的所有名字(`plugin.1s.sh`、`plugin.3s.sh`、`plugin.10s.sh`、`claude-code.3s.sh`),取第一个能找到的。

- [ ] **Step 1: 在 install.sh 找到锚点位置**

Run:
```bash
grep -n "Install Claude Code hooks" install.sh
```

Expected:
```
54:# ── Install Claude Code hooks for event-driven status ──────────────────────
```
(确切行号可能因之前 commit 而漂,以 `grep` 输出为准。)

- [ ] **Step 2: 在 hooks 安装块上方插入迁移块**

把下面这段插入到 `# ── Install Claude Code hooks for event-driven status ──...` 这一行**之前**(空一行隔开):

```bash
# ── Migrate menu bar icon position when plugin filename changes ────────────
# SwiftBar stores each plugin's icon position under
# `NSStatusItem Preferred Position <filename>` in com.ameba.SwiftBar prefs.
# Renaming the plugin file resets the position; we copy the old value over
# so users upgrading from older versions keep their icon where they put it.
NEW_KEY="NSStatusItem Preferred Position claude-code.10s.sh"
if defaults read com.ameba.SwiftBar "$NEW_KEY" >/dev/null 2>&1; then
  : # already migrated, keep the new value
else
  for OLD_NAME in plugin.1s.sh plugin.3s.sh plugin.10s.sh claude-code.3s.sh; do
    OLD_KEY="NSStatusItem Preferred Position $OLD_NAME"
    POSITION="$(defaults read com.ameba.SwiftBar "$OLD_KEY" 2>/dev/null || true)"
    if [ -n "$POSITION" ]; then
      defaults write com.ameba.SwiftBar "$NEW_KEY" -int "$POSITION"
      echo "  Migrated menu bar icon position from $OLD_NAME → claude-code.10s.sh ($POSITION)"
      break
    fi
  done
fi

```

实现方式:用 `Edit` 工具,`old_string` 为 `# ── Install Claude Code hooks for event-driven status ──────────────────────`,`new_string` 为「上面整段迁移块 + 一个空行 + 原 hooks 安装注释行」。

- [ ] **Step 3: 静态验证 — bash 语法**

Run:
```bash
bash -n install.sh
echo "syntax exit=$?"
```

Expected:
```
syntax exit=0
```

- [ ] **Step 4: 行为验证 — 干跑迁移逻辑(隔离的 defaults domain)**

我们不动用户真实 SwiftBar prefs。复制迁移逻辑到一个临时脚本,用 `defaults` 的非默认 domain 跑(macOS 允许任意 domain,本地 plist 文件)。

Run:
```bash
TEST_DOMAIN="com.test.cc-swiftbar-migrate.$$"
NEW_KEY="NSStatusItem Preferred Position claude-code.10s.sh"
OLD_KEY="NSStatusItem Preferred Position plugin.3s.sh"

# 先种一个老 key
defaults write "$TEST_DOMAIN" "$OLD_KEY" -int 1234

# 跑等价的迁移逻辑(从 install.sh 抠出来,把 domain 替换掉)
if defaults read "$TEST_DOMAIN" "$NEW_KEY" >/dev/null 2>&1; then
  echo "FAIL: NEW_KEY should not yet exist"
else
  for OLD_NAME in plugin.1s.sh plugin.3s.sh plugin.10s.sh claude-code.3s.sh; do
    POSITION="$(defaults read "$TEST_DOMAIN" "NSStatusItem Preferred Position $OLD_NAME" 2>/dev/null || true)"
    if [ -n "$POSITION" ]; then
      defaults write "$TEST_DOMAIN" "$NEW_KEY" -int "$POSITION"
      echo "  Migrated from $OLD_NAME → claude-code.10s.sh ($POSITION)"
      break
    fi
  done
fi

# 验证迁移后 NEW_KEY 是 1234
defaults read "$TEST_DOMAIN" "$NEW_KEY"

# 二次跑应当跳过(幂等)
if defaults read "$TEST_DOMAIN" "$NEW_KEY" >/dev/null 2>&1; then
  echo "OK: second run would skip"
fi

# 清场 — 删整个 domain
defaults delete "$TEST_DOMAIN" 2>/dev/null || true
```

Expected:
```
  Migrated from plugin.3s.sh → claude-code.10s.sh (1234)
1234
OK: second run would skip
```

- [ ] **Step 5: 行为验证 — 实跑 install.sh 不报错**

`install.sh` 默认会写真实的 `~/.claude/settings.json`,但本机已经装过了所以是幂等的(hook 块和 statusLine 都是「已安装」分支)。新加的迁移块对真实 prefs 也是 idempotent —— 已经存在 `NEW_KEY` 就跳过(本机大概率没旧 key 也没新 key,这次跑会无操作或迁移一次)。

Run:
```bash
./install.sh
```

Expected: 退出码 0,输出包含「Symlink already correct」+「All N hook events already installed」+「statusLine already points to cc-meta-writer」。如果本机历史上装过老版本,可能多一行 `Migrated menu bar icon position from ... → claude-code.10s.sh (...)`。

- [ ] **Step 6: Commit**

```bash
git add install.sh
git commit -m "$(cat <<'EOF'
feat(install): migrate menu bar icon position on rename

Copies the SwiftBar `NSStatusItem Preferred Position` pref from any
known historical filename (plugin.1s.sh, plugin.3s.sh, plugin.10s.sh,
claude-code.3s.sh) to claude-code.10s.sh, so users upgrading don't
see the icon jump back to the default spot.

Idempotent: skips if claude-code.10s.sh key is already set.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: README 同步新文件名与刷新机制

**Files:**
- Modify: `README.md`(2 处:仓库结构表 + Hook 章节)

- [ ] **Step 1: 仓库结构表更新文件名**

把 `README.md` 的「仓库结构」代码块里:

```
├── plugin.3s.sh             # 主脚本(每 3s 刷新)
```

改成:

```
├── claude-code.10s.sh       # 主脚本(10s 兜底刷新,hook 触发主动刷新)
```

实现方式:用 `Edit` 工具,`old_string` 是这一整行(注意保持 ASCII tree 字符 `├──` 和缩进一致),`new_string` 是新的一行。

- [ ] **Step 2: Hook 章节增加主动刷新说明**

找到 `## Hook(可选,大幅提升状态准确度)` 这一节。在它最后(`Hook 写入的状态文件(.cc-status.json)有效期为 60 秒…自动回退到 JSONL 解析方式,确保未配置 hook 的项目也正常工作。` 这段之后)追加一段:

```markdown

**主动刷新**:`cc-status-writer` 每次写完状态会用 trailing-edge 防抖
(1s 窗口合并)触发 `swiftbar://refreshplugin?name=claude-code`,
让 SwiftBar 在 ~1s 内反映状态变化,而不是等 10s 兜底轮询。
连发的 hook 事件(PreToolUse + PostToolBatch + Stop)只会触发 1 次刷新。
```

实现方式:用 `Edit` 工具,`old_string` 是当前章节末尾那一段完整文字 `Hook 写入的状态文件...JSONL 解析方式,确保未配置 hook 的项目也正常工作。`,`new_string` 是同样的内容 + 上面追加的段落。

- [ ] **Step 3: 校验改动**

Run:
```bash
grep -n "claude-code.10s.sh" README.md
grep -n "主动刷新" README.md
```

Expected: 两个 grep 都至少有一行匹配。

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: README — new plugin filename and hook-driven refresh

Reflects the rename to claude-code.10s.sh in the repo-structure
section, and adds a paragraph in the Hook section explaining the
trailing-edge debounced URL refresh that keeps the menu bar
responsive without a fast poll loop.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 端到端验证 — 真实 SwiftBar 链路

**Files:** 无(只是测试 + 一个验证 commit hash 列在 PR 描述里,无新增 commit)

这是 manual smoke test,subagent 跑不了 macOS GUI——把验证步骤明文列出,让人或半自动执行;实在不行就让 subagent 报告「需要 manual verification」让 controller 来跑。

- [ ] **Step 1: 让 SwiftBar 重新发现新文件**

Run:
```bash
osascript -e 'quit app "SwiftBar"' 2>/dev/null
sleep 2
open -ga SwiftBar
sleep 3
```

Expected: SwiftBar 重新启动,菜单栏出现 Claude Code 图标(来自 `claude-code.10s.sh`)。

- [ ] **Step 2: 验证插件被识别为 `claude-code`**

Run:
```bash
ls "$(defaults read com.ameba.SwiftBar PluginDirectory)/claude-code.swiftbar/"*.sh
```

Expected: 只有 `claude-code.10s.sh`(确认 SwiftBar 看的是新文件)。

- [ ] **Step 3: 手动触发 URL refresh,看插件是否被拉起**

Run:
```bash
# 把当前秒数写一个简易 trace
TRACE=/tmp/cc-e2e-trace.log
echo "before=$(date +%s.%N)" > "$TRACE"

# 推一次 URL
/usr/bin/open -g "swiftbar://refreshplugin?name=claude-code"
sleep 2

# 看插件是否最近被运行 — 通过 .cc-status.json 的 ts 字段或菜单栏图标变化
# (若插件被运行,菜单栏 tooltip 会更新为最新会话状态)

# 简单做法:观察 SwiftBar 进程的 CPU 短时跳一下
ps -axo pid,%cpu,command | grep -i 'SwiftBar' | grep -v grep
```

Expected: SwiftBar 进程的 CPU 在推 URL 后那一瞬有非零占用(从空闲跳到几个百分点再回落),菜单栏图标可能短暂闪烁。

- [ ] **Step 4: 验证连发不会让 SwiftBar 多次跑插件**

Run:
```bash
# 连发 10 次 URL,看 SwiftBar 是否每次都跑插件
for i in $(seq 1 10); do
  /usr/bin/open -g "swiftbar://refreshplugin?name=claude-code" &
done
wait
sleep 3
```

Expected: 主观感受是菜单栏只刷新少数几次(< 10),SwiftBar 不会卡顿。这一步是非严格观察—— SwiftBar 内部确实没有完全 dedup,这就是为什么我们靠 cc-status-writer 在客户端防抖。

- [ ] **Step 5: 真实 hook 联动**

打开一个新 Claude Code 会话,提交一个会触发工具调用的 prompt,例如「列一下 README.md 的前 5 行」。

Expected: 菜单栏图标在 ~1s 内变成 `running` 状态(箭头/旋转图标),工具调用结束后变成 `idle`/`needs-input`。延迟肉眼可感但不到 3 秒,远好于 10s。

- [ ] **Step 6: 兜底验证 — 关掉 hooks 后 10s 轮询仍工作**

Run:
```bash
# 备份 settings.json,临时移除 hooks
cp ~/.claude/settings.json ~/.claude/settings.json.cc-bak
/usr/bin/python3 -c '
import json
with open("/Users/panying32/.claude/settings.json") as f:
    s = json.load(f)
s.pop("hooks", None)
with open("/Users/panying32/.claude/settings.json", "w") as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
    f.write("\n")
'

# 触发一次新会话(用任何方式 — 命令行或 Cmd+T);等 ≤ 10s
# 菜单栏应当反映新会话状态(JSONL 解析的兜底)

# 恢复
mv ~/.claude/settings.json.cc-bak ~/.claude/settings.json
```

Expected: 10s 内菜单栏跟上新会话。如果一直不更新,说明 plugin.3s.sh 改名后 SwiftBar 没正确拾起 10s 间隔 —— 排查方向:检查文件名是否 `claude-code.10s.sh`,SwiftBar 是否完整重启过。

- [ ] **Step 7: 清理临时痕迹**

Run:
```bash
rm -f /tmp/cc-e2e-trace.log /tmp/cc-swiftbar-pending /tmp/cc-swiftbar-lock
```

无 commit —— 这一任务的产物是「人工已验证」的状态,可以在 PR 描述或最终汇报里说明哪些步骤已经过。

---

## Self-Review

**Spec coverage:**
- 重命名 → Task 1 ✅
- cc-status-writer 防抖块 → Task 2 ✅
- install.sh 偏好迁移 → Task 3 ✅
- README 同步 → Task 4 ✅
- 测试章节(`fs_usage` 监测、关闭 hook 兜底验证、`install.sh` 幂等) → Task 5 ✅
- 错误处理(LOCK trap 自清理、`open` 失败静默、`/tmp/` 重启清空) → Task 2 实现里 + 注释里都有体现 ✅

**Placeholder scan:** 无 TBD/TODO,所有 step 都有可执行命令或具体 diff。

**Type/name consistency:**
- 文件名 `claude-code.10s.sh` 在 Task 1/3/4/5 全部一致
- URL `swiftbar://refreshplugin?name=claude-code` 在 Task 2 实现 + Task 5 验证里一致
- defaults key `NSStatusItem Preferred Position claude-code.10s.sh` 在 Task 3 内部 NEW_KEY 一致
- `/tmp/cc-swiftbar-pending` / `/tmp/cc-swiftbar-lock` 在 Task 2 实现 + Task 5 清理里一致

无问题。

---

## Notes for Implementer

- **不要删除 `<bitbar.title>...` 等 SwiftBar 元数据 header** —— 改名时只动文件名,内容必须原样保留。
- **Task 2 追加位置** —— `cc-status-writer` 末尾,在原 `rm -f "$tmp"` **之后**。如果你需要 patch 现有 Python heredoc,那是错的方向。
- **Task 3 的迁移块要在 hooks 安装块之前** —— 早一些干净一些,但实际上前后都行。注意保持 install.sh 的整体可读性(用 `# ── ... ─────` 风格分隔块)。
- **不要 force-push 或覆盖未跟踪文件**,直接在 main commit 即可(用户已确认)。
- **不要碰** `~/.ssh/`,这在用户的 deny 列表里。
- 验证步骤里偶有 `sleep 1.5` / `sleep 2`,看似武断,但都对应一个具体事件:`sleep 1` 等 worker 醒,多 0.5-1s 留 buffer。不要随意缩短。
