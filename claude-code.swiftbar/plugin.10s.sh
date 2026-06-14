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

# Persist user-tunable env (set via SwiftBar Preferences → <swiftbar.environment>
# on SwiftBar versions that render the env UI; on older builds — notably v2.0.1
# with packaged plugins — that UI is missing and the user edits the file
# directly). To make manual edits stick, we only seed defaults when the file
# is absent. When SwiftBar DOES inject CC_NOTIFY into our env, that value is
# already authoritative for any process we spawn from here, but hooks won't
# inherit it — so users on UI-capable SwiftBar should also poke the file via
# the Preferences UI, which writes the same key.
CONFIG_ENV="$HOME/.claude/.cc-config.env"
mkdir -p "$HOME/.claude" 2>/dev/null
if [ ! -e "$CONFIG_ENV" ]; then
  {
    echo "# claude-code.swiftbar config — edit CC_NOTIFY=0 to silence notifications,"
    echo "# CC_NOTIFY=1 to enable. Sourced by cc-status-writer hook."
    echo "CC_NOTIFY=${CC_NOTIFY:-1}"
  } > "$CONFIG_ENV" 2>/dev/null || true
fi

exec /usr/bin/python3 -S "$RENDER_PY" "$PROJECTS_DIR" "$JUMP_BIN" "$ICON_DIR"
