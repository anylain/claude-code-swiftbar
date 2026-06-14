#!/bin/bash
# build-CCJump.sh — Compile CCJump.applescript into a .app bundle and register
# it with LaunchServices so cc-jump:// URLs are routed to it.
#
# Run from install.sh; idempotent. Output: ../../.bin/CCJump.app/
#
# Why a .app: macOS only routes custom URL scheme clicks to apps registered in
# LaunchServices via CFBundleURLTypes in their Info.plist. A bare shell script
# can't receive the GURL Apple Event. So we wrap a tiny AppleScript handler in
# an osacompile-generated applet bundle and patch the plist post-build.
#
# Failures here are non-fatal for the rest of install.sh — the only thing that
# breaks is "click notification to jump", which falls back to "go look at the
# menu bar yourself".

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/CCJump.applescript"
DEST="$SCRIPT_DIR/../../.bin/CCJump.app"  # claude-code.swiftbar/.bin/CCJump.app
DEST_ABS="$(cd "$SCRIPT_DIR/../../.bin" && pwd)/CCJump.app"

if ! command -v /usr/bin/osacompile >/dev/null 2>&1; then
  echo "[CCJump] /usr/bin/osacompile not found, skipping URL handler build" >&2
  exit 0
fi

if [ ! -f "$SOURCE" ]; then
  echo "[CCJump] source not found: $SOURCE" >&2
  exit 0
fi

# Compile
rm -rf "$DEST"
if ! /usr/bin/osacompile -o "$DEST" "$SOURCE" 2>&1; then
  echo "[CCJump] osacompile failed, skipping" >&2
  exit 0
fi

PLIST="$DEST/Contents/Info.plist"

# Inject CFBundleURLTypes (claim cc-jump:// scheme) and LSUIElement (no dock icon)
/usr/bin/defaults write "$PLIST" CFBundleIdentifier -string "com.anylain.claude-code-swiftbar.ccjump"
/usr/bin/defaults write "$PLIST" LSUIElement -bool true
/usr/bin/defaults write "$PLIST" CFBundleURLTypes -array \
  '{ CFBundleURLName = "com.anylain.claude-code-swiftbar.ccjump"; CFBundleURLSchemes = ("claude-code-swiftbar"); }'

# osacompile's signature is invalidated by plist mutation. Re-sign ad-hoc.
/usr/bin/codesign --force --sign - "$DEST" 2>/dev/null || true

# Register with LaunchServices so `open cc-jump://…` finds us. We don't need
# to specifically `lsregister -f` here — LaunchServices auto-discovers apps in
# common locations on next launch — but doing it explicitly avoids a 0-1
# minute window where the URL doesn't resolve yet.
LSREG="/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister"
if [ -x "$LSREG" ]; then
  "$LSREG" -f "$DEST_ABS" 2>/dev/null || true
fi

echo "[CCJump] built and registered: $DEST_ABS"
