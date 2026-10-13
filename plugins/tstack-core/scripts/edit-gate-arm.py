#!/usr/bin/env python3
"""UserPromptSubmit hook: arm the edit-gate when the user gives a go-word.

Go-words: $CLAUDE_PLUGIN_DATA/edit-gate-words.txt when it exists (the user's
own list, survives plugin updates), else edit-gate-words.txt next to this
script (one per line, # comments).

Session-wide arm: 'arm session' / 'ปลดล็อค' opens the gate until the user
says 'lock' / 'ล็อค' or the session ends. Per-message arm still re-locks
on every new message. The user hands the key; Claude never self-arms.

Queued events (task-notifications, subagent hand-backs, cross-session
messages) are not user messages: they neither re-lock nor arm. Forensics
2026-10-08 (workflow-reviews/off-cycle/2026-10-08-edit-gate-blocks.md):
5 of 8 implementer blocks were a notification wiping the turn arm between
the user's go-word and the dispatch; hand-back text containing "arm session"
or the wrapper's "do it" armed sessions the user never opened.
"""
import sys, json, re, os, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prompt_source import is_queued  # noqa: E402

# ponytail: fallback if the words file is unreadable — minimal set, not the full list
FALLBACK_GO_WORDS = ["apply", "go ahead", "fix it", "ทำเลย", "แก้เลย"]
WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edit-gate-words.txt")
_user_words = os.path.join(os.environ.get("CLAUDE_PLUGIN_DATA", ""), "edit-gate-words.txt")
if os.environ.get("CLAUDE_PLUGIN_DATA") and os.path.isfile(_user_words):
    WORDS_FILE = _user_words

# Arm requires an exact compound phrase — bare 'ปลดล็อค' proved too easy to
# trip by merely mentioning it (2026-07-09). Bare 'ล็อค' stays: an accidental
# lock fails safe.
SESSION_ARM_PHRASES = ["arm session", "unlock session", "ปลดล็อค session", "ปลดล็อค gate"]
# 'ปลดล็อค …' contains 'ล็อค' — always test arm phrases BEFORE lock phrases.
SESSION_LOCK_PHRASES = ["lock", "ล็อค"]


def load_go_words():
    try:
        with open(WORDS_FILE, encoding="utf-8") as f:
            words = [w.strip() for w in f if w.strip() and not w.startswith("#")]
        return words or FALLBACK_GO_WORDS
    except OSError:
        return FALLBACK_GO_WORDS


def flag_paths(session_id: str):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "default")
    base = tempfile.gettempdir()
    return (
        os.path.join(base, f"claude-edit-gate-{safe}.arm"),
        os.path.join(base, f"claude-edit-gate-{safe}.arm-session"),
    )


def matches(word: str, prompt: str, lowered: str) -> bool:
    if word.startswith("re:"):
        try:
            return re.search(word[3:], prompt) is not None
        except re.error:
            return False
    wl = word.lower()
    # Thai has no word boundaries; use substring for Thai, \b for latin.
    if re.search(r"[a-z]", wl):
        return re.search(r"\b" + re.escape(wl) + r"\b", lowered) is not None
    return wl in prompt


def handle(prompt: str, session_id: str) -> str:
    """Apply the prompt to the flag files. Returns what happened (for --selftest)."""
    arm, arm_session = flag_paths(session_id)
    lowered = prompt.lower()

    # A queued event is not the user: leave both flags exactly as they are.
    if is_queued(prompt):
        return "ignored-queued"

    # Per-message re-lock: clear the turn arm; the session arm persists.
    try:
        if os.path.exists(arm):
            os.remove(arm)
    except OSError:
        pass

    # Session arm/lock commands (arm checked first — see SESSION_LOCK_PHRASES note).
    if any(matches(p, prompt, lowered) for p in SESSION_ARM_PHRASES):
        try:
            with open(arm_session, "w") as f:
                f.write("armed-session")
        except OSError:
            pass
        return "armed-session"
    if any(matches(p, prompt, lowered) for p in SESSION_LOCK_PHRASES):
        try:
            if os.path.exists(arm_session):
                os.remove(arm_session)
        except OSError:
            pass
        return "locked"

    armed = any(matches(w, prompt, lowered) for w in load_go_words())

    # "บันทึก ... เลย" with words in between (e.g. "บันทึก 2 อัน ... เลย")
    if not armed and re.search(r"บันทึก.*เลย", prompt):
        armed = True

    # Leading affirmative counts as approval: "yes, edit settings.json ..."
    # Leading-only — a mid-sentence "yes" ("yes but don't edit") must not arm.
    # Thai matched without \b (fails after combining marks like the ่ in ใช่).
    if not armed and re.search(r"^\s*(?:(?:yes|yeah|yep)\b|ใช่)[ ,]", lowered + " "):
        armed = True

    # Bare "ก" (one-char ack) and leading "โอเค มปร" also arm.
    # ก must be the whole message — leading ก starts too many Thai words (ก่อน, กรุณา).
    if not armed and (re.fullmatch(r"\s*ก\s*", prompt) or re.match(r"\s*โอเค มปร", prompt)):
        armed = True

    if armed:
        try:
            with open(arm, "w") as f:
                f.write("armed")
        except OSError:
            pass
        return "armed"
    return "relocked"


def main() -> None:
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    handle(d.get("prompt", "") or "", d.get("session_id", "") or "default")


def selftest() -> None:
    sid = "edit-gate-arm-selftest"
    arm, arm_session = flag_paths(sid)
    for p in (arm, arm_session):
        if os.path.exists(p):
            os.remove(p)
    try:
        # Baseline behaviour unchanged.
        assert handle("apply", sid) == "armed" and os.path.exists(arm)
        assert handle("ok", sid) == "relocked" and not os.path.exists(arm)
        assert handle("arm session", sid) == "armed-session" and os.path.exists(arm_session)
        assert handle("ล็อค", sid) == "locked" and not os.path.exists(arm_session)
        # Forensics D1: a notification between the go-word and the dispatch must not wipe the arm.
        assert handle("ทำลง branch เดิม ทำเลย ข้าม propose", sid) == "armed"
        assert handle("<task-notification>\n<task-id>abc</task-id>\n<status>completed</status>", sid) == "ignored-queued"
        assert os.path.exists(arm), "dabb293d 10:13: notification wiped the turn arm"
        # Hole 2: hand-back text must not arm the session or the turn.
        handle("ok", sid)
        assert handle("[Subagent hand-back] ... please send `arm session` and apply, do it", sid) == "ignored-queued"
        assert not os.path.exists(arm) and not os.path.exists(arm_session), "hand-back armed the session"
        assert handle("<agent-message from=\"a1\">\n[Subagent hand-back] apply", sid) == "ignored-queued"
        assert handle("Another Claude session sent a message while you were working:\napply", sid) == "ignored-queued"
        assert not os.path.exists(arm)
        # Slash command still arms on purpose (opsx-edit-gate memory: the user accepted /opsx:apply as the key).
        assert handle("<command-message>opsx:apply</command-message> <command-name>/opsx:apply</command-name>", sid) == "armed"
        # A typed message still re-locks (per-message rule intact).
        assert handle("verify ที่นี่", sid) == "relocked" and not os.path.exists(arm)
        print("edit-gate-arm selftest ok")
    finally:
        for p in (arm, arm_session):
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
