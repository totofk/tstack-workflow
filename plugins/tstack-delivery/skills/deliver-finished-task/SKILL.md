---
name: deliver-finished-task
description: Finishes the work in one command — review (review-before-deliver), commit, push, PR (or push + merge forward when the branch already has a PR). The invocation is the go for the whole chain; ask only for values that cannot be derived, stop only when blocked. Use for every delivery round, first or follow-up, when the user says "deliver finished task", "ส่งงาน", "เปิด PR", or wants commit+PR from local changes.
disable-model-invocation: true
---

# Deliver Finished Task

## Purpose

One command that finishes the work: review → commit → push → PR, or push + merge forward when the branch already has a PR. Every delivery round runs it — first delivery and follow-ups (QA defect rounds, bot/feedback fixes, requirement tweaks) alike; the existing-PR fast path below is what makes a follow-up cheap.

**One gate.** The user invoking this skill (or one go-word after the verify-end diff) is the go for the whole chain. Never re-ask before commit, push, or PR. Stop only when blocked: a blocking review finding, lint/tsc red, stash or merge conflict, a push that would need force, or no verify yet (say so; `skip verify` in the same message overrides). Unknown values (base, branch name, any placeholder the configured PR title pattern needs that cannot be derived) go into ONE batched question, then continue without asking again.

**Config.** `workflow-config` prints the repo's delivery values merged over defaults: `base`, `protected`, `assignee`, `title.{feature|bugfix|hotfix}`, `acceptance`. A team adapter plugin may add rules on top (title placeholders from a ticket, paired PRs, extra protected branches); those rules win on conflict.

**Precondition:** verify has passed on this exact code (or the user said `skip verify`). A review fix applied here voids that verify — say so and ask once: re-verify, or ship with the fix noted in the PR body.

## Required Defaults

- Base branch: settled only by an existing PR's base, the branch's upstream, the plan/handoff, or the user's message. The branch prefix alone never settles it (a `features/*` branch can legitimately target a release branch) — anything short of settled goes into the single batched question with the candidates listed (`workflow-config base` / `main` / the team adapter's release branches), never a silent default.

## Branch Naming Rules

- Derive the branch prefix from the PR base branch:
  - base `main` → `hotfix/<short-description>`
  - any other base → `features/<short-description>`
- A branch that already exists, or a name the user gave, is final. Otherwise propose one in the single batched question (`PR base is "main" — branch "hotfix/fix-payment-timeout"?`).

## Existing PR (follow-up fast path)

`gh pr list --head <branch> --state open` non-empty → this is a follow-up round: commit → push → merge forward into any paired branch(es) the team adapter defines (twin PRs) → push those → report the PR URLs. No new PR, no naming question. Re-read `gh pr view <n> --json mergeable` after the push.

## Already-committed work (fast path)

If the tree is clean and the work already sits as commits on a branch cut from `origin/<base>` (the usual state after a verify → commit flow in a worktree): steps 3–9 are no-ops — say so, do NOT stash or re-branch. Run step 1 (review range = `origin/<base>..HEAD`), step 2 (confirm base + the existing branch name), then 10–12. Moving a branch to another base = a NEW branch name + `git rebase --onto origin/<new-base> <old-merge-base>`, never a rewrite of the pushed branch.

## PR rules (a team adapter's rules win on any conflict)

- State `source → target` in one line before every push / PR (information, not a question).
- Title: pick the kind from the branch prefix (`hotfix/*` → `hotfix`, `fix/*`, `bugfix/*` → `bugfix`, else `feature`) and fill `workflow-config title.<kind>`. Defaults are conventional (`{type}: {summary}`, `fix: {summary}`, `hotfix: {summary}`); `{summary}` is the branch slug with dashes as spaces unless the user gave a title. A placeholder the pattern needs that cannot be derived (a sprint tag, a ticket id) goes into the single batched question — never guessed.
- Right after opening a PR read `gh pr view <n> --json mergeable`. `CONFLICTING` (possible even when a local `git merge-tree` exits 0 — criss-cross merge bases) → never resolve on GitHub: cut `<branch>-<base>` = branch + `git merge origin/<base>`, open a NEW PR from it, close the old one with a pointer comment.
- Paired / dependent PRs declare the merge dependency in every PR body (order-dependent vs safe together; what QA must wait for).
- The first line of `## Context` is the ticket URL verbatim (the ticket `workflow-config acceptance` names — the same one the title placeholders were read from); no ticket → the literal line `No ticket`. Never omit the line: routines parse it.
- Assign with `gh pr edit <n> --add-assignee <workflow-config assignee>` after every `gh pr create` (default `@me`). No AI attribution in commits or PR bodies.
- Pushes to any branch in `workflow-config protected` happen only on explicit per-instance permission in the conversation (the git guard hook also asks on `main`/`master`).
- Shared trees: never bare `git stash` — if steps 3–7 are really needed, `git stash push -u -m "<unique-tag>"`, apply by SHA, or use a temporary `git worktree`.

