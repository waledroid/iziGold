import importlib

from fastapi.testclient import TestClient

from app.sr_zones import M5_ROWS_FOR_ZONES


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "sr.db"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "shots"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return TestClient(main.app), main


def test_overlays_carry_sr_zones_from_the_candles_table(tmp_path, monkeypatch):
    client, main = _client(tmp_path, monkeypatch)
    with client:
        t0 = 1_700_000_000 - (1_700_000_000 % 14400)
        rows = []
        for i in range(M5_ROWS_FOR_ZONES):
            px = 4000.0
            # i == 8000 (as in the brief) falls outside the H1/M15 400-bucket
            # trailing LOOKBACK windows (they only reach back ~4800/~1200 M5
            # bars) so those tiers would see no pivot at all; i == 14000 sits
            # inside all three tiers' windows (H4 needs the last 14400 bars,
            # H1 the last 4800, M15 the last 1200) -- verified against
            # sr_zones.zones_from_m5 directly before adopting this index.
            spike = (i == 14000)         # single bar: distinct highs after resampling
            rows.append({"t": t0 + 300 * i, "o": px, "h": (px + 40.0) if spike else px + 0.5,
                         "l": px - 0.5, "c": (px + 1.0) if spike else px, "v": 1.0})
        main.app.state.db.upsert_candles("XAUUSD", "M5", rows)
        # the overlays endpoint needs an accumulator to key its cache on
        from app.models import Candle
        main.app.state.recent_candles = {"symbol": "XAUUSD", "timeframe": "M5",
                                         "candles": [Candle(**r) for r in rows[-300:]]}
        body = client.get("/api/overlays", params={"strategy": "izi5"}).json()
        assert "sr_zones" in body
        assert {z["tier"] for z in body["sr_zones"]} == {"H4", "H1", "M15"}
        assert all(z["hi"] == 4040.0 for z in body["sr_zones"])


def test_overlays_sr_zones_fail_open_when_no_history(tmp_path, monkeypatch):
    client, main = _client(tmp_path, monkeypatch)
    with client:
        from app.models import Candle
        rows = [{"t": 900 + 300 * i, "o": 1, "h": 2, "l": 0, "c": 1, "v": 1} for i in range(10)]
        main.app.state.recent_candles = {"symbol": "XAUUSD", "timeframe": "M5",
                                         "candles": [Candle(**r) for r in rows]}
        body = client.get("/api/overlays", params={"strategy": "izi5"}).json()
        assert body["sr_zones"] == []
