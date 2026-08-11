"""Forming-bar merge + snapshot render for the /chart command."""
import types

from app.chart_cmd import merge_forming_bar
from app.models import Candle
from app.render import render_snapshot_chart


def _candles(n=120, start_t=1000, step=300, base=4000.0):
    out = []
    for i in range(n):
        o = base + i
        out.append(Candle(t=start_t + i * step, o=o, h=o + 2, l=o - 2,
                          c=o + 1, v=10))
    return out


def _hb(bar_t, o=5000.0, h=5002.0, l=4998.0, c=5001.0):
    return types.SimpleNamespace(bar_t=bar_t, bar_o=o, bar_h=h, bar_l=l,
                                 bar_c=c)


def test_merge_appends_newer_forming_bar():
    candles = _candles(5)
    merged = merge_forming_bar(candles, _hb(candles[-1].t + 300))
    assert len(merged) == 6
    assert merged[-1].c == 5001.0
    assert len(candles) == 5          # input untouched


def test_merge_replaces_same_bar():
    candles = _candles(5)
    merged = merge_forming_bar(candles, _hb(candles[-1].t))
    assert len(merged) == 5
    assert merged[-1].c == 5001.0


def test_merge_noop_when_bar_t_zero_or_missing():
    candles = _candles(5)
    assert merge_forming_bar(candles, _hb(0)) is candles
    assert merge_forming_bar(candles, types.SimpleNamespace()) is candles


def test_merge_noop_when_older_than_last_closed():
    candles = _candles(5)
    assert merge_forming_bar(candles, _hb(candles[-1].t - 300)) is candles


def test_merge_noop_when_prices_zero():
    candles = _candles(5)
    assert merge_forming_bar(
        candles, _hb(candles[-1].t + 300, o=0.0, h=0.0, l=0.0, c=0.0)) is candles


def test_merge_noop_on_empty_candles():
    assert merge_forming_bar([], _hb(1000)) == []


def test_render_snapshot_writes_png(tmp_path):
    out = tmp_path / "snap.png"
    assert render_snapshot_chart(_candles(), str(out)) is True
    assert out.stat().st_size > 1000


def test_render_snapshot_with_positions(tmp_path):
    out = tmp_path / "snap_pos.png"
    positions = [types.SimpleNamespace(direction="BUY", open_price=4100.0,
                                       sl=4090.0, lots=0.02, profit=1.0,
                                       ticket=1)]
    assert render_snapshot_chart(_candles(), str(out), positions) is True
    assert out.stat().st_size > 1000


def test_render_snapshot_empty_candles_false(tmp_path):
    assert render_snapshot_chart([], str(tmp_path / "x.png")) is False


def test_heartbeat_model_accepts_old_and_new_payloads():
    from app.models import HeartbeatRequest
    old = HeartbeatRequest(equity=1.0, balance=1.0, floating_pl=0.0)
    assert old.bar_t == 0 and old.bar_o == 0.0
    new = HeartbeatRequest(equity=1.0, balance=1.0, floating_pl=0.0,
                           bar_t=123, bar_o=1.0, bar_h=2.0, bar_l=0.5,
                           bar_c=1.5)
    assert new.bar_t == 123 and new.bar_c == 1.5
