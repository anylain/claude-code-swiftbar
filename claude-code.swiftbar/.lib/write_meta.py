#!/usr/bin/env python3
# Reads statusLine event JSON from argv[1], writes session metadata to
# <project_dir>/.cc-meta.json. The plugin treats this file as the
# authoritative source for session_id, cwd, model, workspace.
import json
import os
import sys
import time

tmp_file = sys.argv[1]

try:
    with open(tmp_file) as f:
        event = json.load(f)
except Exception:
    sys.exit(0)

transcript_path = event.get("transcript_path", "")
if not transcript_path:
    sys.exit(0)

proj_dir = os.path.dirname(transcript_path)
if not os.path.isdir(proj_dir):
    sys.exit(0)

meta_file = os.path.join(proj_dir, ".cc-meta.json")

payload = {
    "session_id": event.get("session_id", ""),
    "transcript_path": transcript_path,
    "cwd": event.get("cwd", ""),
    "workspace": event.get("workspace", {}),
    "model": event.get("model", {}),
    "version": event.get("version", ""),
    "output_style": event.get("output_style", {}),
    "last_seen": int(time.time()),
}

try:
    with open(meta_file, "w") as f:
        json.dump(payload, f)
except Exception:
    pass
