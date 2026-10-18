#!/usr/bin/env python3
"""Stop hook: check the turn that just ended against three "say it out loud" rules.

The rules that decay first in a long session are the ones that need a line
said at a moment (2026-10-11 retro §2): the routing line (0 of 2 measured
sessions), the context boundary line, the chat language. The turn is every
transcript entry after the latest user message with text, and:

  language  — the chat language is Thai (the turn's typed prompt has Thai;
              a short typed prompt, a pasted block or a queued/injected turn
              inherits the last typed message that set a language, as in
              terse-by-lang) and the reply's prose has none
              -> BLOCK (Claude answers again in Thai). c1025ec5 l.1668 etc.
  boundary  — context passed one of CONTEXT_STOPS (150k / 300k / 500k, the
              context-band scale) for the first time this session and the
              reply has no `context Nk/1M` line
              -> BLOCK (say the line). 05deb162 l.779/905/1073/1359.
  routing   — >= 5 tool calls or any Edit/Write, and no reply line starts
              with `→` -> LOG only (the fix is lane agent definitions, not
              a line said after the fact). 05deb162 l.642/807/1004/1280,
              c1025ec5 l.1628.

Every finding is appended to ~/.claude/turn-check.log (TSV: ts, session,
check, detail) so the Sunday retro counts them without a transcript scan,
whether or not it blocked. A block never fires twice in a row
(stop_hook_active) or on a turn started by a queued/injected message; on a
turn with at most one tool call only the language check blocks. Any
exception fails open.
"""
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prompt_source import has_thai, kind, last_typed_prompt  # noqa: E402

