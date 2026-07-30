# Strategy Framework + halftrend_ema_v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modular strategy layer — multiple strategies compiled into the EA behind one interface, shadow-evaluated every bar and logged per-strategy in SQLite — plus the first real strategy (Half Trend + EMA 55 dual confirmation).

**Architecture:** MQL5 side gets a `CStrategy` interface (now with `Id()` and `StopPrice()`), a `CStrategyRegistry` that evaluates all strategies each closed bar (active one trades/alerts, others are silent shadows), and `Strategies/HalfTrendEma.mqh`. The Python service gains backward-compatible `strategy_id` + `shadows` request fields, two new SQLite columns with a guarded migration, and a per-strategy scorecard in `stats()`. Spec: `docs/superpowers/specs/2026-07-30-strategy-framework-design.md`.

**Tech Stack:** MQL5 (MetaTrader 5), Python 3.11+ / FastAPI / pydantic v2 / sqlite3 / pytest.

## Global Constraints

- Python tests run from `service/`: `.venv/bin/python -m pytest` (fast suite must stay green; `-m slow` untouched by this work).
- MQL5 **cannot be compiled in this environment.** MQL5 tasks end with a self-check step; actual compilation happens once at the end (Task 10) by the user in MetaEditor on Windows. Expect 0 errors, 0 warnings.
- Strategy ids are exact strings: `"stub"` and `"halftrend_ema_v1"`. Request default `strategy_id` is `"unknown"`. Pre-migration rows show as `"pre-framework"` in stats.
- Strategy inputs and defaults (verbatim from spec): `HtAmplitude=4`, `EmaLength=55`, `ConfirmCloses=2`, `ActiveStrategy="halftrend_ema_v1"`.
- Design rules that bind every task: strategy is sole decision maker; AI grades only; fail-open everywhere; shadows never trade and never alert; no martingale (stop distance may vary, risk % may not grow after a loss).
- Commit messages follow the repo style (`feat(service):`, `feat(mt5):`, `docs:`) and end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- One behavioral deviation from the spec text, decided during planning: `halftrend_ema_v1` never emits `SIGNAL_EXIT`. Its exit condition (opposite Half Trend color + N closes across the EMA) is *identical* to the opposite entry, so the strategy emits the opposite entry signal and `TradeManager` closes an opposite-direction basket before entering (Task 8). The spec's exit behavior is preserved exactly; only the enum value differs. `SIGNAL_EXIT` remains in the enum for other strategies.

---

### Task 1: Request models — `strategy_id` + `shadows`

**Files:**
- Modify: `service/app/models.py`
- Test: `service/tests/test_models.py`

**Interfaces:**
- Produces: `ShadowSignal(strategy_id: str, signal: Literal["NONE","BUY","SELL","EXIT"])`; `AnalyzeRequest.strategy_id: str = "unknown"`; `AnalyzeRequest.shadows: list[ShadowSignal] = []`. Tasks 4 uses both fields; the MQL5 JSON in Task 7 must match these names exactly.

- [ ] **Step 1: Write the failing tests** — append to `service/tests/test_models.py`:

```python
def test_request_defaults_backward_compatible():
    from tests.fixtures import trend_candles
    req = AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="BUY",
                         candles=trend_candles(50))
    assert req.strategy_id == "unknown"
    assert req.shadows == []


def test_request_accepts_shadows():
    from tests.fixtures import trend_candles
    req = AnalyzeRequest(
        symbol="XAUUSD", timeframe="M15", signal="BUY",
        candles=trend_candles(50), strategy_id="halftrend_ema_v1",
        shadows=[{"strategy_id": "stub", "signal": "SELL"}])
    assert req.shadows[0].strategy_id == "stub"
    assert req.shadows[0].signal == "SELL"
```

