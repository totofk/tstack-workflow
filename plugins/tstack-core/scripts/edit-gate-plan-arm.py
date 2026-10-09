#!/usr/bin/env python3
"""PostToolUse hook on ExitPlanMode: arm the edit-gate when a plan is approved.

The user always reviews a plan before approving (decision 2026-07-22), so an
approved ExitPlanMode counts as a go-word: write the same one-round .arm flag
edit-gate-arm.py uses. A rejected plan writes nothing — the gate stays locked.
The next user message clears .arm as usual; the user never loses the key.
"""
import sys, json, re, os, tempfile


def main() -> None:
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    if d.get("tool_name", "") != "ExitPlanMode":
        return
    # PostToolUse only fires after a successful tool run; a rejected plan is
    # denied before the tool executes, so reaching this hook ≈ approval. The
    # old check required "approved your plan" in tool_response, but the raw
    # payload can be structured data without that sentence (2026-07-22 miss).
    # Keep only a guard against explicit rejection markers.
    response = json.dumps(d.get("tool_response", "") or "", ensure_ascii=False).lower()
    if any(marker in response for marker in ("reject", "denied", "not approved")):
        return
    try:  # one-line payload log to confirm the real response shape; delete when verified
        with open(os.path.join(tempfile.gettempdir(), "claude-edit-gate-plan-arm-debug.jsonl"), "a") as f:
            f.write(json.dumps(d.get("tool_response", ""), ensure_ascii=False)[:500] + "\n")
    except OSError:
        pass
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", d.get("session_id", "") or "default")
    arm = os.path.join(tempfile.gettempdir(), f"claude-edit-gate-{safe}.arm")
    try:
        with open(arm, "w") as f:
            f.write("armed-plan-approved")
    except OSError:
        pass


if __name__ == "__main__":
    main()
