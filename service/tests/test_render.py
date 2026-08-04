import importlib

import pytest
from fastapi.testclient import TestClient

from app.render import render_trade_chart
from tests.fixtures import trend_candles


def _trade(**overrides):
    ev = {"event": "open", "strategy_id": "halftrend_ema_v1", "direction": "BUY",
          "lots": 0.1, "price": 2400.0, "sl": 2390.0, "reason": "signal confirmed"}
    ev.update(overrides)
    return ev


# ---------------------------------------------------------------------------
# render_trade_chart unit tests
# ---------------------------------------------------------------------------

def test_render_trade_chart_writes_nonempty_png(tmp_path):
    out_path = str(tmp_path / "render_1.png")
    ok = render_trade_chart(trend_candles(200), _trade(), out_path)
    assert ok is True

    import os
    assert os.path.exists(out_path)
    with open(out_path, "rb") as f:
        data = f.read()
    assert len(data) > 0
    assert data.startswith(b"\x89PNG")


def test_render_trade_chart_close_event(tmp_path):
    out_path = str(tmp_path / "render_close.png")
    ok = render_trade_chart(
        trend_candles(200), _trade(event="close", reason="tp hit"), out_path)
    assert ok is True

    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_render_trade_chart_returns_false_on_empty_candles(tmp_path):
    out_path = str(tmp_path / "render_empty.png")
    ok = render_trade_chart([], _trade(), out_path)
    assert ok is False


def test_render_trade_chart_returns_false_on_bad_out_path(tmp_path):
    # a directory that does not exist -> matplotlib savefig raises -> False,
    # no exception propagates
    bad_path = str(tmp_path / "does" / "not" / "exist" / "render.png")
    ok = render_trade_chart(trend_candles(200), _trade(), bad_path)
    assert ok is False


def test_render_trade_chart_with_overlays_and_sl_writes_nonempty_png(tmp_path):
    # 200 candles (full EA payload size) + a trade with sl>0 exercises the
    # HalfTrend/EMA overlay computation over the full series plus the
    # SL line/label path.
    out_path = str(tmp_path / "render_overlays.png")
    ok = render_trade_chart(trend_candles(200), _trade(sl=2390.0), out_path)
    assert ok is True

    import os
    assert os.path.exists(out_path)
    with open(out_path, "rb") as f:
        data = f.read()
    assert len(data) > 0
    assert data.startswith(b"\x89PNG")


def test_render_trade_chart_no_sl_still_renders(tmp_path):
    out_path = str(tmp_path / "render_no_sl.png")
    ok = render_trade_chart(trend_candles(200), _trade(sl=0.0), out_path)
    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_render_trade_chart_short_candle_series_does_not_raise(tmp_path):
    # Fewer candles than the HalfTrend warm-up window (amplitude=4) --
    # indicators should degrade to all-None overlays, not crash the render.
    out_path = str(tmp_path / "render_short.png")
    ok = render_trade_chart(trend_candles(3), _trade(), out_path)
    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "render.db"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "screenshots"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _analyze_payload(signal="BUY"):
    return {"symbol": "XAUUSD", "timeframe": "M15", "signal": signal,
            "candles": [c.model_dump() for c in trend_candles(200)]}


def test_trade_event_close_renders_chart_after_analyze(client):
    r = client.post("/analyze", json=_analyze_payload())
    assert r.status_code == 200

    trade_id = client.post(
        "/trade-event", json=_trade(event="close", reason="tp hit")).json()["id"]

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] is not None

    import os
    assert os.path.exists(row[0])
    with open(row[0], "rb") as f:
        assert f.read().startswith(b"\x89PNG")

    r = client.get(f"/ui/render/{trade_id}")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_trade_event_open_renders_chart_after_analyze(client):
    client.post("/analyze", json=_analyze_payload())

    trade_id = client.post("/trade-event", json=_trade(event="open")).json()["id"]

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] is not None

    r = client.get(f"/ui/render/{trade_id}")
    assert r.status_code == 200


def test_trade_event_without_prior_candles_still_200_no_render(client):
    trade_id = client.post(
        "/trade-event", json=_trade(event="close")).json()["id"]

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] is None

    r = client.get(f"/ui/render/{trade_id}")
    assert r.status_code == 404


def test_trade_event_render_prunes_to_retention_cap(client, tmp_path):
    import os
    import time

    shot_dir = tmp_path / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    for i in range(502):
        f = shot_dir / f"fake_{i}.png"
        f.write_bytes(b"x")
        os.utime(f, (now - 1000 + i, now - 1000 + i))

    client.post("/analyze", json=_analyze_payload())
    trade_id = client.post(
        "/trade-event", json=_trade(event="close", reason="tp hit")).json()["id"]

    remaining = list(shot_dir.glob("*.png"))
    assert len(remaining) == 500
    # the newly rendered chart (newest) must survive
    assert (shot_dir / f"render_{trade_id}.png") in remaining
    assert not (shot_dir / "fake_0.png").exists()
    assert (shot_dir / "fake_501.png").exists()


def test_ui_render_404_when_missing(client):
    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.get(f"/ui/render/{trade_id}")
    assert r.status_code == 404
    assert r.json()["detail"]


# ---------------------------------------------------------------------------
# Telegram send_photo of render
# ---------------------------------------------------------------------------

class _FakeTransport:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return self.result


def test_trade_event_close_sends_render_photo_when_telegram_configured(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    client.post("/trade-event", json=_trade(event="close", reason="tp hit"))

    photo_calls = [c for c in ft.calls if c[0] == "sendPhoto"]
    assert len(photo_calls) == 1
    _, payload, files = photo_calls[0]
    assert "render" in payload["caption"]
    assert files is not None


def test_trade_event_swallows_telegram_render_failure(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())

    class _BoomTransport:
        def __call__(self, method, payload, files=None):
            raise RuntimeError("boom")

    main.app.state.telegram = TelegramClient("tok", "555", transport=_BoomTransport())

    r = client.post("/trade-event", json=_trade(event="close"))
    assert r.status_code == 200
