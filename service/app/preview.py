"""Pre-entry preview — an approximate, display-only preview of the trade the
strategy would take if the current bar's setup holds.

The EA is the SOLE trading decision maker, so this is deliberately a
best-effort estimate computed service-side from the same primitives the
strategy uses (HalfTrend, the lane's trading EMA, ATR-14, the lane's stop
buffer and the shared profit target). It is never fed back into trading and
never asserts that a trade WILL happen — the frontends draw it only while the
owner enables the preview toggle (config/chart_spec.json `preview`).

Why approximate: the EA waits `confirm_closes` bars and enters at the next
bar's OPEN; this preview fires as soon as the latest CLOSED bar sits on the
trend's side of the EMA, and uses the latest close as the entry proxy. For an
exact call, the EA would have to report its armed/pending state on the
heartbeat (an MQL change) — see the unified-realtime-chart design doc.

Strategy parameters come from config/strategy.json, the same file the EA and
the backtest are pinned to (test_strategy_config.py), so the preview tracks
the live lanes instead of a second hardcoded copy. `profit_target_pct` may be
overridden by the caller with the live SQLite value (the operator can retune
it without recompiling the EA).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app import chart_spec
from app.indicators import ema, halftrend
from app.regime import last_atr

_STRATEGY_PATH = Path(__file__).resolve().parents[2] / "config" / "strategy.json"

# Pre-rename ids that still appear in historical signal/trade rows.
_ALIASES = {
    "halftrend_ema_v1": "iziGold5",
    "halftrend_m15_v1": "iziGold15",
    "izi5": "iziGold5",
    "izi15": "iziGold15",
}

_DEFAULTS = {"amplitude": 4, "ema_len": 55, "stop_buffer_atr": 0.75,
             "confirm_clear_atr": 0.0, "profit_target_pct": 2.0}


from app.config import load_strategy_config


def _strategy_config() -> dict:
    return load_strategy_config()


def lane_params(strategy_id: str) -> dict:
    """Structural params for the lane. Unknown ids fall back to the izi5-style
    defaults rather than raising (the preview is fail-open)."""
    sid = _ALIASES.get(strategy_id, strategy_id)
    cfg = _strategy_config()
    block = cfg.get("strategies", {}).get(sid)
    if not block:
        return dict(_DEFAULTS)
    shared = cfg.get("shared", {})
    return {
        "amplitude": int(block.get("ht_amplitude", 4)),
        "ema_len": int(block.get("ema_length", 55)),
        "stop_buffer_atr": float(block.get("stop_buffer_atr", 0.75)),
        "confirm_clear_atr": float(block.get("confirm_clear_atr", 0.0)),
        "profit_target_pct": float(shared.get("profit_target_pct", 2.0)),
    }


def build_preview(candles, strategy_id: str = "iziGold15",
                  profit_target_pct: float | None = None) -> dict | None:
    """Return the preview band, or None when no setup is visible.

    Shape:
        {"active": true, "direction": "BUY"|"SELL", "entry": float,
         "sl": float, "tp": float, "reason": str}
    Never raises; any bad/short input yields None.
    """
    try:
        p = lane_params(strategy_id)
        amplitude = p["amplitude"]
        ema_len = p["ema_len"]
        if len(candles) < max(amplitude + 2, ema_len + 1):
            return None

        ht = halftrend(candles, amplitude=amplitude)
        last_ht = ht[-1]
        if last_ht is None:
            return None
        _line_value, trend = last_ht

        closes = [c.c for c in candles]
        ema_series = ema(closes, ema_len)
        ema_last = ema_series[-1]
        if ema_last is None:
            return None

        atr = last_atr(candles, 14)
        if not atr or atr <= 0:
            return None

        close = closes[-1]
        clear = p["confirm_clear_atr"] * atr
        if trend == 0 and close > ema_last + clear:
            direction = "BUY"
        elif trend == 1 and close < ema_last - clear:
            direction = "SELL"
        else:
            return None

        entry = close
        window = candles[-amplitude:]
        if direction == "BUY":
            sl = min(c.l for c in window) - p["stop_buffer_atr"] * atr
        else:
            sl = max(c.h for c in window) + p["stop_buffer_atr"] * atr

        pct = profit_target_pct if profit_target_pct is not None else p["profit_target_pct"]
        if direction == "BUY":
            tp = entry * (1.0 + pct / 100.0)
        else:
            tp = entry * (1.0 - pct / 100.0)

        return {
            "active": True,
            "direction": direction,
            "entry": round(float(entry), 2),
            "sl": round(float(sl), 2),
            "tp": round(float(tp), 2),
            "reason": ("HalfTrend up, close above EMA%d" if direction == "BUY"
                       else "HalfTrend down, close below EMA%d") % ema_len,
        }
    except Exception:
        return None
