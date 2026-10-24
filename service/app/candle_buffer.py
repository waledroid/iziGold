"""Unified Multi-Asset In-Memory Candle Buffer Manager for XAU Assistant.

Maintains chronological, deduplicated candle series for all active markets
(Gold M5/M15, BTC M5/M15) with thread-safe updates and bounded window caps.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

from app.models import Candle

_DEFAULT_WINDOW_CAP = 2000


class CandleBufferManager:
    """Thread-safe manager for in-memory candle series keyed by (symbol, timeframe)."""

    def __init__(self, window_cap: int = _DEFAULT_WINDOW_CAP):
        self._window_cap = window_cap
        self._lock = threading.RLock()
        self._series: Dict[Tuple[str, str], List[Candle]] = {}

    def seed_from_db(self, db) -> int:
        """Seed initial candle buffers from SQLite database. Returns count of seeded series."""
        count = 0
        with self._lock:
            try:
                distinct_series = db.conn.execute(
                    "SELECT DISTINCT symbol, timeframe FROM candles"
                ).fetchall()
                for sym, tf in distinct_series:
                    rows = db.get_candles(sym, tf, limit=self._window_cap)
                    if rows:
                        c_list = [Candle(**r) for r in rows]
                        self._series[(sym, tf)] = c_list
                        count += 1
            except Exception:
                pass
        return count

    def merge_incoming(self, symbol: str, timeframe: str, incoming: List[Candle]) -> List[Candle]:
        """Merge incoming candles into the series, deduplicating by bar timestamp 't'
        and enforcing window cap."""
        key = (symbol, timeframe)
        with self._lock:
            existing = self._series.get(key, [])
            merged = {c.t: c for c in existing}
            for c in incoming:
                merged[c.t] = c
            ordered = [merged[t] for t in sorted(merged)][-self._window_cap:]
            self._series[key] = ordered
            return ordered

    def get_window(self, symbol: str, timeframe: str, limit: int = 300) -> List[Candle]:
        """Retrieve recent candles for a series, up to limit."""
        key = (symbol, timeframe)
        with self._lock:
            candles = self._series.get(key, [])
            if not candles:
                return []
            return candles[-limit:] if limit > 0 else list(candles)

    def get_latest_candle(self, symbol: str, timeframe: str) -> Optional[Candle]:
        """Get the most recent closed candle for a series."""
        key = (symbol, timeframe)
        with self._lock:
            candles = self._series.get(key)
            return candles[-1] if candles else None

    def all_series(self) -> List[Tuple[str, str]]:
        """List all tracked (symbol, timeframe) series."""
        with self._lock:
            return list(self._series.keys())

    @property
    def series_dict(self) -> Dict[Tuple[str, str], List[Candle]]:
        """Direct access to series dictionary for backwards-compatibility."""
        with self._lock:
            return dict(self._series)

