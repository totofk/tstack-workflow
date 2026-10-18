#!/usr/bin/env python3
"""PreToolUse hook on mcp__ccd_turn__wrap_up: the box must not swallow the answer.

When wrap_up carries an `ask`, the box ends the turn and the user sees only
the recap and the question. Three ways that went wrong in one retro session
(1329b75d, 2026-10-19):

  chat      — ask set and the turn's chat text is under 200 chars: the
              proposals lived only in the box or on a page.
              -> DENY. L1293 (81 chars), L1419 (122), L1449 (0), L1554 (96), L1619 (101).
  link      — ask set, an answer-me-with-html page was rendered this turn
              (`am.mjs … render`) and chat has no path / basename / link to
              the last render's page.
              -> DENY. L1230 (B page), L1293 (Item D page), L1554, L1619 (ข้อ F page).
  language  — recap, ask or a reply-button label is not in the chat
              language (same rule as turn-check: the last typed message that
              sets a language; a clicked button's message never sets it).
              -> DENY. L890, L1141, L1230, L1293, L1419 (English after Thai).

Every finding is appended to ~/.claude/turn-check.log (turn-check's TSV
format, checks wrapup-chat / wrapup-link / wrapup-language). Any exception
fails open. `--dry-run <transcript.jsonl>` replays every wrap_up in a file.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from prompt_source import has_thai, kind, last_typed_prompt  # noqa: E402

_spec = importlib.util.spec_from_file_location("turn_check", os.path.join(HERE, "turn-check.py"))
tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tc)

WRAP_UP = "mcp__ccd_turn__wrap_up"
MIN_CHAT = 200
PAGE = re.compile(r"✓\s+(\S.*?\.html)\s*$", re.M)
LINK = re.compile(r"link:\s*(\S+)")


def entries_of(path):
    out = []
    for line in open(path, encoding="utf-8").read().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def result_text(c) -> str:
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
    return c if isinstance(c, str) else ""


def load_turn(path):
    """Return (trigger, chat texts, last render's result text or None, every button message so far)."""
    entries = entries_of(path)
    buttons = set()
    for d in entries:
        for b in (d.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == WRAP_UP:
                buttons.update(a.get("message", "") for a in (b.get("input") or {}).get("actions") or [] if isinstance(a, dict))
    for start in range(len(entries) - 1, -1, -1):
        trigger = tc.user_text(entries[start])
        if kind(trigger) != "empty":
            break
    else:
        return "", [], None, buttons
    texts, render_id, results = [], None, {}
    for d in entries[start + 1:]:
        if d.get("isSidechain"):
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if d.get("type") == "assistant" and b.get("type") == "text" and b.get("text", "").strip():
                texts.append(b["text"])
            elif d.get("type") == "assistant" and b.get("type") == "tool_use" and b.get("name") == "Bash":
                cmd = (b.get("input") or {}).get("command", "")
                if "am.mjs" in cmd and " render" in cmd:
                    render_id = b.get("id")
            elif b.get("type") == "tool_result":
                results[b.get("tool_use_id")] = result_text(b.get("content"))
    return trigger, texts, results.get(render_id) if render_id else None, buttons


def thai_expected(trigger, path, buttons):
    """turn-check.thai_expected, except a clicked button's message never sets the language.

    None when no typed message sets one (a spawned session whose brief says which language to use).
    """
    sets = lambda c: tc.sets_language(c) and c.strip() not in buttons  # noqa: E731
    src = trigger if sets(trigger) else last_typed_prompt(path, sets)
    return has_thai(src) if src else None


def off_language(text, thai) -> bool:
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    th = len(re.findall(r"[฀-๿]", text or ""))
    return (latin >= 4 and th == 0) if thai else th > latin


def findings(d):
    """Hook payload -> list of "check: detail" findings."""
    ti = d.get("tool_input") or {}
    path = d.get("transcript_path") or ""
    trigger, texts, render, buttons = load_turn(path)
    chat = "\n".join(texts)
    out = []
    if (ti.get("ask") or "").strip():
        if len(chat.strip()) < MIN_CHAT:
            out.append(f"wrapup-chat: chat text is {len(chat.strip())} chars — the box ends the turn and the user sees only the recap and ask — write the answer/proposals in chat first (what is proposed, options, page link), then call wrap_up")
        m = PAGE.search(render or "")
        if m:
            page = m.group(1).strip()
            link = LINK.search(render)
            wanted = [page, urllib.parse.quote(page), os.path.basename(page)] + ([link.group(1)] if link else [])
            if not any(w in chat for w in wanted):
                out.append(f"wrapup-link: a page was rendered this turn but chat has no link to it — put {link.group(1) if link else 'file://' + urllib.parse.quote(page)} on the reply's last line before the box")
    thai = thai_expected(trigger, path, buttons)
    fields = [("recap", ti.get("recap")), ("ask", ti.get("ask"))]
    fields += [(f"label '{a.get('label', '')}'", a.get("label")) for a in ti.get("actions") or [] if isinstance(a, dict) and "action" not in a]
    bad = [] if thai is None else [name for name, v in fields if v and off_language(v, thai)]
    if bad:
        lang = "Thai" if thai else "English"
        out.append(f"wrapup-language: wrap_up fields <{', '.join(bad)}> are not in the chat language ({lang}) — rewrite them in {lang}")
    return out


def run(d):
    """Hook payload -> the deny decision dict, or None."""
    out = findings(d)
    if not out:
        return None
    tc.log(d.get("session_id") or "default", out)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": " | ".join(out)}}


def dry_run(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    tmp = tempfile.mkdtemp(prefix="wrapup-dry-")
    cut = os.path.join(tmp, "cut.jsonl")
    try:
        for i, line in enumerate(lines):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for b in (d.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == WRAP_UP:
                    with open(cut, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines[:i]) + "\n")
                    out = findings({"tool_input": b.get("input") or {}, "transcript_path": cut})
                    print(f"L{i + 1} " + (" | ".join(out) if out else "ok"))
    finally:
        if os.path.exists(cut):
            os.remove(cut)
        os.rmdir(tmp)


def selftest():
    real_log = tc.LOG
    real_size = os.path.getsize(real_log) if os.path.exists(real_log) else None
    tmp = tempfile.mkdtemp(prefix="wrapup-check-selftest-")
    tc.LOG = os.path.join(tmp, "turn-check.log")

    def user(text):
        return {"type": "user", "message": {"role": "user", "content": text}}

    def said(text):
        return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}

    def transcript(name, rows):
        path = os.path.join(tmp, name + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return path

    def checks(rows, ti, name="t"):
        out = run({"session_id": "wrapup-selftest", "transcript_path": transcript(name, rows), "tool_input": ti})
        return [] if out is None else [s.split(":")[0] for s in out["hookSpecificOutput"]["permissionDecisionReason"].split(" | ")]

    page = "/Users/totofk/.answer-me-with-html/pages/ข-อ-F-wolf-graphify-repowise-20261019-010417.html"
    render = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_f", "name": "Bash", "input": {"command": "node \"/x/scripts/am.mjs\" render - --no-open <<'AM_EOF'\n---\nlang: th\n---\nAM_EOF"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"tool_use_id": "toolu_f", "type": "tool_result", "content": "✓ " + page + "\n  sheet · blueprint · 3 panels · flow×1 callout×1\n  STE 1 warning (fix the draft and run again):\n  L6 [sentence-length] sentence has 60 words (max 25): \"Lead: ข้อเท็จจริงสัปดาห์…\""}]}},
    ]
    f_box = {"recap": "E บันทึกแล้ว: คงไว้ + trigger ใน task file · หน้าข้อ F (ไทย) render แล้ว 4 ข้อย่อย F1–F4 ยังไม่ตัดสิน · เหลือ F–J",
             "ask": "ข้อ F: รับทั้ง 4 ข้อย่อยตามที่เสนอไหม",
             "actions": [{"label": "รับทั้ง 4", "message": "F: รับ F1–F4 ตามที่เสนอ"}, {"label": "ไม่เอาบางข้อ", "message": "F: รับบางข้อ เดี๋ยวบอกเลขที่ไม่เอา"}]}
    thai_trigger = user("E: คงไว้ ตั้ง trigger ขยายเมื่อ index > 17.1 KB หรือเจอ recall miss")

    # L1619 shape: short chat, page rendered and not linked -> chat + link.
    assert checks([thai_trigger, said("E บันทึกแล้ว ต่อข้อ F")] + render, f_box) == ["wrapup-chat", "wrapup-link"]
    # Same box after a 250-char Thai answer ending with the percent-encoded file:// link -> pass.
    answer = ("ข้อ F เสนอ 4 ข้อ: F1 wolf เหลือแค่ bug log, F2 ลบ rule graphify ก่อน, F3 repowise เฉพาะ project, F4 วัด hook cost ก่อนลงเพิ่ม " * 2) + "\nfile://" + urllib.parse.quote(page)
    assert len(answer) >= 250
    assert checks([thai_trigger] + render + [said(answer)], f_box) == []

    # L1230 shape: English ask/labels after a Thai trigger -> language.
    b_box = {"recap": "Build session audited: 5 commits match items 1/9/2/6/3+4, selftests green.",
             "ask": "Item B: build turn-check + verify-preflight and drop context-meter?",
             "actions": [{"label": "Yes, both", "message": "B: build turn-check + verify-preflight, drop context-meter"},
                         {"label": "Wait for G", "message": "B: build verify-preflight now; drop context-meter"}]}
    long_en = "Item B proposes: build turn-check as a Stop hook and verify-preflight as a skill step, and drop the context-meter mod. " * 3
    th_trigger = user("โอเค เดี๋ยวผมเอาไปให้ แล้วก็เหมือน session build เสร็จงานแรกแล้วนะ คุณจะเช็คอะไรมั้ย")
    out = run({"session_id": "wrapup-selftest", "transcript_path": transcript("b", [th_trigger, said(long_en)]), "tool_input": b_box})
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and reason.startswith("wrapup-language"), out
    assert "recap" in reason and "ask" in reason and "label 'Yes, both'" in reason and "(Thai)" in reason, reason
    # Clicking the English button does not make English the chat language.
    button_turn = [th_trigger, said(long_en), {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": WRAP_UP, "input": b_box}]}},
                   user("B: build turn-check + verify-preflight, drop context-meter"), said(long_en)]
    assert checks(button_turn, b_box, "button") == ["wrapup-language"]

    # Thai box after a Thai trigger with long chat -> pass.
    assert checks([thai_trigger, said(answer)], f_box) == []
    # No ask + English recap after an English typed trigger -> pass (chat/link checks need an ask).
    en_trigger = user("please rerun the failed verify scenarios now")
    assert checks([en_trigger, said("rerun done")], {"recap": "Verify reran: 3 of 3 scenarios pass on the worktree."}) == []
    # Spawned session: only a start_session brief, no typed message -> language unknown, not checked.
    brief = user("Background (from the session that started this one): reply to the user in Thai.")
    assert checks([brief, said(answer)], f_box) == []
    # App action (no label) is skipped; a reply label still counts.
    assert checks([thai_trigger, said(answer)], dict(f_box, actions=[{"action": "commit"}])) == []
    assert checks([thai_trigger, said(answer)], dict(f_box, actions=[{"action": "commit", "label": "Commit"}])) == []
    # Missing transcript -> run raises, main() turns it into a silent exit 0.
    try:
        run({"transcript_path": os.path.join(tmp, "missing.jsonl"), "tool_input": f_box})
        raise AssertionError("expected OSError")
    except OSError:
        pass

    logged = [r.split("\t")[2] for r in open(tc.LOG, encoding="utf-8").read().splitlines()]
    assert {"wrapup-chat", "wrapup-link", "wrapup-language"} <= set(logged), logged
    assert (os.path.getsize(real_log) if os.path.exists(real_log) else None) == real_size, "real log was written"
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print("wrapup-check selftest ok")


def main():
    try:
        out = run(json.load(sys.stdin))
    except Exception:
        return
    if out:
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--dry-run" in sys.argv:
        dry_run(sys.argv[sys.argv.index("--dry-run") + 1])
    else:
        main()
