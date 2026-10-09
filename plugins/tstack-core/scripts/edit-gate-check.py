#!/usr/bin/env python3
"""PreToolUse hook on Edit/Write/NotebookEdit: block file mutation unless armed.

Scope (agreed 2026-07-09): a file is gated only when it lives inside a git
work tree AND is not gitignored — i.e. real project files. Everything else
(temp, scratchpad, Claude's memory dir, ignored/generated files, any dir
that isn't a repo) is free to write.

Armed by: a go-word this turn (.arm) or an explicit session arm (.arm-session),
both written by edit-gate-arm.py. A new user message clears .arm; .arm-session
persists until the user says lock. Claude cannot arm either flag itself.

Exit 0 = allow. Exit 2 = block with feedback to Claude.
"""
import sys, json, re, os, subprocess, tempfile

GATED_TOOLS = {"Edit", "Write", "NotebookEdit"}

# .md/.mdx living in tool scratch dirs stay free even inside a repo.
SCRATCH_DOC_DIRS = ("/.cursor/", "/.wolf/", "/openspec/")


def flag_paths(session_id: str):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "default")
    base = tempfile.gettempdir()
    return (
        os.path.join(base, f"claude-edit-gate-{safe}.arm"),
        os.path.join(base, f"claude-edit-gate-{safe}.arm-session"),
    )


def is_gated_path(target: str) -> bool:
    # Fast-path exemptions — skip the git subprocesses entirely.
    if target.startswith(tempfile.gettempdir()) or "/scratchpad/" in target:
        return False
    if target.endswith((".md", ".mdx")) and any(seg in target for seg in SCRATCH_DOC_DIRS):
        return False

    # New files: walk up to the nearest existing dir to ask git about it.
    d = os.path.dirname(target) or "."
    while d and d != "/" and not os.path.isdir(d):
        d = os.path.dirname(d)
    if not d or not os.path.isdir(d):
        return True  # unresolvable parent — fail toward protection

    try:
        r = subprocess.run(
            ["git", "-C", d, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, timeout=5,
        )
        if r.returncode != 0 or r.stdout.strip() != b"true":
            return False  # not inside a git work tree → free
        r = subprocess.run(
            ["git", "-C", d, "check-ignore", "-q", "--", target],
            capture_output=True, timeout=5,
        )
        return r.returncode != 0  # ignored → free; tracked/trackable → gated
    except Exception:
        return True  # git hiccup on a repo-ish path: fail toward protection


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        d = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # fail-open on malformed input; don't wedge the session

    if d.get("tool_name", "") not in GATED_TOOLS:
        sys.exit(0)

    target = (d.get("tool_input", {}) or {}).get("file_path", "") or ""
    if not is_gated_path(target):
        sys.exit(0)

    arm, arm_session = flag_paths(d.get("session_id", "") or "default")
    if os.path.exists(arm) or os.path.exists(arm_session):
        sys.exit(0)

    block(
        "Edit blocked by edit-gate: no go-word from the user this turn and no "
        "session arm. Show the user the intended change (ready-to-apply diff), "
        "then wait for a go-word (apply / fix it / ทำเลย / แก้เลย / ...) or "
        "'arm session'/'ปลดล็อค' for the whole session. If the user's message "
        "clearly intended action but used a word not in the go-word list, "
        "offer to add it to $CLAUDE_PLUGIN_DATA/edit-gate-words.txt."
    )


if __name__ == "__main__":
    main()