(If `test_models.py` doesn't already import `AnalyzeRequest`, add `from app.models import AnalyzeRequest` at the top.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_models.py -v`
Expected: FAIL — `AnalyzeRequest` has no attribute `strategy_id` / validation error on `shadows`.

- [ ] **Step 3: Implement** — in `service/app/models.py`, add above `AnalyzeRequest`:

```python
class ShadowSignal(BaseModel):
    strategy_id: str
    signal: Literal["NONE", "BUY", "SELL", "EXIT"]
```

and add to `AnalyzeRequest`:

```python
    strategy_id: str = "unknown"
    shadows: list[ShadowSignal] = []
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_models.py -v` — Expected: PASS.
Then the whole fast suite: `.venv/bin/python -m pytest` — Expected: all pass (fields are optional, nothing else changes).

- [ ] **Step 5: Commit**

```bash
git add service/app/models.py service/tests/test_models.py
git commit -m "feat(service): strategy_id + shadows fields on AnalyzeRequest"
```

---

### Task 2: SQLite migration + tagged inserts

**Files:**
- Modify: `service/app/db.py`
- Test: `service/tests/test_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SignalDb.insert_signal(..., strategy_id: str = "unknown", is_active: bool = True)`; `signals` table columns `strategy_id TEXT`, `is_active INTEGER DEFAULT 1`. Task 3 and Task 4 rely on these.

- [ ] **Step 1: Write the failing tests** — append to `service/tests/test_db.py`:

```python
def test_migrates_pre_framework_db(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts INTEGER NOT NULL,
      bar_time INTEGER NOT NULL, symbol TEXT NOT NULL, signal TEXT NOT NULL,
      price REAL NOT NULL, direction TEXT, confidence REAL, regime TEXT,
      verdict TEXT, mode TEXT, ai_available INTEGER,
      outcome_price REAL, outcome_move REAL, ai_correct INTEGER)""")
    conn.execute("INSERT INTO signals (created_ts, bar_time, symbol, signal, price)"
                 " VALUES (1, 1, 'XAUUSD', 'BUY', 3000)")
    conn.commit()
    conn.close()
    db = SignalDb(path)  # must not raise; must add the new columns
    row = db.conn.execute("SELECT strategy_id, is_active FROM signals").fetchone()
    assert row == (None, 1)


def test_insert_records_strategy(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    db.insert_signal(bar_time=1, symbol="XAUUSD", signal="BUY", price=3000.0,
                     direction="bullish", confidence=0.8, regime="trend",
                     verdict="confirm", mode="grading", ai_available=True,
                     strategy_id="halftrend_ema_v1", is_active=False)
    row = db.conn.execute("SELECT strategy_id, is_active FROM signals").fetchone()
    assert row == ("halftrend_ema_v1", 0)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: FAIL — no such column `strategy_id` / unexpected keyword `strategy_id`.

- [ ] **Step 3: Implement** — in `service/app/db.py`:

Add the two columns to `_SCHEMA` (fresh databases get them directly):

```python
_SCHEMA = """CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  bar_time INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  signal TEXT NOT NULL,
  price REAL NOT NULL,
  direction TEXT, confidence REAL, regime TEXT, verdict TEXT,
  mode TEXT, ai_available INTEGER,
  outcome_price REAL, outcome_move REAL, ai_correct INTEGER,
  strategy_id TEXT, is_active INTEGER DEFAULT 1
)"""
```

Guarded migration in `__init__` after the `CREATE TABLE` executes:

```python
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(signals)")}
        if "strategy_id" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN strategy_id TEXT")
        if "is_active" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN is_active INTEGER DEFAULT 1")
        self.conn.commit()
```

Extend `insert_signal` (keyword-only, defaulted — existing callers keep working):

```python
    def insert_signal(self, *, bar_time, symbol, signal, price, direction,
                      confidence, regime, verdict, mode, ai_available,
                      strategy_id="unknown", is_active=True) -> int:
        cur = self.conn.execute(
            "INSERT INTO signals (created_ts, bar_time, symbol, signal, price, direction,"
            " confidence, regime, verdict, mode, ai_available, strategy_id, is_active)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), bar_time, symbol, signal, price, direction,
             confidence, regime, verdict, mode, int(ai_available),
             strategy_id, int(is_active)))
        self.conn.commit()
        return cur.lastrowid
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_db.py -v` — Expected: PASS.
Then: `.venv/bin/python -m pytest` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add service/app/db.py service/tests/test_db.py
git commit -m "feat(service): strategy_id/is_active columns with guarded migration"
```

---

### Task 3: Per-strategy scorecard in `stats()`

**Files:**
- Modify: `service/app/db.py`
- Test: `service/tests/test_db.py`

**Interfaces:**
- Consumes: columns from Task 2.
- Produces: `stats()` return gains `"by_strategy": {<strategy_id>: {"signals": int, "resolved": int, "hit_pct": float, "avg_move": float}}`. `hit_pct` = % of resolved BUY/SELL rows where the 16-bar move went the signal's way (this grades the strategy — distinct from the existing `ai_correct_pct`, which grades the AI). `avg_move` = mean move in the signal's direction (negative = strategy loses on average). `NULL` strategy_id groups under `"pre-framework"`. The UI spec will consume this dict as-is.

- [ ] **Step 1: Write the failing test** — append to `service/tests/test_db.py`:

```python
def test_per_strategy_stats(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    candles = trend_candles(200)
    common = dict(bar_time=candles[100].t, symbol="XAUUSD", price=candles[100].c,
                  direction="bullish", confidence=0.8, regime="trend",
                  verdict="confirm", mode="grading", ai_available=True)
    db.insert_signal(signal="BUY", strategy_id="winner", is_active=True, **common)
    db.insert_signal(signal="SELL", strategy_id="loser", is_active=False, **common)
    db.resolve_outcomes(candles)
    s = db.stats()
    assert s["by_strategy"]["winner"] == {
        "signals": 1, "resolved": 1, "hit_pct": 100.0,
        "avg_move": s["by_strategy"]["winner"]["avg_move"]}
    assert s["by_strategy"]["winner"]["avg_move"] > 0   # uptrend: BUY gains
    assert s["by_strategy"]["loser"]["hit_pct"] == 0.0
    assert s["by_strategy"]["loser"]["avg_move"] < 0    # uptrend: SELL loses
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_per_strategy_stats -v`
Expected: FAIL — KeyError `by_strategy`.

