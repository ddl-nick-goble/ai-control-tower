#!/usr/bin/env bash
# Session preflight for the agent-team build. Prints PASS/FAIL/WARN lines.
set -u
fail=0
restart=0
REPO_URL="https://github.com/dominodatalab/domino-ai-plugin"
PLUGIN="dominodatalab@domino-marketplace"
QA_ROOT="$(python3 -c 'import json;p=json.load(open("/mnt/code/build/budget.json"))["cost_log"];import os;print(os.path.dirname(p))' 2>/dev/null)"

# 1. Domino plugin at the newest release
latest=$(git ls-remote --tags "$REPO_URL" 'release-*' 2>/dev/null \
  | sed -n 's#.*refs/tags/release-\([0-9.]*\)$#\1#p' | sort -V | tail -1)
installed=$(find "$HOME/.claude/plugins" -path '*dominodatalab*' -path '*.claude-plugin*' \
  -name plugin.json 2>/dev/null | xargs -r -n1 python3 -c \
  'import json,sys;print(json.load(open(sys.argv[1])).get("version",""))' 2>/dev/null \
  | sort -V | tail -1)
if [ -z "$installed" ]; then
  claude plugin marketplace add dominodatalab/domino-ai-plugin \
    && claude plugin install "$PLUGIN" \
    && { echo "PASS plugin installed (${latest:-version unknown})"; restart=1; } \
    || { echo "FAIL plugin install failed"; fail=1; }
elif [ -n "$latest" ] && [ "$(printf '%s\n%s\n' "$installed" "$latest" | sort -V | tail -1)" != "$installed" ]; then
  claude plugin marketplace update domino-marketplace \
    && claude plugin update "$PLUGIN" \
    && { echo "PASS plugin updated $installed -> $latest"; restart=1; } \
    || { echo "FAIL plugin update failed"; fail=1; }
elif [ -z "$latest" ]; then
  echo "WARN could not reach GitHub; using installed plugin $installed"
else
  echo "PASS plugin $installed is current"
fi

# 2. Playwright and a headless browser
if python3 - <<'PY' >/dev/null 2>&1
import pytest_playwright  # noqa: F401
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch().close()
PY
then
  echo "PASS playwright + chromium"
else
  echo "FAIL playwright or chromium missing. Add to the compute environment Dockerfile:"
  echo "  USER root"
  echo "  RUN pip install --no-cache-dir playwright pytest-playwright"
  echo "  ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright"
  echo "  RUN playwright install --with-deps chromium && chmod -R a+rX /opt/ms-playwright"
  echo "  USER ubuntu"
  fail=1
fi

# 3. QA dataset folder is writable
if [ -n "$QA_ROOT" ] && mkdir -p "$QA_ROOT" 2>/dev/null && touch "$QA_ROOT/.write-test" 2>/dev/null; then
  rm -f "$QA_ROOT/.write-test"; echo "PASS QA folder $QA_ROOT"
else
  echo "FAIL QA folder not writable: ${QA_ROOT:-unset} (set cost_log in build/budget.json)"; fail=1
fi

# 4. Budget set
budget=$(python3 -c 'import json;print(json.load(open("/mnt/code/build/budget.json")).get("budget_usd",0))' 2>/dev/null)
case "$budget" in
  ""|0|0.0) echo "WARN budget_usd is 0 in build/budget.json; the budget guard is off" ;;
  *) echo "PASS budget \$$budget" ;;
esac

# 5. Git push access and ignored worktrees
if git -C /mnt/code push --dry-run origin HEAD >/dev/null 2>&1; then
  echo "PASS git push access"
else
  echo "FAIL cannot push to origin from /mnt/code"; fail=1
fi
if grep -qxF '.worktrees/' /mnt/code/.gitignore 2>/dev/null; then
  echo "PASS .worktrees/ is ignored"
else
  echo "FAIL add .worktrees/ to /mnt/code/.gitignore"; fail=1
fi

[ "$restart" = 1 ] && echo "RESTART plugin changed; run /exit and start claude again"
exit $fail
