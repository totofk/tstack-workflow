---
name: verify
description: Use when the user approves a verify pass ("verify", "verify local", "test it") after a code change. Prepares the environment, then dispatches a verifier subagent with reusable scripts. Ports, worktree path and launch entry come from `workflow-config` (repo .agents/workflow.json, personal .claude/workflow.json, or a team adapter).
---

The orchestrator delivers a **proven-ready** environment; the verifier only verifies. Every step below has a checkable criterion — do not dispatch until all pass.

## 0. Config
`workflow-config` prints the repo's delivery config merged over defaults. This skill uses `worktree` (path template with `{port}`), `launch.entry` (launch.json entry name template with `{port}`) and `launch.ports` (the ports those entries serve). A repo with no workflow file (lookup order in the `workflow-config` docstring) gets the defaults; a repo whose launch entries do not exist yet needs them added once (user-side, in launch.json) before the first verify.

## 1. Route
First line of the reply: `→ delegate: Opus (verify — adaptive loop)`. Only an explicit "do it yourself" from the user changes this to `→ self`.

## 2. Preflight (done by me, in order)
Size first: state the diff size and the path a real customer walks. A one-file UI change gets ONE end-to-end smoke of that path (to the last screen) + screenshot, not a scenario matrix; scale up only for FE↔BE contract, payload, or multi-module changes.

Task home: `~/.claude/handoffs/<task>/verify/` — `mkdir -p` it and `touch ~/.claude/handoffs/<task>` so the 30-day cleanup sees the task as active. It survives sessions and travels with the handoff.

**Private worktree, always.** Other sessions/agents work in the main tree concurrently, so the verify server never runs from it. The repo keeps one permanent launch entry per port in `launch.ports` (name = `launch.entry` with `{port}` filled in) that serves the worktree at `worktree` with `{port}` filled in — no `launch.json` edit (and no edit-gate round trip) per run:

```bash
PORT=$(for p in $(workflow-config launch.ports | tr -d '[]' | tr ',' ' '); do lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1 || { echo $p; break; }; done)
WT=$(workflow-config worktree | sed "s/{port}/$PORT/")
git worktree add --detach "$WT" <sha-under-test>
# dependencies: a real install in the worktree (`pnpm install --frozen-lockfile`); symlink the main tree's node_modules only when both trees share the same lockfile
```
then `preview_start` with the entry name from `workflow-config launch.entry` (`{port}` → `$PORT`). Uncommitted edits under test → commit them (or `git stash create` + `git worktree add --detach … <stash-sha>`) so the worktree has them. Teardown in the same turn the result is reported: `preview_stop`, `git worktree remove "$WT"`, `git worktree prune`.

