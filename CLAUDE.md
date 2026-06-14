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

无测试、无 linter、无包管理文件。验证方式 = 跑脚本看输出，或 SwiftBar `Refresh All` 后看菜单栏。

迭代时每次保存都需要 SwiftBar 重跑脚本：等 10s 兜底轮询、点下拉菜单里的 `Refresh`、或主动触发上面那条 URL。

## 改 hook 脚本的安全规程

`cc-status-writer` 和 `cc-meta-writer` 是 Claude Code hook 脚本，被 `UserPromptSubmit` 等**阻塞型 hook** 调用 —— 任何非零退出会 block 用户输入框，整套 Claude Code 失灵直到脚本被修复或卸载。所以改这两个 bash 文件、以及它们调的 `.lib/write_status.py` / `.lib/write_meta.py` / `.lib/is_stop_event.py` **必须**：

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

#### 宿主识别的 env 关键字（覆盖 30+ 工具）

按优先级匹配，命中即停：

- **终端模拟器**：`ITERM_SESSION_ID` → iterm；`TERM_PROGRAM=Apple_Terminal` → terminal；`TERM_PROGRAM=WarpTerminal` → warp；类似还有 ghostty、wezterm、hyper、tabby；`ALACRITTY_LOG` → alacritty；`KITTY_WINDOW_ID` / `TERM=xterm-kitty` → kitty。
- **VSCode 系**：`VSCODE_INJECTION` / `VSCODE_PID` / `TERM_PROGRAM=vscode` 命中后，再看 `VSCODE_GIT_ASKPASS_NODE` / `VSCODE_IPC_HOOK` 路径里有没有 "cursor" / "windsurf" 子串，分流出 cursor / windsurf / vscode。
- **JetBrains 全家桶**：`TERMINAL_EMULATOR` 含 `JetBrains` 后，从父进程链 (`pid_ppid` + `pid_comm`) 上溯 `.app` bundle 路径里的关键字，分流出 intellij / pycharm / webstorm / goland / rubymine / clion / phpstorm / rider / datagrip / rustrover / aqua / appcode / dataspell / androidstudio / fleet。匹配不到具体产品时回落到 `jetbrains`。

**多 claude 进程同 cwd 的消歧**：同一项目同时被 iTerm CLI + VSCode 扩展打开时，`cwd_map[proj_path]` 会有 2 个候选 proc。按 `CLAUDE_CODE_ENTRYPOINT` env 与会话 `entrypoint` 字段（来自 jsonl）的匹配度排序：CLI session 偏好 env 里**没有** `CLAUDE_CODE_ENTRYPOINT` 的 proc；扩展 session 偏好 env 值相等的 proc。否则 `matched[0]` 随 ps 输出顺序落在错的进程上，host 标签就乱了。

#### 菜单里 host 是文本 tag,不是图标

每个会话项后缀 `  [iTerm]` / `  [VSCode]` / `  [PyCharm]` 之类的纯文本 —— 来自 `HOST_TAG` 字典(`render_menu.py` 顶部)。曾经为每个会话画 PNG 图标,被 SwiftBar 渲染时存在的 CPU 抖动放大,改文本后稳定且零成本。**只有菜单栏标题的状态图标(✨/🔐/✋ 等)是 b64 PNG**,且全程不变化(状态码相同则 hash 相同),SwiftBar 增量 diff 在 `MenuItemChange.unchanged` 上 break,不重建 NSMenuItem。

### 渲染性能（fast path）

`render_menu.py` 在调 `inspect_claude_procs()` 之前先做一遍 cheap 项目预扫描（`os.scandir`），只在至少一个项目有 **live signal** 时才跑那 3 个 subprocess。Live signal = 该项目下最新 `*.jsonl` 的 mtime 在 `ALIVE_SECS`（120s）内 **或** 项目目录里存在 `.cc-status.json`（hook sentinel）。

