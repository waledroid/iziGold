import importlib
import time
import types
import pytest
from fastapi.testclient import TestClient

from app.models import Candle, HeartbeatRequest, Position, TradeEventRequest
from app.tg.formatters import _ea_connection_line, _format_status, format_proposal, format_report
from app.trade_report import _pl_message


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "multi.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_format_proposal_badging():
    resp = types.SimpleNamespace(
        direction="bullish", confidence=0.85, verdict="confirm",
        ai_available=True, regime="trend"
    )
    # Gold default
    gold_text = format_proposal("entry", "BUY", 2650.0, resp, symbol="XAUUSD")
    assert "📥 Entry proposal: BUY @ 2650.0" in gold_text

    # Bitcoin badging
    btc_text = format_proposal("entry", "BUY", 68500.0, resp, symbol="BTCUSD")
    assert "📥 Entry proposal [₿ BTCUSD]: BUY @ 68500.0" in btc_text


def test_format_report_badging():
    req_gold = types.SimpleNamespace(symbol="XAUUSD", timeframe="M15", signal="BUY")
    req_btc = types.SimpleNamespace(symbol="BTCUSD", timeframe="M15", signal="BUY")
    resp = types.SimpleNamespace(
        direction="bullish", confidence=0.85, verdict="confirm",
        ai_available=True, regime="trend", mode="grading"
    )
    rep_gold = format_report(req_gold, resp)
    assert rep_gold.startswith("🥇 XAUUSD M15 — BUY")

    rep_btc = format_report(req_btc, resp)
    assert rep_btc.startswith("₿ BTCUSD M15 — BUY")


def test_pl_message_badging():
    gold_msg = _pl_message(150.0, "BUY", None, 0.0, symbol="XAUUSD")
    assert gold_msg == "💰 [🥇 XAU] Trade closed: +$150.00 profit"

    btc_msg = _pl_message(250.0, "BUY", None, 0.0, symbol="BTCUSD")
    assert btc_msg == "💰 [₿ BTC] Trade closed: +$250.00 profit"


def test_multi_asset_heartbeat_and_status(client: TestClient):
    now = time.time()
    # Heartbeat from Gold EA
    hb_gold = HeartbeatRequest(
        symbol="XAUUSD",
        equity=10500.0,
        balance=10000.0,
        floating_pl=500.0,
        positions=[Position(ticket=101, direction="BUY", symbol="XAUUSD", lots=0.03, open_price=2650.0, sl=2640.0, profit=500.0)],
        kill_switch=False,
        hwm=10500.0,
        exposure_min=30,
        window_open=True,
        spread_points=15.0,
        active_strategy="izi15",
        algo_trading=True,
    )
    res_gold = client.post("/heartbeat", json=hb_gold.model_dump())
    assert res_gold.status_code == 200

    # Heartbeat from Bitcoin EA
    hb_btc = HeartbeatRequest(
        symbol="BTCUSD",
        equity=10500.0,
        balance=10000.0,
        floating_pl=120.0,
        positions=[Position(ticket=202, direction="BUY", symbol="BTCUSD", lots=0.10, open_price=68000.0, sl=67000.0, profit=120.0)],
        kill_switch=False,
        hwm=10500.0,
        exposure_min=10,
        window_open=True,
        spread_points=50.0,
        active_strategy="iziBit15",
        algo_trading=True,
    )
    res_btc = client.post("/heartbeat", json=hb_btc.model_dump())
    assert res_btc.status_code == 200

    # Verify both tracked in state
    assert "XAUUSD" in client.app.state.latest_heartbeats
    assert "BTCUSD" in client.app.state.latest_heartbeats

    # Connection line displays both symbols
    conn = _ea_connection_line(client.app)
    assert "EA: 🟢 connected" in conn
    assert "🥇" in conn and "XAUUSD" in conn
    assert "₿" in conn and "BTCUSD" in conn

    # Status summary shows both strategies and positions
    status_text = _format_status(client.app)
    assert "[🥇 XAUUSD]" in status_text
    assert "[₿ BTCUSD]" in status_text
    assert "izi15" in status_text
    assert "iziBit15" in status_text
    assert "#101 BUY [🥇 XAUUSD]" in status_text
    assert "#202 BUY [₿ BTCUSD]" in status_text


