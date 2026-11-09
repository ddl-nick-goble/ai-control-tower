#!/usr/bin/env python3
"""PreToolUse path guard.
Usage: guard_paths.py [--worktree-only] --allow|--deny PREFIX [PREFIX ...]
Relative prefixes apply inside whichever checkout the file is in: /mnt/code or
/mnt/code/.worktrees/<task-id>. Absolute prefixes match as given.
--worktree-only blocks writes to the main checkout."""
import json
import os
import sys

REPO = "/mnt/code"
WORKTREES = REPO + "/.worktrees/"

args = sys.argv[1:]
worktree_only = "--worktree-only" in args
args = [a for a in args if a != "--worktree-only"]
mode, prefixes = args[0], args[1:]

path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path", "")
if not path:
    sys.exit(0)
absolute = os.path.abspath(os.path.join(REPO, path))
in_repo = absolute == REPO or absolute.startswith(REPO + "/")
if absolute.startswith(WORKTREES):
    root = WORKTREES + absolute[len(WORKTREES):].split("/", 1)[0]
else:
    root = REPO
relative = os.path.relpath(absolute, root)


def block(reason):
    print(f"Blocked: {reason} Report the change you need to the orchestrator instead.", file=sys.stderr)
    sys.exit(2)


def under(prefix):
    prefix = prefix.rstrip("/")
    if os.path.isabs(prefix):
        return absolute == prefix or absolute.startswith(prefix + "/")
    return in_repo and (relative == prefix or relative.startswith(prefix + "/"))


if worktree_only and in_repo and root == REPO:
    block(f"{absolute} is in the main checkout; work only in your task worktree.")
hit = any(under(p) for p in prefixes)
if (mode == "--deny" and hit) or (mode == "--allow" and not hit):
    block(f"this agent may not write {relative if in_repo else absolute}.")