| Check | Criterion |
|---|---|
| Previous round | `ls ~/.claude/handoffs/<task>/verify/` — existing scripts/report are listed in the brief for reuse |
| Served tree = code under test | `lsof -a -p <next-server pid> -d cwd -Fn` = the worktree; `git -C <worktree> rev-parse --short HEAD` = the sha under test |
| Frontend server | `python3 <task evidence dir>/smoke.py`, where smoke.py does `urllib.request.urlopen('http://localhost:<port>/<entry-page>').status` → `200` (compiles the page so the chunk greps below are meaningful); `curl`/`wget` are refused by the shell gate; use `python3` urllib or node, saved as a script in the task's verify directory |
| Bundle sanity | `grep -rl '<feature symbol>' <worktree>/.next/static/chunks` ≥ 1 file; `grep -rl 'localhost:<any other port>' …/chunks` = 0 files (a stale/foreign build output sends `/api/*` to another session's server and passes silently while that server is up — see the delivery rules, "Verify serves a private worktree") |
| Worktree env | the worktree gets the main repo's `.env` by `ln -s ../../.env .env` (the secret gate allows a bare link/copy); port-specific overrides go in `.env.local`, listed in `.git/info/exclude` with the Edit tool (shell appends to that file are denied) |
| Generated code | the repo's code generation (GraphQL codegen, Prisma generate, OpenAPI types) run after any schema change |
| Backend branch | checkout has the fields under test (`grep` the field in the schema) and every port in `launch.ports` is in the CORS allowlist |
| Test data | one read-only query proves a row matching the scenario exists; note ids. Every page named in the brief is opened in a real browser (pane or Playwright) and its FINAL URL + rendered content checked — HTTP 200 does not rule out a client-side redirect or a spinner that waits for params/login (2026-09-29: `/privacy`, `/claim`, `/rns` all passed the 200 smoke and blocked the verifier) |
| Selectors | every selector the brief names is proven against the LIVE page in this round: one `javascript_tool` call in the pane returning `document.querySelectorAll(sel).length` per selector (expected count noted in the brief). Never copy selectors from an older brief or script — a rebase or a shared-component change renames them silently (MUI radios lost `name=` between two rounds; the verifier burned 108 tool calls and reported a false "regression") |
| Smoke check | ONE manual request that reaches the scenario's entry point (page → 200, endpoint returns data). Paste command + result into the brief |
| Preflight script | the block below, run against the worktree once the server is up: every row PASS (or SKIP); its PASS/FAIL table is pasted verbatim into the brief. Any FAIL = no dispatch — I fix the env first and rerun it. It reads `.env` key names only and never prints a value; pass directories, never a `.env` path |

```bash
# tstack-core's scripts/ sits beside its bin/, which the enabled plugin puts on PATH (this skill lives in tstack-delivery)
PF=$(echo "$PATH" | tr ':' '\n' | while read -r d; do [ -f "$d/../scripts/verify-preflight.py" ] && { echo "$d/../scripts/verify-preflight.py"; break; }; done)
python3 "$PF" --web-dir "$WT" [--api-dir <api checkout> --web-origin http://localhost:$PORT] \
  [--page http://localhost:$PORT/<entry-page>]... [--probe "NAME|URL|must-contain|must-not-contain"]...
```
Rows: `env-keys` (`.env.example` keys missing from `.env`), `cors` (web origin in the api allowlist), `migrate` (`prisma migrate status` clean; `--skip migrate` for a repo without Prisma), `framework` (installed major = pinned major), `page` (200, final path = requested, real body), `probe` (a third-party auth or test-data id resolves).

Smoke or preflight fails → fix it myself, no dispatch. Anything the user must start (tunnel, ngrok) → ask first.

## 3. Brief (write to `~/.claude/handoffs/<task>/verify/BRIEF.md`, then dispatch)
- Proven facts: URLs, ports, ids, file paths, `envrun` invocation, the smoke command + its output.
- Scenarios: numbered, each with expected outcome. Non-overlapping scopes when running several agents.
- Reuse: paths of existing scripts from the previous round.
- Screenshots of dialogs/drawers/toasts (anything `position: fixed` or animated): **viewport** capture, never `fullPage`, taken ≥600 ms after the element appears. A fullPage shot unrolls the fixed layer and catches MUI's fade mid-way — the image proves nothing. Say this in the brief whenever a scenario ends on a modal.
- Findings discipline (verbatim in the brief): "If something looks like a product defect, re-check it once with a plain interaction and report the exact locator + measurement. Do not call it a regression, and do not name a root cause in code you have not read."
- Stop budget: "at the first environment blocker (the smoke check failing on your side), stop and report — no env repair, no workarounds" + a tool-call cap the verifier counts itself: "after ~N tool calls (default 120), write REPORT.md with what you have and return, even if scenarios remain".
- Output: PNG screenshots in the verify dir captured with Playwright `channel: 'chrome'`, scripts and JSON saved there for the next round. The harness refuses report-file writes from subagents ("return findings as text"), so the verifier RETURNS the per-scenario table (PASS/FAIL/NOT TESTED + evidence file + measurement) as its message, and I write `REPORT.md` from it.
- Evidence rule (verbatim in the brief): "Evidence = PNG on disk from Playwright `channel: 'chrome'`; a scenario without a disk PNG is NOT TESTED. The Browser pane may be used to find selectors only, ≤10 calls, then back to the script." (pane-driven rounds cost 163 turns / 21.9M cache-read for zero evidence; the script rerun cost 31 turns.)

Dispatch: `Agent` with `model: opus` (the routing rule: verify has an adaptive loop), `run_in_background: true`, `subagent_type: general-purpose`. Sonnet only for a rerun of a script that already passed.

## 4. On return
1. Read the returned table in full and write it to `REPORT.md` (with my own DB checks); check it against the JSON/PNG evidence on disk, not the summary line.
2. Every FAIL and every "defect" the agent reports is reproduced by me (pane or a 5-line script) before it reaches the user; count PNGs against scenarios — a PASS without a PNG is NOT TESTED, say so.
3. `SendUserFile` every PNG with a caption mapping image → scenario.
4. Report to the user per scenario: PASS / FAIL / NOT TESTED, with the reason for anything not PASS.
5. Environment failure in the report is my defect: fix it, then continue the SAME agent via `SendMessage` only if the next step needs context not yet on disk; a rerun of an existing `verify.mjs` goes to a NEW agent (brief + script path) — a resumed agent carries every earlier round's context into each turn.
6. Note turns / minutes / outcome in the reply and write the dispatch-ledger row (outcome judged against the evidence on disk, not the agent's summary).

Done when every scenario has a status and its evidence file, and the user has the screenshots.
