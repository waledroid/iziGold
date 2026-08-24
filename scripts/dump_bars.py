"""Dump M5 XAUUSD bars from the RUNNING MT5 terminal to JSON in the
/api/candles format consumed by scripts/backtest.py.
Run with WINDOWS python: python.exe scripts/dump_bars.py [bars] [out.json]
"""
import json
import sys

import MetaTrader5 as mt5

n = int(sys.argv[1]) if len(sys.argv) > 1 else 2100
out = sys.argv[2] if len(sys.argv) > 2 else "week.json"

if not mt5.initialize():
    sys.exit(f"mt5.initialize failed: {mt5.last_error()}")
rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 1, n)
mt5.shutdown()
if rates is None or len(rates) == 0:
    sys.exit("no rates returned")
candles = [{"t": int(r["time"]), "o": float(r["open"]), "h": float(r["high"]),
            "l": float(r["low"]), "c": float(r["close"]),
            "v": float(r["tick_volume"])} for r in rates]
json.dump({"symbol": "XAUUSD", "timeframe": "M5", "candles": candles},
          open(out, "w"))
print(f"wrote {len(candles)} bars -> {out}")
