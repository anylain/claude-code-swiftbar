# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 工程概览

一个 SwiftBar 插件，在 macOS 菜单栏实时显示 Claude Code 的会话状态，点击可一键跳回会话所在窗口（iTerm tab、VS Code、JetBrains）。实现仅依赖 bash + `/usr/bin/python3`，无 Homebrew、无 node、无构建步骤。

## 常用命令

```bash
./install.sh                                          # 把插件软链进 SwiftBar 插件目录，并往 ~/.claude/settings.json 注册 hooks 和 statusLine
bash claude-code.swiftbar/plugin.10s.sh                # 跑一次插件主脚本，查看 SwiftBar 风格的输出
open -g 'swiftbar://refreshplugin?name=claude-code'    # 立刻让 SwiftBar 重跑插件（与 hook 触发同一 URL）
```

无测试、无 linter、无包管理文件。验证方式 = 跑脚本看输出，或 SwiftBar `Refresh All` 后看菜单栏。

迭代时每次保存都需要 SwiftBar 重跑脚本：等 10s 兜底轮询、点下拉菜单里的 `Refresh`、或主动触发上面那条 URL。

## 架构

插件在 `plugin.10s.sh` 里维护**三个互相独立的信号层，按字段级 fallback**。理解优先级是关键 —— 几乎所有 bug 都是"错误的层级压过了正确的层级"。

### 信号层优先级（每个会话）

1. **`.cc-status.json`** —— `cc-status-writer` 通过 Claude Code hooks 写入。事件驱动，~1s 延迟。有 60s TTL（`HOOK_STATUS_TTL`）。新鲜时完全覆盖 JSONL 启发式。
2. **`.cc-meta.json`** —— `cc-meta-writer` 通过 `statusLine` hook 写入。提供权威的 `session_id`、`cwd`、`workspace.current_dir`、`model`。**没有 TTL**，覆盖式更新，作为 `proj_path` 的真理来源。这是"用户在会话中 `cd` 切目录后菜单栏 1s 内能反映"的关键 —— 不再依赖 JSONL 第一条记录里的 `cwd`。
3. **JSONL 启发式** —— `classify()` 读最新 `*.jsonl` 的最后 20 条，结合 `tool_use`/`tool_result` 的配对、`stop_reason`、最后一条的 age、claude 父进程是否有活跃的 Bash 工具子进程，推断状态。这是用户没装 hook 时的兜底。

`alive` 与 `state` 是两个**正交**维度：alive 当且仅当 (a) JSONL 在 `ALIVE_SECS`（120s）内被写过，或 (b) 有 claude 进程的 lsof cwd 与解析后的 `proj_path` 相等。Layer 1 在新鲜时强制 alive=true。

### 路径解析有损 —— 永远不要反推目录名

项目目录把 `/` 编码为 `-`（如 `-Users-panying32-git-claude-code-swiftbar`）。反向解码**天然存在歧义**（真实目录名里就含 `-`）。脚本永远优先使用真实 `cwd`（先看 `.cc-meta.json`，再看 JSONL 的 `cwd` 字段），**解码出的路径只用于显示，绝不用于 `cwd_map` 匹配**。见 `proj_path_decoded` 周边注释。

### 同 cwd 去重

当两个会话解析到同一个 `proj_path`（典型场景：父目录会话的 `meta.workspace.current_dir` 被推进到子目录，而那个子目录本来就有自己的会话），它们都会去认领同一个 claude 进程，被同时判定为 alive。`plugin.10s.sh` 里的第二遍循环按 mtime 决胜：同一 `proj_path` 下只有 mtime 最新者保留进程匹配；其他同路径的旧会话退回 `is_recent` 判定，超期就静默消失。

### 进程探测

