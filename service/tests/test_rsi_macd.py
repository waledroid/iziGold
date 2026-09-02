# rsi()/macd() — added 2026-09-02 for the replay filter study (owner:
# "reporting purposes only" — nothing here touches the EA). RSI is Wilder's
# (matches MT5's iRSI); MACD is EMA12−EMA26 with an EMA-9 signal (classic
# form; note MT5's built-in MACD draws an SMA-9 signal instead — documented
# in the function docstring).
from app.indicators import ema, macd, rsi


def test_rsi_hand_computed_period_2():
    # prices 1,2,3,2,3 with period 2, Wilder smoothing, worked by hand:
    #   changes:            +1  +1  -1  +1
    #   seed  ag=1  al=0            -> RSI = 100
    #   next  ag=(1+0)/2=.5 al=.5   -> RS=1  -> RSI = 50
    #   next  ag=(.5+1)/2=.75 al=.25-> RS=3  -> RSI = 75
    assert rsi([1.0, 2.0, 3.0, 2.0, 3.0], 2) == [None, None, 100.0, 50.0, 75.0]


def test_rsi_extremes_and_flat():
    up = [float(i) for i in range(1, 40)]
    assert all(v == 100.0 for v in rsi(up, 14)[14:])
    down = [float(40 - i) for i in range(39)]
    assert all(v == 0.0 for v in rsi(down, 14)[14:])
    flat = [5.0] * 30
    assert all(v == 50.0 for v in rsi(flat, 14)[14:])   # no gains, no losses
    assert rsi([1.0, 2.0], 14) == [None, None]          # short input degrades


def test_rsi_bounds_and_length():
    closes = [100.0 + ((i * 7919) % 13) - 6 for i in range(200)]
    out = rsi(closes, 14)
    assert len(out) == len(closes)
    assert all(v is None for v in out[:14])
    assert all(0.0 <= v <= 100.0 for v in out[14:])


def test_macd_is_ema_composition():
    closes = [100.0 + ((i * 104729) % 17) * 0.3 for i in range(120)]
    line, signal, hist = macd(closes)
    e12, e26 = ema(closes, 12), ema(closes, 26)
    assert len(line) == len(signal) == len(hist) == len(closes)
    # macd line = EMA12 - EMA26 wherever both exist
    for i, v in enumerate(line):
        if e26[i] is None:
            assert v is None
        else:
            assert abs(v - (e12[i] - e26[i])) < 1e-9
    # signal = EMA9 over the defined macd-line values, re-aligned
    defined = [v for v in line if v is not None]
    sig_tail = [v for v in ema(defined, 9) if v is not None]
    got_tail = [v for v in signal if v is not None]
    assert len(got_tail) == len(sig_tail)
    assert all(abs(a - b) < 1e-9 for a, b in zip(got_tail, sig_tail))
    # histogram = line - signal wherever the signal exists
    for i, s in enumerate(signal):
        if s is not None:
            assert abs(hist[i] - (line[i] - s)) < 1e-9


def test_macd_short_input_degrades():
    line, signal, hist = macd([1.0, 2.0, 3.0])
    assert line == signal == hist == [None, None, None]


def test_backtest_series_match_indicators():
    """scripts/backtest.py carries local copies (it is deliberately
    standalone) — they must stay byte-equal to app.indicators."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts" / "backtest.py"
    spec = importlib.util.spec_from_file_location("bt_parity", path)
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    closes = [100.0 + ((i * 6151) % 23) * 0.7 for i in range(300)]
    assert bt.rsi_series(closes, 14) == rsi(closes, 14)
    _, _, hist = macd(closes)
    got = bt.macd_hist_series(closes)
    for a, b in zip(got, hist):
        assert (a is None) == (b is None)
        if a is not None:
            assert abs(a - b) < 1e-9


def test_miniapp_serves_rsi_and_macd_series():
    """Every TF tab's /api/history payload carries the sub-panel series
    (owner 2026-09-02); the frontend panel plugins consume them."""
    from app.miniapp import _indicator_series
    rows = [{"t": 900 + 300 * i, "o": 1.0, "h": 2.0, "l": 0.5,
             "c": 100.0 + (i % 9) + i * 0.02, "v": 1} for i in range(120)]
    closes = [r["c"] for r in rows]
    out = _indicator_series(rows, "M5")
    assert out["rsi"] == rsi(closes, 14)
    line, sig, hist = macd(closes)
    assert out["macd_line"] == line
    assert out["macd_signal"] == sig
    assert out["macd_hist"] == hist
