"""Characterization test for the backtest engine.

Pins the exact trade list `scripts/backtest.py` produces over a fixed slice
of real M5 data. This is NOT a correctness test -- it is a change detector.
If it fails, the replay's behaviour moved. Unless the task you are doing
explicitly changes replay behaviour, the change is a bug.

Regenerate deliberately (and only deliberately) with:
    cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py --regen-golden
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = pathlib.Path(__file__).parent / "data"
BARS = DATA / "bars_slice.json"
GOLDEN = DATA / "golden_trades.json"


def _load_bt():
    """Import scripts/backtest.py as a module. It is a script, not a package
    member, so it is loaded by path; importing it is side-effect free (the
    argparse/main call is guarded by __name__ == '__main__')."""
    spec = importlib.util.spec_from_file_location(
        "bt", ROOT / "scripts" / "backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _digest(trades):
    """Compact, human-diffable summary of a trade list."""
    return [{"dir": t["dir"],
             "entry": round(t["legs"][0]["px"], 2),
             "legs": len(t["legs"]),
             "exit": round(t["exit"], 2),
             "why": t["why"],
             "pl": round(t["pl"], 2)} for t in trades]


def _replay():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, max_dd, _valley = bt.run(candles, 4000.0, False)
    return _digest(trades), round(bal, 2), round(max_dd, 2)


def test_replay_matches_golden():
    digest, bal, max_dd = _replay()
    golden = json.loads(GOLDEN.read_text())
    assert len(digest) == len(golden["trades"]), (
        f"trade COUNT moved: {len(golden['trades'])} -> {len(digest)}")
    for i, (got, want) in enumerate(zip(digest, golden["trades"])):
        assert got == want, f"trade {i} changed:\n  was {want}\n  now {got}"
    assert bal == golden["final_balance"]
    assert max_dd == golden["max_dd"]
