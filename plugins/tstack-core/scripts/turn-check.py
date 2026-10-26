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
  boundary  — context passed one of CONTEXT_STOPS (300k / 500k, the
              context-band scale), no earlier reply in the transcript said a
              `context Nk/1M` line at or past that stop, and this reply has none
              -> BLOCK (say the line). 05deb162 l.779/905/1073/1359. The
              transcript says what was already said: a resume gets a new
              session id and TMPDIR, and a state file keyed on them fired the
              150k stop twice (6fbb0112 → 32391224). Which band was already
              logged or blocked lives in a state file keyed on the transcript
              path, so a band is logged once and blocked once (2026-10-25
              retro: 29 repeat "first time" rows). The 150k stop was dropped
              the same retro: turn-1 context is already 150–218k.
  routing   — the turn has an Agent call or an Edit/Write on a file inside a
              git repository, and no reply line starts with `→` -> LOG only.
              05deb162 l.642/807/1004/1280, c1025ec5 l.1628. Read-only and
              scratch-file turns are not checked (2026-10-25 retro: 185 rows,
              0 complaints).
  wrapup    — the turn called wrap_up, and (a) wrap_up had an `ask` and the
              closing text after it is under WRAPUP_MIN_CHAT chars, or (b) a
              shell fence sits only in text before the turn's tool calls
              (the desktop app collapses it, so "run the blocks above" points
              at nothing: 37dda5c5 04:21, 4c37721d 16:48) -> BLOCK. Moved here
              from the wrap_up PreToolUse hook (2026-10-25 retro): text in the
              same message as the wrap_up call is not in the transcript yet at
              PreToolUse time, and the app puts the closing message after the
              call, so a pre-call count refused 83 times on answers it could
              not see (d9b10a57 L435, 1ecce147 L838-883).

Every finding is appended to ~/.claude/turn-check.log (TSV: ts, session,
check, detail) so the Sunday retro counts them without a transcript scan,
whether or not it blocked. A block never fires twice in a row
(stop_hook_active) or on a turn started by a queued/injected message; on a
turn with at most one tool call only the language check blocks. Any
exception fails open.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prompt_source import has_thai, kind, last_typed_prompt  # noqa: E402

# Band stops in tokens, the same scale the context-band mod draws (green -> yellow -> red).
# Decision G, 2026-10-19: replaced "180k then every 50k" (6-8 lines per long session) with 3 stops;
# 2026-10-25 retro verdict: 150k dropped (rang at turn 1-2, 0 moves after it; 3 of 3 moves came after 300k).
CONTEXT_STOPS = (300_000, 500_000)
LOG = os.path.expanduser("~/.claude/turn-check.log")
STATE_DIR = os.path.expanduser("~/.claude/turn-check-state")
CONTEXT_LINE = re.compile(r"context\s*~?(\d+)k\s*/\s*1M", re.I)
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
WRAP_UP = "mcp__ccd_turn__wrap_up"
WRAPUP_MIN_CHAT = 200
SHELL_FENCE = re.compile(r"```(?:bash|sh|zsh|shell)\b")
WRAPUP_HINT = "put the commands / proposals in the closing text after wrap_up, never 'see above'"


