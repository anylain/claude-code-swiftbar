# Claude Code SwiftBar

一个 [SwiftBar](https://github.com/swiftbar/SwiftBar) 插件,在 macOS 菜单栏实时显示
[Claude Code](https://claude.com/claude-code) 会话状态,点击可一键跳回会话所在窗口
(iTerm 标签页、VS Code、JetBrains)。

![菜单栏](docs/menubar.png)

## 状态说明

菜单栏图标会反映所有 Claude Code 项目中"最值得你关注"的那一个会话:

| 状态             | 含义                                                |
| ---------------- | --------------------------------------------------- |
| running          | Claude 正在输出中                                   |
| idle             | 上一轮回复已完成,无需操作                          |
| needs-input      | 等待你输入                                          |
| needs-permission | 有工具调用待你授权                                  |
| interrupted      | 会话在中途被打断                                    |
| error            | 输出被截断(`max_tokens`)或其他错误                |

点击菜单栏图标可看到所有活跃会话,按宿主分组(iTerm / VS Code / JetBrains)。
点中某个会话即跳到对应窗口 —— 对 iTerm 会精确切到与 `claude` 进程 `tty`
匹配的那个标签页。

## 依赖

- macOS,已安装 [SwiftBar](https://github.com/swiftbar/SwiftBar)
- Bash 与 `/usr/bin/python3`(macOS 自带)
- Claude Code(会在 `~/.claude/projects/` 下生成 JSONL 会话文件)

不需要 Homebrew 或其他额外依赖。

## Hook（可选，大幅提升状态准确度）

插件默认通过解析 JSONL 推断状态，有 1s 轮询延迟且依赖启发式规则。
启用 Claude Code hooks 后，状态变为**事件驱动**，毫秒级响应，准确度大幅提升。

只需在 `~/.claude/settings.json` 中加入：

```json
{
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "PreToolUse":       [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "PostToolBatch":    [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "Stop":             [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "StopFailure":      [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "PermissionRequest":[{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "PreCompact":       [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "PostCompact":      [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "SessionStart":     [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.bin/cc-status-writer\""}]}]
  }
}
```

**注意**：`$CLAUDE_PROJECT_DIR` 指向插件 bundle 根目录（即 `claude-code.swiftbar/`），
hook 脚本位于 `.bin/cc-status-writer`。如果你把插件装在其他位置，请相应调整路径。

Hook 写入的状态文件（`.cc-status.json`）有效期为 60 秒，超时后插件
自动回退到 JSONL 解析方式，确保未配置 hook 的项目也正常工作。

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

## 安装

```bash
git clone https://github.com/anylain/claude-code-swiftbar.git
cd claude-code-swiftbar
./install.sh
```

脚本会读取 SwiftBar 配置的插件目录(`defaults read com.ameba.SwiftBar PluginDirectory`),
把 `claude-code.swiftbar/` 软链过去。然后在 SwiftBar 菜单选 **Refresh All** 即可。

后续升级:`git pull` 即可,软链会自动指向新代码。

## 手动安装

不想跑脚本的话:

```bash
ln -s "$(pwd)/claude-code.swiftbar" \
      "$(defaults read com.ameba.SwiftBar PluginDirectory)/claude-code.swiftbar"
```

## 仓库结构

```
claude-code.swiftbar/        # SwiftBar 插件 bundle
├── plugin.1s.sh             # 主脚本(每 1s 刷新)
├── .Contents/Info.plist     # bundle metadata
├── .bin/cc-jump             # 窗口跳转助手(bash 脚本)
├── .bin/cc-status-writer    # Hook 事件→状态写入器(.cc-status.json)
├── .bin/cc-meta-writer      # statusLine 元数据写入器(.cc-meta.json)
└── .assets/icons/           # 菜单栏 / 菜单图标(.b64 + .png)
```

## 卸载

```bash
rm "$(defaults read com.ameba.SwiftBar PluginDirectory)/claude-code.swiftbar"
```

## License

MIT —— 详见 [LICENSE](LICENSE)。
