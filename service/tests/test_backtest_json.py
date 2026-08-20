"""The JSON artifact is the ONLY interface between engine and page."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _artifact(tmp_path, balance=10000.0):
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(candles, balance, False)
    args = bt.build_parser().parse_args(["--balance", str(balance)])
    return bt, bt.build_run_json(candles, trades, args,
                                 {"bal": bal, "max_dd": dd, "valley": valley})


def test_candles_are_parallel_arrays_of_equal_length(tmp_path):
    _bt, art = _artifact(tmp_path)
    c = art["candles"]
    n = len(c["t"])
    assert n > 0
    for k in ("o", "h", "l", "c"):
        assert len(c[k]) == n


def test_indicator_series_align_with_the_candles(tmp_path):
    _bt, art = _artifact(tmp_path)
    n = len(art["candles"]["t"])
    for k in ("ema9", "ema21", "ema55", "ema200"):
        assert len(art["ind"][k]) == n
    assert len(art["ind"]["ht"]["v"]) == n
    assert len(art["ind"]["ht"]["trend"]) == n


def test_trades_carry_entry_times_stop_history_and_tp(tmp_path):
    _bt, art = _artifact(tmp_path)
    assert art["trades"]
    for t in art["trades"]:
        assert t["legs"] and all("t" in leg for leg in t["legs"])
        assert t["stop_history"]
        assert "tp" in t and "exit_t" in t and "bal_after" in t


def test_stats_carry_the_clamp_measurement(tmp_path):
    _bt, art = _artifact(tmp_path)
    for k in ("clamp_pct", "risk_median", "risk_p90", "net", "win_rate",
              "start_balance", "end_balance", "max_dd"):
        assert k in art["stats"], f"stats missing {k}"


def test_meta_carries_the_caveats(tmp_path):
    bt, art = _artifact(tmp_path)
    assert art["meta"]["caveats"] == bt.CAVEATS


def test_artifact_round_trips_through_json(tmp_path):
    _bt, art = _artifact(tmp_path)
    p = tmp_path / "run.json"
    p.write_text(json.dumps(art))
    assert json.loads(p.read_text())["stats"]["net"] == art["stats"]["net"]