# Band stops in tokens, the same scale the context-band mod draws (green -> yellow -> red).
# Decision G, 2026-10-19: replaced "180k then every 50k" (6-8 lines per long session) with 3 stops.
CONTEXT_STOPS = (150_000, 300_000, 500_000)
LOG = os.path.expanduser("~/.claude/turn-check.log")
CONTEXT_LINE = re.compile(r"context\s*~?\d+k\s*/\s*1M", re.I)
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def user_text(d) -> str:
    """Text of a main-thread user entry, "" for meta, sidechain and tool_result-only entries."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
        return ""
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return c if isinstance(c, str) else ""


def load_turn(transcript_path: str):
    """Return (trigger prompt, assistant text blocks, tool names, last context total)."""
    entries = []
    for line in open(transcript_path, encoding="utf-8").read().splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            pass
    for start in range(len(entries) - 1, -1, -1):
        trigger = user_text(entries[start])
        if kind(trigger) != "empty":
            break
    else:
        return "", [], [], None
    texts, tools, usage = [], [], None
    for d in entries[start + 1:]:
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        m = d.get("message") or {}
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                texts.append(b["text"])
            elif b.get("type") == "tool_use":
                tools.append(b.get("name", ""))
        u = m.get("usage") or {}
        if u:
            usage = sum(int(u.get(k, 0) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return trigger, texts, tools, usage


def sets_language(c: str) -> bool:
    return kind(c) == "typed" and (has_thai(c) or len(c.split()) >= 5)


def thai_expected(trigger: str, transcript_path: str) -> bool:
    src = trigger if sets_language(trigger) else last_typed_prompt(transcript_path, sets_language)
    return has_thai(src)


def prose(texts) -> str:
    """Reply text minus code fences, routing/table/bullet-marker lines and inline code."""
    out = []
    for t in texts:
        t = re.sub(r"```.*?```", " ", t, flags=re.S)
        for ln in t.splitlines():
            s = ln.strip()
            if not s or s.startswith(("→", "|", "`", "#")):
                continue
            out.append(re.sub(r"`[^`]*`", " ", s))
    return "\n".join(out)


def step_file(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "default")
    return os.path.join(tempfile.gettempdir(), f"claude-turn-check-{safe}.step")


def check(thai, texts, tools, usage, session_id):
    """Return (blocking findings, log-only findings), each "check: detail"."""
    blocks, logs = [], []
    body = prose(texts)
    letters = re.sub(r"[^A-Za-z฀-๿]", "", body)
    if thai and len(letters) >= 120 and not has_thai(body):
        blocks.append("language: the chat language is Thai and this reply's prose has no Thai — answer again in Thai (chat mirrors the user's last typed message)")
    if usage is not None and usage >= CONTEXT_STOPS[0]:
        step = sum(1 for s in CONTEXT_STOPS if usage >= s)  # 150k -> 1, 300k -> 2, 500k -> 3
        sf = step_file(session_id)
        try:
            prev = int(open(sf).read().strip() or 0)
        except (OSError, ValueError):
            prev = 0
        try:
            open(sf, "w").write(str(step))
        except OSError:
            pass
        if step > prev and not CONTEXT_LINE.search("\n".join(texts)):
            blocks.append(f"boundary: context is {usage // 1000}k/1M, past the {CONTEXT_STOPS[step - 1] // 1000}k band stop for the first time this session — say the one-line `context Nk/1M (measured) · next: … · recommendation` (the user decides)")
    heavy = len(tools) >= 5 or any(n in EDIT_TOOLS for n in tools)
    if heavy and not any(ln.lstrip().startswith("→") for t in texts for ln in t.splitlines()):
        logs.append(f"routing: {len(tools)} tool calls ({', '.join(sorted(set(tools)))[:80]}) and no `→ delegate/self` line")
    return blocks, logs


def log(session_id, findings):
    if not findings:
        return
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            for s in findings:
                name, _, detail = s.partition(": ")
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{session_id}\t{name}\t{detail}\n")
    except OSError:
        pass


def run(d):
    """Hook payload -> the block decision dict, or None."""
    sid = d.get("session_id") or "default"
    path = d.get("transcript_path") or ""
    trigger, texts, tools, usage = load_turn(path)
    if not trigger:
        return None
    blocks, logs = check(thai_expected(trigger, path), texts, tools, usage, sid)
    log(sid, blocks + logs)
    if len(tools) < 2:
        # A text-only reply is where the language drifts most and the cheapest to redo;
        # the boundary line is not owed on a short answer.
        blocks = [b for b in blocks if b.startswith("language")]
    if blocks and kind(trigger) in ("typed", "pasted") and not d.get("stop_hook_active"):
        return {"decision": "block", "reason": " | ".join(blocks)}
    return None


def selftest():
    global LOG
    real_log = LOG
    real_size = os.path.getsize(real_log) if os.path.exists(real_log) else None
    tmp = tempfile.mkdtemp(prefix="turn-check-selftest-")
    LOG = os.path.join(tmp, "turn-check.log")
    sid = "turn-check-selftest"
    sf = step_file(sid)
    if os.path.exists(sf):
        os.remove(sf)

    def transcript(name, prompts, reply, tools, usage=50_000):
        """prompts: earlier user messages, last one starts the turn; then tool calls + tool results + reply."""
        path = os.path.join(tmp, name + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for p in prompts:
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": p}}) + "\n")
            for t in tools:
                f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": t}], "usage": {"input_tokens": usage}}}) + "\n")
                f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}], "usage": {"input_tokens": usage}}}) + "\n")
        return path

    def logged(check_name):
        try:
            return [r for r in open(LOG, encoding="utf-8").read().splitlines() if r.split("\t")[2] == check_name]
        except OSError:
            return []

    en = "Verifier back in 5 calls. Three scenarios hit a GraphQL error Expire. API restarted on the worktree and the CORS origin was added; rerunning the two failed scenarios now with the existing script."
    th = "→ self: rerun existing script\nVerifier กลับมาใน 5 calls · 3 scenario ติด GraphQL error Expire · restart API บน worktree แล้ว รัน script เดิมซ้ำ\ncontext 231k/1M (measured) · next: related via verify · ต่อที่นี่ได้"

    # Thai prompt + English reply -> block once; the stop_hook_active rerun does not block again.
    p = transcript("thai", ["ขอ verify ที่นี่"], en, ["Bash", "Bash"])
    out = run({"session_id": sid, "transcript_path": p})
    assert out and out["decision"] == "block" and out["reason"].startswith("language"), out
    assert run({"session_id": sid, "transcript_path": p, "stop_hook_active": True}) is None
    # Text-only and one-tool-call turns: the language check still blocks.
    for name, tools in (("zero", []), ("one", ["Read"])):
        out = run({"session_id": sid, "transcript_path": transcript(name, ["ขอ verify ที่นี่"], en, tools)})
        assert out and out["reason"].startswith("language"), (name, out)
    # Short typed "apply" and a pasted English resume inherit Thai from the last typed message.
    resume = "# Resume: exit-survey apply\n\n" + ("Read ~/.claude/handoffs/exit-survey/HANDOFF.md first.\n" * 8)
    assert kind(resume) == "pasted"
    for name, prompts in (("apply", ["ขอ verify ที่นี่", "apply"]), ("resume", ["ขอ verify ที่นี่", resume])):
        out = run({"session_id": sid, "transcript_path": transcript(name, prompts, en, ["Bash", "Bash"])})
        assert out and out["reason"].startswith("language"), (name, out)
    # Queued turn after Thai: logged, never blocked.
    assert run({"session_id": sid, "transcript_path": transcript("queued", ["ขอ verify ที่นี่", "<task-notification>x</task-notification>"], en, ["Bash", "Bash"])}) is None
    # 5 tool calls, English typed prompt, no `→` line -> routing row logged, no block.
    before = len(logged("routing"))
    p = transcript("routing", ["please rerun the failed verify scenarios now"], en, ["Bash"] * 5)
    assert run({"session_id": sid, "transcript_path": p}) is None
    assert len(logged("routing")) == before + 1, logged("routing")
    # Boundary: passing a band stop (150k / 300k / 500k) without the line blocks once;
    # the same band again, or the line said -> silent; the next stop blocks again.
    b, _ = check(False, [en], ["Bash"] * 2, 231_000, sid)
    assert any(x.startswith("boundary") and "150k" in x for x in b), b
    b, l = check(True, [th], ["Bash"] * 6, 231_000, sid)
    assert b == [] and l == [], (b, l)
    b, _ = check(True, [th.replace("context 231k/1M (measured)", "")], ["Bash"] * 2, 281_000, sid)
    assert b == [], b  # still the 150k band: no new line owed
    b, _ = check(True, [th.replace("context 231k/1M (measured)", "")], ["Bash"] * 2, 320_000, sid)
    assert any(x.startswith("boundary") and "300k" in x for x in b), b
    b, _ = check(True, [th.replace("context 231k/1M (measured)", "")], ["Bash"] * 2, 520_000, sid)
    assert any(x.startswith("boundary") and "500k" in x for x in b), b
    # Boundary alone on a text-only turn: logged, not blocked (no context line owed on a short answer).
    os.remove(sf)
    p = transcript("short", ["ขอ verify ที่นี่"], th.replace("context 231k/1M (measured)", ""), [], usage=231_000)
    assert run({"session_id": sid, "transcript_path": p}) is None
    assert logged("boundary")
    # Broken transcript path -> exception, which main() turns into a silent exit 0.
    try:
        run({"session_id": sid, "transcript_path": os.path.join(tmp, "missing.jsonl")})
        raise AssertionError("expected OSError")
    except OSError:
        pass

    assert (os.path.getsize(real_log) if os.path.exists(real_log) else None) == real_size, "real log was written"
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    os.remove(sf)
    print("turn-check selftest ok")


def main():
    try:
        out = run(json.load(sys.stdin))
    except Exception:
        return
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