def user_text(d) -> str:
    """Text of a main-thread user entry, "" for meta, sidechain and tool_result-only entries."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
        return ""
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return c if isinstance(c, str) else ""


def band(tokens: int) -> int:
    """300k -> 1, 500k -> 2, below 300k -> 0."""
    return sum(1 for s in CONTEXT_STOPS if tokens >= s)


def state_path(transcript_path: str) -> str:
    return os.path.join(STATE_DIR, hashlib.sha1(os.path.abspath(transcript_path).encode()).hexdigest()[:16] + ".json")


def load_state(transcript_path: str) -> dict:
    try:
        return json.load(open(state_path(transcript_path), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(transcript_path: str, state: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(state_path(transcript_path), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


def in_repo(path: str) -> bool:
    d = os.path.dirname(os.path.abspath(path)) if path else ""
    while d and d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, ".git")):
            return True
        d = os.path.dirname(d)
    return False


def needs_routing(tools) -> bool:
    """An Agent dispatch, or an edit to a repository file (scratch and memory files do not count)."""
    return any(n == "Agent" or (n in EDIT_TOOLS and in_repo(i.get("file_path") or i.get("notebook_path") or "")) for n, i in tools)


def said_band(entries) -> int:
    """Highest band stop an earlier main-thread reply already covered with a `context Nk/1M` line."""
    top = 0
    for d in entries:
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "text":
                for m in CONTEXT_LINE.finditer(b.get("text", "")):
                    top = max(top, band(int(m.group(1)) * 1000))
    return top


def load_turn(transcript_path: str):
    """Return (trigger prompt, assistant text blocks, (tool name, input) pairs, last context total, band said before this turn, wrap, own).

    wrap = (number of text blocks before the turn's last wrap_up call, its input), or None.
    own = the tool calls after the turn's last user entry with text, meta entries included.
    """
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
        return "", [], [], None, 0, None
    texts, tools, own, usage, wrap = [], [], [], None, None
    for d in entries[start + 1:]:
        c = (d.get("message") or {}).get("content")
        if d.get("type") == "user" and not d.get("isSidechain") and (isinstance(c, str) or any(isinstance(b, dict) and b.get("type") == "text" for b in c or [])):
            own = []  # a meta entry (hook feedback, another session's message) starts the tools this stop answers for
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        m = d.get("message") or {}
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                texts.append(b["text"])
            elif b.get("type") == "tool_use":
                tools.append((b.get("name", ""), b.get("input") or {}))
                own.append(tools[-1])
                if b.get("name") == WRAP_UP:
                    wrap = (len(texts), b.get("input") or {})
        u = m.get("usage") or {}
        if u:
            usage = sum(int(u.get(k, 0) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return trigger, texts, tools, usage, said_band(entries[:start]), wrap, own


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


def check(thai, texts, tools, usage, prev, routed=None):
    """Return (blocking findings, log-only findings), each "check: detail". prev = band already said.

    routed = the tool calls the routing check judges (default: tools).
    """
    routed = tools if routed is None else routed
    blocks, logs = [], []
    body = prose(texts)
    letters = re.sub(r"[^A-Za-z฀-๿]", "", body)
    if thai and len(letters) >= 120 and not has_thai(body):
        blocks.append("language: the chat language is Thai and this reply's prose has no Thai — answer again in Thai (chat mirrors the user's last typed message)")
    if usage is not None and usage >= CONTEXT_STOPS[0]:
        step = band(usage)
        if step > prev and not CONTEXT_LINE.search("\n".join(texts)):
            blocks.append(f"boundary: context is {usage // 1000}k/1M, past the {CONTEXT_STOPS[step - 1] // 1000}k band stop for the first time this session — say the one-line `context Nk/1M (measured) · next: … · recommendation` (the user decides)")
    if needs_routing(routed) and not any(ln.lstrip().startswith("→") for t in texts for ln in t.splitlines()):
        names = sorted({n for n, _ in routed})
        logs.append(f"routing: {len(routed)} tool calls ({', '.join(names)[:80]}) with an Agent call or a repository edit and no `→ delegate/self` line")
    return blocks, logs


def check_wrapup(texts, wrap):
    """Blocking findings about the text around the turn's last wrap_up call."""
    if wrap is None:
        return []
    k, ti = wrap
    before, closing = "\n".join(texts[:k]), "\n".join(texts[k:]).strip()
    out = []
    if (ti.get("ask") or "").strip() and len(closing) < WRAPUP_MIN_CHAT:
        out.append(f"wrapup-chat: the closing text after wrap_up is {len(closing)} chars and the box shows only the recap and ask — {WRAPUP_HINT}")
    if SHELL_FENCE.search(before) and not SHELL_FENCE.search(closing):
        out.append(f"wrapup-commands: a shell block sits only in text before the tool calls, which the app collapses — {WRAPUP_HINT}")
    return out


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
    trigger, texts, tools, usage, prev, wrap, own = load_turn(path)
    if not trigger:
        return None
    state = load_state(path)
    blocks, logs = check(thai_expected(trigger, path), texts, tools, usage, max(prev, state.get("blocked", 0)), own)
    blocks += check_wrapup(texts, wrap)
    step = band(usage or 0)
    boundary = any(b.startswith("boundary") for b in blocks)
    seen = state.get("logged", 0)
    log(sid, [x for x in blocks + logs if not (x.startswith("boundary") and step <= seen)])
    if boundary:
        state["logged"] = max(seen, step)
    if len(tools) < 2:
        # A text-only reply is where the language drifts most and the cheapest to redo;
        # the boundary line is not owed on a short answer. A lone wrap_up call still owes its closing text.
        blocks = [b for b in blocks if b.startswith(("language", "wrapup"))]
    out = None
    if blocks and kind(trigger) in ("typed", "pasted") and not d.get("stop_hook_active"):
        if any(b.startswith("boundary") for b in blocks):
            state["blocked"] = step
        out = {"decision": "block", "reason": " | ".join(blocks)}
    if boundary:
        save_state(path, state)
    return out


def selftest():
    global LOG, STATE_DIR
    real_log = LOG
    real_size = os.path.getsize(real_log) if os.path.exists(real_log) else None
    tmp = tempfile.mkdtemp(prefix="turn-check-selftest-")
    LOG = os.path.join(tmp, "turn-check.log")
    STATE_DIR = os.path.join(tmp, "state")
    sid = "turn-check-selftest"

    def transcript(name, prompts, reply, tools, usage=50_000):
        """prompts: earlier user messages, last one starts the turn; then tool calls + tool results + reply.

        tools: names, or (name, input) pairs.
        """
        path = os.path.join(tmp, name + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for p in prompts:
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": p}}) + "\n")
            for t in tools:
                n, i = t if isinstance(t, tuple) else (t, {})
                f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": n, "input": i}], "usage": {"input_tokens": usage}}}) + "\n")
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
    # Routing: an Agent call or a repository edit without a `→` line -> one row, no block;
    # a 7-tool read-only turn and a scratch-file edit -> no row.
    repo = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(repo, ".git"))
    prompt = ["please rerun the failed verify scenarios now"]
    for name, tools, rows in (("read-only", ["Read", "Grep", "Bash"] * 2 + ["Read"], 0),
                              ("scratch", [("Write", {"file_path": os.path.join(tmp, "scratch.txt")}), "Bash"], 0),
                              ("agent", [("Agent", {"subagent_type": "verify"}), "Bash"], 1),
                              ("repo-edit", [("Edit", {"file_path": os.path.join(repo, "src", "a.py")}), "Bash"], 1)):
        before = len(logged("routing"))
        assert run({"session_id": sid, "transcript_path": transcript(name, prompt, en, tools)}) is None
        assert len(logged("routing")) == before + rows, (name, logged("routing"))
    # A meta user entry (another session's message, Stop hook feedback) ends the routed window:
    # the earlier Agent call is not counted again in the turn after it (d9b10a57, 2026-10-26).
    for name, meta, active in (("agent-message", "Another Claude session sent a message:\n<agent-message>done</agent-message>", False),
                               ("stop-feedback", "Stop hook feedback:\nboundary: say the context line", True)):
        p = transcript(name, prompt, en, [("Agent", {"subagent_type": "implement"}), "Bash"])
        before = len(logged("routing"))
        run({"session_id": sid, "transcript_path": p})
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": meta}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": en}]}}) + "\n")
        run({"session_id": sid, "transcript_path": p, "stop_hook_active": active})
        assert len(logged("routing")) == before + 1, (name, logged("routing"))
    # Boundary: passing a band stop (300k / 500k) without the line blocks;
    # the band already said, or the line in this reply -> silent; the next stop blocks again.
    th = th.replace("231k", "331k")
    BASH2 = [("Bash", {})] * 2
    no_line = th.replace("context 331k/1M (measured)", "")
    b, _ = check(False, [en], BASH2, 231_000, 0)
    assert b == [], b  # 150k is no longer a stop
    b, _ = check(False, [en], BASH2, 331_000, 0)
    assert any(x.startswith("boundary") and "300k" in x for x in b), b
    b, l = check(True, [th], BASH2 * 3, 331_000, 0)
    assert b == [] and l == [], (b, l)
    b, _ = check(True, [no_line], BASH2, 420_000, 1)
    assert b == [], b  # still the 300k band: no new line owed
    b, _ = check(True, [no_line], BASH2, 520_000, 1)
    assert any(x.startswith("boundary") and "500k" in x for x in b), b
    # Boundary alone on a text-only turn: logged, not blocked (no context line owed on a short answer).
    p = transcript("short", ["ขอ verify ที่นี่"], no_line, [], usage=331_000)
    assert run({"session_id": sid, "transcript_path": p}) is None
    assert len(logged("boundary")) == 1
    # Resumed transcript with the 300k row already logged: the next text-only turn adds no row.
    with open(p, "a", encoding="utf-8") as f:
        f.write(open(transcript("short-2", ["ต่อ"], no_line, [], usage=340_000), encoding="utf-8").read())
    assert run({"session_id": "resumed-new-id", "transcript_path": p}) is None
    assert len(logged("boundary")) == 1, logged("boundary")
    # Blocked once at 300k and answered without the line: the next heavy turn is not blocked again.
    p = transcript("blocked", ["ขอ verify ที่นี่"], no_line, BASH2, usage=331_000)
    out = run({"session_id": sid, "transcript_path": p})
    assert out and "300k" in out["reason"], out
    with open(p, "a", encoding="utf-8") as f:
        f.write(open(transcript("blocked-2", ["ต่อเลย"], no_line, BASH2, usage=350_000), encoding="utf-8").read())
    assert run({"session_id": sid, "transcript_path": p}) is None

    def two_turns(name, first_reply, first_usage, usage=350_000):
        """Turn 1 replies at first_usage; turn 2 (heavy, no context line) runs at usage."""
        p1 = transcript(name, ["ขอ verify ที่นี่"], first_reply, [], usage=first_usage)
        p2 = transcript(name + "-2", ["ต่อเลย"], no_line, BASH2, usage=usage)
        with open(p1, "a", encoding="utf-8") as f:
            f.write(open(p2, encoding="utf-8").read())
        return p1

    # wrap_up: text written after the call is the closing message.
    def wrap_turn(name, rows):
        """rows: ("text", str) or ("tool", name, input), after one Thai typed prompt."""
        path = os.path.join(tmp, name + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "ข้อ 1 ย้ายไป Stop hook ตามที่เสนอ"}}) + "\n")
            for r in rows:
                b = {"type": "text", "text": r[1]} if r[0] == "text" else {"type": "tool_use", "name": r[1], "input": r[2]}
                f.write(json.dumps({"type": "assistant", "message": {"content": [b], "usage": {"input_tokens": 50_000}}}, ensure_ascii=False) + "\n")
        return run({"session_id": sid, "transcript_path": path})

    box = {"recap": "ตรวจแล้ว", "ask": "ข้อ 1 ใช้แนวทางไหน"}
    long_th = "ข้อ 1 ต้นเหตุจริงคือ hook มองไม่เห็น text ใน message เดียวกับ wrap_up · ข้อเสนอ: ย้าย check ไป Stop hook " * 12
    assert len(long_th.encode()) >= 1500
    # d21f577c shape: a 28-char line, tools, wrap_up with an ask, then the 1.5 KB closing -> pass.
    assert wrap_turn("wrap-ok", [("text", "→ self: plugin repo ของผมเอง"), ("tool", "Bash", {}), ("tool", WRAP_UP, box), ("text", long_th)]) is None
    # d9b10a57 L435 shape: the long answer sits before the call and nothing follows -> block.
    out = wrap_turn("wrap-short", [("tool", "Bash", {}), ("text", long_th), ("tool", WRAP_UP, box), ("text", "รอคำตอบ")])
    assert out and out["reason"].startswith("wrapup-chat") and "never 'see above'" in out["reason"], out
    # 37dda5c5 04:21 shape: a bash fence only before the tools, closing says "the blocks above" -> block with the hint.
    fence = "รัน 5 บล็อกนี้ก่อน\n```bash\npnpm prisma:generate\n```\n"
    out = wrap_turn("wrap-fence", [("text", fence), ("tool", "Bash", {}), ("tool", WRAP_UP, {"recap": "ตรวจแล้ว"}), ("text", "รันบล็อกด้านบนแล้วบอกผล")])
    assert out and out["reason"].startswith("wrapup-commands") and "never 'see above'" in out["reason"], out
    # The same fence repeated in the closing text -> pass; a lone wrap_up (one tool) still blocks.
    assert wrap_turn("wrap-fence-ok", [("text", fence), ("tool", "Bash", {}), ("tool", WRAP_UP, {"recap": "ตรวจแล้ว"}), ("text", fence)]) is None
    assert wrap_turn("wrap-lone", [("tool", WRAP_UP, box)])
    assert logged("wrapup-chat") and logged("wrapup-commands")

    # Resume with a new session id: the 300k line said before the resume still counts (6fbb0112 → 32391224).
    p = two_turns("resume", th, 331_000)
    assert run({"session_id": "resumed-new-id", "transcript_path": p}) is None
    # Earlier turn passed 300k without the line (text-only, not blocked) -> the next heavy turn still owes it.
    p = two_turns("owed", no_line, 331_000)
    out = run({"session_id": "resumed-new-id", "transcript_path": p})
    assert out and "boundary" in out["reason"] and "300k" in out["reason"], out
    # A line said at 331k does not cover the 500k stop.
    out = run({"session_id": sid, "transcript_path": two_turns("next", th, 331_000, usage=520_000)})
    assert out and "500k" in out["reason"], out
    # Broken transcript path -> exception, which main() turns into a silent exit 0.
    try:
        run({"session_id": sid, "transcript_path": os.path.join(tmp, "missing.jsonl")})
        raise AssertionError("expected OSError")
    except OSError:
        pass

    assert (os.path.getsize(real_log) if os.path.exists(real_log) else None) == real_size, "real log was written"
    shutil.rmtree(tmp)
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
