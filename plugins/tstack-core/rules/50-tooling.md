# Tooling gotchas

Behaviours of the hooks and tools in this plugin that are easy to misread.

- Secrets are gated by the secret read gate hook (`.env*`, `*.pem`, `*.key`, ssh keys; bare `env` / `printenv`). The gate denies the read and points at a `<masked:N>` copy; that copy is what the assistant reads, never an indirect read of the original. To use a value unseen: `envrun <dir> -- sh -c '…"$KEY"…'` (on the shell PATH from the plugin's `bin/`): it loads `<dir>/.env` into the child only and rewrites every loaded value in the output to `<masked:KEY>`; `NEXT_PUBLIC_*` values are never masked; values are not sliced or transformed in the child. A raw value showing anywhere in a masked copy is a gate bug: it is not repeated, the user is told, and the gate's `--selftest` is run. A self-test warning at session start is reported before any secret file is touched.
- `envrun` runs the child in the environment directory, not the caller's working directory: `cd <worktree>` goes inside the `sh -c`, and scripts get absolute paths.
- Dev servers are user-run. The dev-server gate denies `pnpm|npm|yarn|bun dev` / `start:dev` / `next dev` when the port already listens and asks otherwise; servers start through named launch entries, never ad hoc.
- The git guard denies `git push --force*` and `--no-verify`, and asks on any push to `main` or `master`.
- Large shell output through context-trimming tools can be silently truncated (wrong grep matches, empty `git branch -r | grep`). Working-tree files are read with the file tool or `| head -N`; refs with `git ls-remote origin <ref>`; a verified-complete fetch of a file at a ref goes through the hosting API and is checked with `wc -l`; a remote repository's tree is listed through the API, never `git ls-tree` on a sparse clone.
- A new agent definition in the agents directory loads at the next user turn, not mid-turn: "Agent type not found" right after creating one means end the turn and retry.
- HTTP smoke checks inside gated shells use the system Python's urllib or node, saved as a script in the task's verify directory; this is stated in every subagent brief.
