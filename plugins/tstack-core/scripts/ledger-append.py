#!/usr/bin/env python3
"""Append one validated row to the dispatch ledger.

    ledger-append --session 1a2b3c4d --agent a7da4073 --lane verify --weight heavy \
        --turns 42 --cost 139.4k --wall 12.5 --outcome correct --note "s1-s7 pass" [--model ID]

Ledger: $CLAUDE_PLUGIN_DATA/dispatch-ledger.tsv, else this plugin's data dir
(Bash calls from a session do not get CLAUDE_PLUGIN_DATA). The date is today.
The model id comes from ~/.claude/agents/<lane>.md (`model: opus` ->
claude-opus-5-5) unless --model is given. `-` marks an unknown value and is
meant for backfill only.

Rejected (2026-10-25 retro: drift the weekly retro had to clean by hand):
turns that are not an integer ("59 tool uses", blank), cost not in
`<N.N>k tok` or `$x` form ("139419tok"), an outcome outside
correct | partial | blocked | wrong | -, tabs or newlines in any field.
Exit 2 with the reason on a rejected row; nothing is written.
"""
import argparse
import os
import re
import sys
import tempfile
import time

HEADER = "date\tsession\tagent\tmodel\tlane\tweight\tturns\tcost_usd\twall_min\toutcome\tnote"
OUTCOMES = ("correct", "partial", "blocked", "wrong", "-")
MODEL_IDS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5", "fable": "claude-fable-5-1"}
AGENTS_DIR = os.path.expanduser("~/.claude/agents")


def ledger_path() -> str:
    data = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.claude/plugins/data/tstack-core-tstack-workflow")
    return os.path.join(data, "dispatch-ledger.tsv")


def lane_model(lane: str, agents_dir: str = AGENTS_DIR) -> str:
    try:
        text = open(os.path.join(agents_dir, lane + ".md"), encoding="utf-8").read()
    except OSError:
        raise ValueError(f"no lane file {agents_dir}/{lane}.md — pass --model with the exact id")
    m = re.search(r"^model:\s*(\S+)", text, re.M)
    if not m:
        raise ValueError(f"{lane}.md has no model: line — pass --model")
    return MODEL_IDS.get(m.group(1), m.group(1))


def cost(v: str) -> str:
    v = v.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)k(?: tok)?", v)
    if m:
        return f"{float(m.group(1)):.1f}k tok"
    if re.fullmatch(r"\$\d+(?:\.\d+)?", v) or v == "-":
        return v
    raise ValueError(f"cost {v!r}: use <N.N>k tok (e.g. 139.4k) or $x (e.g. $0.25)")


def row(a, agents_dir: str = AGENTS_DIR) -> str:
    turns = a.turns.strip()
    if not (turns.isdigit() or turns == "-"):
        raise ValueError(f"turns {a.turns!r}: an integer, or - when unknown")
    wall = a.wall.strip()
    if not (re.fullmatch(r"\d+(?:\.\d+)?", wall) or wall == "-"):
        raise ValueError(f"wall_min {a.wall!r}: a number of minutes, or -")
    if a.outcome not in OUTCOMES:
        raise ValueError(f"outcome {a.outcome!r}: one of {' | '.join(OUTCOMES)}")
    fields = [a.date or time.strftime("%Y-%m-%d"), a.session, a.agent, a.model or lane_model(a.lane, agents_dir),
              a.lane, a.weight, turns, cost(a.cost), wall, a.outcome, a.note]
    for name, f in zip(HEADER.split("\t"), fields):
        if not f.strip() or re.search(r"[\t\r\n]", f):
            raise ValueError(f"{name} {f!r}: empty, or holds a tab/newline")
    return "\t".join(f.strip() for f in fields)


def append(line: str, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fresh = not os.path.exists(path) or os.path.getsize(path) == 0
    if not fresh:
        with open(path, encoding="utf-8") as f:
            if f.readline().rstrip("\n") != HEADER:
                raise ValueError(f"{path}: header differs from {HEADER!r}")
    with open(path, "a", encoding="utf-8") as f:
        f.write((HEADER + "\n" if fresh else "") + line + "\n")


def parser():
    p = argparse.ArgumentParser(description="Append one validated row to the dispatch ledger.")
    for name in ("session", "agent", "lane", "weight", "turns", "cost", "wall", "outcome"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--note", default="-")
    p.add_argument("--model")
    p.add_argument("--date")
    p.add_argument("--ledger")
    return p


def selftest():
    tmp = tempfile.mkdtemp(prefix="ledger-append-selftest-")
    ledger = os.path.join(tmp, "dispatch-ledger.tsv")
    agents = os.path.join(tmp, "agents")
    os.makedirs(agents)
    with open(os.path.join(agents, "research.md"), "w") as f:
        f.write("---\nname: research\nmodel: sonnet\nmaxTurns: 80\n---\n")
    base = ["--session", "1a2b3c4d", "--agent", "a7da4073", "--lane", "research", "--weight", "light",
            "--turns", "10", "--cost", "92.7k", "--wall", "0.7", "--outcome", "correct", "--note", "locate CarService deps"]

    def parse(*over):
        args = list(base)
        for k, v in zip(over[::2], over[1::2]):
            args[args.index(k) + 1] = v
        return parser().parse_args(args)

    r = row(parse(), agents).split("\t")
    assert r[3] == "claude-sonnet-5-5" and r[7] == "92.7k tok" and r[6] == "10", r
    assert row(parse("--cost", "139.4k tok"), agents).split("\t")[7] == "139.4k tok"
    assert row(parse("--cost", "$0.25"), agents).split("\t")[7] == "$0.25"
    over = parser().parse_args(base + ["--model", "claude-opus-5-5"])
    assert row(over, agents).split("\t")[3] == "claude-opus-5-5"
    backfill = row(parse("--turns", "-", "--cost", "-", "--wall", "-", "--outcome", "-"), agents).split("\t")
    assert backfill[6:10] == ["-", "-", "-", "-"], backfill
    for bad in (("--turns", "59 tool uses"), ("--turns", ""), ("--turns", " "), ("--cost", "139419tok"),
                ("--cost", "139419"), ("--outcome", "ok"), ("--wall", "3 min"), ("--note", "a\tb"), ("--lane", "nope")):
        try:
            row(parse(*bad), agents)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    append(row(parse(), agents), ledger)
    append(row(parse("--agent", "a4ae4a7c"), agents), ledger)
    lines = open(ledger, encoding="utf-8").read().splitlines()
    assert lines[0] == HEADER and len(lines) == 3 and all(len(ln.split("\t")) == 11 for ln in lines), lines
    with open(ledger, "w") as f:
        f.write("date\tsession\n")
    try:
        append(row(parse(), agents), ledger)
        raise AssertionError("appended under a foreign header")
    except ValueError:
        pass
    for f in (ledger, os.path.join(agents, "research.md")):
        os.remove(f)
    os.rmdir(agents)
    os.rmdir(tmp)
    print("ledger-append selftest ok")


def main():
    a = parser().parse_args()
    path = a.ledger or ledger_path()
    try:
        line = row(a)
        append(line, path)
    except ValueError as e:
        print(f"ledger-append: rejected — {e}", file=sys.stderr)
        sys.exit(2)
    print(f"appended to {path}:\n{line}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
