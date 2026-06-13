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
└── .assets/icons/           # 菜单栏 / 菜单图标(.b64 + .png)
```

子目录加 `.` 前缀是有意为之:SwiftBar 2.0.1 还不支持 packaged plugin,
用点开头可以让它在插件发现时跳过这些资源目录。

## 卸载

```bash
rm "$(defaults read com.ameba.SwiftBar PluginDirectory)/claude-code.swiftbar"
```

## License

MIT —— 详见 [LICENSE](LICENSE)。
