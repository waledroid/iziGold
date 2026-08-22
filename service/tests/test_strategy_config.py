"""Enforcement for config/strategy.json, the single source of truth for
strategy parameters that used to be declared TWICE: once as an MQL5 `input`
default in mt5/Experts/XauAssistant.mq5, once as a module constant in
scripts/backtest.py. They agreed only because a human remembered to edit
both files every time.

The file has two sections:

  shared      TradeManager/RiskManager parameters that apply no matter which
              registered strategy is ActiveStrategy -- there is only ONE set
              of these EA inputs (not duplicated per strategy).
  strategies  per-instance HalfTrend parameters that DO differ between the M5
              lane (halftrend_ema_v1) and the M15 lane (halftrend_m15_v1,
              added 2026-08-22). Each block mirrors that lane's own EA
              inputs (M15's are the `M15...`-prefixed inputs).

test_strategy_config_matches_the_ea is the load-bearing test: it parses the
EA's `input` defaults straight out of the .mq5 source (the EA is the LIVE
authority and cannot be imported -- it only compiles in MetaEditor on
Windows) and asserts each equals the JSON value, numerically (so `50` and
`50.0` agree), for BOTH strategies' own inputs plus the shared block.

test_backtest_constants_match_config proves scripts/backtest.py actually
LOADS the JSON (shared + the halftrend_ema_v1 block -- the only lane it
replays) rather than hardcoding its own copy.

test_config_has_no_unmapped_or_missing_keys is the completeness check: add a
key (or a strategy block) to the JSON without wiring it into the mappings
below (or vice versa) and this fails, so an unwired parameter can't silently
ship.
"""
import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "strategy.json"
EA_PATH = ROOT / "mt5" / "Experts" / "XauAssistant.mq5"

# shared config key -> (EA `input` name, backtest.py module attribute)
# The trading window is a single EA input pair mapped onto one backtest
# tuple constant (WINDOW), handled specially in test_backtest_constants_match_config.
SHARED_MAPPING = {
    "risk_per_trade_pct": ("RiskPerTradePct", "RISK_PCT"),
    "profit_target_pct": ("ProfitTargetPct", "PROFIT_TARGET_PCT"),
    "trail_lock_pct": ("TrailLockPct", "TRAIL_LOCK_PCT"),
    "trail_activate_r": ("TrailActivateR", "TRAIL_ACTIVATE_R"),
    "add_trigger_atr": ("AddTriggerATR", "ADD_TRIGGER_ATR"),
    "max_positions": ("MaxPositions", "MAX_POSITIONS"),
    "adx_trend_threshold": ("AdxTrendThreshold", "ADX_MIN"),
    "max_daily_exposure_min": ("MaxDailyExposureMin", "EXPO_MIN"),
    # window is EA-input-per-key but a single backtest tuple; the module
    # attribute names here are handled by the WINDOW-special-case, not read
    # generically -- kept here only for the EA-side check and completeness.
    "trading_window_start_hour": ("TradingWindowStartHour", None),
    "trading_window_end_hour": ("TradingWindowEndHour", None),
}

# strategy id -> {config key -> EA `input` name for THAT strategy's own inputs}
STRATEGY_EA_NAMES = {
    "halftrend_ema_v1": {
        "confirm_closes": "ConfirmCloses",
        "ema_length": "EmaLength",
        "ht_amplitude": "HtAmplitude",
        "stop_buffer_atr": "StopBufferATR",
        "htf_confirm_ema": "HtfConfirmEma",
        "htf_confirm_buffer_atr": "HtfConfirmBufferATR",
        "htf_chop_eff_max": "HtfChopEffMax",
        "htf_chop_bars": "HtfChopBars",
    },
    "halftrend_m15_v1": {
        "confirm_closes": "M15ConfirmCloses",
        "ema_length": "M15EmaLength",
        "ht_amplitude": "M15Amplitude",
        "stop_buffer_atr": "M15StopBufferATR",
        "htf_confirm_ema": "M15HtfConfirmEma",
        "htf_confirm_buffer_atr": "M15HtfConfirmBufferATR",
        "htf_chop_eff_max": "M15HtfChopEffMax",
        "htf_chop_bars": "M15HtfChopBars",
    },
}

