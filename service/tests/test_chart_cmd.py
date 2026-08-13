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


import asyncio
import pathlib

from app.telegram import PINNED_HELP_VERSION, TelegramClient, format_pinned_help


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return {"ok": True, "result": {"message_id": 1}}

    def of(self, method):
        return [c for c in self.calls if c[0] == method]


class _Db:
    def __init__(self, channel_id=""):
        self._c = channel_id

    def get_kv(self, key):
        return self._c if key == "channel_id" else None


def _snap_app(tmp_path, candles, hb=None, hb_age=0.0, channel_id=""):
    import time as _time
    from app import main as app_main
    transport = FakeTransport()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        telegram=TelegramClient("tok", "555", transport=transport),
        recent_candles=({"symbol": "XAUUSD", "timeframe": "M5",
                         "candles": candles} if candles else None),
        latest_heartbeat=((_time.time() - hb_age, hb) if hb is not None else None),
        screenshot_dir=pathlib.Path(tmp_path),
        db=_Db(channel_id)))
    return app, transport, app_main


def _full_hb(bar_t, positions=()):
    from app.models import HeartbeatRequest
    return HeartbeatRequest(equity=1000.0, balance=1000.0, floating_pl=0.0,
                            positions=list(positions), bar_t=bar_t,
                            bar_o=5000.0, bar_h=5002.0, bar_l=4998.0,
                            bar_c=5001.0)


def test_chart_sends_photo_with_caption(tmp_path):
    candles = _candles()
    app, t, m = _snap_app(tmp_path, candles,
                          hb=_full_hb(candles[-1].t + 300))
    asyncio.run(m._send_chart_snapshot(app))
    photos = t.of("sendPhoto")
    assert len(photos) == 1
    assert photos[0][1]["chat_id"] == "555"
    assert "XAUUSD" in photos[0][1]["caption"]
    assert "closed bars only" not in photos[0][1]["caption"]


def test_chart_no_candles_replies_text(tmp_path):
    app, t, m = _snap_app(tmp_path, candles=None)
    asyncio.run(m._send_chart_snapshot(app))
    assert t.of("sendPhoto") == []
    assert "no candles yet" in t.of("sendMessage")[0][1]["text"]


def test_chart_stale_heartbeat_notes_closed_bars_only(tmp_path):
    candles = _candles()
    app, t, m = _snap_app(tmp_path, candles,
                          hb=_full_hb(candles[-1].t + 300), hb_age=120.0)
    asyncio.run(m._send_chart_snapshot(app))
    assert "closed bars only" in t.of("sendPhoto")[0][1]["caption"]


def test_chart_mirrors_photo_to_channel_owner_first(tmp_path):
    candles = _candles()
    app, t, m = _snap_app(tmp_path, candles,
                          hb=_full_hb(candles[-1].t + 300),
                          channel_id="-1001234")
    asyncio.run(m._send_chart_snapshot(app))
    photos = t.of("sendPhoto")
    assert [p[1]["chat_id"] for p in photos] == ["555", "-1001234"]
    assert photos[1][1]["caption"].startswith("👤 /chart")
    assert "reply_markup" not in photos[1][1]


def test_pinned_help_lists_chart_and_version_bumped():
    assert "/chart" in format_pinned_help()
    assert PINNED_HELP_VERSION == "6"
