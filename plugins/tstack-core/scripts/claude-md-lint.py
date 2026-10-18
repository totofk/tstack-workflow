#!/usr/bin/env python3
"""PostToolUse hook on Edit|Write: keep history out of ~/.claude/CLAUDE.md rule lines.

CLAUDE.md says "Rules only" and sends origins, dates and incidents to
~/.claude/working-agreement-history.md, yet rule lines regrow them on every
edit: on 2026-10-19 lines 10 ("as of 2026-09-29", "built 2026-10-19"),
16 ("decision F1, 2026-10-19"), 23 ("verified 2026-10-19") and 30
("(user, 2026-10-19: "…")"). A `- ` line is flagged when, after its
`(trial from …)` parenthetical is dropped, it carries a date, the word
"verified", a decision id, or a quote attributed to the user. Quoted trigger
phrases ("จำไว้" / "remember this") are not history and pass.

Warn-only: the finding goes back as additionalContext, the edit stands.
CLI: `claude-md-lint.py [path]` prints `L<n>: <tokens>`. Any exception fails open.
"""
import json
import os
import re
import sys
import tempfile

TARGET = os.path.expanduser("~/.claude/CLAUDE.md")
TRIAL = re.compile(r"\(trial from [^)]*\)")
HISTORY = re.compile(r"20\d\d-\d\d-\d\d|\bverified\b|\bdecision [A-Z]\d|\buser\b[^\"\n]{0,30}:\s*[\"“]")


