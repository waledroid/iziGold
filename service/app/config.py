"""Centralized configuration module for XAU Assistant.

Unifies three sources of truth into a single coherent configuration layer:
1. Environment configuration (Settings / pydantic-settings): ports, keys, models, timeouts.
2. Shared strategy configuration (config/strategy.json): lanes (izi5, izi15), ATR multipliers, window.
3. Live runtime configuration (SQLite db): execution mode, entry mode, agreement toggles, AI switch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
STRATEGY_CONFIG_PATH = REPO_ROOT / "config" / "strategy.json"

# Historical pre-rename aliases
STRATEGY_ALIASES = {
    "halftrend_ema_v1": "iziGold5",
    "halftrend_m15_v1": "iziGold15",
    "izi5": "iziGold5",
    "izi15": "iziGold15",
}

LANE_DEFAULTS = {
    "amplitude": 4,
    "ema_len": 55,
    "stop_buffer_atr": 0.75,
    "confirm_clear_atr": 0.0,
    "profit_target_pct": 2.0,
    "confirm_closes": 2,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    forecaster: str = "chronos"          # chronos | timemoe | fake
    chronos_model: str = "amazon/chronos-bolt-small"
    horizon: int = 16
    context_len: int = 200
    mode: str = "grading"                # grading | veto
    confirm_threshold: float = 0.6
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    db_path: str = "xau_assistant.db"
    screenshot_dir: str = "screenshots"
    feed_key: str = ""
    # Mini-app HTTP port. Single source of truth for the live-chart
    # service, the Windows bridge push target, the ngrok forward
    # target and the watchdog probes. Default moved 9001 -> 9101 on
    # 2026-08-19: 9001 is a popular default (a Docker mosquitto
    # WebSocket listener owns it on the owner's machine) and the
    # collision cost the chart, the tunnel AND the watchdog.
    miniapp_port: int = 9101
    miniapp_health_url: str = ""
    miniapp_dev_bypass: bool = False
    miniapp_auth_max_age_s: int = 3600
    miniapp_public_url: str = ""
    # BotFather-registered direct link (t.me/<bot>/<shortname>) — works in
    # channels where web_app buttons cannot; falls back to the raw public URL.
    miniapp_direct_link: str = ""

    # --- DeepSeek supervisory agent (optional, modular) --------------------
    # The agent is an ASYNC layer on top of the strategy. When disabled, or
    # when the model is unreachable, every existing path behaves exactly as
    # before: the strategy decides, grading stays fail-open, trading runs
    # normally. Nothing here is on the synchronous /analyze path.
    agent_enabled: bool = False            # master switch; false = zero influence
    agent_level: str = "L0"                # L0 observer | L1 governor | L3 supervisory
    agent_provider: str = "opencode-go"    # informational label for the audit row
    agent_model: str = "deepseek-v4.1-flash"
    agent_base_url: str = ""               # OpenAI-compatible base, e.g. https://.../v1
    agent_api_key: str = ""
    agent_timeout_s: int = 30
    agent_temperature: float = 0.2
    # opencode-go requires a routing header (x-opencode-session). ANY stable
    # id works; empty = generated per service process.
    agent_session_header: str = "x-opencode-session"
    agent_session_id: str = ""
    agent_context_bars: int = 120          # candles sent per grade (token control)
    agent_supervisor_interval_s: int = 300 # state-watch cadence when idle
    # Hard bounds for autonomous actions (see app/agent_actions.py).
    agent_max_actions_per_hour: int = 4
    agent_max_actions_per_day: int = 10
    agent_allow_live_actions: bool = False # demo-only actions unless enabled
    agent_allow_lane_switch: bool = False  # lane/entry-mode changes off by default
    # Calibration gate: live actions require this much resolved evidence.
    agent_min_resolved: int = 50
    agent_min_accuracy_pct: float = 55.0
    agent_gate_bypass: bool = False        # explicit, audited escape hatch
    agent_lessons_max: int = 5             # lessons fed back per grade
    agent_prompt_version: str = "xau-agent-v2"

    # Live heartbeat interval (seconds) the service pushes to the EA. The
    # service is authoritative so a running EA cannot keep a stale saved input.
    heartbeat_sec: int = 1

    # Optional dashboard PIN protection for state mutations over public tunnels.
    # Empty string = no PIN required (fail-open for local development).
    dashboard_pin: str = ""


settings = Settings()


@lru_cache(maxsize=1)
def load_strategy_config() -> dict[str, Any]:
    """Load config/strategy.json, cached in memory."""
    try:
        with open(STRATEGY_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"shared": {}, "strategies": {}}


def clear_strategy_config_cache() -> None:
    """Clear cached strategy JSON (useful for hot-reload or test fixtures)."""
    load_strategy_config.cache_clear()


def get_shared_strategy_config() -> dict[str, Any]:
    """Return the shared block of strategy.json (risk, stops, window)."""
    return dict(load_strategy_config().get("shared", {}))


def get_lane_config(strategy_id: str) -> dict[str, Any]:
    """Return parameters for a specific strategy lane with defaults."""
    sid = STRATEGY_ALIASES.get(strategy_id, strategy_id)
    cfg = load_strategy_config()
    strategies = cfg.get("strategies", {})
    block = strategies.get(sid)
    if not block:
        res = dict(LANE_DEFAULTS)
        res["strategy_id"] = sid
        return res
    res = dict(LANE_DEFAULTS)
    res.update(cfg.get("shared", {}))
    res.update(block)
    res["strategy_id"] = sid
    return res


def calculate_adaptive_tier(equity: float) -> dict[str, Any]:
    """Compute adaptive hybrid sizing tier, limits, and max positions for $50 - $5,000 equity."""
    eq = float(equity) if equity and equity > 0 else 1000.0
    if eq <= 200.0:
        tier = "micro"
        max_lots = 0.01
        max_pos = 1
        desc = "Micro (≤$200): 0.01 lot, 1 pos, Instant BE lock"
    elif eq <= 500.0:
        tier = "mini"
        max_lots = 0.02
        max_pos = 2
        desc = "Mini ($200–$500): 0.01–0.02 lots, 2 pos, 50% bank + BE"
    elif eq <= 2000.0:
        tier = "core"
        max_lots = 0.05
        max_pos = 3
        desc = "Core ($500–$2,000): 0.02–0.05 lots (anchor 0.03), 3 pos, 50% bank + adds"
    else:
        tier = "pro"
        max_lots = min(max(round(eq * 0.000024, 2), 0.05), 0.15)
        max_pos = 3
        desc = f"Pro ($2,000+): up to {max_lots:.2f} lots, 3 pos, compounding"
    return {
        "tier": tier,
        "max_lots": max_lots,
        "max_pos": max_pos,
        "description": desc,
        "equity": round(eq, 2),
    }


def get_trading_window() -> tuple[int, int]:
    """Return (start_hour, end_hour) of server trading window."""
    shared = get_shared_strategy_config()
    start = int(shared.get("trading_window_start_hour", 4))
    end = int(shared.get("trading_window_end_hour", 22))
    return start, end


def is_weekend_market(dt_now: datetime | None = None) -> bool:
    """Return True if gold/traditional financial markets are closed for the weekend.

    Gold closes Friday ~22:00 UTC and reopens Sunday ~22:00 UTC.
    """
    if dt_now is None:
        dt_now = datetime.now(timezone.utc)
    elif dt_now.tzinfo is None:
        dt_now = dt_now.replace(tzinfo=timezone.utc)
    else:
        dt_now = dt_now.astimezone(timezone.utc)

    weekday = dt_now.weekday()
    hour = dt_now.hour

    if weekday == 4 and hour >= 22:
        return True
    if weekday == 5:
        return True
    if weekday == 6 and hour < 22:
        return True
    return False


def is_gold_active_window(dt_now: datetime | None = None) -> bool:
    """Return True if gold/traditional financial markets are within active trading hours.

    Gold is closed:
    1. During weekends (Friday 22:00 UTC to Sunday 22:00 UTC).
    2. Daily outside 04:00 - 22:00 server time.
    """
    if is_weekend_market(dt_now):
        return False
    if dt_now is None:
        dt_now = datetime.now(timezone.utc)
    elif dt_now.tzinfo is None:
        dt_now = dt_now.replace(tzinfo=timezone.utc)
    else:
        dt_now = dt_now.astimezone(timezone.utc)

    start_hour, end_hour = get_trading_window()
    hour = dt_now.hour
    if hour < start_hour or hour >= end_hour:
        return False
    return True


def get_market_status(symbol: str, dt_now: datetime | None = None) -> dict[str, Any]:
    """Return market open status, schedule description, and reopen info for a symbol."""
    sym = (symbol or "").upper()
    is_crypto = "BTC" in sym or "ETH" in sym or "CRYPTO" in sym
    if is_crypto:
        return {
            "symbol": symbol,
            "is_crypto": True,
            "open": True,
            "reason": "crypto_24_7",
            "reopens_at": None,
            "message": "Crypto 24/7 Market Active",
        }

    weekend = is_weekend_market(dt_now)
    if weekend:
        return {
            "symbol": symbol,
            "is_crypto": False,
            "open": False,
            "reason": "weekend_closed",
            "reopens_at": "Sunday 22:00 UTC",
            "message": "Market Closed for Weekend • Reopens Sunday 22:00 UTC",
        }

    if not is_gold_active_window(dt_now):
        start_hour, end_hour = get_trading_window()
        return {
            "symbol": symbol,
            "is_crypto": False,
            "open": False,
            "reason": "outside_window",
            "reopens_at": f"{start_hour:02d}:00 Server",
            "message": f"Gold Session Closed ({end_hour:02d}:00-{start_hour:02d}:00) • Auto-routed to 24/7 Bitcoin",
        }

    return {
        "symbol": symbol,
        "is_crypto": False,
        "open": True,
        "reason": "normal_trading",
        "reopens_at": None,
        "message": "Market Open",
    }


def get_unified_config(db: Any = None) -> dict[str, Any]:
    """Return unified configuration merging service, strategy, and runtime state."""
    strategy_cfg = load_strategy_config()
    svc_dict = settings.model_dump()
    runtime_dict = db.runtime_config() if db is not None and hasattr(db, "runtime_config") else {}

    # Build effective flattened configuration
    effective = dict(strategy_cfg.get("shared", {}))
    effective.update({
        "forecaster": settings.forecaster,
        "mode": settings.mode,
        "horizon": settings.horizon,
        "heartbeat_sec": settings.heartbeat_sec,
        "miniapp_port": settings.miniapp_port,
        "agent_enabled": settings.agent_enabled,
        "agent_model": settings.agent_model,
        "agent_level": settings.agent_level,
    })
    effective.update(runtime_dict)

    return {
        "service": svc_dict,
        "strategy": strategy_cfg,
        "runtime": runtime_dict,
        "effective": effective,
    }
