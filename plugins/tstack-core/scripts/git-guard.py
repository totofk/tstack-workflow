#!/usr/bin/env python3
# Replaces claude-code-harness guardrails R06 / R10 / R12 after the plugin was disabled (2026-09-29).
import json, re, sys

cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
RULES = [
    (r"\bgit\b[^|;&]*\bpush\b[^|;&]*(\s(-f|--force\S*)\b|\s\+\S)", "deny",
     "git push --force is not allowed. History-destroying operations are forbidden."),
    (r"\bgit\b[^|;&]*\s--no-verify\b", "deny", "--no-verify is not allowed. Do not bypass hooks."),
    (r"\bgit\b[^|;&]*\bpush\b[^|;&]*\s(\S+:)?(main|master)\b", "ask", "Direct push to main/master. Confirm?"),
]
for pat, decision, reason in RULES:
    if re.search(pat, cmd):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": decision, "permissionDecisionReason": reason}}))
        break
