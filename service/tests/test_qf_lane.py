"""Two lanes, one balance, neither able to touch the other's position."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _run(strategy, balance=10000.0):
    bt = _load_bt()
    bt.STRATEGY = strategy
    trades, bal, dd, valley = bt.run(json.loads(BARS.read_text()), balance, False)
    return bt, trades, bal


def test_ht_only_produces_no_qf_trades():
    _bt, trades, _bal = _run("ht")
    assert trades
    assert all(t["lane"] == "ht" for t in trades)


def test_qf_only_produces_no_ht_trades():
    _bt, trades, _bal = _run("qf")
    assert trades, "the fixture must contain at least one QuickFlip setup"
    assert all(t["lane"] == "qf" for t in trades)


def test_both_runs_both_lanes():
    _bt, trades, _bal = _run("both")
    lanes = {t["lane"] for t in trades}
    assert lanes == {"ht", "qf"}


def test_lanes_close_only_their_own_positions():
    """The real independence property: neither lane can close the other's
    position. A QuickFlip trade may only end for a QuickFlip reason, and a
    HalfTrend trade may never end for one."""
    _bt, trades, _bal = _run("both")
    qf_reasons = {"qf target", "qf stop", "qf expired"}
    for t in trades:
        if t["lane"] == "qf":
            assert t["why"] in qf_reasons, f"qf trade closed by {t['why']!r}"
        else:
            assert t["why"] not in qf_reasons, f"ht trade closed by {t['why']!r}"


def test_ht_decisions_match_until_the_first_qf_close():
    """The QuickFlip lane's presence does not perturb HalfTrend BEFORE
    QuickFlip trades.

    That weaker claim is all this can honestly assert on the frozen fixture,
    and the earlier docstring claimed more. The mechanism is real -- until a
    QuickFlip trade RESOLVES the shared balance is untouched, so HalfTrend's
    decisions must be bit-identical to running it alone -- but the fixture
    holds only two QuickFlip setups (opening 2025-10-30 14:55 and 2025-11-06
    14:00), and the last HalfTrend trade this compares exits 2025-10-30 06:10,
    nearly NINE HOURS before the first QuickFlip position is even opened. So
    it exercises leakage before QuickFlip trades at all; it does not exercise
    the interesting window -- a HalfTrend basket held open ACROSS a live
    QuickFlip position, before that position resolves. Do not stretch the
    fixture to fix this: it is frozen and three golden pins depend on it.

    After the first QuickFlip close the two paths may legitimately diverge --
    the profit target is a dollar amount off a balance both lanes now share.
    """
    _b1, ht_only, _x = _run("ht")
    _b2, both, _y = _run("both")
    qf_closes = [t["exit_t"] for t in both if t["lane"] == "qf"]
    if not qf_closes:
        return                      # nothing to compare against
    first_qf_close = min(qf_closes)
    early_alone = [t for t in ht_only if t["exit_t"] <= first_qf_close]
    early_both = [t for t in both
                  if t["lane"] == "ht" and t["exit_t"] <= first_qf_close]
    assert early_alone, "fixture must contain a HalfTrend trade before the first qf close"
    assert len(early_alone) == len(early_both)
    for a, b in zip(early_alone, early_both):
        assert a["dir"] == b["dir"]
        assert round(a["legs"][0]["px"], 2) == round(b["legs"][0]["px"], 2)
        assert round(a["exit"], 2) == round(b["exit"], 2)
        assert a["why"] == b["why"]


def test_qf_trades_carry_the_full_record_shape():
    """Downstream (--json, --web, the report page) reads these keys."""
    _bt, trades, _bal = _run("qf")
    for t in trades:
        assert t["legs"] and "t" in t["legs"][0] and "oz" in t["legs"][0]
        assert t["stop_history"] and t["tp"] is not None
        assert t["exit_t"] > t["legs"][0]["t"]
        assert t["why"] in ("qf target", "qf stop", "qf expired")


def test_balances_chain_across_both_lanes():
    _bt, trades, bal = _run("both")
    ordered = sorted(trades, key=lambda t: t["exit_t"])
    assert round(ordered[-1]["bal_after"], 2) == round(bal, 2)
