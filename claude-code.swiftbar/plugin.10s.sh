#!/bin/bash
# <bitbar.title>Claude Code Status</bitbar.title>
# <bitbar.version>v3.0</bitbar.version>
# <bitbar.author>PanYing</bitbar.author>
# <bitbar.author.github>anylain</bitbar.author.github>
# <bitbar.desc>Realtime Claude Code task/session status with click-to-jump</bitbar.desc>
# <bitbar.dependencies>bash,python3</bitbar.dependencies>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
# <swiftbar.environment>[CC_NOTIFY=1]</swiftbar.environment>

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

PROJECTS_DIR="$HOME/.claude/projects"
# Packaged-plugin layout: SwiftBar discovers `plugin.*` as the entry; sibling
# dirs (.bin, .lib, .assets, .Contents) are auxiliary resources inside the bundle.
# Prefer SwiftBar's authoritative path over $0 self-discovery.
PKG_DIR="${SWIFTBAR_PLUGIN_PACKAGE_PATH:-$(cd "$(dirname "$0")" && pwd)}"
JUMP_BIN="$PKG_DIR/.bin/cc-jump"
ICON_DIR="$PKG_DIR/.assets/icons"
RENDER_PY="$PKG_DIR/.lib/render_menu.py"

# Persist user-tunable env (set via SwiftBar Preferences → <swiftbar.environment>)
# to a sidecar file that hooks can source. SwiftBar injects these vars into
# THIS process, but Claude Code's hook subprocesses don't inherit them — so
# we relay via ~/.claude/.cc-config.env which cc-status-writer sources.
CONFIG_ENV="$HOME/.claude/.cc-config.env"
mkdir -p "$HOME/.claude" 2>/dev/null
{
  echo "# auto-written by claude-code.swiftbar plugin.10s.sh — do not edit by hand"
  echo "CC_NOTIFY=${CC_NOTIFY:-1}"
} > "$CONFIG_ENV" 2>/dev/null || true

exec /usr/bin/python3 -S "$RENDER_PY" "$PROJECTS_DIR" "$JUMP_BIN" "$ICON_DIR"
