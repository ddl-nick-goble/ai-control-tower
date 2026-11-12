#!/usr/bin/env bash
# Session preflight for the agent-team build. Prints PASS/FAIL/WARN lines.
set -u
fail=0
restart=0
REPO_URL="https://github.com/dominodatalab/domino-ai-plugin"
PLUGIN="dominodatalab@domino-marketplace"
QA_ROOT="$(python3 -c 'import json;p=json.load(open("/mnt/code/build/budget.json"))["cost_log"];import os;print(os.path.dirname(p))' 2>/dev/null)"

# 0. Real container limits (free/nproc show the host, not the workspace)
cpu_q=$(awk '{if($1=="max")print 0; else printf "%d", $1/$2}' /sys/fs/cgroup/cpu.max 2>/dev/null)
mem_b=$(cat /sys/fs/cgroup/memory.max 2>/dev/null)
case "$mem_b" in ""|max) mem_g=0 ;; *) mem_g=$((mem_b/1024/1024/1024)) ;; esac
echo "INFO workspace limits: ${cpu_q:-?} cores, ${mem_g} GiB (browser suites at once: $([ "${mem_g:-0}" -ge 15 ] && echo 2 || echo 1); xdist workers: -n $([ "${cpu_q:-1}" -ge 4 ] && echo $((cpu_q-1)) || echo 0))"
if [ "${mem_g:-0}" -gt 0 ] && [ "$mem_g" -lt 8 ]; then
  echo "WARN workspace has ${mem_g} GiB / ${cpu_q} core(s): run ONE browser suite at a time, never pytest -n auto, at most 2 subagents. Restart the workspace on Large (6 cores, 22 GiB) for parallel runs."
fi

# 1. Domino plugin at the newest release
latest=$(git ls-remote --tags "$REPO_URL" 'release-*' 2>/dev/null \
  | sed -n 's#.*refs/tags/release-\([0-9.]*\)$#\1#p' | sort -V | tail -1)
# Look in both the plugin cache and local-directory marketplaces.
installed=$(find "$HOME/.claude/plugins" "$HOME/.claude/marketplaces" \
  \( -path '*dominodatalab*' -o -path '*domino*' \) -path '*.claude-plugin*' \
  -name plugin.json 2>/dev/null | xargs -r -n1 python3 -c \
  'import json,sys;print(json.load(open(sys.argv[1])).get("version",""))' 2>/dev/null \
  | grep -v '^$' | sort -V | tail -1)
# A marketplace registered as a local directory in settings cannot be re-added or
# updated from GitHub; report its version instead of trying.
local_mkt=$(python3 -c '
import json,os
for f in ("~/.claude/settings.json","~/.claude/settings.local.json","/etc/claude-code/managed-settings.json"):
    try: d=json.load(open(os.path.expanduser(f)))
    except Exception: continue
    s=(d.get("extraKnownMarketplaces") or {}).get("domino-marketplace",{}).get("source",{})
    if s.get("source")=="directory": print(s.get("path","")); break
' 2>/dev/null)
if [ -n "$local_mkt" ]; then
  if [ -n "$installed" ]; then
    echo "PASS plugin $installed installed from local marketplace $local_mkt (not auto-updated)"
    [ -n "$latest" ] && [ "$(printf '%s\n%s\n' "$installed" "$latest" | sort -V | tail -1)" != "$installed" ] \
      && echo "WARN newer plugin release $latest exists; refresh $local_mkt to update"
  else
    echo "FAIL domino-marketplace is a local directory ($local_mkt) but no Domino plugin is installed from it"; fail=1
  fi
elif [ -z "$installed" ]; then
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

# 2b. Test plugins must come from the environment image, not a per-session ~/.local install
loc=$(python3 -c 'import xdist,os;print(os.path.dirname(xdist.__file__))' 2>/dev/null)
if [ -z "$loc" ]; then
  echo "FAIL pytest-xdist missing (parallel e2e). Add to the environment Dockerfile: RUN pip install --no-cache-dir pytest-xdist"; fail=1
elif [ "${loc#$HOME}" != "$loc" ]; then
  echo "WARN pytest-xdist only in $loc (lost on workspace stop); bake it into the environment image"
else
  echo "PASS pytest-xdist (in image)"
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
