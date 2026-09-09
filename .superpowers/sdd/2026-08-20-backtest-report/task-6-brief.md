### Task 6: `--json PATH` — the run artifact

One artifact, read by the standalone page now and the Mini App tab later. The page must never re-implement a rule.

**Files:**
- Modify: `scripts/backtest.py` — add `build_run_json()` and the `--json` flag
- Create: `service/tests/test_backtest_json.py`

**Interfaces:**
- Consumes: everything from Tasks 2–5.
- Produces: `bt.build_run_json(candles, trades, args, stats) -> dict` with keys `meta`, `stats`, `candles`, `ind`, `trades`, exactly as spec §2 describes. `stats` carries `clamp_pct`, `risk_median`, `risk_p90` from `run.sizing`.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_backtest_json.py`:

```python
"""The JSON artifact is the ONLY interface between engine and page."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _artifact(tmp_path, balance=10000.0):
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(candles, balance, False)
    args = bt.build_parser().parse_args(["--balance", str(balance)])
    return bt, bt.build_run_json(candles, trades, args,
                                 {"bal": bal, "max_dd": dd, "valley": valley})


def test_candles_are_parallel_arrays_of_equal_length(tmp_path):
    _bt, art = _artifact(tmp_path)
    c = art["candles"]
    n = len(c["t"])
    assert n > 0
    for k in ("o", "h", "l", "c"):
        assert len(c[k]) == n


def test_indicator_series_align_with_the_candles(tmp_path):
    _bt, art = _artifact(tmp_path)
    n = len(art["candles"]["t"])
    for k in ("ema9", "ema21", "ema55", "ema200"):
        assert len(art["ind"][k]) == n
    assert len(art["ind"]["ht"]["v"]) == n
    assert len(art["ind"]["ht"]["trend"]) == n


def test_trades_carry_entry_times_stop_history_and_tp(tmp_path):
    _bt, art = _artifact(tmp_path)
    assert art["trades"]
    for t in art["trades"]:
        assert t["legs"] and all("t" in leg for leg in t["legs"])
        assert t["stop_history"]
        assert "tp" in t and "exit_t" in t and "bal_after" in t


def test_stats_carry_the_clamp_measurement(tmp_path):
    _bt, art = _artifact(tmp_path)
    for k in ("clamp_pct", "risk_median", "risk_p90", "net", "win_rate",
              "start_balance", "end_balance", "max_dd"):
        assert k in art["stats"], f"stats missing {k}"


def test_meta_carries_the_caveats(tmp_path):
    bt, art = _artifact(tmp_path)
    assert art["meta"]["caveats"] == bt.CAVEATS


def test_artifact_round_trips_through_json(tmp_path):
    _bt, art = _artifact(tmp_path)
    p = tmp_path / "run.json"
    p.write_text(json.dumps(art))
    assert json.loads(p.read_text())["stats"]["net"] == art["stats"]["net"]
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_json.py -q
```

Expected: FAIL — `build_run_json` does not exist.

- [ ] **Step 3: Implement `build_run_json()`**

Add to `scripts/backtest.py`, above `plot()`:

```python
def build_run_json(candles, trades, args, res):
    """The run artifact (spec 2026-08-20 section 2). Parallel arrays, not
    per-bar objects: 12 months of M5 is ~74k bars, and the array form roughly
    halves the payload with no loss of detail."""
    closes = [x["c"] for x in candles]
    ht = halftrend([type("C", (), x)() for x in candles], amplitude=AMPLITUDE)
    r2 = lambda v: None if v is None else round(v, 2)   # noqa: E731
    sizing = getattr(run, "sizing", {}) or {}
    n = len(trades)
    wins = sum(1 for t in trades if t["pl"] > 0)
    net = res["bal"] - args.balance
    return {
        "meta": {
            "generated_at": int(dt.datetime.now(dt.UTC).timestamp()),
            "source": args.source, "tf": TF, "bars": len(candles),
            "start": int(candles[0]["t"]), "end": int(candles[-1]["t"]),
            "strict_window": STRICT_WINDOW,
            "entry_mode": ENTRY_MODE,
            "args": {k: v for k, v in vars(args).items() if v is not None},
            "caveats": CAVEATS,
        },
        "stats": {
            "trades": n, "wins": wins, "losses": n - wins,
            "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
            "net": round(net, 2),
            "start_balance": round(args.balance, 2),
            "end_balance": round(res["bal"], 2),
            "max_dd": round(res["max_dd"], 2),
            "max_valley": round(res["valley"], 2),
            "best": round(max((t["pl"] for t in trades), default=0.0), 2),
            "worst": round(min((t["pl"] for t in trades), default=0.0), 2),
            "clamp_pct": sizing.get("clamp_pct", 0.0),
            "risk_median": sizing.get("risk_median", 0.0),
            "risk_p90": sizing.get("risk_p90", 0.0),
        },
        "candles": {
            "t": [int(x["t"]) for x in candles],
            "o": [r2(x["o"]) for x in candles],
            "h": [r2(x["h"]) for x in candles],
            "l": [r2(x["l"]) for x in candles],
            "c": [r2(x["c"]) for x in candles],
        },
        "ind": {
            "ema9": [r2(v) for v in ema(closes, 9)],
            "ema21": [r2(v) for v in ema(closes, 21)],
            "ema55": [r2(v) for v in ema(closes, 55)],
            "ema200": [r2(v) for v in ema(closes, 200)],
            "ht": {"v": [r2(p[0]) if p else None for p in ht],
                   "trend": [p[1] if p else None for p in ht]},
        },
        "trades": [{
            "dir": t["dir"],
            "legs": [{"t": leg["t"], "px": r2(leg["px"]), "oz": leg["oz"]}
                     for leg in t["legs"]],
            "tp": r2(t.get("tp")),
            "stop_history": [{"t": h["t"], "stop": r2(h["stop"])}
                             for h in t["stop_history"]],
            "exit": r2(t["exit"]), "exit_t": t["exit_t"], "why": t["why"],
            "pl": round(t["pl"], 2), "bal_after": round(t["bal_after"], 2),
            "regime": t.get("regime"),
        } for t in trades],
    }
```

- [ ] **Step 4: Add the flag and the write**

In `build_parser()`'s **Output** group:

```python
    out.add_argument("--json", default=None, metavar="PATH",
                     help="write the full run (candles, indicators, trades, "
                          "stats) to this JSON file")
```

In `main()`, in the report tail next to the `--chart` handling:

```python
    if args.json:
        art = build_run_json(candles, trades, args,
                             {"bal": bal, "max_dd": max_dd, "valley": max_valley})
        Path(args.json).write_text(json.dumps(art, separators=(",", ":")))
        print(f"json       {args.json} "
              f"({Path(args.json).stat().st_size / 1e6:.1f} MB)")
```

Use whatever local names `main()` already holds for the run results — check them before writing this, rather than assuming.

- [ ] **Step 5: Run the tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_json.py -q
```

Expected: 6 passed.

- [ ] **Step 6: Produce a real 12-month artifact and check its size**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 \
    --balance 4000 --json /tmp/run12m.json 2>&1 | tail -4
service/.venv/bin/python -c "
import json; a=json.load(open('/tmp/run12m.json'))
print('bars', len(a['candles']['t']), 'trades', len(a['trades']))
print('stats', a['stats'])"
```

Expected: ~74,000 bars, a few hundred trades, and a file in the 5–7 MB range. Record the actual size — Task 7 embeds it.

- [ ] **Step 7: Commit**

```bash
git add scripts/backtest.py service/tests/test_backtest_json.py
git commit -m "feat(backtest): --json writes the full run artifact (candles, indicators, trades, stats)"
```

---

