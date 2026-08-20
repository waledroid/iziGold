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
    """How many QuickFlip trades overlapped a HalfTrend position -- the
    thing a combined equity curve hides, and the reason exposure can exceed
    one lane's. Pinned against the frozen fixture so a broken overlap check
    (e.g. an equality test instead of interval intersection, or a constant
    0) cannot slip through unnoticed."""
    _bt, art = _artifact()
    assert art["stats"]["lanes"]["qf_trades_overlapping_ht"] == 1
