"""Reconciliation tools. propose_adjustment only proposes; nothing is executed (ACT is evidence)."""
import json
from datetime import date
from pathlib import Path

DATA = Path(__file__).parent / "data"


def fetch_ledger(name: str) -> list:
    p = DATA / f"{Path(str(name)).stem}.json"
    if not p.is_file():
        raise ValueError(f"unknown ledger {name!r}; available: {sorted(x.stem for x in DATA.glob('*.json'))}")
    return json.loads(p.read_text())


def _as_ledger(x):
    return fetch_ledger(x) if isinstance(x, str) else x


def compare_entries(ledger_a, ledger_b) -> list:
    """Return breaks keyed by ref: amount_mismatch, timing, missing_a / missing_b."""
    a = {e["ref"]: e for e in _as_ledger(ledger_a)}
    b = {e["ref"]: e for e in _as_ledger(ledger_b)}
    breaks = []
    for ref in sorted(set(a) | set(b)):
        ea, eb = a.get(ref), b.get(ref)
        br = {"ref": ref, "amount_diff": 0.0, "days_diff": 0, "missing_side": 0}
        if ea is None or eb is None:
            br.update(missing_side=1, kind="missing_entry", missing_in="a" if ea is None else "b",
                      amount=(ea or eb)["amount"])
        else:
            ad = round(abs(ea["amount"] - eb["amount"]), 2)
            dd = abs((date.fromisoformat(ea["date"]) - date.fromisoformat(eb["date"])).days)
            if not ad and not dd:
                continue
            br.update(amount_diff=ad, days_diff=dd, amount=ea["amount"],
                      kind="amount_mismatch" if ad else "timing")
        breaks.append(br)
    for i, br in enumerate(breaks, 1):
        br["break_id"] = str(i)
    return breaks


def propose_adjustment(break_id, reason, amount=None, ref=None, debit_account="SUSPENSE",
                       credit_account="CASH") -> dict:
    """Proposed journal entry (proposal only)."""
    return {"status": "proposed", "executed": False, "break_id": str(break_id), "ref": ref,
            "reason": reason, "debit_account": debit_account, "credit_account": credit_account,
            "amount": amount}