- [ ] **Step 3: Implement** — replace `stats()` in `service/app/db.py`:

```python
    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        done = self.conn.execute(
            "SELECT COUNT(*), COALESCE(AVG(ai_correct) * 100, 0) FROM signals"
            " WHERE outcome_price IS NOT NULL").fetchone()
        by_strategy = {}
        rows = self.conn.execute(
            "SELECT COALESCE(strategy_id, 'pre-framework'), COUNT(*),"
            " COUNT(outcome_price),"
            " AVG(CASE WHEN outcome_price IS NOT NULL THEN"
            "   CASE WHEN (signal='BUY' AND outcome_move > 0)"
            "          OR (signal='SELL' AND outcome_move < 0)"
            "   THEN 1.0 ELSE 0.0 END END) * 100,"
            " AVG(CASE WHEN outcome_price IS NOT NULL THEN"
            "   CASE WHEN signal='BUY' THEN outcome_move ELSE -outcome_move END END)"
            " FROM signals WHERE signal IN ('BUY','SELL')"
            " GROUP BY COALESCE(strategy_id, 'pre-framework')").fetchall()
        for sid, count, resolved, hit, avg in rows:
            by_strategy[sid] = {"signals": count, "resolved": resolved,
                                "hit_pct": round(hit or 0.0, 1),
                                "avg_move": round(avg or 0.0, 2)}
        return {"total": total, "resolved": done[0],
                "ai_correct_pct": round(done[1], 1), "by_strategy": by_strategy}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_db.py -v` — Expected: PASS (including the pre-existing `stats` assertions in `test_insert_and_resolve`).
Then: `.venv/bin/python -m pytest` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add service/app/db.py service/tests/test_db.py
git commit -m "feat(service): per-strategy hit-rate scorecard in stats()"
```

---

### Task 4: `/analyze` logs shadows (silently)

**Files:**
- Modify: `service/app/main.py`
- Test: `service/tests/test_api.py`

**Interfaces:**
- Consumes: `req.strategy_id` / `req.shadows` (Task 1), `insert_signal(strategy_id=, is_active=)` (Task 2), existing `combine()` from `app/verdict.py`.
- Produces: `/analyze` behavior — active row `is_active=1` + one Telegram alert; one row per non-NONE shadow with its own verdict, `is_active=0`, **no** alert. Response body unchanged.

- [ ] **Step 1: Write the failing tests** — append to `service/tests/test_api.py`:

```python
def test_shadows_logged_active_alerted_once(client, monkeypatch):
    from app import main
    alerts = []
    monkeypatch.setattr(main, "send_alert", lambda text, settings: alerts.append(text))
    payload = _payload("BUY")
    payload["strategy_id"] = "halftrend_ema_v1"
    payload["shadows"] = [{"strategy_id": "stub", "signal": "SELL"},
                          {"strategy_id": "quiet", "signal": "NONE"}]
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200
    rows = main.app.state.db.conn.execute(
        "SELECT strategy_id, signal, is_active FROM signals ORDER BY id").fetchall()
    assert rows == [("halftrend_ema_v1", "BUY", 1), ("stub", "SELL", 0)]
    assert len(alerts) == 1          # active signal only; shadows are silent


def test_old_style_request_tagged_unknown(client):
    from app import main
    r = client.post("/analyze", json=_payload("BUY"))   # no new fields
    assert r.status_code == 200
    row = main.app.state.db.conn.execute(
        "SELECT strategy_id, is_active FROM signals").fetchone()
    assert row == ("unknown", 1)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: the two new tests FAIL (`strategy_id` is NULL, shadow row missing).

- [ ] **Step 3: Implement** — in `service/app/main.py`, replace the `if req.signal != "NONE":` block at the end of `analyze()` with:

