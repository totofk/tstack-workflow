---
name: review-before-deliver
description: >-
  Code review for the current repo's diff before delivery — never commits, pushes, or opens a PR, but DOES auto-apply low-cost correctness fixes to the working tree. Detects the repo, reuses that repo's own review rules (its reviewer skill named by `reviewer` in .claude/workflow.json, or its CLAUDE.md + framework best-practices), applies cheap+high-confidence fixes, reports everything, and stops. Use before delivering finished work, or as the review gate inside deliver-finished-task. Triggers: "review before deliver", "review only", "review my diff", "check before I push". Run it at apply-end (after lint/tsc, BEFORE verify) so findings can still be fixed without voiding a verify.
---

# Review Before Deliver

## Purpose

Review the current branch's diff so the user can decide whether to deliver. This skill NEVER stages, commits, pushes, or opens a PR — delivery is owned by `deliver-finished-task` (first delivery of a card) or the direct follow-up flow (commit → push → merge forward → PR) in the user's CLAUDE.md. It runs on a first delivery and on heavy follow-ups (whole-flow change, wide impact, regression of ≥ half the feature); light follow-ups skip it. It MAY edit the working tree to auto-apply low-cost correctness fixes (see Auto-fix Gate); those edits are left uncommitted for the delivery step to pick up.

The review rules are never hard-coded here. They are always sourced from the repo you are standing in, so this skill stays correct as each repo's conventions evolve.

## Workflow

1. Inspect the diff:
   - `git status --short`
   - `git diff --stat`
   - `git diff`
   - Clean tree but the branch has commits ahead of its base (work already committed): review `<base>..HEAD` instead — say which range you reviewed.
   - Neither uncommitted changes nor commits ahead of base → say so and stop.
   - Large diffs piped through Bash get silently truncated here (`git diff … | grep` returned 185 of ~1,500 lines once). Run checklist greps on the working-tree files and check the line count against `git diff --stat` before trusting an all-zero result.

2. Pick the rule source for THIS repo (decision = does the file exist):
   - `workflow-config reviewer` names a skill (e.g. `code-reviewer`) → **if `<repo>/.claude/skills/<that name>/SKILL.md` exists → Path A.**
   - No `reviewer` configured → look for exactly one `<repo>/.claude/skills/*reviewer*/SKILL.md`; found → Path A.
   - **Otherwise → Path B.**

3. **Path A — repo has its own reviewer.** Do NOT invoke that skill (it commits/pushes/PRs). Instead, read it as the rule source:
   - Read its `SKILL.md` — use its review checklist, conventions, and any best-practice skills it references (e.g. `vercel-react-best-practices`, `nestjs-best-practices`).
   - Read its `references/*` if present (conventions, typescript rules, etc.). These vary per repo and may be absent — skip silently if missing.
   - Review the diff against those rules.

4. **Path B — no reviewer in this repo.** Source the rules live from the repo itself:
   - Read `<repo>/CLAUDE.md` (and the monorepo root `CLAUDE.md`) for that repo's conventions — this is the primary criterion.
   - Read `package.json` to identify the framework (Next.js / NestJS / Bun-Hono / etc.) and apply the matching central best-practice skill if one fits (`vercel-react-best-practices` for Next, `nestjs-best-practices` for Nest).
   - Review the diff against those.

5. Apply auto-fixes that pass the Auto-fix Gate (below), then produce the report and **stop**. Never touch git (working-tree edits only — no stage/commit/push).

## Severity & Gate

- **Critical / blocking** (data loss, security, broken build, convention violation that fails review): surface prominently and tell the user to fix before delivering.
- **Minor / suggestion**: list, but do not block.

## Auto-fix Gate

Within this skill (review-before-deliver only), the general "confirm before editing" rule is **suspended for fixes that clear this gate** — apply them directly to the working tree without asking, then report them under "Applied fixes". This is a scoped override; outside this skill, the normal confirm-before-edit workflow still applies.

Two limits on that override:
- The edit-gate hook is mechanical and does not read this file. If an auto-fix is blocked, do not retry or route around it — list the fix under "Blocking"/"Suggestions" with the ready diff and let the user arm the gate.
- If a verify pass already ran on this code, do NOT auto-fix anything: any edit voids the verify. Report the finding with its cost ("fixing this means re-running the verify matrix") and let the user choose.

Rule-source conflicts: when the repo's reviewer skill disagrees with the repo's `CLAUDE.md` (stale i18n paths, `Co-Authored-By` instructions), `CLAUDE.md` and the user's global rules win — note the stale rule in the report.

**Auto-apply a finding only when ALL hold:**
- It came from THIS review (not a new idea you went looking for).
- Cost is low / very low: a few lines, no new dependency, no public API change.
- It buys correctness or clear impact (e.g. missing `encodeURIComponent`, null/undefined guard, off-by-one, a typo that breaks behavior).
- High confidence — not a "probably nicer" stylistic preference.

**Also always auto-fix (objective, not preference):**
- **Mode-prefix leaks in comments/strings** — a `ponytail:` / `caveman:` / `pordee:` prefix (or any assistant-mode marker) that ended up in a code comment or string. These are agent-mode artifacts and must never enter the codebase. Delete the prefix; keep the remaining explanation if it's useful, drop the whole comment if it only existed to narrate the mode.

**Still report-only — ask, do NOT auto-fix, even if cheap — when the fix:**
- Changes user-visible behavior (redirect target, copy, field order, timing).
- Deletes code or reverses existing logic.
- Touches a security / money / data-loss path.
- Reaches outside the current diff's scope.
- Is stylistic / debatable / preference.
- Is a **verbose / rambling comment** (multi-line where one line would do, narrates decision history, restates the code). Flag it under Suggestions with a tighter rewrite and let the user decide — repo comment policy (e.g. CLAUDE.md: short, explain WHY not WHAT) is the yardstick. Exception: a mode-prefix leak is objective — auto-fix that per the gate above.

After auto-fixing, re-check that the change still typechecks/lints if a quick check is available. This skill applies fixes and reports — it does not deliver. The user decides what to do with the rest.

## Output Format

```markdown
## Review Before Deliver: [repo]

Rule source: [Path A — repo reviewer | Path B — CLAUDE.md + <framework> best practices]

### Verdict
[✅ Ready to deliver | ⚠️ Fix blocking issues first]

### Applied fixes
- `file:line` — what was wrong → what I changed (auto-applied per Auto-fix Gate)

### Blocking
- `file:line` — issue → how to fix

### Suggestions
- `file:line` — note

### Notes
- [anything the user should know before delivery]
```

Omit the "Applied fixes" section when nothing was auto-fixed.

## Rules

- Never stage, commit, push, amend, or open a PR — leave auto-fixes uncommitted in the working tree for the delivery step (`deliver-finished-task` or the follow-up flow).
- Only auto-fix findings that clear the Auto-fix Gate; everything else is report-only.
- Never invoke a repo's reviewer skill — read it as rules, don't run it.
- Source rules from the repo, never from memory of what a repo "usually" uses.
- If the rule source is thin or missing, say so in Notes rather than inventing standards.
- Keep the report concise and decision-ready.
