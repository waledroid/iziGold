import types
from app.config import (
    Settings,
    get_lane_config,
    get_shared_strategy_config,
    get_trading_window,
    get_unified_config,
    load_strategy_config,
)


def test_defaults(monkeypatch):
    # Isolate from environment when testing hardcoded defaults
    monkeypatch.delenv("FORECASTER", raising=False)
    monkeypatch.delenv("MODE", raising=False)
    monkeypatch.delenv("HORIZON", raising=False)
    monkeypatch.delenv("CONFIRM_THRESHOLD", raising=False)
    monkeypatch.delenv("CHRONOS_MODEL", raising=False)
    monkeypatch.delenv("CONTEXT_LEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("SCREENSHOT_DIR", raising=False)
    s = Settings(_env_file=None)
    assert s.forecaster == "chronos"
    assert s.mode == "grading"
    assert s.horizon == 16
    assert s.confirm_threshold == 0.6


def test_env_override(monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    assert Settings(_env_file=None).forecaster == "fake"


def test_miniapp_port_default_and_override(monkeypatch):
    # 9101, not 9001: the old default collides with common local services
    # (a Docker mosquitto WebSocket listener owns 9001 on the owner's box),
    # which on 2026-08-19 cost the chart, the tunnel and the watchdog.
    monkeypatch.delenv("MINIAPP_PORT", raising=False)
    assert Settings(_env_file=None).miniapp_port == 9101
    monkeypatch.setenv("MINIAPP_PORT", "9202")
    assert Settings(_env_file=None).miniapp_port == 9202


def test_load_strategy_config():
    cfg = load_strategy_config()
    assert "shared" in cfg
    assert "strategies" in cfg
    assert "iziGold5" in cfg["strategies"]
    assert "iziGold15" in cfg["strategies"]


def test_get_shared_strategy_config():
    shared = get_shared_strategy_config()
    assert "trading_window_start_hour" in shared
    assert "trading_window_end_hour" in shared
    assert "risk_per_trade_pct" in shared


def test_get_lane_config():
    lane5 = get_lane_config("iziGold5")
    assert lane5["strategy_id"] == "iziGold5"
    assert "ht_amplitude" in lane5 or "amplitude" in lane5

    lane15 = get_lane_config("iziGold15")
    assert lane15["strategy_id"] == "iziGold15"

    # Alias resolution
    legacy = get_lane_config("halftrend_ema_v1")
    assert legacy["strategy_id"] == "iziGold5"
    legacy_izi5 = get_lane_config("izi5")
    assert legacy_izi5["strategy_id"] == "iziGold5"

    # Unknown strategy falls back safely
    unknown = get_lane_config("custom_lane_x")
    assert unknown["strategy_id"] == "custom_lane_x"
    assert "profit_target_pct" in unknown


def test_get_trading_window():
    start, end = get_trading_window()
    assert isinstance(start, int)
    assert isinstance(end, int)
    assert start == 4
    assert end == 22


def test_get_unified_config():
    # Without db
    u = get_unified_config()
    assert "service" in u
    assert "strategy" in u
    assert "runtime" in u
    assert "effective" in u
    assert u["effective"]["trading_window_start_hour"] == 4

    # With mock db
    mock_db = types.SimpleNamespace(
        runtime_config=lambda: {
            "mode": "auto",
            "entry_mode": "fixed",
            "active_strategy": "izi15",
            "ai_enabled": True,
        }
    )
    u_db = get_unified_config(mock_db)
    assert u_db["runtime"]["mode"] == "auto"
    assert u_db["effective"]["mode"] == "auto"
    assert u_db["effective"]["ai_enabled"] is True
    assert u_db["effective"]["active_strategy"] == "izi15"
