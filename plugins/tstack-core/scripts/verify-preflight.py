#!/usr/bin/env python3
"""Verify preflight: PASS/FAIL rows for the environment a verifier will be dispatched into.

Run by the main session BEFORE writing the brief; paste the output into the
brief. Any FAIL = no dispatch (Environment handoff contract). Every row is a
past incident:
  env-keys   missing keys in .env vs .env.example         c1025ec5 03:01 (PAYMENT_GATEWAY_*)
  cors       web origin in the api CORS list              c1025ec5 04:25, 21993926
  migrate    prisma migrate status clean                  c1025ec5 (12 pending migrations)
  framework  installed major == pinned major per tree     db2d7919 ac2eb8f0 (Next 15 served, 16 pinned)
  page       final URL path == requested, 200, real body  21993926 (/privacy, /claim, /rns redirected)
  probe      one request through a third-party auth or    c1025ec5 af6acb4e (Caller UAT Expire),
             a test-data id resolves                      c1025ec5 lead ids not on Caller UAT

Secrets: .env files are read by this script only to compare KEY NAMES and to
load the api env for `prisma migrate status`; no value is ever printed. The
invocation itself names no .env file, so the secret-read gate does not fire;
keep it that way (pass directories, not env paths).
Network: urllib only (curl/wget are refused by the context-mode gate).

Usage (the script sits in tstack-core's scripts/, beside the bin/ the enabled plugin puts on PATH):
  PF=$(echo "$PATH" | tr ':' '\n' | while read -r d; do [ -f "$d/../scripts/verify-preflight.py" ] && { echo "$d/../scripts/verify-preflight.py"; break; }; done)
  python3 "$PF" \
      --web-dir <worktree> --api-dir <api checkout> --web-origin http://localhost:3001 \
      --page http://localhost:3001/claim/car-insurance \
      --probe "caller-auth|http://localhost:2001/health/caller|ok|Expire" \
      --probe "lead-9876|http://localhost:2001/leads/9876|\"id\""
  --probe NAME|URL[|must-contain[|must-not-contain]]   (pipes separate the fields)
  --cors-key NAME   env key holding the allowlist (default CORS_ENDPOINTS; tries CORS_ORIGINS, CORS_ORIGIN too)
  --skip migrate    skip a row by name (env-keys, cors, migrate, framework)
Exit 1 on any FAIL. --selftest runs the asserts below.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

FRAMEWORKS = ("next", "@nestjs/core", "nuxt", "vite", "express", "hono")


def env_keys(path: str):
    keys = set()
    try:
        for ln in open(path, encoding="utf-8"):
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
            if m:
                keys.add(m.group(1))
    except OSError:
        return None
    return keys


def env_values(path: str):
    vals = {}
    try:
        for ln in open(path, encoding="utf-8"):
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", ln)
            if m:
                vals[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return vals


def missing_keys(example, actual):
    if example is None or actual is None:
        return None
    return sorted(example - actual)


def cors_allows(allowlist: str, origin: str) -> bool:
    items = [s.strip().strip('"').strip("'") for s in re.split(r"[,\s]+", allowlist or "") if s.strip()]
    o = urlparse(origin)
    for it in items:
        if it in ("*", origin):
            return True
        p = urlparse(it if "://" in it else "http://" + it)
        if (p.hostname, p.port or 80) == (o.hostname, o.port or 80):
            return True
    return False


def major(v: str):
    m = re.search(r"(\d+)", v or "")
    return int(m.group(1)) if m else None


def framework_versions(tree: str):
    """[(pkg, installed, pinned)] for the frameworks the tree's package.json pins."""
    out = []
    try:
        pkg = json.load(open(os.path.join(tree, "package.json")))
    except (OSError, ValueError):
        return out
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for name in FRAMEWORKS:
        if name not in deps:
            continue
        try:
            installed = json.load(open(os.path.join(tree, "node_modules", *name.split("/"), "package.json")))["version"]
        except (OSError, ValueError, KeyError):
            installed = None
        out.append((name, installed, deps[name]))
    return out


