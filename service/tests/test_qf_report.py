"""A combined run must report the lanes separately -- a blended number hides
which strategy actually made the money."""
import json

from tests.test_backtest_golden import BARS, ROOT, _load_bt


def _artifact(balance=10000.0):
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(
        candles, balance, False, bt.lanes_for("both"))
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


def test_lane_stats_carry_a_per_lane_max_drawdown():
    """Spec 2026-08-20 asks for "(trades, win%, net, max DD)" per lane. The
    first three shipped; max DD did not."""
    _bt, art = _artifact()
    for key in ("ht", "qf"):
        assert "max_dd" in art["stats"]["lanes"][key]
        assert art["stats"]["lanes"][key]["max_dd"] >= 0.0


def test_lane_drawdown_is_a_path_measure_not_a_sum_of_losers():
    bt = _load_bt()
    rows = [{"pl": 100.0, "exit_t": 4}, {"pl": -30.0, "exit_t": 1},
            {"pl": -20.0, "exit_t": 3}, {"pl": 10.0, "exit_t": 2}]
    # chronological: -30, +10, -20, +100 -> cumulative -30, -20, -40, +60.
    # The peak starts at 0 (the lane's own starting point), so the deepest
    # dig below any prior peak is 40, not the 50 you get by summing losers.
    assert bt._lane_drawdown(rows) == 40.0
    assert bt._lane_drawdown([{"pl": 5.0, "exit_t": 1},
                              {"pl": 7.0, "exit_t": 2}]) == 0.0
    assert bt._lane_drawdown([]) == 0.0


def test_quickflip_entries_reach_the_sizing_report():
    """M8: sizing["clamped"] was incremented only in the HalfTrend block, so
    a lane clamping its own entries to the minimum lot was invisible in the
    printed clamp rate."""
    bt = _load_bt()
    bt.run(json.loads(BARS.read_text()), 10000.0, False, bt.lanes_for("qf"))
    s = bt.run.sizing
    assert s["entries"] == 0, "a qf-only run risk-sizes no HalfTrend entry"
    assert s["qf_entries"] > 0, "...but QuickFlip's entries must be counted"
    assert s["qf_clamp_pct"] is not None
    assert s["qf_risk_median"] is not None


def test_the_html_report_names_both_lanes_in_its_title(tmp_path):
    """M4b: --web/--json carry `lane` "so the report can colour and filter
    them" (spec 2026-08-20), but scripts/backtest_report.py contained the
    string "lane" zero times -- so the shared page blended both lanes while
    `both` is the default."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from backtest_report import write_report

    _bt, art = _artifact()
    out = tmp_path / "report.html"
    write_report(art, out)
    html = out.read_text(encoding="utf-8")
    assert "ht)" in html and "qf)" in html, "the title does not break out the lanes"