# scripts/backtest.py replays the M5 (`ht`) lane only -- it does not gain an
# M15 lane -- so only halftrend_ema_v1's block has a backtest.py attribute
# to check.
STRATEGY_BT_ATTRS = {
    "halftrend_ema_v1": {
        "confirm_closes": "CONFIRM_CLOSES",
        "ema_length": "EMA_LEN",
        "ht_amplitude": "AMPLITUDE",
        "stop_buffer_atr": "STOP_BUFFER_ATR",
        "htf_confirm_ema": "BIAS_EMA",
        "htf_confirm_buffer_atr": "BIAS_BUFFER_ATR",
        "htf_chop_eff_max": "CHOP_EFF_MAX",
        "htf_chop_bars": "CHOP_EFF_BARS",
    },
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
    """Every mapped JSON value -- the shared block AND each strategy's own
    block -- must equal the live EA's `input` default, parsed straight out
    of mt5/Experts/XauAssistant.mq5. The EA is the authority -- if this
    fails, config/strategy.json (or the backtest that reads it) has drifted
    from what actually trades live."""
    config = _load_config()
    ea_inputs = _load_ea_inputs()
    mismatches = []

    for key, (ea_name, _bt_attr) in SHARED_MAPPING.items():
        assert ea_name in ea_inputs, (
            f"EA input {ea_name!r} (for shared config key {key!r}) not "
            f"found in {EA_PATH}")
        json_value = config["shared"][key]
        ea_value = ea_inputs[ea_name]
        if json_value != ea_value:
            mismatches.append(
                f"shared.{key}: config/strategy.json={json_value!r} != "
                f"EA input {ea_name}={ea_value!r}")

    for strategy_id, names in STRATEGY_EA_NAMES.items():
        block = config["strategies"][strategy_id]
        for key, ea_name in names.items():
            assert ea_name in ea_inputs, (
                f"EA input {ea_name!r} (for {strategy_id}.{key}) not found "
                f"in {EA_PATH}")
            json_value = block[key]
            ea_value = ea_inputs[ea_name]
            if json_value != ea_value:
                mismatches.append(
                    f"strategies.{strategy_id}.{key}: "
                    f"config/strategy.json={json_value!r} != "
                    f"EA input {ea_name}={ea_value!r}")

    assert not mismatches, "strategy config drifted from the live EA:\n" + \
        "\n".join(mismatches)


def test_backtest_constants_match_config():
    """scripts/backtest.py must LOAD these values from the JSON (shared +
    the halftrend_ema_v1 block, the only lane it replays), not carry its own
    hardcoded copy. Proves the load is faithful, one attribute at a time,
    plus the WINDOW tuple special case."""
    config = _load_config()
    bt = _load_bt()
    mismatches = []

    for key, (_ea_name, bt_attr) in SHARED_MAPPING.items():
        if bt_attr is None:
            continue
        bt_value = getattr(bt, bt_attr)
        json_value = config["shared"][key]
        if bt_value != json_value:
            mismatches.append(
                f"shared.{key}: config/strategy.json={json_value!r} != "
                f"backtest.{bt_attr}={bt_value!r}")

    m5_block = config["strategies"]["halftrend_ema_v1"]
    for key, bt_attr in STRATEGY_BT_ATTRS["halftrend_ema_v1"].items():
        bt_value = getattr(bt, bt_attr)
        json_value = m5_block[key]
        if bt_value != json_value:
            mismatches.append(
                f"strategies.halftrend_ema_v1.{key}: "
                f"config/strategy.json={json_value!r} != "
                f"backtest.{bt_attr}={bt_value!r}")

    assert not mismatches, "backtest.py drifted from the config:\n" + \
        "\n".join(mismatches)
    assert bt.WINDOW == (config["shared"]["trading_window_start_hour"],
                          config["shared"]["trading_window_end_hour"])


def test_config_has_no_unmapped_or_missing_keys():
    """Adding a parameter to the JSON without wiring it into the mappings
    above (or vice versa) must fail -- an unwired parameter is exactly the
    kind of silent drift this config exists to prevent. Covers the shared
    block, the set of registered strategy blocks, and each block's own
    keys."""
    config = _load_config()

    shared_keys = set(config["shared"].keys())
    mapped_shared_keys = set(SHARED_MAPPING.keys())
    assert not (shared_keys - mapped_shared_keys), (
        "config/strategy.json 'shared' has keys not wired into "
        f"SHARED_MAPPING: {sorted(shared_keys - mapped_shared_keys)}")
    assert not (mapped_shared_keys - shared_keys), (
        "SHARED_MAPPING references keys missing from config/strategy.json "
        f"'shared': {sorted(mapped_shared_keys - shared_keys)}")

    config_strategy_ids = set(config["strategies"].keys())
    mapped_strategy_ids = set(STRATEGY_EA_NAMES.keys())
    assert not (config_strategy_ids - mapped_strategy_ids), (
        "config/strategy.json 'strategies' has a block not wired into "
        f"STRATEGY_EA_NAMES: {sorted(config_strategy_ids - mapped_strategy_ids)}")
    assert not (mapped_strategy_ids - config_strategy_ids), (
        "STRATEGY_EA_NAMES references a strategy missing from "
        f"config/strategy.json 'strategies': {sorted(mapped_strategy_ids - config_strategy_ids)}")

    for strategy_id, names in STRATEGY_EA_NAMES.items():
        block_keys = set(config["strategies"][strategy_id].keys())
        mapped_keys = set(names.keys())
        assert not (block_keys - mapped_keys), (
            f"config/strategy.json strategies.{strategy_id} has keys not "
            f"wired into STRATEGY_EA_NAMES[{strategy_id!r}]: "
            f"{sorted(block_keys - mapped_keys)}")
        assert not (mapped_keys - block_keys), (
            f"STRATEGY_EA_NAMES[{strategy_id!r}] references keys missing "
            f"from config/strategy.json strategies.{strategy_id}: "
            f"{sorted(mapped_keys - block_keys)}")


def test_config_file_has_explanatory_header():
    """The JSON has no comment syntax, so the header lives in a `_meta`
    string field. Just confirm it exists and mentions the enforcing test,
    so a reader who opens the file understands why it exists."""
    config = _load_config()
    assert "_meta" in config
    assert "test_strategy_config_matches_the_ea" in config["_meta"]


@pytest.mark.parametrize("key,names", [(k, v) for k, v in SHARED_MAPPING.items()])
def test_every_shared_mapping_entry_has_an_ea_name(key, names):
    ea_name, _bt_attr = names
    assert ea_name, f"SHARED_MAPPING[{key!r}] has no EA input name"


@pytest.mark.parametrize(
    "strategy_id,key,ea_name",
    [(sid, k, v) for sid, names in STRATEGY_EA_NAMES.items()
     for k, v in names.items()])
def test_every_strategy_mapping_entry_has_an_ea_name(strategy_id, key, ea_name):
    assert ea_name, f"STRATEGY_EA_NAMES[{strategy_id!r}][{key!r}] has no EA input name"


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
