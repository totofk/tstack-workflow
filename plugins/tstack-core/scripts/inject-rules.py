"""SessionStart hook: print the plugin's rules files so they load as session context.
A plugin's CLAUDE.md is not loaded by Claude Code; stdout of a SessionStart hook is.
One hook's stdout is one string capped at 10,000 characters, so hooks.json lists one
hook entry per rules file: `inject-rules.py <name.md>`. With no argument every file
is printed (dry runs only). Files are read from rules/ next to scripts/."""
import glob
import os
import sys

RULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rules")
CAP = 9500

if len(sys.argv) > 1:
    paths = [os.path.join(RULES_DIR, os.path.basename(sys.argv[1]))]
else:
    paths = sorted(glob.glob(os.path.join(RULES_DIR, "*.md")))

for path in paths:
    try:
        with open(path, encoding="utf-8") as f:
            body = f.read().strip()
    except OSError:
        continue
    if not body:
        continue
    name = os.path.basename(path)
    if len(body) > CAP:
        body = body[:CAP] + f"\n[truncated: {name} exceeds {CAP} characters; split the file]"
    sys.stdout.write(f"tstack-core rules ({name}):\n{body}\n\n")
