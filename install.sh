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

# ── Install Claude Code hooks for event-driven status ──────────────────────
HOOK_SCRIPT="$LINK/.bin/cc-status-writer"
SETTINGS_FILE="$HOME/.claude/settings.json"

if [ -x "$HOOK_SCRIPT" ]; then
  echo
  echo "Installing Claude Code hooks for real-time status..."
  /usr/bin/python3 - "$SETTINGS_FILE" "$HOOK_SCRIPT" <<'PY'
import json, sys

settings_file = sys.argv[1]
hook_script = sys.argv[2]

try:
    with open(settings_file) as f:
        settings = json.load(f)
except Exception:
    print("  (no existing settings.json, creating one)")
    settings = {}

hook_events = [
    "UserPromptSubmit", "PreToolUse", "PostToolBatch",
    "Stop", "StopFailure", "PermissionRequest",
    "PreCompact", "PostCompact", "SessionStart", "SessionEnd",
]

new_hooks = {
    ev: [{"hooks": [{"type": "command", "command": f"bash \"{hook_script}\""}]}]
    for ev in hook_events
}

existing = settings.get("hooks", {})

# Idempotent: skip events whose command already matches
added = 0
skipped = 0
for ev in hook_events:
    existing_commands = set()
    for entry in existing.get(ev, []):
        for h in entry.get("hooks", []):
            if h.get("type") == "command":
                existing_commands.add(h.get("command", ""))
    new_cmd = f"bash \"{hook_script}\""
    if new_cmd in existing_commands:
        skipped += 1
    else:
        existing.setdefault(ev, []).append(
            {"hooks": [{"type": "command", "command": new_cmd}]}
        )
        added += 1

if added:
    settings["hooks"] = existing
    with open(settings_file, "w") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  Hook events added: {added}, skipped (already present): {skipped}")
    print(f"  Run /reload-plugins or restart Claude Code to activate.")
else:
    print(f"  All {skipped} hook events already installed — nothing to do.")
PY
else
  echo
  echo "Warning: cc-status-writer not found at $HOOK_SCRIPT — skipping hook setup."
  echo "Make sure the plugin bundle is up to date, then re-run install.sh."
fi

echo
echo "Next step: open SwiftBar menu > Refresh All (or restart SwiftBar)."
