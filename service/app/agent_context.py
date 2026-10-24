"""Strategy facts for the supervisory agent.

The agent's first live grades were made from raw closes and prose, which is
why it read the tape generically. This module computes the SAME facts the
strategy itself trades on — HalfTrend state and the strict 3-bar window, the
EMA stack in ATR units, ADX/RSI/MACD, ATR percentile, spread history and the
nearest S/R walls — so the agent can judge setup quality against the actual
system instead of guessing.

Pure and defensive by design: any failure returns {} so a grading context can
lose its facts but never the grade itself.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.indicators import ema, halftrend, macd, rsi
from app.regime import adx_series, atr_series, classify_regime

ROOT = Path(__file__).resolve().parents[2]
STRATEGY_JSON = ROOT / "config" / "strategy.json"

# Lane defaults if the shared config cannot be read: the M5 lane waits 2 bars.
_DEFAULT_CONFIRM = {"iziGold5": 2, "iziGold15": 1, "iziBit5": 2, "iziBit15": 1, "izi5": 2, "izi15": 1}


def _r(value, digits=2):
    try:
        if value is None:
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


from app.config import load_strategy_config


def _strategy_params() -> dict:
    return load_strategy_config()


def confirm_closes(strategy_id: str) -> int:
    """Waiting bars for the lane, from the shared strategy config."""
    params = _strategy_params().get("strategies", {})
    block = params.get(strategy_id) or {}
    value = block.get("confirm_closes")
    if isinstance(value, (int, float)):
        return int(value)
    return _DEFAULT_CONFIRM.get(strategy_id, 2)


def strict_window_state(candles, confirm: int) -> dict:
    """Where the current bar sits in the strategy's strict entry window.

    flip (HalfTrend arrow) -> `confirm` waiting bars -> entry bar, taken at the
    entry bar's OPEN and only if it is on the trend's side of the EMA. Anything
    after that is dead until the next flip (that is the 2026-08-17 rule)."""
    lines = halftrend(candles, amplitude=4)
    defined = [i for i, v in enumerate(lines) if v is not None]
    if not defined:
        return {"state": "unknown", "bars_since_flip": None}
    trend = lines[defined[-1]][1]
    flip_idx = defined[0]
    for i in defined:
        if lines[i][1] != trend:
            flip_idx = i
    bars_since_flip = len(candles) - 1 - flip_idx
    if bars_since_flip < confirm:
        state = "waiting"
    elif bars_since_flip == confirm:
        state = "entry_bar_next_open"
    else:
        state = "missed_until_next_flip"
    return {"state": state, "bars_since_flip": bars_since_flip,
            "halftrend_trend": "up" if trend == 0 else "down",
            "flip_bar_index": flip_idx}


def compute_volume_profile(candles, num_bins: int = 28, value_area_pct: float = 0.70) -> dict:
    """Computes Volume Profile: Point of Control (POC), Value Area (VAH, VAL),
    and relative positioning for the current price."""
    try:
        if not candles or len(candles) < 20:
            return {}
        highs = [float(c.h) for c in candles]
        lows = [float(c.l) for c in candles]
        volumes = [float(getattr(c, "v", 1.0) or 1.0) for c in candles]

        min_p = min(lows)
        max_p = max(highs)
        if max_p <= min_p:
            return {}

        bin_size = (max_p - min_p) / num_bins
        if bin_size <= 0:
            return {}

        bins = [0.0] * num_bins
        buy_bins = [0.0] * num_bins
        sell_bins = [0.0] * num_bins
        total_vol = 0.0

        for c in candles:
            h = float(c.h)
            l = float(c.l)
            o = float(c.o)
            cl = float(c.c)
            v = float(getattr(c, "v", 1.0) or 1.0)
            if v <= 0:
                v = 1.0
            total_vol += v
            is_buy = (cl >= o)
            start_b = max(0, min(num_bins - 1, int((l - min_p) / bin_size)))
            end_b = max(0, min(num_bins - 1, int((h - min_p) / bin_size)))
            span = max(1, end_b - start_b + 1)
            vol_per_b = v / span
            for b in range(start_b, end_b + 1):
                bins[b] += vol_per_b
                if is_buy:
                    buy_bins[b] += vol_per_b
                else:
                    sell_bins[b] += vol_per_b

        if total_vol <= 0:
            return {}

        poc_idx = max(range(num_bins), key=lambda i: bins[i])
        poc_price = min_p + (poc_idx + 0.5) * bin_size

        target_vol = total_vol * value_area_pct
        cur_vol = bins[poc_idx]
        low_idx, high_idx = poc_idx, poc_idx

        while cur_vol < target_vol and (low_idx > 0 or high_idx < num_bins - 1):
            next_low_vol = bins[low_idx - 1] if low_idx > 0 else -1.0
            next_high_vol = bins[high_idx + 1] if high_idx < num_bins - 1 else -1.0

            if next_high_vol >= next_low_vol and high_idx < num_bins - 1:
                high_idx += 1
                cur_vol += bins[high_idx]
            elif low_idx > 0:
                low_idx -= 1
                cur_vol += bins[low_idx]
            else:
                break

        val_price = min_p + low_idx * bin_size
        vah_price = min_p + (high_idx + 1) * bin_size

        curr_price = float(candles[-1].c)
        if curr_price > vah_price:
            location = "above_vah"
        elif curr_price < val_price:
            location = "below_val"
        elif abs(curr_price - poc_price) <= bin_size * 1.5:
            location = "at_poc"
        else:
            location = "inside_value_area"

        bin_list = []
        max_b_vol = max(bins) if bins else 1.0
        for b_i in range(num_bins):
            b_low = min_p + b_i * bin_size
            b_high = b_low + bin_size
            b_tot = bins[b_i]
            b_buy = buy_bins[b_i]
            b_sell = sell_bins[b_i]
            tot_ratio = (b_tot / max_b_vol) if max_b_vol > 0 else 0.0
            buy_ratio = (b_buy / b_tot) if b_tot > 0 else 0.5
            bin_list.append({
                "price": _r(b_low + bin_size * 0.5),
                "low": _r(b_low),
                "high": _r(b_high),
                "vol": _r(b_tot, 1),
                "buy_vol": _r(b_buy, 1),
                "sell_vol": _r(b_sell, 1),
                "pct": _r(tot_ratio, 3),
                "buy_pct": _r(tot_ratio * buy_ratio, 3),
                "sell_pct": _r(tot_ratio * (1.0 - buy_ratio), 3),
                "in_va": (low_idx <= b_i <= high_idx),
            })

        return {
            "poc": _r(poc_price),
            "vah": _r(vah_price),
            "val": _r(val_price),
            "price_vs_poc": _r(curr_price - poc_price),
            "location": location,
            "bins": bin_list,
        }
    except Exception:
        return {}


def compute_mtf_alignment(candles, db=None) -> dict:
    """Computes multi-timeframe trend alignment across M5, M15, and H1."""
    try:
        if not candles or len(candles) < 30:
            return {}
        closes = [float(c.c) for c in candles]
        curr_price = closes[-1]

        def _last_val(seq):
            for v in reversed(seq):
                if v is not None:
                    return v
            return None

        # M5 bias
        m5_e = _last_val(ema(closes, min(55, len(closes))))
        m5_bias = "BULLISH" if (m5_e is not None and curr_price >= m5_e) else "BEARISH"

        # M15 bias (sample every 3rd candle)
        m15_closes = closes[2::3] if len(closes) >= 6 else closes
        m15_e = _last_val(ema(m15_closes, min(20, len(m15_closes))))
        m15_bias = "BULLISH" if (m15_e is not None and curr_price >= m15_e) else "BEARISH"

        # H1 anchor bias (sample every 12th candle)
        h1_closes = closes[11::12] if len(closes) >= 24 else closes
        h1_e = _last_val(ema(h1_closes, min(20, len(h1_closes))))
        h1_bias = "BULLISH" if (h1_e is not None and curr_price >= h1_e) else "BEARISH"

        bull_count = sum(1 for b in (m5_bias, m15_bias, h1_bias) if b == "BULLISH")
        if bull_count == 3:
            alignment = "3/3_STRONG_BULLISH"
        elif bull_count == 0:
            alignment = "3/3_STRONG_BEARISH"
        elif bull_count == 2:
            alignment = "2/3_MODERATE_BULLISH"
        else:
            alignment = "2/3_MODERATE_BEARISH"

        return {
            "m5_bias": m5_bias,
            "m15_bias": m15_bias,
            "h1_anchor_bias": h1_bias,
            "alignment": alignment,
            "h1_confirms_buy": h1_bias == "BULLISH",
            "h1_confirms_sell": h1_bias == "BEARISH",
        }
    except Exception:
        return {}


def strategy_facts(candles, *, symbol: str = "XAUUSD", timeframe: str = "M5",
                   strategy_id: str = "", db=None, sr_cache=None,
                   spread_hours: int = 24) -> dict:
    """Compact facts dict for the grading context. {} on any failure."""
    try:
        if not candles or len(candles) < 30:
            return {}
        import numpy as np
        closes = [float(c.c) for c in candles]
        price = closes[-1]

        # regime.* expects numpy arrays (it does element-wise arithmetic).
        highs_arr = np.array([float(c.h) for c in candles])
        lows_arr = np.array([float(c.l) for c in candles])
        closes_arr = np.array(closes)
        atr_values = [float(v) for v in atr_series(highs_arr, lows_arr, closes_arr)
                      if v == v]                      # drop NaN warm-up
        atr = atr_values[-1] if atr_values else 0.0
        rank = None
        if atr_values:
            tail = atr_values[-100:]
            if tail:
                rank = int(round(100 * sum(1 for v in tail if v < tail[-1]) / len(tail)))

        def last(seq):
            for v in reversed(seq):
                if v is not None:
                    return v
            return None

        e9, e21, e55, e200 = (last(ema(closes, p)) for p in (9, 21, 55, 200))
        _, _, hist = macd(closes)
        facts = {
            "price": _r(price),
            "atr14": _r(atr),
            "atr_percentile_rank_100": rank,
            "adx14": _r(last(adx_series(highs_arr, lows_arr, closes_arr))),
            "rsi14": _r(last(rsi(closes))),
            "macd_hist": _r(last(hist), 3),
            "ema9": _r(e9), "ema21": _r(e21), "ema55": _r(e55), "ema200": _r(e200),
            "regime_classical": classify_regime(candles),
        }
        if atr:
            facts["price_vs_ema55_atr"] = _r((price - e55) / atr) if e55 else None
            facts["price_vs_ema200_atr"] = _r((price - e200) / atr) if e200 else None
            facts["price_on_trend_side_of_ema55"] = (
                None if e55 is None else bool(price >= e55))
        facts["window"] = strict_window_state(
            candles, confirm_closes(strategy_id) if strategy_id else 2)

        vp = compute_volume_profile(candles)
        if vp:
            facts["volume_profile"] = vp

        mtf = compute_mtf_alignment(candles, db=db)
        if mtf:
            facts["mtf_alignment"] = mtf

        if db is not None:
            try:
                facts["spread_24h"] = db.spread_stats(spread_hours)
            except Exception:
                pass
            try:
                facts["agent_accuracy"] = db.agent_accuracy()
            except Exception:
                pass
            try:
                from app.sr_zones import sr_zones_from_db
                zones = sr_zones_from_db(db, sr_cache if sr_cache is not None else {})
                walls = []
                for z in zones:
                    dist = None
                    if atr:
                        if z.role == "resistance" and z.lo >= price:
                            dist = (z.lo - price) / atr
                        elif z.role == "support" and z.hi <= price:
                            dist = (price - z.hi) / atr
                    if dist is not None:
                        walls.append({"role": z.role, "tier": z.tier,
                                      "lo": _r(z.lo), "hi": _r(z.hi),
                                      "dist_atr": _r(dist)})
                walls.sort(key=lambda w: w["dist_atr"] if w["dist_atr"] is not None else 99)
                facts["nearest_sr_walls"] = walls[:4]
            except Exception:
                pass
        try:
            from app.telegram import market_session
            facts["session"] = market_session()
        except Exception:
            pass
        return facts
    except Exception:
        return {}
