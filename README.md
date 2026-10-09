# tstack-workflow

A gated, human-in-the-loop workflow for Claude Code, packaged as two plugins.
Every phase of a task waits for a decision from you; hooks enforce the parts that must never slip
(no edit without a go-word, no secret values in the transcript, no force-push); the rest is a small
set of written rules that load at session start.

The full description of the flow, the gates, the sidecars and the weekly retro is in
[docs/working-with-claude-code.html](docs/working-with-claude-code.html).

## Install

```bash
claude plugin marketplace add totofk/tstack-workflow
claude plugin install tstack-core@tstack-workflow
claude plugin install tstack-delivery@tstack-workflow   # optional, see below
```

Private repository: the `marketplace add` runs `git` with your own credentials. Either an SSH key
loaded in `ssh-agent` with `github.com` in `known_hosts`, or `gh auth login` followed by
`gh auth setup-git`. Hooks load at the next session (or `/reload-plugins`).

Updates are not automatic for third-party marketplaces:

```bash
claude plugin marketplace update tstack-workflow
claude plugin update tstack-core@tstack-workflow
claude plugin update tstack-delivery@tstack-workflow
```

No `version` is set on purpose, so an update tracks the latest commit.

## What you get

### tstack-core

| Piece | Kind | What it does |
|---|---|---|
| Edit gate | PreToolUse hook on Edit / Write / NotebookEdit | Blocks edits to files inside a git work tree (not gitignored) unless your current message contains a go-word, you said `arm session`, or you approved a plan. Scratch, memory and generated files are free. |
| Secret read gate | PreToolUse hook on Read / Grep / Bash | Denies reads of `.env*`, key files, ssh keys and bare `env` / `printenv`; offers a copy with every value masked. Fails closed. Self-test at session start. |
| Git guard | PreToolUse hook on Bash | Denies `git push --force*` and `--no-verify`; asks before any push to `main` / `master`. |
| Dev-server gate | PreToolUse hook on Bash | Denies `pnpm|npm|yarn|bun dev`, `start:dev`, `next dev` when the port already listens; asks otherwise. Servers start from named launch entries. |
| Prompt hooks | UserPromptSubmit | Go-word check (arms the edit gate for one round), a git-state line after a gap of 10 minutes or more, a terse-reply rule matched to the language of your message (Thai or English). |
| Plan arm | PostToolUse on ExitPlanMode | An approved plan arms the edit gate for one round. |
| Rules | SessionStart | Five short rule files load as context: editing and approval, verify → commit → PR, orchestrator and subagents, memory and sessions, tooling gotchas. See `plugins/tstack-core/rules/`. |
| `envrun` | `bin/` | Runs a command with a `.env` loaded into the child only and every loaded value masked in the output: `envrun <dir> -- sh -c '…"$KEY"…'`. |

Your own go-word list: copy `plugins/tstack-core/scripts/edit-gate-words.txt` to
`$CLAUDE_PLUGIN_DATA/edit-gate-words.txt` (the hook prints the path in its block message) and edit it.
Latin words match whole-word, case-insensitive; Thai matches as a substring; `re:` lines are regexes.

### tstack-delivery

Three skills that run the verify → review → deliver part of the flow. Team-specific values are read
from the repository's `.claude/workflow.json`; without the file the defaults apply.

| Skill | Invoked by | What it does |
|---|---|---|
| `verify` | you say "verify" | Prepares a private worktree on a free port, proves the served code is the code under test, writes a brief, dispatches a verifier subagent that leaves PNG evidence and reusable scripts, then reports per scenario. |
| `review-before-deliver` | you or `deliver-finished-task` | Reviews the diff against the repository's own rules, auto-applies cheap correctness fixes, reports, never touches git. |
| `deliver-finished-task` | you (`/tstack-delivery:deliver-finished-task`) | Review → commit → push → PR in one go. Stops only when blocked; asks one batched question for values it cannot derive. |

`workflow-config` (on the shell PATH while the plugin is enabled) prints the merged config;
`workflow-config title.feature` prints one value. Copy [workflow.example.json](workflow.example.json)
to `<repo>/.claude/workflow.json` and change what differs:

| Key | Default | Meaning |
|---|---|---|
| `base` | `develop` | Base branch for feature work |
| `protected` | `main, master, develop` | Branches that take no direct push without explicit permission |
| `assignee` | `@me` | PR assignee passed to `gh` |
| `title.feature` / `bugfix` / `hotfix` | conventional | PR title patterns; `{summary}` = branch slug with spaces |
| `acceptance` | `brief` | Where acceptance criteria come from (`brief`, or a name your team adapter understands) |
| `launch.entry` / `launch.ports` | `wt-{port}` / `[3001, 3002]` | Launch entry name template and the ports it serves |
| `worktree` | `.worktrees/wt-{port}` | Worktree path template |
| `reviewer` | none | Name of the repository's own reviewer skill, if any |

## Requirements

- Claude Code 2.1 or newer (plugins, hooks, skills).
- `python3` for the hooks, `git`, `gh` (logged in) for delivery, `lsof` for the port checks.
- For `verify`: Playwright with `channel: 'chrome'` in the repository, and one launch entry per port in `launch.ports`.

## What a plugin cannot do for you

- Install other plugins, change `permissions` in `settings.json`, add launch entries, create scheduled tasks or claude.ai connectors. The README is the checklist for those.
- Load a `CLAUDE.md`: rules travel as SessionStart output instead, one file per hook entry (10,000-character cap per entry).

## Team adapters

Everything here is generic. A team keeps its own conventions (branch policy, PR naming from a ticket
system, who to assign, which review bots respond) in a separate plugin that installs on top of this
one and in each repository's `.claude/workflow.json`.

## Layout

```
.claude-plugin/marketplace.json
plugins/tstack-core/        hooks/hooks.json, scripts/, rules/, bin/envrun
plugins/tstack-delivery/    skills/{verify,review-before-deliver,deliver-finished-task}, bin/workflow-config
workflow.example.json
docs/working-with-claude-code.html
```