- 全空闲机器：10s 轮询从 ~140ms 降到 ~40ms（3 次 fork+exec 全跳过）。
- 有活会话：行为完全不变，走全量 inspect。
- 已知缺口：刚启动、还没写第一行 jsonl 的 claude 在第一个 10s tick 看不到，下一 tick 一旦写入立即出现。

`.cc-status.json` 文件本身没在这步做 mtime 校验（只看存在），但 `read_hook_status()` 内部有 `HOOK_STATUS_TTL`（60s）freshness gate，过期内容只会浪费一次 inspect，不会误判 alive。

### Bundle 布局（packaged plugin）

本插件采用 SwiftBar 的 packaged plugin 形式（见 `SwiftBar/Plugin/PackagedPlugin.swift`）：bundle 目录以 `.swiftbar` 结尾、入口以 `plugin.` 开头。SwiftBar 把整个 `claude-code.swiftbar/` 当作 bundle 加载，发现 `plugin.10s.sh` 作为主脚本入口，并设置 `SWIFTBAR_PLUGIN_PACKAGE_PATH` 指向 bundle 根。

`.bin/`、`.lib/`、`.assets/`、`.Contents/` 仍带点前缀 —— packaged 模式下其实不必（SwiftBar 只扫 `plugin.*`），保留只是历史习惯加一层防御性隐藏，不影响行为。

- `.bin/` —— 用户可直接调用的可执行 bash 脚本：`cc-status-writer`、`cc-meta-writer`（hook 入口）、`cc-jump`（菜单项点击的 bash 命令）。
- `.lib/` —— python 实现：`render_menu.py`（plugin.10s.sh 调）、`write_status.py` / `is_stop_event.py`（cc-status-writer 调）、`write_meta.py`（cc-meta-writer 调）。bash 脚本通过 `$(dirname "$0")/../.lib/...` 定位，hook 上下文里 `SWIFTBAR_PLUGIN_PACKAGE_PATH` 不一定有，所以不能依赖。

**packaged 模式下两条恒等关系**（与普通脚本插件不同）：

- **`?name=` 由目录名决定**：`name = packageDirectory.lastPathComponent.replacingOccurrences(of: ".swiftbar", with: "")` → 永远是 `claude-code`，与入口文件名解耦。改入口文件名不会破坏 URL。
- **`plugin.id` = bundle 目录的 resolved path**（不是入口文件路径）。`autosaveName` = `plugin.id`，但实测 macOS 把 `NSStatusItem Preferred Position` 的 key 取了 basename，仍是入口文件名（即 `plugin.10s.sh`）。`install.sh` 已按此迁移历史 key（`claude-code.10s.sh` 等）。

### 刷新去抖（cc-status-writer）

Hook 事件成簇出现（PreToolUse + PostToolBatch + Stop 等）。writer 用 `/tmp/cc-swiftbar-lock-$UID` 做 trailing-edge 去抖：1s 窗口里只有一个 worker 存活并最终触发 `swiftbar://refreshplugin`。**锁按 `$UID` 隔离**，单机多用户互不影响。**陈旧锁回收**：超过 5s 的锁视为孤儿（处理被 SIGKILL 后 EXIT trap 没跑的 worker）。

代价是状态变化到菜单栏有 ~1s 延迟。**调试或验证状态切换时机**时可临时关闭：`CC_REFRESH_DEBOUNCE=0` 或 `touch /tmp/cc-refresh-debounce.disabled`，refresh 即每次 hook 立即触发。

## 值得记住的约束

