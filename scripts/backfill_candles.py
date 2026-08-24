#!/usr/bin/env python3
"""Load dump_bars.py JSON dumps into the service's persistent candles table.

Two-step backfill (the MT5 python package only runs under WINDOWS python,
so the pull and the load are separate steps):

  1. pull from the running terminal (Windows python, from the repo root):
       python.exe scripts/dump_bars.py 75000 bars_max.json    # ~12 months of M5
  2. load into SQLite (WSL, from service/ so the default db path matches):
       cd service && python3 ../scripts/backfill_candles.py ../bars_max.json

Idempotent: bars are keyed (symbol, timeframe, bar_time); re-running a load
replaces identical rows and never duplicates.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))
from app.db import SignalDb  # noqa: E402


def load_dump(db: SignalDb, path: str) -> int:
    data = json.load(open(path))
    return db.upsert_candles(data["symbol"], data["timeframe"], data["candles"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dumps", nargs="+", help="dump_bars.py JSON file(s)")
    ap.add_argument("--db", default="xau_assistant.db",
                    help="SQLite db path (default: xau_assistant.db in CWD"
                         " -- run from service/)")
    args = ap.parse_args()
    db = SignalDb(args.db)
    for p in args.dumps:
        n = load_dump(db, p)
        data = json.load(open(p))
        rng = db.candles_range(data["symbol"], data["timeframe"])
        print(f"{p}: loaded {n} bars -> table holds {rng['count']} "
              f"({rng['start']} .. {rng['end']})")


if __name__ == "__main__":
    main()
