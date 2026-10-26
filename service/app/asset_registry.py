"""Centralized Multi-Asset & Strategy Registry for XAU Assistant.

Provides a single source of truth for asset specifications, price formatting,
display badges, default lot sizes, and strategy lane associations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class AssetSpec:
    symbol: str
    display_name: str
    badge: str
    tag: str
    price_decimals: int
    default_lot: float
    lot_step: float
    strategy_lanes: Tuple[str, ...]
    default_lane: str

    def format_price(self, price: float) -> str:
        return f"{price:.{self.price_decimals}f}"

    def format_tag(self, include_symbol: bool = False) -> str:
        if include_symbol:
            return f"[{self.badge} {self.symbol}]"
        return f"[{self.badge} {self.tag}]"


ASSETS: Dict[str, AssetSpec] = {
    "XAUUSD": AssetSpec(
        symbol="XAUUSD",
        display_name="Gold",
        badge="🥇",
        tag="XAU",
        price_decimals=2,
        default_lot=0.03,
        lot_step=0.01,
        strategy_lanes=("iziGold5", "iziGold15"),
        default_lane="iziGold15",
    ),
    "BTCUSD": AssetSpec(
        symbol="BTCUSD",
        display_name="Bitcoin",
        badge="₿",
        tag="BTC",
        price_decimals=2,
        default_lot=0.01,
        lot_step=0.01,
        strategy_lanes=("iziBit5", "iziBit15"),
        default_lane="iziBit15",
    ),
}

# Strategy ID to Asset mapping for fast O(1) lookups
_LANE_TO_ASSET: Dict[str, AssetSpec] = {
    "izi5": ASSETS["XAUUSD"],
    "izi15": ASSETS["XAUUSD"],
}
for _spec in ASSETS.values():
    for _lane in _spec.strategy_lanes:
        _LANE_TO_ASSET[_lane] = _spec


def get_asset(symbol_or_lane: str = "XAUUSD") -> AssetSpec:
    """Retrieve the AssetSpec for a given symbol (e.g. 'XAUUSD', 'BTCUSD')
    or strategy lane (e.g. 'izi5', 'iziBit15'). Fallback is Gold."""
    if not symbol_or_lane:
        return ASSETS["XAUUSD"]

    key = symbol_or_lane.strip().upper()
    if key in ASSETS:
        return ASSETS[key]

    if "BTC" in key:
        return ASSETS["BTCUSD"]

    # Check strategy lane
    if symbol_or_lane in _LANE_TO_ASSET:
        return _LANE_TO_ASSET[symbol_or_lane]

    return ASSETS["XAUUSD"]


def format_asset_price(symbol: str, price: float) -> str:
    """Format a price with the asset's precision."""
    return get_asset(symbol).format_price(price)


def format_asset_badge(symbol: str, include_symbol: bool = False) -> str:
    """Format a badge tag, e.g. '[🥇 XAU]' or '[₿ BTC]'."""
    return get_asset(symbol).format_tag(include_symbol)

