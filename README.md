# Claude Code SwiftBar

[SwiftBar](https://github.com/swiftbar/SwiftBar) 插件,在 macOS 菜单栏实时显示
[Claude Code](https://claude.com/claude-code) 会话状态,点击可一键跳回会话所在窗口
(iTerm 标签页、VS Code、JetBrains)。

![菜单栏](docs/menubar.png)

## 状态图标

菜单栏只显示**优先级最高**的活跃会话状态(由上至下):

| 图标 | 状态             | 含义                                                |
| :--: | ---------------- | --------------------------------------------------- |
| 🔐   | needs-permission | 有工具调用待你授权                                  |
| ✋   | needs-decision   | Claude 在等你做决策(AskUserQuestion / ExitPlanMode) |
| ❌   | error            | 输出被截断(`max_tokens`)或其他错误                  |
| ⛔   | interrupted      | 会话在中途被打断                                    |
| 💬   | needs-input      | 等待你输入                                          |
| ✨   | running          | Claude 正在输出中                                   |
| 💤   | idle             | 上一轮回复已完成,无需操作                           |
| ❓   | unknown          | 状态无法判定(jsonl 为空或异常)                      |

点开下拉看所有活跃会话,按宿主分组(iTerm / VS Code / JetBrains)。点中某个会话即跳到对应窗口 —— 对 iTerm 会精确切到与 `claude` 进程 `tty` 匹配的标签页。

## 安装

依赖:macOS、[SwiftBar](https://github.com/swiftbar/SwiftBar)、Claude Code。无需 Homebrew 或其他依赖(用 macOS 自带的 bash + `/usr/bin/python3`)。

```bash
git clone https://github.com/anylain/claude-code-swiftbar.git
cd claude-code-swiftbar
./install.sh
```

`install.sh` 把 `claude-code.swiftbar/` 软链到 SwiftBar 的插件目录,并幂等地往 `~/.claude/settings.json` 写入 hook 与 statusLine 配置。装完在 SwiftBar 菜单选 **Refresh All**。

升级:`git pull` 即可,软链自动指向新代码。

卸载:`rm "$(defaults read com.ameba.SwiftBar PluginDirectory)/claude-code.swiftbar"`。

## Hook 与 statusLine 在做什么

`install.sh` 注册的 hook(`cc-status-writer`)让状态变成**事件驱动**:Claude Code 一发生权限请求 / 工具开始 / Stop 等事件,菜单栏 ~1s 内反映,而不是等 10s 兜底轮询。没装 hook 时插件回退到 JSONL 启发式,可用但有 10s 延迟。

statusLine 钩子(`cc-meta-writer`)让会话内 `cd` 切目录后,菜单栏 1-2 秒内更新到新 cwd / model。

**已经装了别的 statusline 工具**(ccometix、claude-code-statusline-pro 等):脚本会警告并跳过,不覆盖你的配置。要兼容,在你现有的 statusline 命令最前面调一次 `cc-meta-writer`(它的 stdout 是空字符串,不影响展示)。

## 从 v2.x 升级到 v3.x

v3 把入口脚本从 `claude-code.10s.sh` 改名为 `plugin.10s.sh`,转为 SwiftBar 标准的 packaged plugin 形式。`git pull` + 重跑 `./install.sh` + **重启 SwiftBar**(`killall SwiftBar && open -a SwiftBar`,Refresh All 不够 —— 它只重跑已注册的脚本路径,不重新发现 bundle 入口)。

## License

MIT —— 详见 [LICENSE](LICENSE)。