```python
    if req.signal != "NONE":
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=req.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, verdict=resp.verdict, mode=settings.mode,
            ai_available=ai_available, strategy_id=req.strategy_id, is_active=True)
        send_alert(format_report(req, resp), settings)
    for shadow in req.shadows:
        if shadow.signal == "NONE":
            continue
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=shadow.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, mode=settings.mode, ai_available=ai_available,
            verdict=combine(shadow.signal, direction, confidence,
                            settings.confirm_threshold),
            strategy_id=shadow.strategy_id, is_active=False)
    return resp
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_api.py -v` — Expected: PASS.
Then: `.venv/bin/python -m pytest` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add service/app/main.py service/tests/test_api.py
git commit -m "feat(service): log shadow strategy signals without alerting"
```

---

### Task 5: MQL5 — `CStrategy` interface gains `Id()` and `StopPrice()`

**Files:**
- Modify: `mt5/Include/XauAssistant/Strategy.mqh`

**Interfaces:**
- Produces: `virtual string Id()` (returns `"stub"` in the base class — the base *is* the stub strategy) and `virtual double StopPrice(ENUM_SIGNAL dir)` (0 = "no preference, use the ATR default"). Tasks 6–9 rely on these exact signatures.

- [ ] **Step 1: Implement** — replace the `CStrategy` class in `mt5/Include/XauAssistant/Strategy.mqh` (enum and `SignalToString` stay unchanged):

```mql5
class CStrategy
  {
public:
   // Stable identifier — flows through the API into SQLite per-strategy stats.
   virtual string      Id() { return "stub"; }
   // Called once per closed bar.
   virtual ENUM_SIGNAL Evaluate() { return SIGNAL_NONE; }
   // True while the entry condition remains valid (pyramiding gate, spec 5b).
   virtual bool        ConditionStillTrue(ENUM_SIGNAL dir) { return false; }
   // Strategy's preferred stop for a new position; 0 = use the ATR default.
   virtual double      StopPrice(ENUM_SIGNAL dir) { return 0.0; }
  };
```

Also update the file's header comment: it is no longer "the ONLY file the real strategy rules will touch" — replace with:

```mql5
// Strategy.mqh — strategy interface. Concrete strategies live in
// Include/XauAssistant/Strategies/ and register in the EA's OnInit.
```

- [ ] **Step 2: Self-check** — confirm: base class still compiles as a concrete stub (all methods have bodies); no other file references removed symbols (`grep -rn "CStrategy" mt5/` — only Strategy.mqh, SignalManager.mqh include, XauAssistant.mq5, TradeManager.mqh include should appear).

- [ ] **Step 3: Commit**

```bash
git add mt5/Include/XauAssistant/Strategy.mqh
git commit -m "feat(mt5): CStrategy interface with Id() and StopPrice()"
```

---

### Task 6: MQL5 — `StrategyRegistry.mqh`

**Files:**
- Create: `mt5/Include/XauAssistant/StrategyRegistry.mqh`

**Interfaces:**
- Consumes: `CStrategy` (Task 5).
- Produces: `CStrategyRegistry` with `Register(CStrategy *s)`, `bool SetActive(string id)`, `CStrategy *Active()`, `int Count()`, `CStrategy *Get(int i)`, `void Clear()` (deletes owned pointers). Task 8 wires it into the EA.

- [ ] **Step 1: Implement** — create `mt5/Include/XauAssistant/StrategyRegistry.mqh`:

```mql5
// StrategyRegistry.mqh — owns all compiled-in strategies; one is active.
// The active strategy trades and alerts; the rest are silent shadows.
#ifndef XAU_STRATEGYREGISTRY_MQH
#define XAU_STRATEGYREGISTRY_MQH
#include <XauAssistant/Strategy.mqh>

class CStrategyRegistry
  {
private:
   CStrategy *m_strategies[];
   int        m_active;

public:
   CStrategyRegistry() : m_active(-1) {}

   void Register(CStrategy *s)
     {
      int n = ArraySize(m_strategies);
      ArrayResize(m_strategies, n + 1);
      m_strategies[n] = s;
     }

   bool SetActive(string id)
     {
      for(int i = 0; i < ArraySize(m_strategies); i++)
         if(m_strategies[i].Id() == id) { m_active = i; return true; }
      return false;
     }

   CStrategy *Active()    { return (m_active >= 0) ? m_strategies[m_active] : NULL; }
   int        Count()     { return ArraySize(m_strategies); }
   CStrategy *Get(int i)  { return m_strategies[i]; }

   void Clear()
     {
      for(int i = 0; i < ArraySize(m_strategies); i++)
         if(CheckPointer(m_strategies[i]) == POINTER_DYNAMIC) delete m_strategies[i];
      ArrayResize(m_strategies, 0);
      m_active = -1;
     }
  };
