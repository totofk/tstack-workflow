#!/usr/bin/env python3
"""UserPromptSubmit hook: inject a one-line terse-reply rule in the chat language.

The chat language is the language of the user's LAST TYPED message
(CLAUDE.md: "a message with no typed text keeps the language of the user's
last typed message"). So:
  typed, Thai present      -> Thai rule
  typed, Latin, >= 5 words -> English rule
  typed, short ("apply")   -> language of the last typed message that set one
                              (never flips the language), default Thai
  pasted block (resume prompt, report, start_session brief, <pasted_content>)
                           -> same lookup
  queued / injected (task-notification, hand-back, slash command, scheduled
  preamble, bash-input)    -> same lookup. Printing nothing here was the
                              2026-10-04..10 drift: 5 sessions answered in
                              English after a non-typed turn.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prompt_source import has_thai, kind, last_typed_prompt  # noqa: E402

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


def rule_for_typed(p: str) -> str:
    if has_thai(p):
        return THAI + EXEMPT
    if len(p.split()) < 5:
        return ""
    return ENGLISH + EXEMPT


def rule(prompt: str, transcript_path: str = "") -> str:
    k = kind(prompt)
    if k == "empty":
        return ""
    if k == "typed":
        out = rule_for_typed(prompt.strip())
        if out:
            return out
    # short typed / pasted / queued / injected: the chat language comes from the
    # last typed message that set one (printing nothing let d9fa9c24 drift to English after "apply").
    prev = last_typed_prompt(transcript_path, lambda c: kind(c) == "pasted" or bool(rule_for_typed(c.strip()))) if transcript_path else ""
    if prev and kind(prev) == "typed":
        return rule_for_typed(prev.strip())
    return THAI + EXEMPT


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import tempfile
        assert rule("แก้ตรงนี้หน่อย").startswith("Terse mode (Thai)")
        assert rule("apply ทั้งสองข้อ").startswith("Terse mode (Thai)")
        assert rule("please explain why this hook fails on resume").startswith("Terse mode (English)")
        assert rule("  ") == ""
        # d9fa9c24 2026-10-12 08:53Z: bare "apply" with no history -> default Thai, never silence.
        assert rule("apply").startswith("Terse mode (Thai)")
        # dcdc87e4 2026-10-15 12:16Z: a short Thai prompt stays Thai.
        assert rule("ผมเปิด + login แล้วคุณเห็นมั้ยนะ").startswith("Terse mode (Thai)")
        # Non-typed turns inherit the last typed language from the transcript.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as t:
            for c in ["ขอ verify ที่นี่", "<task-notification>x</task-notification>"]:
                t.write(json.dumps({"type": "user", "message": {"role": "user", "content": c}}) + "\n")
            t.write(json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": "Base directory for this skill: /x"}]}}) + "\n")
            path = t.name
        # c1025ec5 l.1668: task-notification after a Thai turn -> Thai, not "Terse mode (English)".
        assert rule("<task-notification>\n<task-id>a</task-id>", path).startswith("Terse mode (Thai)")
        # dc0563a7 02:54: slash command after Thai -> Thai.
        assert rule("<command-message>pr-review</command-message> <command-name>/pr-review</command-name>", path).startswith("Terse mode (Thai)")
        # 19078ecf 20:57 / 62a527c1 10:04: pasted English resume prompt -> Thai (last typed was Thai).
        resume = "# Resume: exit-survey\n\n" + ("Read ~/.claude/handoffs/exit-survey/HANDOFF.md first.\n" * 8)
        assert rule(resume, path).startswith("Terse mode (Thai)")
        # 7159add6 03:04: scheduled preamble with no typed history -> default Thai.
        assert rule("<scheduled-task name=\"sprint-workflow-review\"> This is an automated run", "/nonexistent").startswith("Terse mode (Thai)")
        # d9fa9c24: "apply" after a Thai turn -> Thai.
        assert rule("apply", path).startswith("Terse mode (Thai)")
        # c47845a0 2026-10-15 21:40Z: a 392-char English <pasted_content> after Thai -> Thai (was typed -> English).
        decisions = ('\n\n<pasted_content id="6232">\n# Re: CES-5850 — English labels for the new columns\n\n## Decisions\n'
                     "1. [B] Which naming for the 3 table columns?\n   → **handed_off / pulled_away / closed_holding** _(not answered; suggestion kept)_\n"
                     '</pasted_content id="6232">\n')
        assert rule(decisions, path).startswith("Terse mode (Thai)")
        # 65dfad12 2026-10-17 11:16Z: an English start_session brief as the first message -> default Thai,
        # and as history it never sets English.
        brief = ("Background (from the session that started this one):\nFiles Vault (repo folder file-vault-web-app under this "
                 "project root; Next.js 16 App Router, React 19, shadcn/ui, Tailwind 4, pnpm) is getting a CamScanner-style scanner")
        assert rule(brief, "/nonexistent").startswith("Terse mode (Thai)")
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as t:
            t.write(json.dumps({"type": "user", "message": {"role": "user", "content": brief}}) + "\n")
            only_pasted = t.name
        assert rule("<task-notification>x</task-notification>", only_pasted).startswith("Terse mode (Thai)")
        os.unlink(only_pasted)
        # A typed English sentence still gets English (the mirror rule).
        assert rule("this fix seem was fixed only single places, but what I want is component fixing").startswith("Terse mode (English)")
        os.unlink(path)
        print("terse-by-lang selftest ok")
        sys.exit(0)
    try:
        d = json.load(sys.stdin)
        out = rule(d.get("prompt") or "", d.get("transcript_path") or "")
    except Exception:
        out = ""
    if out:
        print(out)
