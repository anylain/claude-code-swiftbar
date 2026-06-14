# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 工程概览

一个 SwiftBar 插件，在 macOS 菜单栏实时显示 Claude Code 的会话状态，点击可一键跳回会话所在窗口（iTerm tab、VS Code、JetBrains）。实现仅依赖 bash + `/usr/bin/python3`，无 Homebrew、无 node、无构建步骤。

## 常用命令

```bash
./install.sh                                          # 把插件软链进 SwiftBar 插件目录，并往 ~/.claude/settings.json 注册 hooks 和 statusLine
bash claude-code.swiftbar/plugin.10s.sh                # 跑一次插件主脚本，查看 SwiftBar 风格的输出
open -g 'swiftbar://refreshplugin?name=plugin.10s.sh'    # 立刻让 SwiftBar 重跑插件（与 hook 触发同一 URL；name 用脚本文件名,见下）
```

无测试、无 linter、无包管理文件。验证方式 = 跑脚本看输出，或通过 SwiftBar 重跑（10s 兜底轮询、菜单里点 `Refresh`、或主动触发上面那条 URL）。

## 改 hook 脚本的安全规程

`cc-status-writer` 和 `cc-meta-writer` 是 Claude Code hook 脚本，被 `UserPromptSubmit` 等**阻塞型 hook** 调用 —— 任何非零退出会 block 用户输入框，整套 Claude Code 失灵直到脚本被修复或卸载。所以改这两个 bash 文件、以及它们调的 `.lib/write_status.py` / `.lib/write_meta.py` / `.lib/is_stop_event.py` / `.lib/maybe_notify.py` **必须**：

1. 改完先 `bash -n <bash-path>` 或 `python3 -m py_compile <py-path>` 静态语法检查（毫秒级，0 副作用）
2. 用真实 payload 跑一次：`echo '<json>' | bash <path>`，看 exit code 是 0
3. bash 脚本头部已有 `set -uo pipefail` + `trap 'exit 0' ERR` + 末尾 `exit 0` 三重兜底；**不要**改回 `set -e` 或删掉 trap，宁可状态文件偶尔脏也不能 block 用户。Python 一侧失败也只 `sys.exit(0)`，绝不抛异常出脚本。

改完没验证就让 Claude 直接保存，等于赌运气 —— 出过事故，参见 git log 里 cc-status-writer 的相关提交。

## 架构

插件在 `plugin.10s.sh` 里维护**三个互相独立的信号层，按字段级 fallback**。理解优先级是关键 —— 几乎所有 bug 都是"错误的层级压过了正确的层级"。

### 信号层优先级（每个会话）

1. **`.cc-status.json`** —— `cc-status-writer` 通过 Claude Code hooks 写入。事件驱动，~1s 延迟。有 60s TTL（`HOOK_STATUS_TTL`）。新鲜时完全覆盖 JSONL 启发式。
2. **`.cc-meta.json`** —— `cc-meta-writer` 通过 `statusLine` hook 写入。提供权威的 `session_id`、`cwd`、`workspace.{project_dir,current_dir}`、`model`。**没有 TTL**,覆盖式更新,作为 `proj_path` 的真理来源。
   - **`workspace.project_dir`** 是会话启动时 Claude Code 识别的项目根(通常是最近的 git 仓库根),`cd` 到子目录**不会**改变它。`proj_path` 取这个 —— 它同时是显示名 `basename(proj_path)` 的稳定来源,以及 `cwd_map` 进程匹配的唯一 key(claude 父进程从不 chdir,其 lsof cwd 永远等于 `project_dir`)。
   - **不能用 `current_dir` 做 cwd_map 备用 lookup**:任何父子目录关系都会让父项目的陈旧 session 偷走子项目的活跃进程(典型场景:`/Users/x/git` 项目的旧 session,`current_dir` 被推到 `/Users/x/git/foo`,而 foo 项目此刻有活跃的 claude 进程 cwd=`/Users/x/git/foo` —— 父 session 会借此误判为 alive)。`current_dir` 当前没有用途,只有 `project_dir` 是匹配权威。
3. **JSONL 启发式** —— `classify()` 读最新 `*.jsonl` 的最后 20 条，结合 `tool_use`/`tool_result` 的配对、`stop_reason`、最后一条的 age、claude 父进程是否有活跃的 Bash 工具子进程，推断状态。这是用户没装 hook 时的兜底。

`alive` 与 `state` 是两个**正交**维度：alive 当且仅当 (a) JSONL 在 `ALIVE_SECS`（120s）内被写过，或 (b) 有 claude 进程的 lsof cwd 与解析后的 `proj_path` 相等。Layer 1 在新鲜时强制 alive=true。

### 路径解析有损 —— 永远不要反推目录名