#endif
```

- [ ] **Step 2: Self-check** — every registry method used by later tasks exists with the exact names above; `Clear()` only deletes dynamic pointers.

- [ ] **Step 3: Commit**

```bash
git add mt5/Include/XauAssistant/StrategyRegistry.mqh
git commit -m "feat(mt5): strategy registry with active selection and shadow access"
```

---

### Task 7: MQL5 — `Strategies/HalfTrendEma.mqh`

**Files:**
- Create: `mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh`

**Interfaces:**
- Consumes: `CStrategy` (Task 5).
- Produces: `CHalfTrendEmaStrategy(int amplitude, int emaLen, int confirmCloses)` implementing `Id()="halftrend_ema_v1"`, `Evaluate()`, `ConditionStillTrue()`, `StopPrice()`. Task 9 constructs it with the EA inputs.

Algorithm notes for the implementer (spec §6): Half Trend (Everget) trend state depends only on: highest high / lowest low over `amplitude` bars, SMA of highs / SMA of lows over `amplitude` bars, and the previous bar's high/low. The ATR(100)-based channel in the original affects **drawing only**, not the trend state, so it is omitted. Trend 0 = blue/up, 1 = red/down. A 600-bar warm-up replay on first call washes out seed-value differences vs TradingView. The strategy is stop-and-reverse: the exit condition equals the opposite entry, so only BUY/SELL/NONE are ever returned (see Global Constraints).

- [ ] **Step 1: Implement** — create `mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh`:

```mql5
// HalfTrendEma.mqh — Half Trend (amplitude 4) + EMA 55 dual confirmation.
// Adapted from the Crypto9ite TradingView strategy for XAUUSD M15.
// Entry: Half Trend color + ConfirmCloses consecutive closes beyond the EMA,
// fired once per Half Trend flip. Stop: wick extreme since the flip.
#ifndef XAU_STRAT_HALFTREND_EMA_MQH
#define XAU_STRAT_HALFTREND_EMA_MQH
#include <XauAssistant/Strategy.mqh>

class CHalfTrendEmaStrategy : public CStrategy
  {
private:
   int      m_amplitude;
   int      m_emaLen;
   int      m_confirm;
   int      m_emaHandle;
   int      m_warmupBars;

   int      m_trend;         // 0 = blue/up, 1 = red/down, -1 = not yet seeded
   int      m_nextTrend;
   double   m_maxLowPrice;
   double   m_minHighPrice;
   double   m_extreme;       // lowest low since flip to blue / highest high since flip to red
   int      m_consecAbove;
   int      m_consecBelow;
   bool     m_fired;         // one entry per Half Trend flip
   datetime m_lastProcessed;

   void ProcessClosedBar(int shift)
     {
      double hi[], lo[], cl[];
      if(CopyHigh(_Symbol, PERIOD_CURRENT, shift, m_amplitude, hi) != m_amplitude) return;
      if(CopyLow(_Symbol, PERIOD_CURRENT, shift, m_amplitude, lo)  != m_amplitude) return;
      if(CopyClose(_Symbol, PERIOD_CURRENT, shift, 1, cl) != 1) return;
      double highPrice = hi[ArrayMaximum(hi)];
      double lowPrice  = lo[ArrayMinimum(lo)];
      double highma = 0, lowma = 0;
      for(int i = 0; i < m_amplitude; i++) { highma += hi[i]; lowma += lo[i]; }
      highma /= m_amplitude;
      lowma  /= m_amplitude;
      double close    = cl[0];
      double prevLow  = iLow(_Symbol, PERIOD_CURRENT, shift + 1);
      double prevHigh = iHigh(_Symbol, PERIOD_CURRENT, shift + 1);

      if(m_trend < 0)  // seed on the very first processed bar
        {
         m_trend = 0; m_nextTrend = 0;
         m_maxLowPrice = prevLow; m_minHighPrice = prevHigh;
         m_extreme = lowPrice;
        }

      int prevTrend = m_trend;
      if(m_nextTrend == 1)
        {
         m_maxLowPrice = MathMax(lowPrice, m_maxLowPrice);
         if(highma < m_maxLowPrice && close < prevLow)
           { m_trend = 1; m_nextTrend = 0; m_minHighPrice = highPrice; }
        }
      else
        {
         m_minHighPrice = MathMin(highPrice, m_minHighPrice);
         if(lowma > m_minHighPrice && close > prevHigh)
           { m_trend = 0; m_nextTrend = 1; m_maxLowPrice = lowPrice; }
        }

      double barLow  = iLow(_Symbol, PERIOD_CURRENT, shift);
      double barHigh = iHigh(_Symbol, PERIOD_CURRENT, shift);
      if(m_trend != prevTrend)
        {
         m_fired = false;   // a flip re-arms the once-per-trend entry
         m_extreme = (m_trend == 0) ? barLow : barHigh;
        }
      else
         m_extreme = (m_trend == 0) ? MathMin(m_extreme, barLow)
                                    : MathMax(m_extreme, barHigh);

      double emaBuf[];
      if(CopyBuffer(m_emaHandle, 0, shift, 1, emaBuf) == 1)
        {
         if(close > emaBuf[0])      { m_consecAbove++; m_consecBelow = 0; }
         else if(close < emaBuf[0]) { m_consecBelow++; m_consecAbove = 0; }
        }
     }

public:
   CHalfTrendEmaStrategy(int amplitude, int emaLen, int confirmCloses)
      : m_amplitude(amplitude), m_emaLen(emaLen), m_confirm(confirmCloses),
        m_warmupBars(600), m_trend(-1), m_nextTrend(0),
        m_maxLowPrice(0), m_minHighPrice(0), m_extreme(0),
        m_consecAbove(0), m_consecBelow(0), m_fired(false), m_lastProcessed(0)
     {
      m_emaHandle = iMA(_Symbol, PERIOD_CURRENT, m_emaLen, 0, MODE_EMA, PRICE_CLOSE);
     }

   virtual string Id() { return "halftrend_ema_v1"; }

   virtual ENUM_SIGNAL Evaluate()
     {
      datetime closed = iTime(_Symbol, PERIOD_CURRENT, 1);
      if(closed == 0 || closed == m_lastProcessed) return SIGNAL_NONE;
      if(m_lastProcessed == 0)
        {
         int avail = Bars(_Symbol, PERIOD_CURRENT) - m_amplitude - 2;
         int from = MathMin(m_warmupBars, MathMax(avail, 1));
         for(int s = from; s >= 1; s--) ProcessClosedBar(s);   // oldest -> newest
        }
      else
         ProcessClosedBar(1);
      m_lastProcessed = closed;

      if(!m_fired)
        {
         if(m_trend == 0 && m_consecAbove >= m_confirm)
           { m_fired = true; return SIGNAL_BUY; }
         if(m_trend == 1 && m_consecBelow >= m_confirm)
           { m_fired = true; return SIGNAL_SELL; }
        }
      return SIGNAL_NONE;
     }

   virtual bool ConditionStillTrue(ENUM_SIGNAL dir)
     {
      double emaBuf[];
      if(CopyBuffer(m_emaHandle, 0, 1, 1, emaBuf) != 1) return false;
      double close = iClose(_Symbol, PERIOD_CURRENT, 1);
      if(dir == SIGNAL_BUY)  return m_trend == 0 && close > emaBuf[0];
      if(dir == SIGNAL_SELL) return m_trend == 1 && close < emaBuf[0];
      return false;
     }

   virtual double StopPrice(ENUM_SIGNAL dir)
     {
      if(dir == SIGNAL_BUY  && m_trend == 0) return m_extreme;
      if(dir == SIGNAL_SELL && m_trend == 1) return m_extreme;
      return 0.0;
     }
  };
