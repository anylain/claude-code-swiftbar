#!/usr/bin/env python3
# Exit 0 if the JSON file at argv[1] has hook_event_name == "Stop", else 1.
# Used by cc-status-writer's debug archival to filter only Stop payloads.
import json
import sys

try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
sys.exit(0 if d.get("hook_event_name") == "Stop" else 1)
