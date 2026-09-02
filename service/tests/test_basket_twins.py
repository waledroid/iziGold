"""Pins the shared contract between the basket-boundary twins:
`app/trade_report.py::_basket_legs` and `app/reports.py::_group_baskets`. Both answer
the same question -- "which trade rows belong to this basket, in what
order" -- from the same underlying `trades` table, but independently (see
the TWIN WARNING comment on each function).

This stage deliberately does NOT unify the two functions -- pin first,
merge later."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tw.db"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "screenshots"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _trade(**overrides):
    ev = {"event": "open", "strategy_id": "halftrend_ema_v1", "direction": "BUY",
          "lots": 0.1, "price": 2400.0, "sl": 2390.0, "tp": 0.0,
          "reason": "signal confirmed", "ticket": 12345, "entry_mode": "adr",
          "htf_agree": 1, "ema200_agree": 1}
    ev.update(overrides)
    return ev


def _rows_for_group_baskets(db):
    """The same row shape `/api/trades` and the Trades report build for
    `_group_baskets` -- see `_fetch_closed_baskets` in app/reports.py."""
    cols = ["id", "ts", "event", "direction", "lots", "price", "profit",
            "final", "entry_mode", "reason", "strategy_id", "htf_agree",
            "ema200_agree"]
    raw = db.conn.execute(
        f"SELECT {', '.join(cols)} FROM trades ORDER BY id ASC").fetchall()
    return [dict(zip(cols, r)) for r in raw]


def test_basket_legs_and_group_baskets_agree_on_the_same_legs(client):
    """Build two closed baskets (one single-leg, one multi-leg spanning a
    non-final partial-stop close) plus a trailing still-open basket, then
    check both twins agree on the leg set, order, and (price, lots) --
    the fields they share -- while documenting exactly where they diverge
    on purpose."""
    from app.trade_report import _basket_legs
    from app.reports import _group_baskets
    from app import main
    db = main.app.state.db

    # `_basket_legs` is only ever called (in production) with `trade_id`
    # being the row JUST inserted -- its SQL has no upper bound, only a
    # lower one (`id > last_close`), so it must be called immediately after
    # each insert, before the next basket's rows exist, or it silently pulls
    # in later baskets' legs too. Mirror that call pattern here.

    # --- basket 1: single leg, opened and closed cleanly ---
    # htf_agree and ema200_agree deliberately DISAGREE with each other here
    # (1 vs 0) so a test that only checked one of them couldn't hide the
    # other silently dropping.
    b1_open = client.post("/trade-event", json=_trade(
        price=2300.0, lots=0.1, sl=2290.0, htf_agree=1, ema200_agree=0,
        reason="signal confirmed")).json()["id"]
    b1_close = client.post("/trade-event", json=_trade(
        event="close", price=2310.0, profit=10.0, ticket=101,
        reason="profit target")).json()["id"]
    legs1 = _basket_legs(db, b1_close)

    # --- basket 2: open + add, survives a non-final partial stop, then
    # closes for good ---
    b2_open = client.post("/trade-event", json=_trade(
        price=2400.0, lots=0.1, sl=2390.0, tp=2420.0, htf_agree=1,
        reason="signal confirmed", ticket=201)).json()["id"]
    b2_add = client.post("/trade-event", json=_trade(
        event="add", price=2405.0, lots=0.05, sl=2390.0, tp=2425.0,
        htf_agree=-1, reason="pyramid add", ticket=202)).json()["id"]
    client.post("/trade-event", json=_trade(
        event="close", price=2402.0, profit=-3.0, final=False,
        reason="partial stop", ticket=203))
    b2_close = client.post("/trade-event", json=_trade(
        event="close", price=2415.0, profit=20.0, ticket=204,
        reason="profit target")).json()["id"]
    legs2 = _basket_legs(db, b2_close)

    # --- basket 3: still open, no close row yet (trailing basket) ---
    b3_open = client.post("/trade-event", json=_trade(
        price=2500.0, lots=0.1, sl=2490.0, htf_agree=0,
        reason="signal confirmed", ticket=301)).json()["id"]
    legs3 = _basket_legs(db, b3_open)

    rows = _rows_for_group_baskets(db)
    baskets = _group_baskets(rows, cap=None)
    assert len(baskets) == 3, "one closed basket each for b1/b2, plus b3 trailing"

    b1 = next(b for b in baskets if b["entries"][0]["price"] == 2300.0)
    b2 = next(b for b in baskets if b["entries"][0]["price"] == 2400.0)
    b3 = next(b for b in baskets if b["entries"][0]["price"] == 2500.0)

    assert b1["exit"]["price"] == 2310.0
    assert b2["exit"]["price"] == 2415.0
    assert b3["exit"] is None, "trailing basket has no close row yet"

    for legs, basket, ids in (
        (legs1, b1, [b1_open]),
        (legs2, b2, [b2_open, b2_add]),
        (legs3, b3, [b3_open]),
    ):
        entries = basket["entries"]
        assert len(legs) == len(entries) == len(ids), (
            "leg count must agree between the twins")
        for leg, entry in zip(legs, entries):
            # --- the shared contract: same rows, in the same order, on the
            # fields both twins carry ---
            assert leg["price"] == entry["price"]
            assert leg["lots"] == entry["lots"]

    # basket 2's non-final partial stop must NOT appear as a third leg/entry
    # on either side, and its profit must still land in the basket P/L
    assert len(legs2) == 2
    assert len(b2["entries"]) == 2
    assert b2["pl"] == pytest.approx(-3.0 + 20.0)

    # --- documented divergence: each twin carries fields the other has no
    # reason to. This is the explicit assertion the shared-contract test
    # must make so the difference is a choice, not silent drift. ---
    leg_keys = {"price", "lots", "event", "sl", "tp"}
    entry_keys = {"ts", "price", "lots", "htf_agree", "ema200_agree", "news_blackout", "rsi_agree"}
    for legs in (legs1, legs2, legs3):
        for leg in legs:
            assert set(leg.keys()) == leg_keys
    for basket in (b1, b2, b3):
        for entry in basket["entries"]:
            assert set(entry.keys()) == entry_keys
        # basket-level display fields _basket_legs has no equivalent for:
        # it returns a flat list of legs, not a basket record.
        assert set(basket.keys()) >= {
            "direction", "entries", "exit", "pl", "entry_mode",
            "strategy_id", "reason"}
    # _basket_legs' legs carry sl/tp (chart-render backfill); _group_baskets'
    # entries carry ts/htf_agree/ema200_agree (report display) instead --
    # neither field crosses over, on purpose (see the TWIN WARNING comments).
    assert leg_keys - entry_keys == {"event", "sl", "tp"}
    assert entry_keys - leg_keys == {"ts", "htf_agree", "ema200_agree", "news_blackout", "rsi_agree"}

    # --- ema200_agree survives grouping just like htf_agree, and the two
    # verdicts are independent (basket 1 deliberately disagrees between
    # them) -- this is the regression _group_baskets once had for htf_agree
    # (dropping a field silently made every M15 cell render a dash); prove
    # the new field doesn't repeat it.
    from app.reports import _htf_flag, _ema200_flag
    assert _htf_flag(b1["entries"]) is True
    assert _ema200_flag(b1["entries"]) is False
