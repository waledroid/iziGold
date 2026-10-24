"""Parity between config/chart_spec.json (the single source of truth for
every chart surface) and the MT5 native chart colours.

The MT5 side can't be imported or run from Linux -- it only compiles in
MetaEditor on Windows -- so this is a TEXT parse of the colour literals out of
the chart-painting MQL files, exactly like test_strategy_config.py parses the
EA's `input` defaults. S/R zones are outline-only, so their MQL border
colours must equal the spec's unblended colours. Only filled trade-box
colours are pre-blended against the dark theme background; the expected
value for those is app.chart_spec.blend_mt5(base, alpha).

If this fails, either config/chart_spec.json drifted or an MQL colour was
edited without updating the spec (remember the EA also needs recompiling on
Windows after the edit). Fix the drift, not the test.
"""
import re
from pathlib import Path

from app import chart_spec

ROOT = Path(__file__).resolve().parents[2]
EA = ROOT / "mt5" / "Experts" / "XauAssistant.mq5"
HT = ROOT / "mt5" / "Include" / "XauAssistant" / "Strategies" / "HalfTrendEma.mqh"
SRB = ROOT / "mt5" / "Include" / "XauAssistant" / "SrBoxes.mqh"
TRB = ROOT / "mt5" / "Include" / "XauAssistant" / "TradeBoxes.mqh"

_LIT = r"C'(\d+),(\d+),(\d+)'"


def _rgb_of(text: str, pattern: str) -> tuple[int, int, int]:
    m = re.search(pattern, text)
    assert m is not None, f"colour literal not found via {pattern!r}"
    return tuple(int(x) for x in m.groups())


def _rgb_all(text: str, pattern: str) -> list[tuple[int, int, int]]:
    return [tuple(int(x) for x in g) for g in re.findall(pattern, text)]


def test_mt5_theme_matches_spec():
    text = EA.read_text(encoding="utf-8")
    bg = _rgb_of(text, r"CHART_COLOR_BACKGROUND,\s*" + _LIT)
    grid = _rgb_of(text, r"CHART_COLOR_GRID,\s*" + _LIT)
    assert bg == chart_spec.hex_to_rgb(chart_spec.theme_background())
    assert grid == chart_spec.hex_to_rgb(chart_spec.theme_grid())


def test_mt5_series_colours_match_spec():
    text = HT.read_text(encoding="utf-8")
    # HalfTrend up/down: one ternary on a single line.
    m = re.search(r"\(m_trend == 0\) \? " + _LIT + r" : " + _LIT, text)
    assert m is not None, "HalfTrend ternary not found"
    assert (int(m.group(1)), int(m.group(2)), int(m.group(3))) == \
        chart_spec.hex_to_rgb(chart_spec.halftrend_up())
    assert (int(m.group(4)), int(m.group(5)), int(m.group(6))) == \
        chart_spec.hex_to_rgb(chart_spec.halftrend_down())
    # Trade EMA (the 6th arg of the xau_ema_ DrawSeg call).
    trade = _rgb_of(text, r'"xau_ema_",\s*(?:[^,]*,){4}\s*' + _LIT)
    assert trade == chart_spec.hex_to_rgb(chart_spec.series_color("ema_trade"))
    # Context EMAs (the token right after each xau_emaN_ string literal).
    for key in ("ema9", "ema21", "ema200"):
        got = _rgb_of(text, r'"xau_' + key + r'_",\s*' + _LIT)
        assert got == chart_spec.hex_to_rgb(chart_spec.series_color(key))


def test_mt5_sr_boxes_match_spec():
    text = SRB.read_text(encoding="utf-8")
    # Outline-only: one unblended border colour per role, no fill, dashed
    # 1px border. The tier argument remains (and ordering is retained), but
    # it must not change the colour.
    got = _rgb_all(text, r"return\s*\(role == SR_ROLE_SUP\) \? " + _LIT +
                   r" : " + _LIT)
    assert len(got) == 1
    support, resistance = got[0][:3], got[0][3:]
    assert support == chart_spec.hex_to_rgb(chart_spec.sr_color("support"))
    assert resistance == chart_spec.hex_to_rgb(
        chart_spec.sr_color("resistance"))
    assert "OBJPROP_FILL, false" in text
    assert "OBJPROP_STYLE, STYLE_DASH" in text
    assert "OBJPROP_WIDTH, 1" in text


def test_mt5_trade_boxes_match_spec():
    text = TRB.read_text(encoding="utf-8")
    risk = _rgb_of(text, r"RiskColor\(\)\s*\{\s*return\s+" + _LIT)
    profit = _rgb_of(text, r"ProfitColor\(\)\s*\{\s*return\s+" + _LIT)
    assert risk == chart_spec.blend_mt5(
        chart_spec.trade_box("risk")["color"],
        chart_spec.trade_box("risk")["alpha"])
    assert profit == chart_spec.blend_mt5(
        chart_spec.trade_box("profit")["color"],
        chart_spec.trade_box("profit")["alpha"])


def test_spec_structure_and_accessors():
    """The spec itself is well-formed and the accessor surface is complete."""
    spec = chart_spec.load()
    assert set(spec["series"]) == {"halftrend", "ema9", "ema21",
                                   "ema_trade", "ema200"}
    assert chart_spec.panel_enabled("rsi") is False
    assert chart_spec.panel_enabled("macd") is False
    assert "alpha" not in spec["sr_zones"]
    assert chart_spec.sr_outline() == {
        "fill": False, "width": 1, "style": "dashed"}
    for kind in ("entry", "sl", "tp"):
        line = chart_spec.preview_line(kind)
        assert set(line) == {"color", "width", "style"}
        # every preview colour parses as #RRGGBB
        assert len(chart_spec.hex_to_rgb(line["color"])) == 3


def _ea_bool(text: str, name: str) -> bool:
    match = re.search(r"input bool\s+" + re.escape(name) + r"\s*=\s*(true|false);",
                      text)
    assert match is not None, f"EA input {name} not found"
    return match.group(1) == "true"


def test_mt5_chart_panels_are_removed_by_default():
    """RSI and MACD stay available as opt-in EA inputs, but new chart attachments
    must not add them. Charts remain clean."""
    text = EA.read_text(encoding="utf-8")
    assert _ea_bool(text, "ShowRsiPanel") is False
    assert _ea_bool(text, "ShowMacdPanel") is False
