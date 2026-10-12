#!/usr/bin/env python3
"""PreToolUse hook: deny reads of secret files, hand Claude a masked copy instead.

Fail-closed by design (agreed 2026-08-18): the tool call touching `.env*` /
key material is DENIED via the permission system; a copy with every
`KEY=value` rewritten to `KEY=<masked:N>` is written under a temp dir and the
deny reason tells Claude to read that copy. Claude therefore sees key names,
never values. Any crash in this script must also block — the settings.json
wrapper is `python3 … || exit 2` and main() catches everything into exit 2.

Covers: Read, Grep (path), Bash (command text), context-mode MCP execute
tools (whole tool_input). Also denies bare `env` / `printenv` dumps. A bare
`ln -s` / `cp` of a secret file is allowed because it does not print values.

Run with --selftest to assert the gate still behaves (wired into SessionStart).
"""
import hashlib, json, os, re, sys, tempfile

MASK_ROOT = os.path.join(tempfile.gettempdir(), "claude-masked")
PUBLIC_PREFIX = "NEXT_PUBLIC_"  # shipped to the browser anyway — never masked
ENVRUN = re.compile(r"^\s*(?:\S*/)?envrun\s")  # bin/envrun: uses values, scrubs output
# a link/copy never prints the file's content: exactly one `[cd p &&] ln -s|cp src dst`, no /dev/ target
_ARG = r"[^\s|;&<>`$()]+"
LINK_COPY = re.compile(r"^\s*(?:cd\s+" + _ARG + r"\s+&&\s+)?(?:ln\s+-[sfn]+|cp(?:\s+-[A-Za-z]+)*)\s+" + _ARG + r"\s+(?!/dev/)" + _ARG + r"\s*$")
ALLOWED_ENV_SUFFIXES = (".example", ".sample", ".template", ".dist")

_PATH = r"[^\s'\"|;&<>()=]*"
_ENV_TOKEN = _PATH + r"(?:^|/|(?<=[\s'\"=(:]))" + r"\.env(?:\.[A-Za-z0-9_.-]+)?|" + _PATH + r"\.envrc"
_KEY_TOKEN = _PATH + r"(?:\.pem|\.key|\.p12|\.pfx|id_rsa[A-Za-z0-9_.-]*|id_ed25519[A-Za-z0-9_.-]*)"
SECRET_IN_TEXT = re.compile(
    r"(?:^|[\s'\"=(:])(" + _ENV_TOKEN + r"|" + _KEY_TOKEN + r")(?=$|[\s'\"|;&<>()])",
    re.M,
)
ENV_DUMP = re.compile(r"(?:^|[;&|(]\s*)(?:printenv|env)(?:\s*$|\s*[|>;&)])", re.M)
KV_LINE = re.compile(r"^(\s*(?:#+\s*)?(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*)(.*?)(\s*)$")  # commented-out KEY=value masked too


def is_secret_basename(name: str) -> bool:
    if name.startswith(".env"):
        return not name.endswith(ALLOWED_ENV_SUFFIXES)
    return bool(re.fullmatch(_KEY_TOKEN, name)) and name != ""


def is_secret_path(path: str) -> bool:
    if not path or path.startswith(MASK_ROOT):
        return False
    return is_secret_basename(os.path.basename(path))


def mask_text(text: str, binary: bool) -> str:
    if binary:
        return f"<masked: {len(text)} bytes of key material>\n"
    out = []
    for line in text.splitlines():
        m = KV_LINE.match(line)
        if m and m.group(3) and not m.group(2).startswith(PUBLIC_PREFIX):
            val = m.group(3).strip("'\"")
            out.append(f"{m.group(1)}<masked:{len(val)}>{m.group(4)}")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def write_masked_copy(path: str) -> str:
    real = os.path.realpath(path)
    with open(real, "r", errors="replace") as f:
        text = f.read()
    binary = not os.path.basename(real).startswith(".env")
    d = os.path.join(MASK_ROOT, hashlib.sha1(real.encode()).hexdigest()[:10])
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, os.path.basename(real) + ".masked")
    with open(out, "w") as f:
        f.write(mask_text(text, binary))
    return out


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def resolve_hits(text: str, cwd: str):
    hits = []
    for m in SECRET_IN_TEXT.finditer(text):
        tok = m.group(1)
        if not is_secret_basename(os.path.basename(tok)):
            continue
        p = tok if os.path.isabs(tok) else os.path.join(cwd, tok)
        if p.startswith(MASK_ROOT):
            continue
        hits.append(p if os.path.isfile(p) else tok)
    return hits


def decide(d: dict):
    """Return deny reason or None."""
    tool = d.get("tool_name", "")
    ti = d.get("tool_input", {}) or {}
    cwd = d.get("cwd") or os.getcwd()

    if tool in ("Read", "Grep"):
        p = ti.get("file_path") or ti.get("path") or ""
        if is_secret_path(p) and os.path.isfile(p):
            return f"Secret file: values are hidden by policy. Read the masked copy instead: {write_masked_copy(p)}"
        return None

    if tool == "Bash":
        text = ti.get("command", "")
        if ENVRUN.match(text):
            text = re.sub(r"--env-file\s+\S+", "", text)  # envrun may name the file; the rest is still scanned
    elif tool.startswith("mcp__") and "ctx_" in tool:
        text = json.dumps(ti)
    else:
        return None

    if tool == "Bash" and LINK_COPY.match(text):
        return None
    hits = resolve_hits(text, cwd)
    if hits:
        copies = [write_masked_copy(h) for h in hits if os.path.isabs(h) and os.path.isfile(h)]
        note = (" Masked copies: " + ", ".join(copies)) if copies else ""
        return f"Command touches secret file(s) {sorted(set(os.path.basename(h) for h in hits))}; denied by policy (values must stay hidden).{note}"
    if ENV_DUMP.search(text):
        return "Dumping the environment (`env`/`printenv`) is denied by policy — it prints secret values. Query a specific non-secret variable instead."
    return None


