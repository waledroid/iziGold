"""Dynamic strategy router for switching between iziGold5 and iziGold15.

Evaluates market sessions, volatility rank, ADX trend strength, and
open-basket safety to select the optimal strategy lane.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import numpy as np

from app.regime import adx_series, atr_series, _hlc
from app.tg.formatters import market_session

log = logging.getLogger("uvicorn.error")

# Minimum dwell time before switching lanes again to prevent flapping (15 minutes)
MIN_DWELL_SECONDS = 900


class DynamicStrategyRouter:
    def __init__(self):
        self._last_switch_ts: float = 0.0
        self._last_target: str = "iziGold15"

    def evaluate_optimal_strategy(
        self,
        candles: list,
        current_active: str,
        open_count: int = 0,
        dt: datetime | None = None,
        force: bool = False,
    ) -> tuple[str, str]:
        """Determine optimal strategy ('iziGold5' or 'iziGold15') based on market conditions.

        Returns (target_strategy, reason).
        """
        # Normalize legacy aliases if passed
        if current_active == "izi5":
            current_active = "iziGold5"
        elif current_active == "izi15":
            current_active = "iziGold15"

        # 1. Open Basket Invariance Guard: never switch while a basket is open
        if open_count > 0:
            return current_active, f"basket open ({open_count} positions) — holding {current_active}"

        session = market_session(dt)

        # 2. Extract technical factors if candles available
        adx_val = 0.0
        atr_rank = 0.5
        if candles and len(candles) >= 30:
            try:
                h, l, c = _hlc(candles)
                atr = atr_series(h, l, c)
                valid_atr = atr[~np.isnan(atr)]
                if len(valid_atr) > 0:
                    recent = valid_atr[-100:]
                    atr_rank = float((recent < recent[-1]).mean())
                adx_arr = adx_series(h, l, c)
                valid_adx = adx_arr[~np.isnan(adx_arr)]
                if len(valid_adx) > 0:
                    adx_val = float(valid_adx[-1])
            except Exception as e:
                log.debug("DynamicStrategyRouter indicators calculation error: %s", e)

        # 3. Base Strategy Selection: iziGold15 is the primary default anchor
        # Most of the day (Asia, London morning, NY afternoon, Evening) runs on iziGold15
        # to filter micro-swings, fakeouts, and stop-hunts.
        target = "iziGold15"
        reason = f"session={session} baseline (M15 noise filter)"

        # 4. Specific Super-Expansion Windows for iziGold5 (Sniper Mode)
        # Only promote to iziGold5 during peak liquidity open bursts AND confirmed momentum:
        # - London Open initial surge (09:00 - 10:30 Paris)
        # - US Data window / NY Open overlap (14:00 - 16:30 Paris)
        is_expansion_window = session in (
            "London open",
            "London+NY overlap · US data window",
        )

        if is_expansion_window:
            # Promote to iziGold5 ONLY if true trend momentum exists (ADX >= 24 & expanding ATR)
            if adx_val >= 24.0 and atr_rank >= 0.50:
                target = "iziGold5"
                reason = f"{session} high-velocity expansion (ADX={adx_val:.1f}, ATR rank={atr_rank:.0%})"
            else:
                target = "iziGold15"
                reason = f"{session} without breakout momentum (ADX={adx_val:.1f}) -> holding iziGold15"

        # 5. Volatility Spike Override
        # Extreme volatility breakout spike (ATR rank >= 85% and ADX >= 25)
        if atr_rank >= 0.85 and adx_val >= 25.0 and session not in ("Rollover — thin market",):
            target = "iziGold5"
            reason = f"volatility breakout spike (ATR rank={atr_rank:.0%}, ADX={adx_val:.1f})"

        # 6. Low-ADX / Range Protection
        # In sideways or compressed market, always lock to iziGold15
        if adx_val > 0 and adx_val < 20.0:
            target = "iziGold15"
            reason = f"compression / low ADX ({adx_val:.1f}) -> locked to iziGold15"

        # 6. Hysteresis Guard (unless forced or switching to match current_active)
        now = time.time()
        if not force and target != current_active:
            if now - self._last_switch_ts < MIN_DWELL_SECONDS:
                remaining = int(MIN_DWELL_SECONDS - (now - self._last_switch_ts))
                return current_active, f"dwell lock active ({remaining}s remaining) — holding {current_active}"
            self._last_switch_ts = now

        self._last_target = target
        return target, reason

# Global singleton router instance
strategy_router = DynamicStrategyRouter()
