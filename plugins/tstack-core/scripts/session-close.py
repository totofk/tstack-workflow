#!/usr/bin/env python3
"""UserPromptSubmit hook: inject the close checklist when the user starts wrapping up.

"Offer a memory review at session end" stayed unfollowed for four retro
reports (2026-10-18: the user asked "จบยัง / น่าจะจบ" themselves in 8
sessions), so the trigger is mechanical. Thai is matched as a prefix with no
word boundary: "จบยัง" has no break between จบ and ยัง for \\b to find.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prompt_source import kind  # noqa: E402

CLOSE = re.compile(r"^\s*(?:น่าจะจบ|จบ|save\b)", re.I)
CHECKLIST = (
    "Close checklist: memory proposals (or 'nothing worth saving') · retro signal y/n · "
    "3-line git state · worktrees/servers/background tasks to stop."
)


def rule(prompt: str) -> str:
    if kind(prompt) != "typed":
        return ""
    return CHECKLIST if CLOSE.match(prompt) else ""


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        for p in ["จบยัง", "จบยังนะ session นี้", "น่าจะจบแล้ว", "น่าจะจบแล้วมั้ย", "  จบ", "จบแล้ว ขอบคุณ", "save", "Save memory แล้วจบ"]:
            assert rule(p) == CHECKLIST, p
        for p in ["ยังไม่จบ ทำข้อ 3 ต่อ", "saved the file?", "apply", "<task-notification>จบ</task-notification>", ""]:
            assert rule(p) == "", p
        print("session-close selftest ok")
        sys.exit(0)
    try:
        out = rule(json.load(sys.stdin).get("prompt") or "")
    except Exception:
        out = ""
    if out:
        print(out)