def lint(text):
    """Return [(line number, [matched tokens])] for rule lines that carry history."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.startswith("- "):
            continue
        hits = [m.group(0) for m in HISTORY.finditer(TRIAL.sub("", line))]
        if hits:
            out.append((n, hits))
    return out


def fmt(n, hits):
    return f"L{n}: {', '.join(hits)}"


def run(d):
    """Hook payload -> the additionalContext dict, or None."""
    path = (d.get("tool_input") or {}).get("file_path") or ""
    if not path or os.path.realpath(os.path.expanduser(path)) != os.path.realpath(TARGET):
        return None
    found = lint(open(os.path.realpath(TARGET), encoding="utf-8").read())
    if not found:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
            "CLAUDE.md lint: rule lines carry history (dates/verified/decision ids/user quotes belong in "
            "~/.claude/working-agreement-history.md): " + ", ".join(fmt(n, h) for n, h in found)}}


def selftest():
    l10 = "- **Weigh every dispatch on two questions, say the answer in the routing line:** (1) does the work have an adaptive loop — a running env, tests to make green, spec to interpret, \"if X fails do Y\" in the brief; (2) would a wrong result be cheap for me to catch (one grep / one command)? **Opus** (`model: opus`, 2× Sonnet per token as of 2026-09-29, wins on turns) when (1) is yes or (2) is no: verify, implement with a test loop or multi-layer diff, why/architecture research, risky whole-branch review, escalation after ONE blocked result. **Sonnet** when the brief is mechanical AND I re-check the result in one command: locate-only research (pin `model: sonnet` on Explore), tasks.md items that already name the file and the value, TDD loops with the tests written first, rerun of a script that already passed. **Fable:** only after Opus is blocked. **Haiku:** pure runners only. **Always pass a lane as `subagent_type`** (`verify` / `implement` / `research` / `tracking` in `~/.claude/agents/`, built 2026-10-19 — model, effort, tools and `maxTurns` live in the file); `model` only to override the file, and the override goes in the ledger note. Never 3× Opus in parallel for routine verify."
    l15 = "- **\"จำไว้\" / \"remember this\" → save it that turn and confirm on the reply's FIRST line** with the file path (\"บันทึกแล้ว: `<path>`\"), as plain text in the closing message, never only in a pre-tool line, the recap, or the middle of a long report. A behavioural correction is also saved at user level when it applies beyond one project."
    l16 = "- **Route by scope:** our-workflow/global → this file or user auto-memory; project gotcha → project auto-memory (openwolf's `.wolf/cerebrum.md` / buglog are optional bug logs, never a routing target — decision F1, 2026-10-19: 40 memory files vs 0 cerebrum lines in a week); machine/person-specific facts (ports, tunnel, test-row ids, which branch a checkout sits on) → project auto-memory. Behavioral corrections default to USER level. No migration of misplaced entries."
    l18 = "- **User references past work not in context (feature/ticket name, \"ที่เราทำไว้\", \"เมื่อวาน\") → ONE search before asking:** claude-mem `search` with the feature/file name, no `project` filter (worktree sessions store under `wt-3001`/`wt-3002`); worker down → `search_session_transcripts`. Open at most 2 hits, then ask only what is still missing. Never dispatch an agent to hunt transcripts for this."
    l23 = "- **Browser pane & links:** the pane is separate from Chrome, GitHub login persists there, plain markdown links are fine. A `file://` page outside the project folder renders as a **static snapshot — no JavaScript** (page tools refuse it: \"This tab shows a local file, not a web page\"), so an answer-me-with-html page opened from its file:// link has no Reply dialog and no copy; the same page over `http://127.0.0.1` runs fully (verified 2026-10-19: dialog opens, secure context, clipboard). Fix: the user runs `am serve` (port 8765, I never start it); then `am render` prints a `link:` line and that http URL is the page link. Run buttons can't open external browsers; for real Chrome use `open -a \"Google Chrome\" <url>` via Bash. Widget-card links only where a widget already exists."
    l29 = "## answer-me-with-html (trial from 2026-10-14; Sunday retro decides keep / widen / narrow / remove by the pages the user opened)"
    l30 = "- [answer-me-with-html always-on] Main session only, never in subagents. Render a page (2–4 panels, `--no-open`, **draft in the chat language** — `lang: th` when the user's latest typed message is Thai, `lang: en` when it is English; technical terms, file names and identifiers stay as they are; never both languages on one page; a Thai page sets `style: off` in the frontmatter — the STE check counts Thai words by spaces and warns on every sentence (2026-10-19); this is the one artifact that follows the chat language (user, 2026-10-19: \"ควร answer me with html ควรเป็นภาษาไทย\"), rendered before the reply is written, file:// link on the reply's last line) whenever the reply is: a why / how-does-it-work / what-differs answer or a post-mortem; an explore, wayfinder, grill or propose summary; review findings with 2+ items; a plan or decision with options to choose; a retro report. Never at apply-end, verify-end, deliver, routing or weigh lines, one-line answers, command output, or when the user asks for plain text."
    found = dict(lint("\n".join([l10, l15, l16, l18, l23, l29, l30, "- " + l29[3:]])))
    assert sorted(found) == [1, 3, 5, 7], found
    assert found[1] == ["2026-09-29", "2026-10-19"], found[1]
    assert "decision F1" in found[3] and "verified" in found[5], found
    assert any(h.startswith("user") for h in found[7]), found[7]

    global TARGET
    real = TARGET
    tmp = tempfile.mkdtemp(prefix="claude-md-lint-selftest-")
    TARGET = os.path.join(tmp, "CLAUDE.md")
    try:
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write("\n".join(["# Working agreement", l15, l16]) + "\n")
        out = run({"tool_input": {"file_path": TARGET}})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse" and "L3: decision F1, 2026-10-19" in ctx, out
        assert run({"tool_input": {"file_path": os.path.join(tmp, "other.md")}}) is None
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(l15 + "\n" + l18 + "\n")
        assert run({"tool_input": {"file_path": TARGET}}) is None
    finally:
        TARGET = real
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
    print("claude-md-lint selftest ok")


def cli(path):
    for n, hits in lint(open(os.path.expanduser(path), encoding="utf-8").read()):
        print(fmt(n, hits))


def main():
    try:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        if not raw.strip():
            return cli(TARGET)  # no hook payload: CLI on the default file
        out = run(json.loads(raw))
    except Exception:
        return
    if out:
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) > 1:
        cli(sys.argv[1])
    else:
        main()
