"""Characterization suite for the HalfTrend lane's OPTIONAL-FEATURE surface.

test_backtest_golden.py already pins three configurations of scripts/
backtest.py's run(): default settings under the loose entry window, under the
strict entry window, and both lanes together. None of those three exercise
the ~nine optional feature blocks tangled into the HalfTrend path (regime
gate, ATR-spike gate, chop filter, bias/HTF-confirm filter, support/
resistance proximity, the profit-floor exit schemes, the minimum-stop floor,
fixed entry mode, the trading window/exposure budget/profit-target overrides,
the M15 timeframe, and the confirm-mode/ConfirmCloses knobs) -- every one of
those is "off" (or at its default) in all three existing pins. Extracting
HalfTrend into its own module with only those three pins in place would let
any one of those features silently break, in a refactor that still passes
every existing test.

This file is that missing net. Each entry in COMBOS below sets a handful of
scripts/backtest.py module globals -- the same mechanism test_backtest_
golden.py uses (STRICT_WINDOW = False, etc.) -- on a freshly-exec'd copy of
the module, replays it over the FROZEN service/tests/data/bars_slice.json
fixture (6,000 M5 bars, same fixture the existing goldens use; never
modified), and pins the resulting trade digest / final balance / max
drawdown to service/tests/data/golden_ht_<name>.json.

Every setting below was chosen because it demonstrably MOVES the trade list
relative to the strict-window default (golden_trades_strict.json) -- verified
by hand before being pinned; see .superpowers/halftrend-step1-report.md for
the settings that were tried and REJECTED as inert (produced zero trades, or
a trade list byte-identical to the default), and why.

If a test here fails, the HalfTrend replay's behaviour moved on that specific
feature. Unless the task you are doing explicitly changes that feature's
behaviour, the change is a bug -- and the failure message names the losing
combination and the first trade that differs, not just "assert False".

Regenerate deliberately (and only deliberately) after a real, intended
behaviour change:
    cd service && .venv/bin/python - <<'PY'
    from tests.test_halftrend_characterization import regenerate_all
    regenerate_all()
    PY
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = pathlib.Path(__file__).parent / "data"
BARS = DATA / "bars_slice.json"


def _load_bt():
    """Import scripts/backtest.py fresh (side-effect free: the argparse/main
    call is guarded by __name__ == '__main__'), exactly like
    test_backtest_golden.py::_load_bt. A fresh module per replay means
    setting globals on it can never leak into another combo."""
    spec = importlib.util.spec_from_file_location(
        "bt", ROOT / "scripts" / "backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _digest(trades):
    """Compact, human-diffable summary of a trade list -- same shape as
    test_backtest_golden.py::_digest, so these pins read as the same
    pattern."""
    return [{"lane": t.get("lane", "ht"),
             "dir": t["dir"],
             "entry": round(t["legs"][0]["px"], 2),
             "legs": len(t["legs"]),
             "exit": round(t["exit"], 2),
             "why": t["why"],
             "pl": round(t["pl"], 2)} for t in trades]


# --- the combinations ------------------------------------------------------
# "set": module globals to assign on the freshly-loaded bt module, mirroring
# what main() would have set from the equivalent CLI flags. "tf": "M15" is a
# special case (needs resampling the source candles too, exactly like main()
# does for --tf M15) so it is handled in _replay_combo below rather than as a
# plain global.
COMBOS = [
    {
        "name": "regime_gate_range",
        "cli": "--regime-gate range",
        "doc": "refuse new entries when the service classifier says range",
        "set": {"REGIME_GATE": "range"},
    },
    {
        "name": "regime_gate_range_strict",
        "cli": "--regime-gate range-strict",
        "doc": "refuse range AND high_volatility -- only trend bars may enter",
        "set": {"REGIME_GATE": "range-strict"},
    },
    {
        "name": "regime_gate_highvol",
        "cli": "--regime-gate highvol",
        "doc": "refuse new entries when the classifier says high_volatility",
        "set": {"REGIME_GATE": "highvol"},
    },
    {
        "name": "atr_spike_gate",
        "cli": "--atr-spike-gate 1.3",
        "doc": "refuse entries when ATR(14) > 1.3x its trailing-100 median",
        "set": {"ATR_SPIKE_RATIO": 1.3},
    },
    {
        "name": "chop_skip",
        "cli": "--chop-flips 1 --chop-bars 24 --chop-box-atr 5.0 --chop-mode skip",
        "doc": "flip-count-only-ish chop tag (wide box) refuses new entries",
        "set": {"CHOP_FLIPS": 1, "CHOP_BARS": 24, "CHOP_BOX_ATR": 5.0,
                "CHOP_MODE": "skip"},
    },
    {
        "name": "chop_soft",
        "cli": "--chop-flips 1 --chop-bars 12 --chop-box-atr 3.0 --chop-mode soft",
        "doc": "soft mode: same entry count as default, half risk + no adds "
               "on the flagged baskets -- pins that the SIZE, not the "
               "entry list, is what soft mode changes",
        "set": {"CHOP_FLIPS": 1, "CHOP_BARS": 12, "CHOP_BOX_ATR": 3.0,
                "CHOP_MODE": "soft"},
    },
    {
        "name": "bias_off",
        "cli": "--bias-ema 0",
        "doc": "the pre-2026-08-20 replay: HTF-confirm filter off entirely",
        "set": {"BIAS_EMA": 0},
    },
    {
        "name": "bias_chop_eff_wide",
        "cli": "--chop-eff-max 0.12",
        "doc": "widen the 'is this tape actually trending' cutoff that "
               "decides whether the M15 side-test runs at all -- see the "
               "report for why this is the single most sensitive knob in "
               "the whole bias block (a shadowed `trending` name)",
        "set": {"CHOP_EFF_MAX": 0.12},
    },
    {
        "name": "bias_tf_m5",
        "cli": "--bias-tf M5",
        "doc": "read the HTF-confirm EMA on the M5 clock instead of M15",
        "set": {"BIAS_TF": "M5"},
    },
    {
        "name": "sr_proximity",
        "cli": "--sr-lookback 50 --sr-min-headroom 0.5",
        "doc": "refuse entries whose nearest opposing level sits < 0.5 ATR away",
        "set": {"SR_LOOKBACK": 50, "SR_MIN_HEADROOM": 0.5},
    },
    {
        "name": "exit_floor_a",
        "cli": "--exit-scheme floor-a",
        "doc": "at target, ratchet the stop to target-0.25*ATR; adds frozen",
        "set": {"EXIT_SCHEME": "floor-a"},
    },
    {
        "name": "exit_floor_b",
        "cli": "--exit-scheme floor-b",
        "doc": "arm the full target as the floor once profit clears it by "
               "0.25*ATR; adds frozen",
        "set": {"EXIT_SCHEME": "floor-b"},
    },
    {
        "name": "exit_floor_a_adds",
        "cli": "--exit-scheme floor-a-adds",
        "doc": "floor-a but pyramid adds stay ON -- quantifies the erosion",
        "set": {"EXIT_SCHEME": "floor-a-adds"},
    },
    {
        "name": "min_stop_floor",
        "cli": "--min-stop-atr 1.5",
        "doc": "entry stop may not sit closer than 1.5x ATR(14) from the fill",
        "set": {"MIN_STOP_ATR": 1.5},
    },
    {
        "name": "entry_mode_fixed",
        "cli": "--entry-mode fixed",
        "doc": "fixed lots, no adds/target/lock -- exits only on reversal/"
               "stop/flatten",
        "set": {"ENTRY_MODE": "fixed"},
    },
    {
        "name": "trading_window",
        "cli": "--window-start 8 --window-end 16",
        "doc": "narrower entry window than the live 4-23 default",
        "set": {"WINDOW": (8, 16)},
    },
    {
        "name": "exposure_budget",
        "cli": "--expo 60",
        "doc": "60 minutes/day of open-position time budget (default unlimited)",
        "set": {"EXPO_MIN": 60},
    },
    {
        "name": "profit_target_off",
        "cli": "--profit-target 0",
        "doc": "profit target OFF (EA semantics): ride to lock/stop/reversal/"
               "flatten only -- NOT the same code path as --entry-mode fixed",
        "set": {"PROFIT_TARGET_PCT": 0},
    },
    {
        "name": "tf_m15",
        "cli": "--tf M15",
        "doc": "every trading decision on M15 bars aggregated from the M5 "
               "source",
        "set": {},
        "tf": "M15",
    },
    {
        "name": "confirm_mode_open_loose",
        "cli": "--confirm-mode open --loose-window",
        "doc": "confirm on next-bar OPEN instead of this-bar CLOSE; only "
               "visibly differs from close-mode under the loose window "
               "(strict's one-shot decision makes open/close identical by "
               "design -- see the report for why that combo was rejected)",
        "set": {"CONFIRM_MODE": "open", "STRICT_WINDOW": False},
    },
    {
        "name": "confirm_closes_3",
        "cli": "--confirm 3",
        "doc": "three waiting bars after the arrow bar instead of the "
               "default two, under the strict one-shot entry window",
        "set": {"CONFIRM_CLOSES": 3},
    },
]

_BY_NAME = {c["name"]: c for c in COMBOS}


def _golden_path(name):
    return DATA / f"golden_ht_{name}.json"


def _replay_combo(combo):
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    if combo.get("tf") == "M15":
        bt.TF = "M15"
        bt.BAR_MIN = bt.TF_SEC["M15"] // 60
        bt.FLATTEN_HM = bt.FLATTEN_BY_TF["M15"]
        candles = bt.resample(candles, bt.TF_SEC["M15"])
    for k, v in combo["set"].items():
        setattr(bt, k, v)
    trades, bal, max_dd, _valley = bt.run(
        candles, 4000.0, False, bt.lanes_for("ht"))
    return _digest(trades), round(bal, 2), round(max_dd, 2)


def _assert_matches(name, digest, bal, max_dd):
    path = _golden_path(name)
    golden = json.loads(path.read_text())
    assert len(digest) == len(golden["trades"]), (
        f"[{name}] trade COUNT moved: "
        f"{len(golden['trades'])} -> {len(digest)}")
    for i, (got, want) in enumerate(zip(digest, golden["trades"])):
        assert got == want, (
            f"[{name}] trade {i} changed (first difference):\n"
            f"  was {want}\n  now {got}")
    assert bal == golden["final_balance"], (
        f"[{name}] final balance moved: {golden['final_balance']} -> {bal}")
    assert max_dd == golden["max_dd"], (
        f"[{name}] max drawdown moved: {golden['max_dd']} -> {max_dd}")


def regenerate_all():
    """Manual, deliberate-only regeneration -- see the module docstring."""
    for combo in COMBOS:
        digest, bal, max_dd = _replay_combo(combo)
        _golden_path(combo["name"]).write_text(json.dumps(
            {"trades": digest, "final_balance": bal, "max_dd": max_dd},
            indent=1))


def test_every_combo_matches_its_golden():
    for combo in COMBOS:
        digest, bal, max_dd = _replay_combo(combo)
        _assert_matches(combo["name"], digest, bal, max_dd)


def test_every_combo_differs_from_the_strict_default():
    """Guards the pins themselves: if a combo's trade list is ever byte-
    identical to the shipped strict-window default, it has stopped
    exercising its feature (e.g. a rebase silently zeroed one of its
    settings back to the default) and is no longer pinning anything."""
    default = json.loads((DATA / "golden_trades_strict.json").read_text())
    for combo in COMBOS:
        golden = json.loads(_golden_path(combo["name"]).read_text())
        assert golden["trades"] != default["trades"] or \
            golden["final_balance"] != default["final_balance"], (
            f"[{combo['name']}] pin is identical to the strict default -- "
            "it no longer exercises its feature")


def test_combo_names_are_unique():
    names = [c["name"] for c in COMBOS]
    assert len(names) == len(set(names))


def test_every_combo_has_a_golden_file():
    missing = [c["name"] for c in COMBOS if not _golden_path(c["name"]).exists()]
    assert not missing, f"missing golden files for: {missing}"
