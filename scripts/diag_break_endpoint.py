"""Diagnostic: POST variants to the break_classifier endpoint; print only status + body (never the key)."""
import itertools, os, sys
import requests

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
URL = os.environ.get("BREAK_CLASSIFIER_ENDPOINT_URL") or (ARGS[0] if ARGS else None)
if not URL:
    sys.exit("error: endpoint URL required: set BREAK_CLASSIFIER_ENDPOINT_URL or pass it as an argument")
KEY = os.environ["BREAK_CLASSIFIER_ACCESS_KEY"]


def call(data):
    r = requests.post(URL, json={"data": data}, auth=(KEY, KEY), timeout=60)
    return r.status_code, r.text[:600]


cases = [
    {"amount_diff": 12.5, "days_diff": 2, "missing_side": 0},
    {"amount_diff": 1200.0, "days_diff": 0, "missing_side": 0},
    {"amount_diff": 12, "days_diff": 2, "missing_side": 0},
    {"amount_diff": 12.5, "days_diff": 2.0, "missing_side": 0.0},
    {"amount_diff": 12.5, "days_diff": 2, "missing_side": 1},
]
if "--sweep" in sys.argv:
    vals = [(5, "int"), (5.5, "float")]
    for (a, an), (d, dn), (m, mn) in itertools.product(vals, vals, [(0, "int"), (1.0, "float")]):
        cases.append({"amount_diff": a, "days_diff": d, "missing_side": m})
for c in cases:
    print(c, "->", *call(c))
