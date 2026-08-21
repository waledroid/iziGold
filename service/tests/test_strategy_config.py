"""Enforcement for config/strategy.json, the single source of truth for the
16 strategy parameters (+ the trading window) that used to be declared
TWICE: once as an MQL5 `input` default in mt5/Experts/XauAssistant.mq5, once
as a module constant in scripts/backtest.py. They agreed only because a
human remembered to edit both files every time.

test_strategy_config_matches_the_ea is the load-bearing test: it parses the
EA's `input` defaults straight out of the .mq5 source (the EA is the LIVE
authority and cannot be imported -- it only compiles in MetaEditor on
Windows) and asserts each equals the JSON value, numerically (so `50` and
`50.0` agree).

test_backtest_constants_match_config proves scripts/backtest.py actually
LOADS the JSON rather than hardcoding its own copy.

test_config_has_no_unmapped_or_missing_keys is the completeness check: add a
key to the JSON without wiring it into MAPPING below (or vice versa) and this
fails, so an unwired parameter can't silently ship.
"""
import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "strategy.json"
EA_PATH = ROOT / "mt5" / "Experts" / "XauAssistant.mq5"

# json key -> (EA `input` name, backtest.py module attribute)
# The trading window is a single EA input pair mapped onto one backtest
# tuple constant (WINDOW), handled specially below.
MAPPING = {
    "confirm_closes": ("ConfirmCloses", "CONFIRM_CLOSES"),
    "ema_length": ("EmaLength", "EMA_LEN"),
    "ht_amplitude": ("HtAmplitude", "AMPLITUDE"),
    "stop_buffer_atr": ("StopBufferATR", "STOP_BUFFER_ATR"),
    "risk_per_trade_pct": ("RiskPerTradePct", "RISK_PCT"),
    "profit_target_pct": ("ProfitTargetPct", "PROFIT_TARGET_PCT"),
    "trail_lock_pct": ("TrailLockPct", "TRAIL_LOCK_PCT"),
    "trail_activate_r": ("TrailActivateR", "TRAIL_ACTIVATE_R"),
    "add_trigger_atr": ("AddTriggerATR", "ADD_TRIGGER_ATR"),
    "max_positions": ("MaxPositions", "MAX_POSITIONS"),
    "adx_trend_threshold": ("AdxTrendThreshold", "ADX_MIN"),
    "max_daily_exposure_min": ("MaxDailyExposureMin", "EXPO_MIN"),
    "htf_confirm_ema": ("HtfConfirmEma", "BIAS_EMA"),
    "htf_confirm_buffer_atr": ("HtfConfirmBufferATR", "BIAS_BUFFER_ATR"),
    "htf_chop_eff_max": ("HtfChopEffMax", "CHOP_EFF_MAX"),
    "htf_chop_bars": ("HtfChopBars", "CHOP_EFF_BARS"),
    # window is EA-input-per-key but a single backtest tuple; the module
    # attribute names here are handled by the WINDOW-special-case tests,
    # not read generically -- kept in MAPPING only for the EA-side check
    # and the completeness check.
    "trading_window_start_hour": ("TradingWindowStartHour", None),
    "trading_window_end_hour": ("TradingWindowEndHour", None),
}

_INPUT_RE = re.compile(
    r"^input\s+\S+\s+(\w+)\s*=\s*([^;]+);", re.MULTILINE)


def _load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_ea_inputs(path=EA_PATH):
    """Parse every `input <type> Name = value;` default out of the EA
    source. Returns {name: python number}. Does not compile or execute
    MQL5 -- this is a text parse of the literal default."""
    text = path.read_text(encoding="utf-8")
    inputs = {}
    for name, raw in _INPUT_RE.findall(text):
        raw = raw.strip()
        try:
            value = int(raw)
        except ValueError:
            try:
                value = float(raw)
            except ValueError:
                continue  # non-numeric input (string/bool/enum) -- not ours
        inputs[name] = value
    return inputs


