"""Drive scripts/backtest.py (UNCHANGED -- the golden pins guard it) as a
subprocess over candles exported from the persistent SQLite table.

Subprocess, not import: the engine configures itself through module globals
in its main() and is not safe to re-enter from a threaded service; isolation
also means an engine crash can never take the service down. One run at a
time (_busy): the engine is CPU-bound and the account of record is a single
run artifact anyway.
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]           # repo root
ENGINE = ROOT / "scripts" / "backtest.py"
RUNS_DIR = ROOT / "service" / "data" / "backtests"
RUN_TIMEOUT_S = 1800

# ConfirmCloses per the EA's registrations (XauAssistant.mq5): M5 lane
# ConfirmCloses=2, M15 lane M15ConfirmCloses=3; every other HalfTrend
# parameter matches the engine's live defaults (amplitude 4, EMA 55,
# stop buffer 0.75). --tf M15 makes the engine resample its M5 source.
STRATEGIES = {
    "halftrend_ema_v1": {"label": "HalfTrend M5",
                         "flags": ["--tf", "M5", "--confirm", "2"]},
    "halftrend_m15_v1": {"label": "HalfTrend M15",
                         "flags": ["--tf", "M15", "--confirm", "3"]},
}

_busy = threading.Lock()


def build_cli(params: dict, source: Path, json_out: Path, web_out: Path) -> list:
    cmd = [sys.executable, str(ENGINE),
           "--source", str(source),
           "--balance", str(params["balance"]),
           "--risk", str(params["risk_pct"]),
           "--entry-mode", params["entry_mode"],
           "--exit-scheme", params["exit_scheme"],
           "--ema200-confirm", params["ema200_confirm"],
           "--json", str(json_out),
           "--web", str(web_out)]
    cmd += STRATEGIES[params["strategy"]]["flags"]
    # M15 bias is the M5 lane's HTF-agreement replay; the M15 lane has no
    # HTF module (EA: "the only confirmation is the ema 200").
    if params.get("m15_bias") == "on" and params["strategy"] == "halftrend_ema_v1":
        cmd += ["--bias-ema", "200", "--bias-tf", "M15", "--bias-mode", "target"]
    return cmd


def start_run(db, params: dict) -> int:
    """Insert the run row and launch the engine in a daemon thread.
    Raises RuntimeError when a run is already in flight (the API maps it
    to 409)."""
    if not _busy.acquire(blocking=False):
        raise RuntimeError("a backtest is already running")
    try:
        run_id = db.insert_backtest_run(json.dumps(params))
        thread = threading.Thread(target=_execute_locked,
                                  args=(db, run_id, params), daemon=True)
        thread.start()
    except BaseException:
        _busy.release()
        raise
    return run_id


def _execute_locked(db, run_id: int, params: dict) -> None:
    try:
        _execute(db, run_id, params)
    finally:
        _busy.release()


def _execute(db, run_id: int, params: dict) -> None:
    """Thread body. Every failure path lands in a 'failed' row -- the
    service itself must never see an exception from here."""
    try:
        run_dir = RUNS_DIR / str(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        rows = db.get_candles(params["symbol"], "M5",
                              start_ts=params["start_ts"],
                              end_ts=params["end_ts"])
        if len(rows) < 300:
            raise RuntimeError(
                f"only {len(rows)} M5 bars in that range -- need at least 300"
                " (run the backfill: see scripts/backfill_candles.py)")
        source = run_dir / "bars.json"
        source.write_text(json.dumps(
            {"symbol": params["symbol"], "timeframe": "M5", "candles": rows},
            separators=(",", ":")))
        json_out = run_dir / "result.json"
        web_out = run_dir / "report.html"
        proc = subprocess.run(build_cli(params, source, json_out, web_out),
                              cwd=str(ROOT), capture_output=True, text=True,
                              timeout=RUN_TIMEOUT_S)
        if proc.returncode != 0 or not json_out.exists():
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise RuntimeError(f"engine exited {proc.returncode}: {tail}")
        stats = json.loads(json_out.read_text()).get("stats", {})
        db.finish_backtest_run(run_id, status="done",
                               stats_json=json.dumps(stats),
                               report_path=str(web_out))
    except Exception as exc:
        try:
            db.finish_backtest_run(run_id, status="failed",
                                   error=str(exc)[:500])
        except Exception:
            pass