def test_multi_asset_trades_isolation(client: TestClient):
    db = client.app.state.db

    # Record trade events for Gold and Bitcoin
    tr_gold = TradeEventRequest(
        event="open",
        symbol="XAUUSD",
        strategy_id="izi15",
        direction="BUY",
        lots=0.03,
        price=2650.0,
        ticket=301,
    )
    res1 = client.post("/trade-event", json=tr_gold.model_dump())
    assert res1.status_code == 200

    tr_btc = TradeEventRequest(
        event="open",
        symbol="BTCUSD",
        strategy_id="iziBit15",
        direction="BUY",
        lots=0.05,
        price=68000.0,
        ticket=302,
    )
    res2 = client.post("/trade-event", json=tr_btc.model_dump())
    assert res2.status_code == 200

    # Query trades by symbol
    gold_trades = db.recent_trades(symbol="XAUUSD")
    btc_trades = db.recent_trades(symbol="BTCUSD")

    assert any(t["ticket"] == 301 for t in gold_trades)
    assert not any(t["ticket"] == 302 for t in gold_trades)

    assert any(t["ticket"] == 302 for t in btc_trades)
    assert not any(t["ticket"] == 301 for t in btc_trades)


def test_ui_state_and_endpoints_multi_asset(client: TestClient):
    # Post heartbeats for both
    hb_gold = HeartbeatRequest(
        symbol="XAUUSD", equity=10000.0, balance=10000.0, floating_pl=150.0,
        positions=[Position(ticket=11, direction="BUY", symbol="XAUUSD", lots=0.03, open_price=2650.0, sl=2640.0, profit=150.0)],
        kill_switch=False, hwm=10000.0, exposure_min=10, window_open=True, spread_points=12.0, active_strategy="izi15",
    )
    hb_btc = HeartbeatRequest(
        symbol="BTCUSD", equity=10000.0, balance=10000.0, floating_pl=-50.0,
        positions=[Position(ticket=22, direction="SELL", symbol="BTCUSD", lots=0.05, open_price=68000.0, sl=68500.0, profit=-50.0)],
        kill_switch=False, hwm=10000.0, exposure_min=5, window_open=True, spread_points=40.0, active_strategy="iziBit15",
    )
    client.post("/heartbeat", json=hb_gold.model_dump())
    client.post("/heartbeat", json=hb_btc.model_dump())

    # GET /api/state returns both heartbeats and all_positions
    r = client.get("/api/state")
    assert r.status_code == 200
    st = r.json()
    assert "heartbeats" in st
    assert "XAUUSD" in st["heartbeats"]
    assert "BTCUSD" in st["heartbeats"]
    assert len(st["all_positions"]) == 2

    # Insert sample candles for BTC in DB
    from tests.fixtures import trend_candles
    candles = [c.model_dump() for c in trend_candles(60)]
    client.app.state.db.upsert_candles("BTCUSD", "M15", [Candle(**c) for c in candles])

    # GET /api/candles with symbol=BTCUSD
    r_c = client.get("/api/candles?symbol=BTCUSD&timeframe=M15")
    assert r_c.status_code == 200
    cd = r_c.json()
    assert cd["symbol"] == "BTCUSD"
    assert len(cd["candles"]) > 0

    # GET /api/overlays with strategy=iziBit15
    r_ov = client.get("/api/overlays?strategy=iziBit15&symbol=BTCUSD")
    assert r_ov.status_code == 200
    ov = r_ov.json()
    assert "halftrend" in ov
    assert "ema55" in ov

    # POST /api/proposal/quick for BTC
    r_q = client.post("/api/proposal/quick", json={
        "direction": "BUY", "strategy_id": "iziBit15", "price": 68500.0, "symbol": "BTCUSD"
    })
    assert r_q.status_code == 200
    assert r_q.json()["ok"] is True


