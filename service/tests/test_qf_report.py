"""A combined run must report the lanes separately -- a blended number hides
which strategy actually made the money."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _artifact(balance=10000.0):
    bt = _load_bt()
    bt.STRATEGY = "both"
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(candles, balance, False)
    args = bt.build_parser().parse_args(["--balance", str(balance)])
    return bt, bt.build_run_json(candles, trades, args,
                                 {"bal": bal, "max_dd": dd, "valley": valley})


def test_every_trade_in_the_artifact_carries_its_lane():
    _bt, art = _artifact()
    assert art["trades"]
    assert all(t["lane"] in ("ht", "qf") for t in art["trades"])


def test_stats_break_down_by_lane():
    _bt, art = _artifact()
    lanes = art["stats"]["lanes"]
    for key in ("ht", "qf"):
        assert key in lanes
        for field in ("trades", "wins", "net"):
            assert field in lanes[key]


def test_lane_nets_sum_to_the_total():
    _bt, art = _artifact()
    lanes = art["stats"]["lanes"]
    total = round(lanes["ht"]["net"] + lanes["qf"]["net"], 2)
    assert abs(total - art["stats"]["net"]) < 0.02


def test_concurrency_is_reported():
    """How often both lanes held a position at once -- the thing a combined
    equity curve hides, and the reason exposure can exceed one lane's."""
    _bt, art = _artifact()
    assert "both_open_bars" in art["stats"]["lanes"]
