import pytest

from app.verdict import combine


@pytest.mark.parametrize("signal,direction,conf,expected", [
    ("BUY", "bullish", 0.8, "confirm"),
    ("BUY", "bearish", 0.8, "conflict"),
    ("BUY", "bullish", 0.4, "neutral"),
    ("BUY", "neutral", 0.0, "neutral"),
    ("SELL", "bearish", 0.7, "confirm"),
    ("SELL", "bullish", 0.7, "conflict"),
    ("EXIT", "bullish", 0.9, "neutral"),
    ("NONE", "bearish", 0.9, "neutral"),
])
def test_combine(signal, direction, conf, expected):
    assert combine(signal, direction, conf) == expected