#endif
```

- [ ] **Step 2: Self-check** against spec §6, line by line: amplitude/EMA/confirm are constructor params (defaults come from EA inputs in Task 9); fires once per flip (`m_fired` reset only on flip); `StopPrice` returns the wick extreme since the flip; `ConditionStillTrue` = trend color + close on correct EMA side; only closed bars are read (all reads use shift ≥ 1); warm-up replays oldest→newest.

- [ ] **Step 3: Commit**

```bash
git add mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh
git commit -m "feat(mt5): HalfTrend+EMA55 dual-confirmation strategy (halftrend_ema_v1)"
```

---

### Task 8: MQL5 — TradeManager: strategy stops + reversal handling

**Files:**
- Modify: `mt5/Include/XauAssistant/TradeManager.mqh`

**Interfaces:**
- Consumes: `StopPrice()` value (passed in by the EA, Task 9).
- Produces: `OnSignal(ENUM_SIGNAL sig, double atr_value, double stopPrice = 0)` — third param optional so existing call sites compile. New behavior: an opposite-direction signal closes the open basket first (stop-and-reverse); a valid `stopPrice` (right side of price) is used for the SL, else the ATR fallback.

- [ ] **Step 1: Implement** — in `mt5/Include/XauAssistant/TradeManager.mqh`:

Add a private helper below `BasketProfit()`:

```mql5
   long OwnType()
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 &&
            PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            return PositionGetInteger(POSITION_TYPE);
      return -1;
     }
