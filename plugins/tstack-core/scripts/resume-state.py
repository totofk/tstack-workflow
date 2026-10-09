#!/usr/bin/env python3
"""UserPromptSubmit hook: after a gap, inject one line of git state so the
model re-syncs instead of continuing on a stale picture of the tree.
Never blocks, never errors; silent when the gap is short or cwd is not a repo."""
import json, os, re, subprocess, sys, tempfile, time

GAP_SECONDS = 10 * 60


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
    return r.stdout.strip() if r.returncode == 0 else None


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", d.get("session_id") or "default")
    cwd = d.get("cwd") or os.getcwd()
    stamp = os.path.join(tempfile.gettempdir(), f"claude-resume-state-{sid}.ts")
    now = time.time()
    last = None
    try:
        with open(stamp) as f:
            last = float(f.read().strip())
    except Exception:
        pass
    try:
        with open(stamp, "w") as f:
            f.write(str(now))
    except Exception:
        pass
    if last is None or now - last < GAP_SECONDS:
        return
    branch = git(cwd, "branch", "--show-current")
    if branch is None:
        return
    if branch == "":
        sha = git(cwd, "rev-parse", "--short", "HEAD") or "?"
        branch = f"(detached {sha})"
    ab = git(cwd, "rev-list", "--left-right", "--count", "@{u}...HEAD") or "no upstream"
    if ab != "no upstream":
        behind, ahead = ab.split()
        ab = f"ahead {ahead} / behind {behind}"
    dirty = git(cwd, "status", "--porcelain") or ""
    n = len(dirty.splitlines())
    gap = int((now - last) / 60)
    common = git(cwd, "rev-parse", "--git-common-dir") or ""
    where = "main tree" if common == ".git" else "worktree"
    print(f"[resume after {gap} min] {where} branch={branch} {ab} dirty={n} — re-read state before continuing a checklist")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
