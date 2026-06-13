#!/bin/bash
# Install Claude Code SwiftBar plugin by symlinking into the SwiftBar plugin folder.
# Run from the repo root: ./install.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_SRC="$REPO_DIR/claude-code.swiftbar"

if [ ! -d "$PLUGIN_SRC" ]; then
  echo "error: $PLUGIN_SRC not found" >&2
  exit 1
fi

# Resolve SwiftBar plugin folder
PLUGIN_DIR="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
if [ -z "$PLUGIN_DIR" ]; then
  echo "SwiftBar plugin folder is not configured."
  echo "Open SwiftBar > Preferences and set the Plugin Folder, then re-run this script."
  exit 1
fi

# Expand ~ if present
PLUGIN_DIR="${PLUGIN_DIR/#\~/$HOME}"

if [ ! -d "$PLUGIN_DIR" ]; then
  echo "error: plugin folder $PLUGIN_DIR does not exist" >&2
  exit 1
fi

LINK="$PLUGIN_DIR/claude-code.swiftbar"

if [ -L "$LINK" ] || [ -e "$LINK" ]; then
  echo "Removing existing $LINK"
  rm -rf "$LINK"
fi

ln -s "$PLUGIN_SRC" "$LINK"
echo "Linked: $LINK -> $PLUGIN_SRC"
echo
echo "Next step: open SwiftBar menu > Refresh All (or restart SwiftBar)."
