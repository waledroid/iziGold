"""Load and derive formats from config/chart_spec.json, the single source of
truth for every chart surface (MT5 terminal, Telegram PNG renders, dashboard,
mini-app). See the JSON's `_meta` for the contract.

Pure, import-time-side-effect-free. Renders and overlay builders read their
colours/widths/parameters from here instead of hardcoding them, and
service/tests/test_chart_spec.py enforces the MT5 side stays in lock-step.

Colour formats each renderer needs:
  - matplotlib: (r, g, b, a) floats in [0, 1]          -> hex_to_rgba01
  - CSS/JS:     "#rrggbb" (as-is)                       -> the hex string itself
  - MQL5:       "R,G,B" (inside a C'R,G,B' literal)     -> hex_to_mql

S/R zone outlines use their unblended border colours with no fill. Filled
trade-box colours on MT5 cannot use alpha, so those literals are PRE-BLENDED
against the dark theme background; blend_mt5() computes that literal, the
EA source stores the result, and the parity test re-derives it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parents[2] / "config" / "chart_spec.json"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


@lru_cache(maxsize=1)
def load() -> dict:
    with open(_SPEC_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    return _hex_to_rgb(hex_color)


def hex_to_rgba01(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    r, g, b = _hex_to_rgb(hex_color)
    return (r / 255.0, g / 255.0, b / 255.0, alpha)


def hex_to_mql(hex_color: str) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"{r},{g},{b}"


def blend_mt5(hex_color: str, alpha: float) -> tuple[int, int, int]:
    """Pre-blend a translucent colour against the dark theme background, the
    MQL5 substitute for alpha (MT5 objects cannot be translucent)."""
    bg = _hex_to_rgb(theme_background())
    base = _hex_to_rgb(hex_color)
    return tuple(round(c * alpha + b * (1.0 - alpha)) for c, b in zip(base, bg))


def theme_background() -> str:
    return load()["theme"]["background"]


def theme_grid() -> str:
    return load()["theme"]["grid"]


def theme_foreground() -> str:
    return load()["theme"]["foreground"]


def series_color(name: str) -> str:
    return load()["series"][name]["color"]


def series_width(name: str) -> int:
    return load()["series"][name]["width"]


def halftrend_up() -> str:
    return load()["series"]["halftrend"]["up"]


def halftrend_down() -> str:
    return load()["series"]["halftrend"]["down"]


def trade_ema_len(tf: str) -> int:
    lens = load()["trade_ema_len"]
    return lens.get(tf, lens["default"])


def sr_color(role: str) -> str:
    return load()["sr_zones"][role]["color"]


def sr_outline() -> dict:
    """S/R outline rendering contract shared by every surface."""
    return load()["sr_zones"]["outline"]


def trade_box(kind: str) -> dict:
    return load()["trade_boxes"][kind]


def panels() -> dict:
    """Sub-panel visibility (id -> bool). The frontends render only the
    panels enabled here, so dropping a pane is a one-file change."""
    return load()["panels"]


def panel_enabled(name: str) -> bool:
    return bool(load()["panels"].get(name, False))


def preview() -> dict:
    return load()["preview"]


def preview_default_enabled() -> bool:
    return bool(load()["preview"]["enabled_default"])


def preview_line(kind: str) -> dict:
    """Preview band line style for kind in {entry, sl, tp}."""
    return load()["preview"][kind]


def spec_payload() -> dict:
    """The dict served to web frontends via GET /api/chart-spec."""
    return load()