- **仅 macOS。** 用了 `lsof`、`osascript`、`defaults`、`ps -A` / `ps -E`、`/usr/bin/python3`，无跨平台需求。
- **只用 bash + 系统 Python。** 不要引入 Homebrew/node/三方 Python 包 —— `install.sh` 没有装它们的能力。
- **`install.sh` 幂等。** hook 事件和 statusLine 都检测后再加。它还会在插件文件名改变时迁移菜单栏图标位置（SwiftBar 把改名当成 uninstall+reinstall 会重置位置）—— 见 `NSStatusItem Preferred Position` 那段。当前迁移目标 key 是 `plugin.10s.sh`，源列表覆盖 `claude-code.10s.sh`、`claude-code.3s.sh`、`plugin.3s.sh`、`plugin.1s.sh`。
- **入口文件名里的数字就是刷新周期。** `plugin.10s.sh` → SwiftBar 每 10s 轮询一次。改名既影响刷新频率也影响 `NSStatusItem Preferred Position` 偏好 key —— 改前在 `install.sh` 的迁移源列表里加上旧名。
- **`swiftbar://refreshplugin?name=…` 双 URL fallback**:`cc-status-writer` 同时发送 `?name=plugin.10s.sh` 和 `?name=claude-code`,任意 SwiftBar 版本下至少一个命中。原因:SwiftBar v2.0.1(当前发布版)还没 packaged plugin 支持(`PackagedPlugin.swift` 在 main 分支但未 tag),`claude-code.swiftbar/` 被当成普通目录递归扫描,`plugin.10s.sh` 被加载为 `ExecutablePlugin`(`id="plugin.10s.sh"`、`name="plugin"`),此时只有 `?name=plugin.10s.sh` 工作。当 SwiftBar 发布带 PackagedPlugin 的版本后,bundle 被识别为单一 packaged plugin(`name="claude-code"`),`?name=plugin.10s.sh` 失效,`?name=claude-code` 接管。两个 URL 互斥但成本极低 — `getPluginByNameOrID` 是 O(n) lookup,miss 是 no-op。两条都失败的诊断方法:`/usr/bin/log stream --process SwiftBar --level info --style compact` 同时 fire URL,看到 `RECEIVED:(GURL,GURL)` 但没 `Refreshing plugin metadata` 就是 name 都没匹配上。
- **状态优先级在两处必须同步**：单会话分类（见上）和标题图标（`priority_order` 在 `plugin.10s.sh`：needs-permission → needs-decision → error → interrupted → needs-input → running）。README 状态表也是按这个顺序排的。改一处就要同步另两处。

## SwiftBar 平台信息

参考 [SwiftBar 官方 README](https://github.com/swiftbar/SwiftBar) 和源码（`SwiftBar/Utility/Environment.swift`、`SwiftBar/Plugin/PackagedPlugin.swift`）。

### SwiftBar 提供的环境变量

已用：

- `SWIFTBAR_PLUGIN_PACKAGE_PATH` —— 仅 packaged plugin 模式下设置，指向 bundle 根。`plugin.10s.sh` 用它给 `PKG_DIR` 赋值，fallback 才走 `$0` 推断。

未用但可考虑：

- `SWIFTBAR_PLUGIN_REFRESH_REASON` —— 本次刷新的触发原因（如 `Schedule`、`FirstLaunch`、用户点击）。可用来在 URL 主动刷新时跳过部分扫描，省 CPU。
- `SWIFTBAR_PLUGIN_DATA_PATH` / `SWIFTBAR_PLUGIN_CACHE_PATH` —— SwiftBar 为每个插件分配的私有数据/缓存目录。如果未来要持久化跨次刷新的状态（比如缓存进程列表），用这两个比 `/tmp` 更规范。
- `OS_APPEARANCE` —— `Light` / `Dark`。当前菜单栏图标用的是固定 b64 PNG，深色模式下可能不够清晰；可以改用 SwiftBar 的 `templateImage=` 参数让系统自动适配，或按 `OS_APPEARANCE` 切两套 b64。

### 元数据约定

- `# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>` —— 用户点开下拉菜单时立刻刷新一次，已启用。
- `# <bitbar.*>` 元数据 —— 与 `xbar.*` / `swiftbar.*` 三种前缀同义，SwiftBar 都接受。脚本头部混用 `bitbar` 和 `swiftbar`，无需统一。

## 设计文档

`docs/superpowers/specs/` 和 `docs/superpowers/plans/` 存放过往变更的设计稿（状态准确度、CPU 性能去抖）。改对应子系统时翻一下；与之无关的工作不必读。