项目目录把 `/` 编码为 `-`（如 `-Users-panying32-git-claude-code-swiftbar`）。反向解码**天然存在歧义**（真实目录名里就含 `-`）。脚本永远优先使用真实 `cwd`（先看 `.cc-meta.json`，再看 JSONL 的 `cwd` 字段），**解码出的路径只用于显示，绝不用于 `cwd_map` 匹配**。见 `proj_path_decoded` 周边注释。

### 同 cwd 去重

当两个会话解析到同一个 `proj_path`(典型场景:同一个项目目录被两个 claude 实例同时启动),它们都会去认领同一个 claude 进程,被同时判定为 alive。`plugin.10s.sh` 里的第二遍循环按 mtime 决胜:同一 `proj_path` 下只有 mtime 最新者保留进程匹配;其他同路径的旧会话退回 `is_recent` 判定,超期就静默消失。

### 进程探测

`inspect_claude_procs()`（在 `.lib/render_menu.py`）跑 3 个批量 subprocess：

1. **`ps -A -o pid=,ppid=,comm=`** —— 列出所有 claude 父进程（comm 完全匹配 "claude"）以及它们的直接子进程。**不能用 `pgrep -x claude`**：macOS 26（Darwin 25.x）的 `pgrep -x` 偶尔会只返回多个 claude 进程中的一个，过滤规则不透明；`ps -A` + 显式 comm 匹配是稳定的。
2. **`lsof -p <pid1>,<pid2>,...  -Fpn`** —— 一次性抓所有 claude pid 的 cwd（`-d cwd` 通过 `-Fpn` 输出 `p<pid>` / `n<path>` 配对）。
3. **`ps -E -o pid=,command=`**（仅对 claude 父进程）—— 抓环境变量串，用于宿主识别。

**判断"工具运行中"用的是 shell comm 白名单**（`TOOL_CHILD_COMMS = {/bin/zsh, /bin/bash, /bin/sh, ...}`），不是黑名单 —— claude 只在跑 Bash 工具时同步 fork 一个 shell；其他长生命周期辅助进程（MCP、LSP、caffeinate、telemetry watchdog）绝不能被算作"running"。黑名单会被裸 `node` 启动的 LSP 绕过。

**宿主识别**：从 claude 进程的 env 串里嗅出运行环境，写到 `s["host"]` 用于显示文本 tag（`[iTerm]` / `[VSCode]` / `[PyCharm]` 等）。覆盖主流终端模拟器、VSCode 系（含 Cursor / Windsurf 分流）、JetBrains 全家桶（按父进程 .app bundle 路径分流到具体产品）。完整匹配规则在 `inspect_claude_procs()` 和 `host_from_parent()`。

**多 claude 进程同 cwd 的消歧**：同一项目同时被 iTerm CLI + VSCode 扩展打开时，`cwd_map[proj_path]` 会有 2 个候选 proc。按 `CLAUDE_CODE_ENTRYPOINT` env 与会话 `entrypoint` 字段（来自 jsonl）的匹配度排序：CLI session 偏好 env 里**没有** `CLAUDE_CODE_ENTRYPOINT` 的 proc；扩展 session 偏好 env 值相等的 proc。否则 `matched[0]` 随 ps 输出顺序落在错的进程上，host 标签就乱了。

### 渲染性能（fast path）

`render_menu.py` 在调 `inspect_claude_procs()` 之前先做一遍 cheap 项目预扫描（`os.scandir`），只在至少一个项目有 **live signal** 时才跑那 3 个 subprocess。Live signal = 该项目下最新 `*.jsonl` 的 mtime 在 `ALIVE_SECS`（120s）内 **或** 项目目录里存在 `.cc-status.json`（hook sentinel）。

- 全空闲机器：10s 轮询从 ~140ms 降到 ~40ms（3 次 fork+exec 全跳过）。
- 已知缺口：刚启动、还没写第一行 jsonl 的 claude 在第一个 10s tick 看不到，下一 tick 一旦写入立即出现。

### Bundle 布局（packaged plugin）

本插件采用 SwiftBar 的 packaged plugin 形式：bundle 目录以 `.swiftbar` 结尾、入口以 `plugin.` 开头。SwiftBar 把 `claude-code.swiftbar/` 当作 bundle 加载，发现 `plugin.10s.sh` 作为入口并设置 `SWIFTBAR_PLUGIN_PACKAGE_PATH` 指向 bundle 根。

- `.bin/` —— 用户可执行的 bash 脚本：`cc-status-writer`、`cc-meta-writer`（hook 入口）、`cc-jump`（菜单点击命令）。
- `.lib/` —— python 实现：`render_menu.py`（plugin.10s.sh 调）、`write_status.py` / `is_stop_event.py` / `maybe_notify.py`（cc-status-writer 调）、`write_meta.py`（cc-meta-writer 调）。bash 脚本通过 `$(dirname "$0")/../.lib/...` 定位 —— hook 上下文里 `SWIFTBAR_PLUGIN_PACKAGE_PATH` 不一定有，所以不能依赖。

