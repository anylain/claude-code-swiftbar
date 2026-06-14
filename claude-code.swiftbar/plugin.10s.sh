#!/bin/bash
# <bitbar.title>Claude Code Status</bitbar.title>
# <bitbar.version>v3.0</bitbar.version>
# <bitbar.author>PanYing</bitbar.author>
# <bitbar.author.github>anylain</bitbar.author.github>
# <bitbar.desc>Realtime Claude Code task/session status with click-to-jump</bitbar.desc>
# <bitbar.dependencies>bash,python3</bitbar.dependencies>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

PROJECTS_DIR="$HOME/.claude/projects"
# Packaged-plugin layout: SwiftBar discovers `plugin.*` as the entry; sibling
# dirs (.bin, .lib, .assets, .Contents) are auxiliary resources inside the bundle.
# Prefer SwiftBar's authoritative path over $0 self-discovery.
PKG_DIR="${SWIFTBAR_PLUGIN_PACKAGE_PATH:-$(cd "$(dirname "$0")" && pwd)}"
JUMP_BIN="$PKG_DIR/.bin/cc-jump"
ICON_DIR="$PKG_DIR/.assets/icons"
RENDER_PY="$PKG_DIR/.lib/render_menu.py"

exec /usr/bin/python3 "$RENDER_PY" "$PROJECTS_DIR" "$JUMP_BIN" "$ICON_DIR"