def test_telegram_multi_asset_trade_deck_and_execution(client: TestClient):
    from app.tg.callbacks import _cb_mtrade, _cb_strat, _cb_exitnow
    from app.tg.commands import _cmd_trade, _manual_entry_guard
    from app.tg.formatters import _format_mode

    app = client.app
    db = app.state.db

    # Both EAs connected
    hb_gold = HeartbeatRequest(
        symbol="XAUUSD", equity=10000.0, balance=10000.0, floating_pl=0.0,
        positions=[], kill_switch=False, hwm=10000.0, exposure_min=0,
        window_open=True, spread_points=12.0, active_strategy="izi15", bar_c=2650.0,
    )
    hb_btc = HeartbeatRequest(
        symbol="BTCUSD", equity=10000.0, balance=10000.0, floating_pl=0.0,
        positions=[], kill_switch=False, hwm=10000.0, exposure_min=0,
        window_open=True, spread_points=40.0, active_strategy="iziBit15", bar_c=68500.0,
    )
    client.post("/heartbeat", json=hb_gold.model_dump())
    client.post("/heartbeat", json=hb_btc.model_dump())

    # 1. /trade command returns dual-asset buttons
    text, kb_dict = _cmd_trade(app, ["/trade"], redacted=False)
    assert "Instant Manual Order Deck" in text
    assert "Gold: $2650.00" in text
    assert "BTC: $68,500.0" in text
    buttons = [b["text"] for row in kb_dict["inline_keyboard"] for b in row]
    assert "🥇 BUY Gold" in buttons
    assert "🥇 SELL Gold" in buttons
    assert "₿ BUY Bitcoin" in buttons
    assert "₿ SELL Bitcoin" in buttons

    # 2. Tap BUY Bitcoin button -> queues Bitcoin entry
    reply, toast = _cb_mtrade(["mtrade", "BTCUSD", "BUY"], app, db, message_id=123)
    assert "₿ BTCUSD BUY @ 68500.00" in reply
    assert "BUY BTCUSD queued" in toast
    prop = db.pending_proposal(kind="entry", status="approved")
    assert prop is not None
    assert prop["strategy_id"] == "iziBit15"
    assert prop["direction"] == "BUY"

    # Consume proposal
    db.set_proposal_status(prop["id"], "executed", expected="approved")

    # 3. Gold open position does NOT block Bitcoin
    hb_gold_with_pos = HeartbeatRequest(
        symbol="XAUUSD", equity=10050.0, balance=10000.0, floating_pl=50.0,
        positions=[Position(ticket=101, direction="BUY", symbol="XAUUSD", lots=0.03, open_price=2650.0, sl=2640.0, profit=50.0)],
        kill_switch=False, hwm=10050.0, exposure_min=5, window_open=True, spread_points=12.0, active_strategy="izi15",
    )
    client.post("/heartbeat", json=hb_gold_with_pos.model_dump())

    # Gold is guarded
    assert _manual_entry_guard(app, db, symbol="XAUUSD") is not None
    # Bitcoin is NOT guarded
    assert _manual_entry_guard(app, db, symbol="BTCUSD") is None

    # 4. /mode displays both Gold and Bitcoin lanes
    mode_text, mode_kb = _format_mode(app)
    assert "🥇 Gold: izi15" in mode_text
    assert "₿ Bitcoin: iziBit15" in mode_text
    mode_callbacks = [b["callback_data"] for row in mode_kb["inline_keyboard"] for b in row]
    assert "strat:iziGold5" in mode_callbacks
    assert "strat:iziGold15" in mode_callbacks
    assert "strat:iziBit5" in mode_callbacks
    assert "strat:iziBit15" in mode_callbacks

    # 5. Switch Bitcoin lane via strat:iziBit5
    strat_text, strat_toast = _cb_strat(["strat", "iziBit5"], app, db, message_id=456)
    assert "Switching ₿ Bitcoin to iziBit5" in strat_text

    # Heartbeat from Gold EA does NOT receive switch to iziBit5
    r_hb_gold = client.post("/heartbeat", json=hb_gold.model_dump())
    assert r_hb_gold.json()["switch_to"] != "iziBit5"

    # Heartbeat from BTC EA receives switch to iziBit5
    r_hb_btc = client.post("/heartbeat", json=hb_btc.model_dump())
    assert r_hb_btc.json()["switch_to"] == "iziBit5"