## Workflow

1. Validate workspace and review scope:
   - Main-tree guard first: `git rev-parse --git-common-dir` printing `.git` means the cwd is the main
     working tree, which the user's GUI git client may switch at will — stop, do not stage or commit
     here; move the work to a worktree (`git worktree add <workflow-config worktree> <branch>` + `EnterWorktree path=`)
     and continue there. `.git/worktrees/<name>` means you are already isolated.
   - `git status`
   - `git diff --staged --stat`
   - `git diff --stat`
   - Run the `review-before-deliver` skill (model-invocable; review-only — never commits/pushes/PRs; this skill owns delivery) unless it already ran this session on the same code — then cite that verdict. Blocking finding → stop and surface it. Cheap fixes it applied → the verify is void: ask once (re-verify / ship with the fix noted), then continue.
2. Resolve delivery parameters (base, branch name, PR base, PR title) by derivation; anything still unknown goes into ONE batched question. Existing PR → take the follow-up fast path and skip to step 8.
3. Stash current changes (include tracked and untracked if needed).
4. Checkout the base branch.
5. Pull latest base branch safely.
6. Create new feature branch from the base branch.
7. Re-apply stash and resolve conflicts.
8. Stage files intentionally.
9. Commit:
   - split commits when there are distinct concerns
   - otherwise create one focused commit
10. Push branch with upstream.
11. Create PR with the body from "PR Body Template" below (skip when the follow-up fast path applies — merge forward instead).
    - Assign the PR: pass `--assignee <workflow-config assignee>` to `gh pr create` (default `@me`).
12. Report, in one message, without asking anything:
   - branch, commit(s), PR URL(s) / merge-forward result
   - review verdict cited, verify status
   - any skipped/split rationale

## Commit Rules

- Use conventional commits.
- Keep each commit scoped to one logical concern.
- Never include unrelated noise.
- Before splitting, `git diff` each file to classify by its ACTUAL change, not by filename/path — a file's path may not match the concern it belongs to (e.g. a `PartnerInsuranceSearch` edit that only swaps in a travel form belongs to the travel commit).
- The working tree may carry unrelated work from other sessions or already staged on the branch. Group by concern regardless of who staged what, and stage per-file (`git add <specific files>`) for each commit — never `git add .`.
- Write commit messages and PR title/body in English (external artifacts are always English, regardless of conversation language).

## PR Body Template

Audience: dev reviewers, now and whoever reads the PR years later. Sections 3 and 4 are mandatory, never trimmed — they convince the reviewer and are the permanent record of why and how. Summary visuals adapted from Dex Horthy's `show-me` via mattpocock/skills `pr`.

```markdown
## Context
<ticket URL verbatim, alone on the first line — or the literal `No ticket`>
<1–2 lines: what was wrong or missing; bugfix → root cause here>

## Summary
<the smallest visual that makes the change clear: diff sketch of a call tree / component tree / state flow, or a shallow file tree; Mermaid only for cross-service flow>
- <≤3 bullets: what changed in behaviour, named by code symbol or user-visible result>

## Decisions
- <who decided what, when (PO / UX / dev), and why this direction over the alternative>
- Out of scope: <items + why>
- Open: <questions handed back, if any>

## Verification record
Automated: <tsc / vitest / lint results with counts>
Manual: <environment, tool, scenario list with results — the coverage matrix>
Before / after: <screenshots or output from the verify round, paired per scenario where the change is visual>
NOT TESTED: <paths not exercised and why>

## Merge Danger
**Door:** <one-way | two-way> — <what rollback is: revert / migration / data>
**Blast radius:** <consumers of the changed code, with the count and its source (grep / graphify)>
<paired PR + merge order or "safe in either order", when applicable>
```

- No standalone "Files" section — GitHub shows the file list.
- Keep every Decisions / Verification entry even when it feels redundant with the ticket: the ticket can move or be edited, the PR body cannot.

## Safety Rules

- Never force push unless user explicitly asks.
- Never amend unless user explicitly asks.
- If stash apply conflicts, stop and resolve before commit.
