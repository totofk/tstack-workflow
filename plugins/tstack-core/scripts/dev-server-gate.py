#!/usr/bin/env python3
"""PreToolUse gate: dev servers are user-run. Deny if port already listening, ask otherwise."""
import json
import re
import subprocess
import sys

d = json.load(sys.stdin)
cmd = d.get("tool_input", {}).get("command", "")

# ponytail: whole-command regex; a dev-string inside quotes can false-positive — worst case an extra ask
DEV = re.compile(r"\b(pnpm|npm|yarn|bun)\s+(run\s+)?(dev|start:dev)\b|\bnext\s+dev\b")
if not DEV.search(cmd):
    sys.exit(0)

m = re.search(r"(?:-p|--port)[=\s]+(\d+)|\bPORT=(\d+)", cmd)
port = next((g for g in (m.groups() if m else ()) if g), "3000")

lsof = subprocess.run(
    ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
    capture_output=True, text=True,
)
if lsof.stdout.strip():
    decision = "deny"
    reason = (
        f"Dev server already listening on port {port} (user-run). "
        f"Use http://localhost:{port} directly — do not start another server."
    )
else:
    decision = "ask"
    reason = (
        f"No listener on port {port}. The user usually runs dev servers themselves — "
        "confirm before starting one."
    )

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }
}))