`inspect_claude_procs()` 跑 `pgrep -x claude`，对每个 pid 抓 lsof cwd、env（用于宿主识别：ITERM_SESSION_ID / VSCODE_INJECTION / TERMINAL_EMULATOR）、直接子进程。**判断"工具运行中"用的是 shell comm 白名单**（`TOOL_CHILD_COMMS = {/bin/zsh, /bin/bash, /bin/sh, ...}`），不是黑名单 —— claude 只在跑 Bash 工具时同步 fork 一个 shell；其他长生命周期辅助进程（MCP、LSP、caffeinate、telemetry watchdog）绝对不能被算作"running"。黑名单会被裸 `node` 启动的 LSP 绕过。过滤逻辑在 `plugin.10s.sh` 中部。

### Bundle 布局（packaged plugin）

本插件采用 SwiftBar 的 packaged plugin 形式（见 `SwiftBar/Plugin/PackagedPlugin.swift`）：bundle 目录以 `.swiftbar` 结尾、入口以 `plugin.` 开头。SwiftBar 把整个 `claude-code.swiftbar/` 当作 bundle 加载，发现 `plugin.10s.sh` 作为主脚本入口，并设置 `SWIFTBAR_PLUGIN_PACKAGE_PATH` 指向 bundle 根。

`.bin/`、`.assets/`、`.Contents/` 仍带点前缀 —— packaged 模式下其实不必（SwiftBar 只扫 `plugin.*`），保留只是历史习惯加一层防御性隐藏，不影响行为。

**packaged 模式下两条恒等关系**（与普通脚本插件不同）：

- **`?name=` 由目录名决定**：`name = packageDirectory.lastPathComponent.replacingOccurrences(of: ".swiftbar", with: "")` → 永远是 `claude-code`，与入口文件名解耦。改入口文件名不会破坏 URL。
- **`plugin.id` = bundle 目录的 resolved path**（不是入口文件路径）。`autosaveName` = `plugin.id`，但实测 macOS 把 `NSStatusItem Preferred Position` 的 key 取了 basename，仍是入口文件名（即 `plugin.10s.sh`）。`install.sh` 已按此迁移历史 key（`claude-code.10s.sh` 等）。

### 刷新去抖（cc-status-writer）

Hook 事件成簇出现（PreToolUse + PostToolBatch + Stop 等）。writer 用 `/tmp/cc-swiftbar-lock-$UID` 做 trailing-edge 去抖：1s 窗口里只有一个 worker 存活并最终触发 `swiftbar://refreshplugin`。**锁按 `$UID` 隔离**，单机多用户互不影响。**陈旧锁回收**：超过 5s 的锁视为孤儿（处理被 SIGKILL 后 EXIT trap 没跑的 worker）。

## 值得记住的约束

- **仅 macOS。** 用了 `lsof`、`osascript`、`defaults`、`pgrep -x`、`ps -E`、`/usr/bin/python3`，无跨平台需求。
- **只用 bash + 系统 Python。** 不要引入 Homebrew/node/三方 Python 包 —— `install.sh` 没有装它们的能力。
- **`install.sh` 幂等。** hook 事件和 statusLine 都检测后再加。它还会在插件文件名改变时迁移菜单栏图标位置（SwiftBar 把改名当成 uninstall+reinstall 会重置位置）—— 见 `NSStatusItem Preferred Position` 那段。当前迁移目标 key 是 `plugin.10s.sh`，源列表覆盖 `claude-code.10s.sh`、`claude-code.3s.sh`、`plugin.3s.sh`、`plugin.1s.sh`。
- **入口文件名里的数字就是刷新周期。** `plugin.10s.sh` → SwiftBar 每 10s 轮询一次。改名既影响刷新频率也影响 `NSStatusItem Preferred Position` 偏好 key —— 改前在 `install.sh` 的迁移源列表里加上旧名。
- **`swiftbar://refreshplugin?name=…` 里的 name 由目录名决定（packaged 模式下）**。见 `SwiftBar/Plugin/PackagedPlugin.swift`：`name = packageDirectory.lastPathComponent.replacingOccurrences(of: ".swiftbar", with: "")`。我们的 `claude-code.swiftbar/` → `name=claude-code`。改 bundle 目录名才会改 URL。`cc-status-writer` 里硬编码的 `claude-code` 必须与目录名（去 `.swiftbar`）一致。
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
