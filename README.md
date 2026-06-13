# Claude Code SwiftBar

A [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin that surfaces realtime
[Claude Code](https://claude.com/claude-code) session status in the macOS menu
bar, with click-to-jump back into the originating window (iTerm tab, VS Code,
JetBrains, or Finder).

![Menu bar](docs/menubar.png)

## Status badges

The menu bar icon reflects the most attention-worthy session across all your
Claude Code projects:

| State            | Meaning                                                       |
| ---------------- | ------------------------------------------------------------- |
| running          | Claude is actively producing output.                          |
| idle             | Last assistant turn finished cleanly; nothing needs you.      |
| needs-input      | Claude is waiting on user input.                              |
| needs-permission | A tool call is pending your approval.                         |
| interrupted      | The session was interrupted mid-turn.                         |
| error            | Output truncated (`max_tokens`) or another error state.       |

Click the menu bar icon to see every active session grouped by host
(iTerm / VS Code / JetBrains). Selecting a session jumps to that window —
for iTerm it switches to the exact tab whose `tty` matches the live `claude`
process.

## Requirements

- macOS with [SwiftBar](https://github.com/swiftbar/SwiftBar) installed
- Bash and `/usr/bin/python3` (ships with macOS)
- Claude Code (writes session JSONL files under `~/.claude/projects/`)

No Homebrew or additional dependencies needed.

## Install

```bash
git clone https://github.com/anylain/claude-code-swiftbar.git
cd claude-code-swiftbar
./install.sh
```

The script reads SwiftBar's configured plugin folder (`defaults read
com.ameba.SwiftBar PluginDirectory`) and symlinks `claude-code.swiftbar/`
into it. Then open SwiftBar menu → **Refresh All**.

To update later, just `git pull` — the symlink picks up the new code.

## Manual install

If you prefer not to run the script:

```bash
ln -s "$(pwd)/claude-code.swiftbar" \
      "$(defaults read com.ameba.SwiftBar PluginDirectory)/claude-code.swiftbar"
```

## Repository layout

```
claude-code.swiftbar/        # SwiftBar plugin bundle
├── plugin.1s.sh             # main script (refreshes every 1s)
├── .Contents/Info.plist     # bundle metadata
├── .bin/cc-jump             # window-jump helper (bash)
└── .assets/icons/           # menu bar / menu icons (.b64 + .png)
```

The `.`-prefixed subdirectories are intentional: SwiftBar 2.0.1 doesn't yet
support packaged plugins, so resource directories are hidden from its plugin
discovery.

## Uninstall

```bash
rm "$(defaults read com.ameba.SwiftBar PluginDirectory)/claude-code.swiftbar"
```

## License

MIT — see [LICENSE](LICENSE).
