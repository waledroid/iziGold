"""Characterization test for the backtest engine.

Pins the exact trade list `scripts/backtest.py` produces over a fixed slice
of real M5 data. This is NOT a correctness test -- it is a change detector.
If it fails, the replay's behaviour moved. Unless the task you are doing
explicitly changes replay behaviour, the change is a bug.

There are TWO pins over the same frozen fixture, because there are two entry
windows and only one of them can be the default at a time:

  golden_trades.json         LOOSE window. Captured 2026-08-20 BEFORE strict
                             became the default, so it survives that flip as a
                             like-for-like change detector. Its provenance is
                             load-bearing: do not regenerate it.
  golden_trades_strict.json  STRICT window -- the path every plain run now
                             takes. Added 2026-08-20 because the shipped
                             default was otherwise pinned by nothing but
                             "strict takes fewer entries than loose".

Regenerate deliberately (and only deliberately) with the manual script from
task-1-brief.md Step 4 (.superpowers/sdd/2026-08-20-backtest-report/):
    cd service && .venv/bin/python - <<'PY'
    import json
    from tests.test_backtest_golden import _replay, GOLDEN
    digest, bal, dd = _replay()
    GOLDEN.write_text(json.dumps(
        {"trades": digest, "final_balance": bal, "max_dd": dd}, indent=1))
    PY
(swap in _replay_strict / GOLDEN_STRICT for the strict pin.)
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = pathlib.Path(__file__).parent / "data"
BARS = DATA / "bars_slice.json"
GOLDEN = DATA / "golden_trades.json"
GOLDEN_STRICT = DATA / "golden_trades_strict.json"


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
    # The golden file pins LOOSE-window behaviour, captured 2026-08-20 before
    # strict became the default. Keep it loose so the pin stays a like-for-like
    # change detector across that default flip.
    bt.STRICT_WINDOW = False
    # Same reason for CONFIRM_CLOSES: this pin was captured at 1, before the
    # owner moved the default to 2 on 2026-08-20. Pinning it here keeps the
    # loose golden a like-for-like detector across BOTH default changes.
    bt.CONFIRM_CLOSES = 1
    # Same again for the M15 agreement filter, which became a default on
    # 2026-08-20, after this pin was captured. Off here = like-for-like.
    bt.BIAS_EMA, bt.BIAS_MODE, bt.BIAS_TF = 0, "tag", "M5"
    # The golden pins HalfTrend alone; QuickFlip is a separate lane added
    # 2026-08-20, after this pin was captured.
    bt.STRATEGY = "ht"
    trades, bal, max_dd, _valley = bt.run(candles, 4000.0, False)
    return _digest(trades), round(bal, 2), round(max_dd, 2)


def _replay_strict():
    """The SHIPPED default path: strict entry window, everything else stock."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    assert bt.STRICT_WINDOW is True, "strict is supposed to be the default"
    # The golden pins HalfTrend alone; QuickFlip is a separate lane added
    # 2026-08-20, after this pin was captured.
    bt.STRATEGY = "ht"
    assert bt.CONFIRM_CLOSES == 2, "2 waiting bars is supposed to be the default"
    assert (bt.BIAS_EMA, bt.BIAS_MODE, bt.BIAS_TF) == (55, "skip", "M15"), \
        "M15 EMA-55 agreement is supposed to be the default"
    trades, bal, max_dd, _valley = bt.run(candles, 4000.0, False)
    return _digest(trades), round(bal, 2), round(max_dd, 2)


def _assert_matches(digest, bal, max_dd, path):
    golden = json.loads(path.read_text())
    assert len(digest) == len(golden["trades"]), (
        f"trade COUNT moved: {len(golden['trades'])} -> {len(digest)}")
    for i, (got, want) in enumerate(zip(digest, golden["trades"])):
        assert got == want, f"trade {i} changed:\n  was {want}\n  now {got}"
    assert bal == golden["final_balance"]
    assert max_dd == golden["max_dd"]


def test_replay_matches_golden():
    _assert_matches(*_replay(), GOLDEN)


def test_strict_replay_matches_golden():
    """The loose pin above cannot see a regression that only touches the
    strict path -- and strict is what every plain run now takes."""
    _assert_matches(*_replay_strict(), GOLDEN_STRICT)


def test_the_two_pins_really_pin_different_runs():
    """Guards against a copy-paste that made both goldens the same file."""
    loose = json.loads(GOLDEN.read_text())
    strict = json.loads(GOLDEN_STRICT.read_text())
    assert len(strict["trades"]) < len(loose["trades"]), (
        "strict can only ever refuse entries loose would take")