```

Replace `OnSignal` with:

```mql5
   void OnSignal(ENUM_SIGNAL sig, double atr_value, double stopPrice = 0)
     {
      if(sig == SIGNAL_EXIT) { CloseAll("strategy EXIT"); return; }
      if(sig != SIGNAL_BUY && sig != SIGNAL_SELL) return;
      if(CountOwn() > 0)
        {
         long ptype = OwnType();
         bool opposite = (sig == SIGNAL_BUY  && ptype == POSITION_TYPE_SELL) ||
                         (sig == SIGNAL_SELL && ptype == POSITION_TYPE_BUY);
         if(!opposite) return;              // same direction: one cycle at a time
         CloseAll("reversal signal");       // stop-and-reverse, then enter below
        }
      string why;
      if(!m_risk.CanEnter(why)) { Print("Entry blocked: ", why); return; }
      double price = (sig == SIGNAL_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl;
      bool validStop = stopPrice > 0 &&
                       ((sig == SIGNAL_BUY  && stopPrice < price) ||
                        (sig == SIGNAL_SELL && stopPrice > price));
      if(validStop) sl = stopPrice;
      else sl = (sig == SIGNAL_BUY) ? price - m_stopAtrMult * atr_value
                                    : price + m_stopAtrMult * atr_value;
      double sl_points = MathAbs(price - sl) / _Point;
      double lots = m_risk.CalcLots(sl_points, m_ratios[0]);
      if(lots <= 0) return;
      bool ok = (sig == SIGNAL_BUY) ? m_trade.Buy(lots, _Symbol, 0, sl)
                                    : m_trade.Sell(lots, _Symbol, 0, sl);
      if(ok)
        {
         m_lastEntryPrice = price;
         GlobalVariableSet(CycleKey(), AccountInfoDouble(ACCOUNT_BALANCE));
        }
     }
```

- [ ] **Step 2: Self-check** — no-martingale invariant intact: lot size still comes only from `m_risk.CalcLots` (fixed-fractional); a wide strategy stop shrinks lots, never grows risk. Reversal path still passes through `m_risk.CanEnter` (kill switch, window, exposure, spread all still gate the new entry). Default `stopPrice = 0` keeps the old two-arg call compiling.

- [ ] **Step 3: Commit**

```bash
git add mt5/Include/XauAssistant/TradeManager.mqh
git commit -m "feat(mt5): strategy-defined stops + stop-and-reverse handling"
```

---

### Task 9: MQL5 — wire registry + shadows into the EA and API client

**Files:**
- Modify: `mt5/Experts/XauAssistant.mq5`
- Modify: `mt5/Include/XauAssistant/AiApi.mqh`

**Interfaces:**
- Consumes: `CStrategyRegistry` (Task 6), `CHalfTrendEmaStrategy` (Task 7), `OnSignal(..., stopPrice)` (Task 8).
- Produces: JSON request whose `strategy_id` / `shadows` fields match Task 1's models exactly: `"strategy_id":"<id>","shadows":[{"strategy_id":"stub","signal":"SELL"}]`.

- [ ] **Step 1: Extend `CAiApi`** — in `mt5/Include/XauAssistant/AiApi.mqh`, change `BuildJson` and `Analyze` to carry the new fields:

`BuildJson` signature and tail (candle loop unchanged):

```mql5
   string BuildJson(ENUM_SIGNAL sig, int count, string strategyId,
                    string &shadowIds[], ENUM_SIGNAL &shadowSigs[])
     {
      // ... existing CopyRates + candle loop unchanged ...
      json += "],\"strategy_id\":\"" + strategyId + "\",\"shadows\":[";
      for(int i = 0; i < ArraySize(shadowIds); i++)
        {
         if(i > 0) json += ",";
         json += "{\"strategy_id\":\"" + shadowIds[i] + "\",\"signal\":\"" +
                 SignalToString(shadowSigs[i]) + "\"}";
        }
      return json + "]}";
     }
```

(The old `return json + "]}";` line is replaced by the block above.)

`Analyze` signature (body otherwise unchanged, first line adapted):

```mql5
   bool Analyze(ENUM_SIGNAL sig, string strategyId, string &shadowIds[],
                ENUM_SIGNAL &shadowSigs[], AiResponse &out)
     {
      out.ai_available = false;
      string json = BuildJson(sig, 200, strategyId, shadowIds, shadowSigs);
      // ... rest unchanged ...
```

- [ ] **Step 2: Wire the EA** — in `mt5/Experts/XauAssistant.mq5`:

Add include and inputs (with the existing includes/inputs):

```mql5
#include <XauAssistant/StrategyRegistry.mqh>
#include <XauAssistant/Strategies/HalfTrendEma.mqh>

input string ActiveStrategy = "halftrend_ema_v1"; // which registered strategy trades
input int    HtAmplitude    = 4;                  // Half Trend amplitude
input int    EmaLength      = 55;                 // confirmation EMA
input int    ConfirmCloses  = 2;                  // consecutive closes beyond EMA
```

Replace the global `CStrategy g_strategy;` with `CStrategyRegistry g_registry;`.

In `OnInit()` (after the live-account guard):

```mql5
   g_registry.Register(new CStrategy());   // "stub" — kept as a shadow baseline
   g_registry.Register(new CHalfTrendEmaStrategy(HtAmplitude, EmaLength, ConfirmCloses));
   if(!g_registry.SetActive(ActiveStrategy))
     {
      g_alerts.Notify("XauAssistant: unknown ActiveStrategy '" + ActiveStrategy + "'");
      return INIT_FAILED;
     }
```

Add `OnDeinit`:

```mql5
void OnDeinit(const int reason) { g_registry.Clear(); }
```

Replace `ProcessBar()` with:

```mql5
void ProcessBar()
  {
   CStrategy *active = g_registry.Active();
   ENUM_SIGNAL sig = SIGNAL_NONE;
   string shadowIds[];
   ENUM_SIGNAL shadowSigs[];
   for(int i = 0; i < g_registry.Count(); i++)
     {
      CStrategy *st = g_registry.Get(i);
      ENUM_SIGNAL s = st.Evaluate();     // every strategy evaluates every bar
      if(st == active) { sig = s; continue; }
      if(s == SIGNAL_NONE) continue;
      int n = ArraySize(shadowIds);
      ArrayResize(shadowIds, n + 1);
      ArrayResize(shadowSigs, n + 1);
      shadowIds[n] = st.Id();
      shadowSigs[n] = s;
     }
   if(DebugFireTestSignal && !g_debugFired) { sig = SIGNAL_BUY; g_debugFired = true; }

   g_risk.OnBarUpdate();
   double atrBuf[];
   double atrVal = (CopyBuffer(g_atrHandle, 0, 1, 1, atrBuf) == 1) ? atrBuf[0] : 0;

   // AUTO mode executes FIRST — the AI is never in the trade path (spec 2.2)
   if(ExecutionMode == EXEC_AUTO && atrVal > 0)
     {
      g_trades.OnSignal(sig, atrVal, active.StopPrice(sig));
      g_trades.Manage(atrVal, active.ConditionStillTrue(sig));
     }

   if(sig == SIGNAL_NONE && ArraySize(shadowIds) == 0)
     {
      AiResponse quiet;
      g_api.Analyze(sig, active.Id(), shadowIds, shadowSigs, quiet);
      return;   // keeps outcome-resolution data flowing (spec 6.3)
     }
   AiResponse r;
   bool ok = g_api.Analyze(sig, active.Id(), shadowIds, shadowSigs, r);
   if(sig == SIGNAL_NONE) return;        // shadows logged; nothing to alert
   string report = g_sm.BuildReport(sig, r, ok) + "\n" + g_risk.Status();
   g_alerts.Draw(sig, report);
   g_alerts.Notify(report);
  }
```

- [ ] **Step 3: Self-check** — every `/analyze` call still happens exactly once per bar (NONE-with-no-shadows, NONE-with-shadows, and active-signal paths all call `Analyze` once); alerts fire only for the active signal; JSON field names match Task 1's pydantic models byte-for-byte; `g_strategy` no longer referenced anywhere (`grep -n "g_strategy" mt5/Experts/XauAssistant.mq5` → no hits).

- [ ] **Step 4: Commit**

```bash
git add mt5/Experts/XauAssistant.mq5 mt5/Include/XauAssistant/AiApi.mqh
git commit -m "feat(mt5): registry-driven evaluation with shadow signal forwarding"
```

---

### Task 10: Docs + full verification

**Files:**
- Modify: `README.md` (inputs table + strategy section)
- Modify: `CLAUDE.md` (current-status paragraph)

- [ ] **Step 1: Update `README.md`** — in the section-4 inputs table add rows:

```markdown
| `ActiveStrategy` | `halftrend_ema_v1` | Which registered strategy trades; others run as logged shadows |
| `HtAmplitude` / `EmaLength` / `ConfirmCloses` | `4` / `55` / `2` | halftrend_ema_v1 parameters |
```

and replace the README line saying the strategy is a stub ("The strategy itself is currently a stub…") with:

```markdown
Strategies live in `mt5/Include/XauAssistant/Strategies/` behind the
`CStrategy` interface and register in the EA's `OnInit`. All registered
strategies are shadow-evaluated and logged every bar; only `ActiveStrategy`
trades. First real strategy: `halftrend_ema_v1` (Half Trend amplitude 4 +
EMA 55 dual confirmation, stop at the wick extreme since the trend flip).
Per-strategy hit-rates accumulate in `xau_assistant.db` (`stats()`).
```

- [ ] **Step 2: Update `CLAUDE.md`** — replace the "Current status" paragraph's first sentence with:

```markdown
Strategy layer is modular: strategies live in `mt5/Include/XauAssistant/Strategies/`
behind `CStrategy` (with `Id()` and `StopPrice()`), registered in the EA's `OnInit`,
all shadow-evaluated per bar (only `ActiveStrategy` trades/alerts). First real
strategy: `halftrend_ema_v1`. The `/analyze` request carries `strategy_id` + `shadows`;
SQLite tags every row and `stats()` reports per-strategy hit-rates.
```

- [ ] **Step 3: Full test run**

Run from `service/`: `.venv/bin/python -m pytest -v`
Expected: all fast tests pass, including the new ones from Tasks 1–4.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: strategy framework usage (ActiveStrategy input, shadow logging)"
```

- [ ] **Step 5: Hand the user the manual MQL5 checklist** (cannot be done in WSL — present this list at the end):

1. Copy `mt5/Experts/XauAssistant.mq5` and `mt5/Include/XauAssistant/` into the MT5 data folder (README §3).
2. Compile in MetaEditor — expect 0 errors, 0 warnings.
3. Attach to XAUUSD M15 with `DebugFireTestSignal=true` → verify the debug BUY row lands in `signals` with `strategy_id` set and `is_active=1`.
4. Watch one real bar → confirm exactly one `/analyze` request per bar in the uvicorn log, with `shadows` present when a shadow fires.
5. Spot-check Half Trend faithfulness: same XAUUSD M15 chart on TradingView (Half Trend amplitude 4) vs the EA's flips (add temporary `Print` of `m_trend` flips, or compare entry arrows) — accept if flip bars match.
6. Strategy Tester backtest of `halftrend_ema_v1` before any AUTO use.