def fetch(url: str, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "verify-preflight/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() or url, (e.read(20_000).decode("utf-8", "replace") if e.fp else "")
    except Exception as e:  # URLError, timeout, ConnectionRefused
        return None, url, str(e)


def page_verdict(requested: str, status, final: str, body: str):
    rp, fp = urlparse(requested).path.rstrip("/") or "/", urlparse(final).path.rstrip("/") or "/"
    if status != 200:
        return False, f"status {status}"
    if rp != fp:
        return False, f"redirected to {fp}"
    if len(body) < 500:
        return False, f"body {len(body)} bytes"
    if re.search(r"__next_error__|Application error|Internal Server Error", body):
        return False, "error page markup"
    return True, f"200, final path {fp}, {len(body)} bytes"


def parse_probe(spec: str):
    parts = spec.split("|")
    if len(parts) < 2:
        raise ValueError(f"probe needs NAME|URL: {spec}")
    name, url = parts[0].strip(), parts[1].strip()
    expect = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    reject = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
    return name, url, expect, reject


def probe_verdict(status, body: str, expect, reject):
    if status is None or not (200 <= status < 300):
        return False, f"status {status}: {body[:80]!r}"
    if expect and expect not in body:
        return False, f"missing {expect!r}"
    if reject and reject in body:
        return False, f"contains {reject!r}"
    return True, f"status {status}"


def migrate_status(api_dir: str):
    env = dict(os.environ)
    env.update({k: v for k, v in env_values(os.path.join(api_dir, ".env")).items() if k not in env})
    try:
        r = subprocess.run(["pnpm", "exec", "prisma", "migrate", "status"], cwd=api_dir, env=env,
                           capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"could not run: {e.__class__.__name__}"
    out = r.stdout + r.stderr
    if "Database schema is up to date" in out:
        return True, "schema up to date"
    m = re.search(r"(\d+) migrations? (?:have|has) not yet been applied", out)
    if m:
        return False, f"{m.group(1)} pending migration(s)"
    return False, (out.strip().splitlines() or ["no output"])[-1][:120]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-dir")
    ap.add_argument("--web-dir")
    ap.add_argument("--web-origin")
    ap.add_argument("--cors-key", default="CORS_ENDPOINTS")
    ap.add_argument("--page", action="append", default=[])
    ap.add_argument("--probe", action="append", default=[])
    ap.add_argument("--skip", action="append", default=[])
    a = ap.parse_args(argv)
    rows = []  # (name, ok, detail)

    for label, d in (("api", a.api_dir), ("web", a.web_dir)):
        if not d or "env-keys" in a.skip:
            continue
        example, actual = env_keys(os.path.join(d, ".env.example")), env_keys(os.path.join(d, ".env"))
        if example is not None and actual is None:
            # the worktree forgot its .env link: every example key is missing
            rows.append((f"env-keys {label}", False, f"no .env beside .env.example ({len(example)} keys)"))
            continue
        miss = missing_keys(example, actual)
        if miss is None:
            rows.append((f"env-keys {label}", None, "no .env.example"))
        else:
            rows.append((f"env-keys {label}", not miss, "all example keys present" if not miss else "missing: " + ", ".join(miss)))

    if a.api_dir and a.web_origin and "cors" not in a.skip:
        vals = env_values(os.path.join(a.api_dir, ".env"))
        key = next((k for k in (a.cors_key, "CORS_ORIGINS", "CORS_ORIGIN", "CORS_ENDPOINTS") if k in vals), None)
        if key is None:
            rows.append(("cors", False, f"no {a.cors_key} key in api .env"))
        else:
            ok = cors_allows(vals[key], a.web_origin)
            rows.append(("cors", ok, f"{key} {'contains' if ok else 'lacks'} {a.web_origin}"))

    if a.api_dir and "migrate" not in a.skip:
        rows.append(("migrate", *migrate_status(a.api_dir)))

    for label, d in (("api", a.api_dir), ("web", a.web_dir)):
        if not d or "framework" in a.skip:
            continue
        for name, inst, pin in framework_versions(d):
            if inst is None:
                rows.append((f"framework {label}", False, f"{name} not installed in {d}/node_modules"))
            else:
                ok = major(inst) == major(pin)
                rows.append((f"framework {label}", ok, f"{name} installed {inst} vs pinned {pin}"))

    for url in a.page:
        status, final, body = fetch(url)
        rows.append((f"page {urlparse(url).path or '/'}", *page_verdict(url, status, final, body)))

    for spec in a.probe:
        name, url, expect, reject = parse_probe(spec)
        status, _, body = fetch(url)
        rows.append((f"probe {name}", *probe_verdict(status, body, expect, reject)))

    width = max((len(r[0]) for r in rows), default=10)
    fails = 0
    for name, ok, detail in rows:
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        fails += tag == "FAIL"
        print(f"{tag}  {name.ljust(width)}  {detail}")
    print(f"\n{'NO DISPATCH' if fails else 'OK TO DISPATCH'}: {fails} FAIL, {len(rows)} rows")
    return 1 if fails else 0


def selftest():
    import contextlib
    import io
    import tempfile
    assert missing_keys({"A", "B"}, {"A"}) == ["B"] and missing_keys({"A"}, {"A", "C"}) == []
    assert missing_keys(None, {"A"}) is None
    assert cors_allows("http://localhost:3000,http://localhost:3001", "http://localhost:3001")
    assert not cors_allows("http://localhost:3000", "http://localhost:3001")  # c1025ec5 04:25
    assert cors_allows("*", "http://localhost:3002") and cors_allows("localhost:3002", "http://localhost:3002")
    assert major("15.5.15") == 15 and major("^16.3.5") == 16 and major("15.5.15") != major("16.3.5")  # ac2eb8f0
    ok, why = page_verdict("http://h/privacy", 200, "http://h/", "x" * 600)
    assert not ok and "redirected" in why  # 21993926 /privacy
    assert page_verdict("http://h/claim/car-insurance", 200, "http://h/claim/car-insurance/", "x" * 600)[0]
    assert not page_verdict("http://h/x", 200, "http://h/x", "spinner")[0]
    assert parse_probe("caller|http://a/b|ok|Expire") == ("caller", "http://a/b", "ok", "Expire")
    assert parse_probe("lead|http://a/b") == ("lead", "http://a/b", None, None)
    assert not probe_verdict(200, '{"error":"Expire (traceId x)"}', None, "Expire")[0]  # af6acb4e
    assert probe_verdict(200, '{"id":9876}', '"id"', None)[0]
    assert not probe_verdict(None, "Connection refused", None, None)[0]

    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def run(*argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    def row(out, name):
        return next(ln for ln in out.splitlines() if re.match(rf"\w+  {re.escape(name)}\s", ln))

    secrets = ("fake-secret-aaa111", "fake-secret-bbb222", "fake-secret-ccc333")
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, ".env.example"), "A=1\nB=2\n# c\nexport C=3\n")
        write(os.path.join(d, ".env"), f"A={secrets[0]}\nC='{secrets[1]}'\n")
        assert missing_keys(env_keys(os.path.join(d, ".env.example")), env_keys(os.path.join(d, ".env"))) == ["B"]
        assert env_values(os.path.join(d, ".env"))["C"] == secrets[1]
        write(os.path.join(d, "package.json"), json.dumps({"dependencies": {"next": "16.3.5"}}))
        write(os.path.join(d, "node_modules", "next", "package.json"), json.dumps({"version": "15.5.15"}))
        assert framework_versions(d) == [("next", "15.5.15", "16.3.5")]

        # missing key + framework major mismatch: both rows FAIL, no value printed
        code, out = run("--web-dir", d)
        assert code == 1, out
        assert row(out, "env-keys web").startswith("FAIL") and "missing: B" in out, out
        assert row(out, "framework web").startswith("FAIL"), out
        assert "NO DISPATCH" in out, out
        assert not any(s in out for s in secrets), out

        # cors row reads the api .env values but prints only the key name and origin
        write(os.path.join(d, ".env"), f"A={secrets[0]}\nB={secrets[2]}\nC=x\nCORS_ENDPOINTS=http://localhost:3001\n")
        code, out = run("--api-dir", d, "--web-origin", "http://localhost:3002", "--skip", "migrate", "--skip", "framework")
        assert row(out, "cors").startswith("FAIL") and not any(s in out for s in secrets), out

        # matching pair + matching major: every row PASS, exit 0
        write(os.path.join(d, "node_modules", "next", "package.json"), json.dumps({"version": "16.0.1"}))
        code, out = run("--web-dir", d)
        assert code == 0, out
        assert row(out, "env-keys web").startswith("PASS") and row(out, "framework web").startswith("PASS"), out
        assert "OK TO DISPATCH" in out and not any(s in out for s in secrets), out

        # example present, .env absent (worktree link forgotten) is a FAIL, not a SKIP
        os.remove(os.path.join(d, ".env"))
        code, out = run("--web-dir", d, "--skip", "framework")
        assert code == 1 and row(out, "env-keys web").startswith("FAIL"), out
    print("verify-preflight selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        sys.exit(main(sys.argv[1:]))
