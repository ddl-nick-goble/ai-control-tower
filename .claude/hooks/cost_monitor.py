#!/usr/bin/env python3
"""Build cost monitor. Modes: statusline, guard, log <phase>."""
import csv
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path("/mnt/code")
LEDGER = ROOT / ".claude" / "cost-ledger.json"
BUDGET = ROOT / "build" / "budget.json"


def load(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def spent():
    return sum(load(LEDGER, {}).values())


def budget():
    b = load(BUDGET, {})
    return float(b.get("budget_usd", 0)), float(b.get("warn_at", 0.8)), b.get("cost_log", "")


def statusline():
    data = json.load(sys.stdin)
    sid = data.get("session_id", "unknown")
    session_cost = float((data.get("cost") or {}).get("total_cost_usd") or 0)
    ledger = load(LEDGER, {})
    ledger[sid] = max(session_cost, ledger.get(sid, 0))
    LEDGER.write_text(json.dumps(ledger))
    total = sum(ledger.values())
    cap, warn, _ = budget()
    flag = ""
    if cap and total >= cap:
        flag = " OVER BUDGET"
    elif cap and total >= warn * cap:
        flag = " near budget"
    model = (data.get("model") or {}).get("display_name", "")
    ctx = (data.get("context_window") or {}).get("used_percentage", 0)
    cap_txt = f" of ${cap:.0f}" if cap else ""
    print(f"[{model}] build ${total:.2f}{cap_txt}{flag} | session ${session_cost:.2f} | ctx {ctx}%")


def guard():
    sys.stdin.read()
    cap, _, _ = budget()
    total = spent()
    if cap and total >= cap:
        print(
            f"Build budget of ${cap:.0f} reached (estimated ${total:.2f}). Start no more subagents. "
            "Write build/STATUS.md with what is done and what remains, then ask the user "
            "whether to raise budget_usd in build/budget.json.",
            file=sys.stderr,
        )
        sys.exit(2)


def log(phase):
    _, _, path = budget()
    total = spent()
    if path:
        p = pathlib.Path(path)
        new = not p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "phase", "cumulative_usd"])
            w.writerow([datetime.datetime.now().isoformat(timespec="seconds"), phase, f"{total:.2f}"])
    print(f"{phase}: cumulative ${total:.2f}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "statusline"
    if mode == "guard":
        guard()
    elif mode == "log":
        log(sys.argv[2] if len(sys.argv) > 2 else "phase")
    else:
        statusline()