def test_single_source_multi_asset_report(client: TestClient):
    from app.telegram import handle_callback, handle_command

    db = client.app.state.db
    # Insert closed Gold and Bitcoin baskets today
    db.insert_trade({"event": "open", "symbol": "XAUUSD", "strategy_id": "izi15",
                     "direction": "BUY", "lots": 0.03, "price": 2650.0, "ticket": 1001})
    db.insert_trade({"event": "close", "symbol": "XAUUSD", "strategy_id": "izi15",
                     "direction": "BUY", "lots": 0.03, "price": 2665.0, "profit": 45.0,
                     "final": True, "reason": "profit target", "ticket": 1001})

    db.insert_trade({"event": "open", "symbol": "BTCUSD", "strategy_id": "iziBit15",
                     "direction": "SELL", "lots": 0.01, "price": 68500.0, "ticket": 2001})
    db.insert_trade({"event": "close", "symbol": "BTCUSD", "strategy_id": "iziBit15",
                     "direction": "SELL", "lots": 0.01, "price": 68200.0, "profit": 30.0,
                     "final": True, "reason": "profit target", "ticket": 2001})

    # 1. Telegram /report command shows Today, This Week, and This Month with Gold & BTC split
    out = handle_command("/report", client.app)
    assert isinstance(out, tuple)
    text, kb_markup = out
    assert "📊 Performance Report" in text
    assert "📅 Today: +$75.00 (2W / 0L · 100%)" in text
    assert "🥇 Gold: +$45.00 (1)" in text
    assert "₿ BTC: +$30.00 (1)" in text
    assert "🗓 This Week:" in text
    assert "📆 This Month:" in text

    # 2. Inline callback nav:report works
    cb_text, cb_toast = handle_callback("nav:report", client.app)
    assert "📊 Performance Report" in cb_text
    assert cb_toast == "report"

    # 3. WebApp /reports page and /api/report endpoint on localhost:9000
    r_page = client.get("/reports")
    assert r_page.status_code == 200
    assert 'id="repPeriods"' in r_page.text

    r_api = client.get("/api/report")
    assert r_api.status_code == 200
    body = r_api.json()
    assert "periods" in body
    assert body["periods"]["today"]["pl"] == 75.0
    assert body["periods"]["today"]["gold"]["pl"] == 45.0
    assert body["periods"]["today"]["btc"]["pl"] == 30.0


def test_telegram_play_stop_toggles(client: TestClient):
    from app.telegram import handle_command
    from app.models import HeartbeatRequest, Position

    db = client.app.state.db
    # Initial state: unpaused
    assert not db.entries_paused("XAUUSD")
    assert not db.entries_paused("BTCUSD")

    # 1. /start returns the reply keyboard with 2 Big Stop buttons
    start_res = handle_command("/start", client.app)
    assert isinstance(start_res, tuple)
    start_text, start_kb = start_res
    assert "IZIGOLD" in start_text
    row0 = start_kb["keyboard"][0]
    assert row0[0]["text"] == "🥇 Gold: ⏹️ STOP"
    assert row0[1]["text"] == "₿ BTC: ⏹️ STOP"

    # 2. Attach active position to XAUUSD
    hb_gold = HeartbeatRequest(
        symbol="XAUUSD",
        equity=10000.0,
        balance=10000.0,
        floating_pl=10.0,
        kill_switch=False,
        hwm=10000.0,
        exposure_min=0,
        window_open=True,
        spread_points=15.0,
        active_strategy="izi15",
        positions=[Position(ticket=999, direction="BUY", lots=0.03, open_price=2650.0, sl=2640.0, profit=10.0, magic=20260729)]
    )
    resp = client.post("/heartbeat", json=hb_gold.model_dump())
    assert resp.status_code == 200

    # Trigger STOP for Gold via button text
    stop_res = handle_command("🥇 Gold: ⏹️ STOP", client.app)
    assert isinstance(stop_res, tuple)
    stop_text, stop_kb = stop_res
    assert "XAUUSD HALTED" in stop_text
    assert "Liquidating 1 open position" in stop_text
    assert db.entries_paused("XAUUSD")
    assert not db.entries_paused("BTCUSD")
    # Verify button flipped to PLAY
    assert stop_kb["keyboard"][0][0]["text"] == "🥇 Gold: ▶️ PLAY"
    assert stop_kb["keyboard"][0][1]["text"] == "₿ BTC: ⏹️ STOP"

    # Verify exit proposal was created and approved
    prop = db.pending_proposal(kind="exit", status="approved")
    assert prop is not None
    assert prop["strategy_id"] == "izi15"

    # Trigger PLAY for Gold
    play_res = handle_command("🥇 Gold: ▶️ PLAY", client.app)
    assert isinstance(play_res, tuple)
    play_text, play_kb = play_res
    assert "XAUUSD RESUMED" in play_text
    assert not db.entries_paused("XAUUSD")
    assert play_kb["keyboard"][0][0]["text"] == "🥇 Gold: ⏹️ STOP"

    # 3. Test Bitcoin STOP toggle via button text
    stop_btc_res = handle_command("₿ BTC: ⏹️ STOP", client.app)
    assert isinstance(stop_btc_res, tuple)
    s_btc_text, s_btc_kb = stop_btc_res
    assert "BTCUSD HALTED" in s_btc_text
    assert db.entries_paused("BTCUSD")
    assert s_btc_kb["keyboard"][0][0]["text"] == "🥇 Gold: ⏹️ STOP"
    assert s_btc_kb["keyboard"][0][1]["text"] == "₿ BTC: ▶️ PLAY"

    # Resume Bitcoin
    play_btc_res = handle_command("₿ BTC: ▶️ PLAY", client.app)
    assert isinstance(play_btc_res, tuple)
    p_btc_text, p_btc_kb = play_btc_res
    assert "BTCUSD RESUMED" in p_btc_text
    assert not db.entries_paused("BTCUSD")
    assert p_btc_kb["keyboard"][0][1]["text"] == "₿ BTC: ⏹️ STOP"


