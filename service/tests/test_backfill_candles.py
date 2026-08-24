import importlib.util
import json
from pathlib import Path

from app.db import SignalDb

_SPEC = importlib.util.spec_from_file_location(
    "backfill_candles",
    Path(__file__).resolve().parents[2] / "scripts" / "backfill_candles.py")
backfill = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backfill)


def test_load_dump_idempotent(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    dump = tmp_path / "week.json"
    dump.write_text(json.dumps({
        "symbol": "XAUUSD", "timeframe": "M5",
        "candles": [{"t": 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}
                    for i in range(1, 11)]}))
    assert backfill.load_dump(db, str(dump)) == 10
    assert backfill.load_dump(db, str(dump)) == 10        # re-run: no dupes
    assert db.candles_range("XAUUSD", "M5")["count"] == 10
