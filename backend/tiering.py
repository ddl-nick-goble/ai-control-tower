"""Tier scorer: weighted consequence criteria -> Tier 1 (highest) to 4."""
import pathlib
from dataclasses import dataclass

import yaml

CONFIG_PATH = pathlib.Path(__file__).resolve().parent.parent / "config" / "tiering.yaml"


class TieringInputRejected(ValueError):
    """Raised for inputs that must not drive a tier (backtest/benchmark)."""


@dataclass
class TierResult:
    tier: int
    score: float
    reasoning: str


def load_config(path=None):
    return yaml.safe_load(pathlib.Path(path or CONFIG_PATH).read_text())


def score_tier(criteria, config=None):
    cfg = config or load_config()
    rule = cfg.get("no_benchmark_rule", {})
    for key in criteria:
        if any(b in key.lower() for b in rule.get("rejected_keys", ["backtest", "benchmark"])):
            raise TieringInputRejected(f"{rule.get('message', 'Backtest/benchmark inputs are not tier inputs')} (input: {key})")
    spec = cfg["criteria"]
    unknown = [k for k in criteria if k not in spec]
    if unknown:
        raise TieringInputRejected(f"Unknown tier criteria: {', '.join(sorted(unknown))}")
    missing = [k for k in spec if k not in criteria]
    if missing:
        raise ValueError(f"Missing criteria: {', '.join(missing)}")
    lo, hi = (cfg.get("score_range") or {}).get("min", 1), (cfg.get("score_range") or {}).get("max", 5)
    total_w = sum(float(v["weight"]) for v in spec.values())
    parts, acc = [], 0.0
    for k, v in spec.items():
        s = float(criteria[k])
        if not lo <= s <= hi:
            raise ValueError(f"Criterion {k}={s} outside {lo}-{hi}")
        acc += float(v["weight"]) * s
        parts.append(f"{k}={s:g}x{float(v['weight']):g}")
    score = round(acc / total_w, 6)
    tier = 4
    for t in sorted(cfg["tiers"], key=lambda t: -t["min_score"]):
        if score >= t["min_score"]:
            tier = int(t["tier"])
            break
    reasoning = f"Weighted score {score:.2f} (" + ", ".join(parts) + f") maps to Tier {tier}."
    return TierResult(tier=tier, score=score, reasoning=reasoning)
