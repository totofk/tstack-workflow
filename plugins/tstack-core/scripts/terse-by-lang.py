#!/usr/bin/env python3
"""UserPromptSubmit hook: inject a one-line terse-reply rule matching the prompt's language.

Thai characters in the prompt -> Thai terse rule (pordee style).
No Thai and >= 5 words     -> English terse rule (caveman style).
Short non-Thai prompts (go-words like "apply") and slash commands print nothing,
so they never flip the reply language.
"""
import json
import re
import sys

EXEMPT = (
    "Write in full as usual for: code, commits, PR text, files and artifacts, HANDOFF and resume prompts, "
    "exact error quotes, evidence (paths, line numbers, numbers with their source), safety warnings, "
    "and anything the user asks to have explained in full."
)
THAI = (
    "Terse mode (Thai): ตอบไทยกระชับ ประโยคสั้น ตัดคำเกริ่น คำลงท้ายสุภาพ คำฟุ่มเฟือย และการทวนคำถาม "
    "คงศัพท์เทคนิคภาษาอังกฤษไว้ ความถูกต้องทางเทคนิคต้องครบ. "
)
ENGLISH = (
    "Terse mode (English): drop filler, pleasantries, hedging and restating the question; "
    "short sentences, fragments are fine; keep full technical accuracy. "
)


def rule(prompt: str) -> str:
    p = prompt.strip()
    if not p or p.startswith("/"):
        return ""
    if re.search(r"[฀-๿]", p):
        return THAI + EXEMPT
    if len(p.split()) < 5:
        return ""
    return ENGLISH + EXEMPT


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        assert rule("แก้ตรงนี้หน่อย").startswith("Terse mode (Thai)")
        assert rule("apply ทั้งสองข้อ").startswith("Terse mode (Thai)")
        assert rule("please explain why this hook fails on resume").startswith("Terse mode (English)")
        assert rule("apply") == "" and rule("/pordee lite") == "" and rule("  ") == ""
        print("selftest ok")
        sys.exit(0)
    try:
        out = rule(json.load(sys.stdin).get("prompt") or "")
    except Exception:
        out = ""
    if out:
        print(out)