def selftest() -> int:
    tmp = tempfile.mkdtemp()
    env = os.path.join(tmp, ".env")
    with open(env, "w") as f:
        f.write("# c\nA=1\nB='two words'\nexport C=\"x\"\nD=\nURL=postgres://u:p@h/db\nNEXT_PUBLIC_API=https://api.example\nSECRET_TOKEN=sekret-value-123\n# Remote\n# OLD=\"postgres://u:p@h/db\"\n# NEXT_PUBLIC_X=y\n")
    ex = os.path.join(tmp, ".env.example")
    open(ex, "w").write("A=\n")
    pem = os.path.join(tmp, "k.pem")
    open(pem, "w").write("-----BEGIN-----\nabc\n")

    def run(tool, ti):
        return decide({"tool_name": tool, "tool_input": ti, "cwd": tmp})

    r = run("Read", {"file_path": env}); assert r and "masked" in r, r
    copy = r.rsplit(": ", 1)[1]
    body = open(copy).read()
    assert body == "# c\nA=<masked:1>\nB=<masked:9>\nexport C=<masked:1>\nD=\nURL=<masked:19>\nNEXT_PUBLIC_API=https://api.example\nSECRET_TOKEN=<masked:16>\n# Remote\n# OLD=<masked:19>\n# NEXT_PUBLIC_X=y\n", body
    assert run("Read", {"file_path": copy}) is None            # masked copy is readable
    assert run("Bash", {"command": f"cat {copy}"}) is None
    assert run("Read", {"file_path": ex}) is None              # .env.example allowed
    assert run("Read", {"file_path": pem}) and "bytes" in open(run("Read", {"file_path": pem}).rsplit(": ", 1)[1]).read()
    assert run("Grep", {"path": env, "pattern": "A"})
    assert run("Bash", {"command": "cat .env"})
    assert run("Bash", {"command": "cat .env.local | head"})
    assert run("Bash", {"command": "grep DB .env.qa"})
    assert run("Bash", {"command": "source ./.env && pnpm dev"})
    assert run("Bash", {"command": "cat ~/.ssh/id_rsa"})
    assert run("Bash", {"command": "openssl x509 -in cert.pem"})
    assert run("Bash", {"command": "printenv"})
    assert run("Bash", {"command": "cd x && env | grep FOO"})
    r = run("Bash", {"command": f"printf 'X=1' > {env}"}); assert r and copy in r, r  # abs path resolved, not cwd/.env
    assert run("Bash", {"command": "cat sub/dotenv/.env.qa"})
    assert run("Bash", {"command": "cat src/dotenv.ts"}) is None
    assert run("Bash", {"command": "cat .env.example"}) is None
    assert run("Bash", {"command": "pnpm add dotenv"}) is None
    assert run("Bash", {"command": "git status"}) is None
    assert run("Bash", {"command": "env FOO=1 pnpm test"}) is None
    assert run("Bash", {"command": "ls -la src/environment"}) is None
    assert run("Bash", {"command": "ln -s ../../.env .env"}) is None   # bare link/copy allowed
    assert run("Bash", {"command": "cd /tmp/wt && ln -sfn ../../.env .env"}) is None
    assert run("Bash", {"command": "cp .env .env.local"}) is None
    assert run("Bash", {"command": "cp -p ../.env ./.env"}) is None
    assert run("Bash", {"command": "ln -s ../../.env .env && cat .env"})
    assert run("Bash", {"command": "cp .env /dev/stdout"})
    assert run("mcp__plugin_context-mode_context-mode__ctx_execute", {"language": "shell", "code": "cat .env"})
    assert run("mcp__plugin_context-mode_context-mode__ctx_execute", {"language": "shell", "code": "ls src"}) is None
    assert run("Edit", {"file_path": env}) is None             # edit-gate owns writes
    # envrun: may name the env file, but anything else touching .env is still denied
    assert run("Bash", {"command": f"envrun {tmp} -- psql \"$URL\" -c 'select 1'"}) is None
    assert run("Bash", {"command": f"~/.claude/scripts/envrun --env-file {tmp}/.env.qa {tmp} -- sh -c 'echo $A'"}) is None
    assert run("Bash", {"command": f"envrun {tmp} -- cat .env"})
    assert run("Bash", {"command": f"envrun {tmp} -- true; cat .env"})
    # envrun end-to-end: child sees values, Claude sees scrubbed output
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    envrun = next((p for p in (os.path.join(here, "envrun"), os.path.join(here, "..", "bin", "envrun")) if os.path.isfile(p)), "envrun")
    p = subprocess.run([envrun, tmp, "--", "sh", "-c", 'echo "$SECRET_TOKEN $URL $NEXT_PUBLIC_API"; echo "$A" >&2; exit 3'],
                       capture_output=True, text=True)
    assert p.returncode == 3, p
    assert p.stdout == "<masked:SECRET_TOKEN> <masked:URL> https://api.example\n", p.stdout
    assert p.stderr == "1\n", p.stderr  # value shorter than MIN_LEN passes through by design
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    d = json.load(sys.stdin)
    reason = decide(d)
    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # fail closed
        print(f"secret-read-gate: internal error, blocking: {e!r}", file=sys.stderr)
        sys.exit(2)
