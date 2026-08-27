import json
import sys
from pathlib import Path

from app import backtest_runner
from app.db import SignalDb


def _params(**over):
    p = {"strategy": "halftrend_ema_v1", "symbol": "XAUUSD",
         "start_ts": 0, "end_ts": 10**10, "balance": 10000.0,
         "risk_pct": 1.0, "entry_mode": "adr", "exit_scheme": "target-exit",
         "ema200_confirm": "off", "m15_bias": "off"}
    p.update(over)
    return p


def test_run_rows_lifecycle(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    rid = db.insert_backtest_run(json.dumps(_params()))
    assert db.get_backtest_run(rid)["status"] == "running"
    db.finish_backtest_run(rid, status="done", stats_json='{"net": 5}',
                           report_path="/x/report.html")
    row = db.get_backtest_run(rid)
    assert row["status"] == "done" and row["report_path"] == "/x/report.html"
    assert db.recent_backtest_runs()[0]["id"] == rid
    assert db.get_backtest_run(999) is None


def test_build_cli_maps_strategies_and_flags(tmp_path):
    src, jout, wout = tmp_path / "b.json", tmp_path / "r.json", tmp_path / "r.html"
    cmd = backtest_runner.build_cli(_params(), src, jout, wout)
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("scripts/backtest.py")
    s = " ".join(cmd)
    assert "--tf M5" in s and "--confirm 2" in s
    assert "--balance 10000.0" in s and "--risk 1.0" in s
    assert "--entry-mode adr" in s and "--exit-scheme target-exit" in s
    assert "--ema200-confirm off" in s and "--bias-ema" not in s

    cmd15 = backtest_runner.build_cli(_params(strategy="halftrend_m15_v1"),
                                      src, jout, wout)
    s15 = " ".join(cmd15)
    assert "--tf M15" in s15 and "--confirm 1" in s15
    # per-lane stop buffers since the 2026-08-25 trend-rider sweep
    assert "--stop-buffer 1.75" in s15
    assert "--stop-buffer 0.75" in s

    biased = " ".join(backtest_runner.build_cli(_params(m15_bias="on"),
                                                src, jout, wout))
    assert "--bias-ema 200" in biased and "--bias-tf M15" in biased \
        and "--bias-mode target" in biased
    # bias is an M5-lane concept: the M15 lane never gets the flags
    b15 = " ".join(backtest_runner.build_cli(
        _params(strategy="halftrend_m15_v1", m15_bias="on"), src, jout, wout))
    assert "--bias-ema" not in b15


def test_execute_happy_path_with_fake_engine(tmp_path, monkeypatch):
    db = SignalDb(str(tmp_path / "t.db"))
    db.upsert_candles("XAUUSD", "M5", [
        {"t": 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0}
        for i in range(1, 400)])
    monkeypatch.setattr(backtest_runner, "RUNS_DIR", tmp_path / "runs")

    def fake_run(cmd, **kw):
        # the engine writes --json and --web outputs; fake both
        jout = Path(cmd[cmd.index("--json") + 1])
        jout.write_text(json.dumps({"stats": {"net": 42.0, "trades": 3}}))
        Path(cmd[cmd.index("--web") + 1]).write_text("<html></html>")
        class P:
            returncode, stdout, stderr = 0, "", ""
        return P()

    monkeypatch.setattr(backtest_runner.subprocess, "run", fake_run)
    rid = db.insert_backtest_run(json.dumps(_params()))
    backtest_runner._execute(db, rid, _params())
    row = db.get_backtest_run(rid)
    assert row["status"] == "done"
    assert json.loads(row["stats_json"])["net"] == 42.0
    assert Path(row["report_path"]).exists()
    # the exported source file holds the db's bars in dump_bars shape
    src = json.loads((tmp_path / "runs" / str(rid) / "bars.json").read_text())
    assert src["timeframe"] == "M5" and len(src["candles"]) == 399


def test_execute_too_few_bars_fails_cleanly(tmp_path, monkeypatch):
    db = SignalDb(str(tmp_path / "t.db"))
    monkeypatch.setattr(backtest_runner, "RUNS_DIR", tmp_path / "runs")
    rid = db.insert_backtest_run(json.dumps(_params()))
    backtest_runner._execute(db, rid, _params())
    row = db.get_backtest_run(rid)
    assert row["status"] == "failed" and "bars" in row["error"]


def test_execute_real_engine_lifecycle(tmp_path, monkeypatch):
    """No monkeypatching of the engine itself -- runs scripts/backtest.py
    (the real, frozen-golden-pins engine) as an actual subprocess over the
    frozen bars_slice.json fixture, spanning its full time range. Doesn't
    pin any numbers -- test_backtest_golden.py already owns that; this just
    proves the runner's plumbing (candle export, subprocess invocation,
    status transition, report.html) works end to end against the real
    binary, not a fake."""
    data_path = Path(__file__).parent / "data" / "bars_slice.json"
    candles = json.loads(data_path.read_text())  # a bare list of OHLCV dicts
    db = SignalDb(str(tmp_path / "t.db"))
    db.upsert_candles("XAUUSD", "M5", candles)
    monkeypatch.setattr(backtest_runner, "RUNS_DIR", tmp_path / "runs")
    params = _params(start_ts=candles[0]["t"], end_ts=candles[-1]["t"])
    rid = db.insert_backtest_run(json.dumps(params))
    backtest_runner._execute(db, rid, params)
    row = db.get_backtest_run(rid)
    assert row["status"] == "done", row.get("error")
    assert Path(row["report_path"]).exists()


def test_start_run_serializes(tmp_path, monkeypatch):
    db = SignalDb(str(tmp_path / "t.db"))
    # hold the busy lock as if a run were in flight
    assert backtest_runner._busy.acquire(blocking=False)
    try:
        import pytest
        with pytest.raises(RuntimeError):
            backtest_runner.start_run(db, _params())
    finally:
        backtest_runner._busy.release()