def _load_bt():
    """Import scripts/backtest.py by path (it's a script, not a package
    member); importing is side-effect free (main() is __name__-guarded)."""
    spec = importlib.util.spec_from_file_location(
        "bt_strategy_config_check", ROOT / "scripts" / "backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_strategy_config_matches_the_ea():
    """Every mapped JSON value must equal the live EA's `input` default,
    parsed straight out of mt5/Experts/XauAssistant.mq5. The EA is the
    authority -- if this fails, config/strategy.json (or the backtest that
    reads it) has drifted from what actually trades live."""
    config = _load_config()
    ea_inputs = _load_ea_inputs()
    mismatches = []
    for key, (ea_name, _bt_attr) in MAPPING.items():
        assert ea_name in ea_inputs, (
            f"EA input {ea_name!r} (for config key {key!r}) not found in "
            f"{EA_PATH}")
        json_value = config[key]
        ea_value = ea_inputs[ea_name]
        if json_value != ea_value:
            mismatches.append(
                f"{key}: config/strategy.json={json_value!r} != "
                f"EA input {ea_name}={ea_value!r}")
    assert not mismatches, "strategy config drifted from the live EA:\n" + \
        "\n".join(mismatches)


def test_backtest_constants_match_config():
    """scripts/backtest.py must LOAD these values from the JSON, not carry
    its own hardcoded copy. Proves the load is faithful, one attribute at a
    time, plus the WINDOW tuple special case."""
    config = _load_config()
    bt = _load_bt()
    mismatches = []
    for key, (_ea_name, bt_attr) in MAPPING.items():
        if bt_attr is None:
            continue
        bt_value = getattr(bt, bt_attr)
        json_value = config[key]
        if bt_value != json_value:
            mismatches.append(
                f"{key}: config/strategy.json={json_value!r} != "
                f"backtest.{bt_attr}={bt_value!r}")
    assert not mismatches, "backtest.py drifted from the config:\n" + \
        "\n".join(mismatches)
    assert bt.WINDOW == (config["trading_window_start_hour"],
                          config["trading_window_end_hour"])


def test_config_has_no_unmapped_or_missing_keys():
    """Adding a parameter to the JSON without wiring it into MAPPING (EA
    input name + backtest attribute) must fail -- an unwired parameter is
    exactly the kind of silent drift this config exists to prevent."""
    config = _load_config()
    config_keys = set(config.keys()) - {"_meta"}
    mapped_keys = set(MAPPING.keys())
    missing_from_mapping = config_keys - mapped_keys
    missing_from_config = mapped_keys - config_keys
    assert not missing_from_mapping, (
        f"config/strategy.json has keys not wired into "
        f"test_strategy_config.py's MAPPING: {sorted(missing_from_mapping)}")
    assert not missing_from_config, (
        f"MAPPING references keys missing from config/strategy.json: "
        f"{sorted(missing_from_config)}")


def test_config_file_has_explanatory_header():
    """The JSON has no comment syntax, so the header lives in a `_meta`
    string field. Just confirm it exists and mentions the enforcing test,
    so a reader who opens the file understands why it exists."""
    config = _load_config()
    assert "_meta" in config
    assert "test_strategy_config_matches_the_ea" in config["_meta"]


@pytest.mark.parametrize("key,names", [(k, v) for k, v in MAPPING.items()])
def test_every_mapping_entry_has_an_ea_name(key, names):
    ea_name, _bt_attr = names
    assert ea_name, f"MAPPING[{key!r}] has no EA input name"


def test_runs_carry_a_dataset_fingerprint():
    """bars_max.json is untracked and mutable: a terminal refresh rewrites
    months of history. A result is only reproducible against the dataset that
    produced it, so every run names one -- and the header and the --json
    artifact must use the SAME function or they will disagree."""
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "bt_fp", root / "scripts" / "backtest.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)

    a = [{"t": 1, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5},
         {"t": 2, "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0}]
    same = [dict(x) for x in a]
    fp = bt._run_fingerprint(a)
    assert fp == bt._run_fingerprint(same), "identical bars must fingerprint alike"
    assert len(fp) == 12

    # a single changed PRICE must move it -- otherwise the fingerprint cannot
    # detect the refresh that actually bit us
    moved = [dict(x) for x in a]
    moved[1]["c"] = 2.01
    assert bt._run_fingerprint(moved) != fp

    # so must an extra bar (the append case: yesterday's file vs today's)
    assert bt._run_fingerprint(a + [{"t": 3, "o": 2.0, "h": 2.1,
                                     "l": 1.9, "c": 2.05}]) != fp
