#!/usr/bin/env python3
"""Shared classifier for UserPromptSubmit hooks: who wrote this prompt?

A hook sees every prompt-shaped message, not only what the user typed:
task-notifications of finished background work, sibling-agent hand-backs,
slash-command expansions, scheduled-task preambles, bash-input echoes.
Two hooks need the same answer (edit-gate-arm: "may this message touch the
arm?", terse-by-lang: "may this message set the chat language?"), so the
patterns live here once.

kind(prompt) -> "empty" | "queued" | "injected" | "pasted" | "typed"
  queued   : an event the harness queued into the session — forensics
             2026-10-08 class D1/hole 2 (task-notification, hand-back,
             cross-session message). Must never arm, lock, or set language.
  injected : harness/command text the user did not write as prose
             (slash command, scheduled preamble, bash-input echo, viewer
             context). Must never set language; arming is the caller's call
             (/opsx:apply arms on purpose via the word "apply").
  pasted   : a long Latin-only block that reads like a resume prompt or
             report, or any start_session brief / <pasted_content> block
             whatever its length or script. Its language is not the chat
             language (19078ecf 20:57, 62a527c1 10:04, 65dfad12 13:26).
  typed    : the user's own words.
"""
import re

THAI = re.compile(r"[฀-๿]")

QUEUED_PREFIXES = (
    "<task-notification",
    "<agent-message",
    "[Subagent hand-back]",
    "Another Claude session sent a message",
    "<cross-session-message",
)
INJECTED_PREFIXES = (
    "<command-message",
    "<command-name",
    "<local-command-stdout",
    "<scheduled-task",
    "<bash-input",
    "<artifact-view-context",
    "<system-reminder",
    "<ci-monitor-event",
    "/",
)
PASTED_MARKERS = ("HANDOFF", "Resume", "resume prompt", "## ", "~/.claude/", "/Users/", "```", "Phase")
PASTED_PREFIXES = ("Background (from the session that started this one)", "<pasted_content")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def has_thai(text: str) -> bool:
    return THAI.search(text or "") is not None


def is_queued(prompt: str) -> bool:
    return (prompt or "").lstrip().startswith(QUEUED_PREFIXES)


def is_brief_or_paste(prompt: str) -> bool:
    return SYSTEM_REMINDER.sub("", prompt or "").lstrip().startswith(PASTED_PREFIXES)


def is_pasted(prompt: str) -> bool:
    if is_brief_or_paste(prompt):
        return True
    p = (prompt or "").strip()
    if has_thai(p) or len(p) < 400:
        return False
    return any(m in p for m in PASTED_MARKERS) or p.count("\n") >= 8


def kind(prompt: str) -> str:
    p = (prompt or "").lstrip()
    if not p:
        return "empty"
    if p.startswith(QUEUED_PREFIXES):
        return "queued"
    if is_brief_or_paste(p):
        return "pasted"
    if p.startswith(INJECTED_PREFIXES):
        return "injected"
    if is_pasted(p):
        return "pasted"
    return "typed"


def last_typed_prompt(transcript_path: str, accept=lambda c: True) -> str:
    """The most recent user message the human actually typed, from the JSONL transcript.

    Skips isMeta entries (skill expansions), tool_result entries, sidechains,
    anything kind() does not call typed/pasted, and anything accept() rejects.
    Returns "" when none.
    """
    import json
    try:
        lines = open(transcript_path, encoding="utf-8").read().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
            continue
        c = (d.get("message") or {}).get("content")
        if isinstance(c, list):
            texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
            if not texts:
                continue  # tool_result-only entry
            c = "\n".join(texts)
        if not isinstance(c, str):
            continue
        if kind(c) in ("typed", "pasted") and accept(c):
            return c
    return ""


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        assert kind("") == "empty"
        assert kind("<task-notification>\n<task-id>a1</task-id>") == "queued"
        assert kind("[Subagent hand-back] The text below ... arm session") == "queued"
        assert kind("  <agent-message from=\"a354\">") == "queued"
        assert kind("Another Claude session sent a message while you were working") == "queued"
        assert kind("<command-message>wayfinder</command-message> <command-name>/opsx:apply</command-name>") == "injected"
        assert kind("/opsx:apply") == "injected"
        assert kind("<scheduled-task name=\"x\"> This is an automated run") == "injected"
        assert kind("<bash-input>psql -c 'select 1'</bash-input>") == "injected"
        resume = "# Resume: exit-survey apply\n\n" + ("Read ~/.claude/handoffs/exit-survey/HANDOFF.md first.\n" * 8) + "Then /opsx:apply group 2."
        assert kind(resume) == "pasted", kind(resume)
        assert kind("Background (from the session that started this one): fix the gate.") == "pasted"
        assert kind("<system-reminder>ctx</system-reminder>\nBackground (from the session that started this one): x") == "pasted"
        assert kind('<pasted_content id="a1">\nอ่าน HANDOFF ก่อน\n</pasted_content id="a1">') == "pasted"
        assert kind("<system-reminder>ctx</system-reminder>\napply") == "injected"
        assert kind("apply") == "typed"
        assert kind("ทำเลย ข้าม propose") == "typed"
        assert kind("this fix seem was fixed only single places, but what I want is component fixing") == "typed"
        assert has_thai("ขอภาษาไทยหน่อย") and not has_thai("apply")
        print("prompt_source selftest ok")