def test_ticker_multi_asset_isolation():
    from app.ticker import ticker_tick, TickerState, _get_ticker_state
    from app.telegram import TelegramClient

    calls = []
    def fake_transport(method, payload, files=None):
        calls.append((method, payload))
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 100 + len(calls)}}
        return {"ok": True, "result": {}}

    class MockApp:
        class State:
            pass
        state = State()

    app = MockApp()
    app.state.telegram = TelegramClient("tok", "123", transport=fake_transport)
    app.state.ticker = TickerState()
    app.state.ticker_db_path = None
    app.state.db = type("MockDb", (), {"exec_mode": lambda: "auto", "get_kv": lambda k: None})()

    hb_gold_open = types.SimpleNamespace(
        symbol="XAUUSD",
        equity=5000.0,
        floating_pl=10.0,
        positions=[types.SimpleNamespace(direction="SELL", lots=0.03, open_price=2650.0, profit=10.0)]
    )
    hb_btc_flat = types.SimpleNamespace(
        symbol="BTCUSD",
        equity=5000.0,
        floating_pl=0.0,
        positions=[]
    )

    # 1. Gold opens a position -> sends 1 message
    ticker_tick(app, hb_gold_open, now=1000.0)
    assert len(calls) == 1
    assert calls[0][0] == "sendMessage"
    assert "LIVE" in calls[0][1]["text"]
    gold_st = _get_ticker_state(app, "XAUUSD")
    assert gold_st.owner_msg_id == 101

    # 2. Bitcoin beats flat -> MUST NOT edit Gold's message or wipe Gold's state
    ticker_tick(app, hb_btc_flat, now=1001.0, previous=(1000.0, hb_gold_open))
    # Calls count should still be 1 (no message sent or edited because BTC has no open positions and its state is None)
    assert len(calls) == 1
    gold_st_after = _get_ticker_state(app, "XAUUSD")
    assert gold_st_after.owner_msg_id == 101

    # 3. Gold beats again -> still open, no new message sent (only edit if throttled, but < 5s so no call)
    ticker_tick(app, hb_gold_open, now=1002.0)
    assert len(calls) == 1
    assert gold_st_after.owner_msg_id == 101


def test_stream_and_basket_multi_asset_isolation(client: TestClient):
    # Test /api/basket with symbol
    resp_gold = client.get("/api/basket?symbol=XAUUSD")
    assert resp_gold.status_code == 200
    assert resp_gold.json()["symbol"] == "XAUUSD"

    resp_btc = client.get("/api/basket?symbol=BTCUSD")
    assert resp_btc.status_code == 200
    assert resp_btc.json()["symbol"] == "BTCUSD"




