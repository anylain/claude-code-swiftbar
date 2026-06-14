# Claude Code SwiftBar

[SwiftBar](https://github.com/swiftbar/SwiftBar) 插件,在 macOS 菜单栏实时显示
[Claude Code](https://claude.com/claude-code) 会话状态,点击可一键跳回会话所在窗口
(iTerm 标签页、VS Code、JetBrains)。

![菜单栏](docs/menubar.png)

## 状态图标

菜单栏只显示**优先级最高**的活跃会话状态(由上至下):

| 图标 | 含义                                                |
| :--: | --------------------------------------------------- |
| 🔐   | 有工具调用待你授权                                  |
| ✋   | Claude 在等你做决策(AskUserQuestion / ExitPlanMode) |
| ❌   | 输出被截断(`max_tokens`)或其他错误                  |
| ⛔   | 会话在中途被打断                                    |
| 💬   | 等待你输入                                          |
| ✨   | Claude 正在输出中                                   |
| 💤   | 上一轮回复已完成,无需操作                           |
| 🌑   | 显示器休眠,暂停轮询以节省资源(唤醒后自动恢复)       |
| ❓   | 状态无法判定(jsonl 为空或异常)                      |

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

## 通知泡

会话出现 🔐 等待授权 / ✋ 等待决策时,如果当前**前台窗口不是该会话所在的窗口**,系统通知中心会弹一条提示;前台正在该窗口则静默(避免重复打扰)。

- iTerm 还会精确判断当前 tab 的 cwd —— 别的项目 tab 在前台不算"看到了"。
- **点击通知跳转**:点一下通知会带你回到该会话所在的窗口/tab(iTerm 精确到 tab,VSCode/JetBrains 切到对应窗口)。靠 `install.sh` 注册的 `claude-code-swiftbar://` URL handler 实现。
- 关闭/打开:`echo 'CC_NOTIFY=0' > ~/.claude/.cc-config.env`(关),`CC_NOTIFY=1` 打开。SwiftBar 较新版本支持在插件 Preferences 里改环境变量,v2.0.1 不支持,只能直接编辑这个文件。
- 第一次弹通知时 macOS 会问要不要授权,允许 SwiftBar 即可。

## 从 v2.x 升级到 v3.x

v3 把入口脚本从 `claude-code.10s.sh` 改名为 `plugin.10s.sh`,转为 SwiftBar 标准的 packaged plugin 形式。`git pull` + 重跑 `./install.sh` + **重启 SwiftBar**(`killall SwiftBar && open -a SwiftBar`,Refresh All 不够 —— 它只重跑已注册的脚本路径,不重新发现 bundle 入口)。

## License

MIT —— 详见 [LICENSE](LICENSE)。
