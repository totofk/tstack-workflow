#!/usr/bin/env python3
"""UserPromptSubmit hook: arm the edit-gate when the user gives a go-word.

Go-words: $CLAUDE_PLUGIN_DATA/edit-gate-words.txt when it exists (the user's
own list, survives plugin updates), else edit-gate-words.txt next to this
script (one per line, # comments).

Session-wide arm: 'arm session' / 'ปลดล็อค' opens the gate until the user
says 'lock' / 'ล็อค' or the session ends. Per-message arm still re-locks
on every new message. The user hands the key; Claude never self-arms.
"""
import sys, json, re, os, tempfile

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


def main() -> None:
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    prompt = d.get("prompt", "") or ""
    session_id = d.get("session_id", "") or "default"
    arm, arm_session = flag_paths(session_id)
    lowered = prompt.lower()

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
        return
    if any(matches(p, prompt, lowered) for p in SESSION_LOCK_PHRASES):
        try:
            if os.path.exists(arm_session):
                os.remove(arm_session)
        except OSError:
            pass
        return

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


if __name__ == "__main__":
    main()