**packaged 模式下 `?name=` 由目录名决定**：永远是 `claude-code`，与入口文件名解耦。改入口文件名不破坏 URL。但 `NSStatusItem Preferred Position` 的 key 仍是入口文件 basename（即 `plugin.10s.sh`），改名要在 `install.sh` 的迁移源列表里加旧名。

SwiftBar 把 `SWIFTBAR_PLUGIN_PACKAGE_PATH` 指向 bundle 根；`plugin.10s.sh` 用它给 `PKG_DIR` 赋值，fallback 才走 `$0` 推断（hook 上下文里没有此 env，所以 `.bin/` 里的脚本一律用 `$(dirname "$0")/../.lib/...`）。

### 刷新去抖（cc-status-writer）

Hook 事件成簇出现（PreToolUse + PostToolBatch + Stop 等）。writer 用 `/tmp/cc-swiftbar-lock-$UID` 做 trailing-edge 去抖：1s 窗口里只有一个 worker 存活并最终触发 `swiftbar://refreshplugin`。**锁按 `$UID` 隔离**，单机多用户互不影响。**陈旧锁回收**：超过 5s 的锁视为孤儿（处理被 SIGKILL 后 EXIT trap 没跑的 worker）。

代价是状态变化到菜单栏有 ~1s 延迟。**调试或验证状态切换时机**时可临时关闭：`CC_REFRESH_DEBOUNCE=0` 或 `touch /tmp/cc-refresh-debounce.disabled`，refresh 即每次 hook 立即触发。

### 通知（maybe_notify.py）

`cc-status-writer` 在 `write_status.py` 之后调 `.lib/maybe_notify.py`,根据刚写入的 `.cc-status.json` 决定是否 fire `swiftbar://notify` URL。条件全部满足才弹:

1. `CC_NOTIFY != 0`(默认开)
2. `state ∈ {needs-permission, needs-decision}`
3. `.cc-notify-last` 里记录的上次 `(sid, state)` 与当前不同(去簇内重复)
4. 宿主前台检测返回 false:iTerm 要求当前 tab 的 `session.path` 以 `proj_dir` 开头;VSCode/JetBrains 仅 frontmost 检测(没稳定 AS API 拿当前项目)。osascript 0.6s timeout → 视作非前台,仍弹。

**配置链路**:`plugin.10s.sh` 头部声明 `<swiftbar.environment>[CC_NOTIFY=1]</swiftbar.environment>`,SwiftBar Preferences UI 据此渲染开关。SwiftBar 把用户选项 inject 进 plugin 主进程的 env,但 Claude Code hook 子进程**不继承** —— 所以 plugin.10s.sh 启动时把 `CC_NOTIFY` 的值落盘到 `~/.claude/.cc-config.env`,`cc-status-writer` 在脚本头 `source` 它。中转文件由 plugin 自己重写,不要手改。

## 值得记住的约束

- **仅 macOS。** 用了 `lsof`、`osascript`、`defaults`、`ps -A` / `ps -E`、`/usr/bin/python3`，无跨平台需求。
- **只用 bash + 系统 Python。** 不要引入 Homebrew/node/三方 Python 包 —— `install.sh` 没有装它们的能力。
- **`install.sh` 幂等。** hook 事件和 statusLine 都检测后再加。它还在插件文件名改变时迁移 `NSStatusItem Preferred Position`（SwiftBar 把改名当成 uninstall+reinstall 会重置菜单栏图标位置）—— 改入口文件名前在 `install.sh` 的迁移源列表里加上旧名。
- **入口文件名里的数字就是刷新周期。** `plugin.10s.sh` → SwiftBar 每 10s 轮询一次。
- **`swiftbar://refreshplugin?name=…` 双 URL fallback**：`cc-status-writer` 同时发送 `?name=plugin.10s.sh`（SwiftBar v2.0.1 当前发布版，把 bundle 当普通目录扫，`name="plugin"`）和 `?name=claude-code`（未来带 packaged plugin 支持的版本，`name="claude-code"`）。两版本 mutex，miss 是 no-op，cost 极低。两条都失败时用 `/usr/bin/log stream --process SwiftBar --level info` 同时 fire URL，看不到 `Refreshing plugin metadata` 就是 name 都没匹配上。
- **状态优先级在两处必须同步**：单会话分类（见上）和标题图标（`priority_order` 在 `plugin.10s.sh`：needs-permission → needs-decision → error → interrupted → needs-input → running）。README 状态表也是按这个顺序排的。改一处就要同步另两处。

## 设计文档

`docs/superpowers/specs/` 和 `docs/superpowers/plans/` 存放过往变更的设计稿（状态准确度、CPU 性能去抖）。改对应子系统时翻一下。
