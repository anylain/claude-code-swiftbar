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

# Avoid recreating an already-correct symlink — SwiftBar treats remove+create
# as plugin uninstall+install and resets the menu bar icon position.
if [ -L "$LINK" ]; then
  CURRENT_TARGET="$(readlink "$LINK")"
  if [ "$CURRENT_TARGET" = "$PLUGIN_SRC" ]; then
    echo "Symlink already correct: $LINK -> $PLUGIN_SRC (skipping)"
  else
    echo "Updating symlink: $LINK -> $PLUGIN_SRC (was: $CURRENT_TARGET)"
    rm -f "$LINK"
    ln -s "$PLUGIN_SRC" "$LINK"
  fi
elif [ -e "$LINK" ]; then
  echo "Replacing non-symlink at $LINK"
  rm -rf "$LINK"
  ln -s "$PLUGIN_SRC" "$LINK"
  echo "Linked: $LINK -> $PLUGIN_SRC"
else
  ln -s "$PLUGIN_SRC" "$LINK"
  echo "Linked: $LINK -> $PLUGIN_SRC"
fi

# ── Migrate menu bar icon position when plugin filename changes ────────────
# SwiftBar stores each plugin's icon position under
# `NSStatusItem Preferred Position <filename>` in com.ameba.SwiftBar prefs.
# Renaming the plugin file resets the position; we copy the old value over
# so users upgrading from older versions keep their icon where they put it.
NEW_KEY="NSStatusItem Preferred Position claude-code.10s.sh"
if defaults read com.ameba.SwiftBar "$NEW_KEY" >/dev/null 2>&1; then
  : # already migrated, keep the new value
else
  for OLD_NAME in plugin.1s.sh plugin.3s.sh plugin.10s.sh claude-code.3s.sh; do
    OLD_KEY="NSStatusItem Preferred Position $OLD_NAME"
    POSITION="$(defaults read com.ameba.SwiftBar "$OLD_KEY" 2>/dev/null || true)"
    if [ -n "$POSITION" ]; then
      defaults write com.ameba.SwiftBar "$NEW_KEY" -int "$POSITION"
      echo "  Migrated menu bar icon position from $OLD_NAME → claude-code.10s.sh ($POSITION)"
      break
    fi
  done
fi

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

# ── Install statusLine hook for authoritative session metadata ──────────────
META_SCRIPT="$LINK/.bin/cc-meta-writer"

if [ -x "$META_SCRIPT" ]; then
  echo
  echo "Installing Claude Code statusLine for metadata capture..."
  /usr/bin/python3 - "$SETTINGS_FILE" "$META_SCRIPT" <<'PY'
import json, sys

settings_file = sys.argv[1]
meta_script = sys.argv[2]
new_cmd = f"bash \"{meta_script}\""

try:
    with open(settings_file) as f:
        settings = json.load(f)
except Exception:
    settings = {}

existing = settings.get("statusLine")
new_entry = {"type": "command", "command": new_cmd}

if isinstance(existing, dict) and existing == new_entry:
    print("  statusLine already points to cc-meta-writer — nothing to do.")
elif isinstance(existing, dict):
    cur_type = existing.get("type", "?")
    cur_cmd = existing.get("command", "(no command)")
    print("  WARNING: existing statusLine detected:")
    print(f"    type={cur_type}, command={cur_cmd}")
    print("  Skipping to avoid clobbering. To enable cc-meta-writer, either:")
    print("    (a) replace settings.json statusLine with:")
    print(f"        {json.dumps(new_entry)}")
    print("    (b) chain it: have your existing statusline tool call")
    print(f"        cc-meta-writer first (it prints empty stdout).")
else:
    settings["statusLine"] = new_entry
    with open(settings_file, "w") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  statusLine installed: {new_cmd}")
    print("  Run /reload-plugins or restart Claude Code to activate.")
PY
else
  echo
  echo "Warning: cc-meta-writer not found at $META_SCRIPT — skipping statusLine setup."
fi

echo
echo "Next step: open SwiftBar menu > Refresh All (or restart SwiftBar)."
