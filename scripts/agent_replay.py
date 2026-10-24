#!/usr/bin/env python3
"""Counterfactual agent replay: score grades against historical outcomes.

Run BEFORE changing the agent prompt, model or thresholds. It walks recorded
candles, grades a sample of bars exactly as the live service would (same
context pack, same strategy facts), then scores each grade against the price
move over the horizon and prints accuracy/calibration by setup.

    docker compose exec main python /workspace/scripts/agent_replay.py \
        --symbol XAUUSD --timeframe M5 --limit 400 --step 5 --out /data/replay.json

Read-only with respect to trading state: it never inserts signals, grades or
orders. It only reads candles and (optionally) writes a JSON report artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app.agent import AgentClient, AgentError, build_context  # noqa: E402
from app.agent_context import strategy_facts  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SignalDb  # noqa: E402
from app.forecaster import get_forecaster  # noqa: E402
from app.analysis import analyze_forecast  # noqa: E402
from app.models import Candle  # noqa: E402
from app.regime import classify_regime, last_atr  # noqa: E402


def _bucket(results: list, key: str) -> dict:
    groups = defaultdict(lambda: [0, 0])
    for r in results:
        if r["correct"] is None:
            continue
        k = r.get(key) or "unknown"
        groups[k][0] += 1
        groups[k][1] += int(r["correct"])
    return {k: {"n": n, "acc_pct": round(100 * c / n, 1)}
            for k, (n, c) in sorted(groups.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--timeframe", default="M5")
    ap.add_argument("--strategy-id", default="izi5")
    ap.add_argument("--limit", type=int, default=300, help="bars to walk back")
    ap.add_argument("--step", type=int, default=5, help="grade every Nth bar")
    ap.add_argument("--horizon", type=int, default=16)
    ap.add_argument("--context-bars", type=int, default=120)
    ap.add_argument("--out", default="", help="write a JSON report here")
    args = ap.parse_args()

    if not settings.agent_enabled and not settings.agent_base_url:
        print("agent is not configured (AGENT_BASE_URL/AGENT_API_KEY)")
        return 2
    client = AgentClient(settings)
    if not client.configured:
        print("agent not configured (AGENT_BASE_URL/AGENT_API_KEY)")
        return 2

    db = SignalDb(settings.db_path)
    rows = db.get_candles(args.symbol, args.timeframe,
                          limit=args.limit + args.context_bars)
    if len(rows) < args.context_bars + args.horizon + 10:
        print(f"not enough candles: {len(rows)}")
        return 2
    candles = [Candle(**r) for r in rows]
    forecaster = get_forecaster(settings)

    results = []
    started = time.time()
    for i in range(args.context_bars, len(candles) - args.horizon, args.step):
        window = candles[: i + 1]
        closes = [c.c for c in window]
        entry = closes[-1]
        future = candles[i + args.horizon].c
        move = future - entry
        try:
            regime = classify_regime(window)
            atr = last_atr(window)
            facts = strategy_facts(window, symbol=args.symbol,
                                   timeframe=args.timeframe,
                                   strategy_id=args.strategy_id, db=db)
            fc = forecaster.forecast(closes, settings.horizon)
            rules_dir, rules_conf = analyze_forecast(fc, entry, atr)
            ctx = build_context(symbol=args.symbol, timeframe=args.timeframe,
                                signal="NONE", strategy_id=args.strategy_id,
                                candles=window, regime=regime, atr_value=atr,
                                db=db, context_bars=args.context_bars,
                                facts=facts)
            grade = client.complete(ctx)
        except AgentError as exc:
            print(f"  bar {window[-1].t}: grade unavailable ({exc})")
            continue
        correct = None
        if grade.direction == "bullish":
            correct = move > 0
        elif grade.direction == "bearish":
            correct = move < 0
        results.append({
            "bar_time": window[-1].t,
            "direction": grade.direction,
            "confidence": grade.confidence,
            "entry_quality": grade.entry_quality,
            "expected_r": grade.expected_r,
            "action": grade.action,
            "move": round(move, 2),
            "correct": correct,
            "window_state": (facts.get("window") or {}).get("state"),
            "regime": facts.get("regime_classical"),
            "session": facts.get("session"),
            "rules_direction": rules_dir,
        })
        print(f"  {window[-1].t} {grade.direction:8s} "
              f"conf={grade.confidence:.2f} move={move:+.2f} "
              f"correct={correct}")

    scored = [r for r in results if r["correct"] is not None]
    wins = sum(1 for r in scored if r["correct"])
    report = {
        "symbol": args.symbol, "timeframe": args.timeframe,
        "strategy_id": args.strategy_id, "model": settings.agent_model,
        "prompt_version": settings.agent_prompt_version,
        "bars_graded": len(results), "directional": len(scored),
        "accuracy_pct": round(100 * wins / len(scored), 1) if scored else None,
        "neutral_rate_pct": round(
            100 * (len(results) - len(scored)) / len(results), 1) if results else None,
        "by_window_state": _bucket(results, "window_state"),
        "by_regime": _bucket(results, "regime"),
        "by_session": _bucket(results, "session"),
        "avg_confidence_when_right": round(
            sum(r["confidence"] for r in scored if r["correct"]) /
            max(wins, 1), 3),
        "avg_confidence_when_wrong": round(
            sum(r["confidence"] for r in scored if not r["correct"]) /
            max(len(scored) - wins, 1), 3),
        "seconds": round(time.time() - started, 1),
        "results": results,
    }
    print("\n=== replay summary ===")
    print(json.dumps({k: v for k, v in report.items() if k != "results"},
                     indent=2, default=str))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str))
        print(f"report written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
