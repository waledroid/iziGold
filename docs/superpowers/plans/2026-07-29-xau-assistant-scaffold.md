# XAU Assistant Scaffold (Phases 1–2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete XAU Assistant pipeline — FastAPI AI service (Chronos-Bolt) + MQL5 EA framework with stub strategy — testable end-to-end with a debug signal, per spec `docs/superpowers/specs/2026-07-29-xau-assistant-design.md`.

**Architecture:** Two subsystems joined by one HTTP contract. The Python service (WSL2, port 8000) turns quantile forecasts into direction/confidence, classifies regime classically, combines a verdict, logs to SQLite, and sends Telegram. The MQL5 EA evaluates the (stub) strategy on each closed bar, executes first in AUTO mode, calls the service after, and alerts. Real strategy rules arrive in a later plan and touch only `Strategy.mqh`.

**Tech Stack:** Python 3.11+, FastAPI, pydantic v2, numpy, httpx, chronos-forecasting (torch CPU), SQLite (stdlib sqlite3), pytest; MQL5 (MetaTrader 5 build ≥ 4000), CTrade.

## Global Constraints

- API contract (spec §7): request `{symbol, timeframe, signal, candles:[{t,o,h,l,c,v}×200]}`; response `{direction: bullish|bearish|neutral, confidence: 0–1, regime: trend|range|high_volatility, verdict: confirm|neutral|conflict, mode: grading|veto, ai_available: bool}`.
- Fail-open (spec §2.3): model failure inside the service → `direction=neutral, confidence=0, ai_available=false`, HTTP still 200. EA-side WebRequest failure → alert marked "AI unavailable".
- The AI is never in the trade path in GRADING mode (spec §2.2): AUTO executes before the API call.
- Strategy signal enum everywhere: `NONE | BUY | SELL | EXIT`.
- Defaults (spec §5/§5a/§5b/§5c): risk 0.5 %, max DD 10 %, pyramid 3 positions at ratios 1.0/0.7/0.4, add trigger 1.0×ATR, profit target 2.0 %, stop 2×ATR, window 15–18 h server time, exposure 60 min/day, ADX threshold 25, confirm threshold 0.6, horizon 16 bars, context 200 candles.
- Model default `amazon/chronos-bolt-small`; forecaster selected via env `FORECASTER=chronos|timemoe|fake`. Heavy deps (torch/chronos) live in `requirements-model.txt` so tests run without them.
- MQL5 layout mirrors the MT5 data folder: `mt5/Experts/XauAssistant.mq5`, `mt5/Include/XauAssistant/*.mqh`, includes via `#include <XauAssistant/File.mqh>`.
- MQL5 compile check from WSL: `"/mnt/c/Program Files/MetaTrader 5/MetaEditor64.exe" /compile:"C:\\Users\\aatanda\\Desktop\\xau\\mt5\\Experts\\XauAssistant.mq5" /inc:"C:\\Users\\aatanda\\Desktop\\xau\\mt5" /log` then read the `.log` beside the file; expect `0 errors`. If MetaEditor is installed elsewhere, adjust the path once and reuse.
- All Python commands run from `service/` with the venv active: `cd service && source .venv/bin/activate`.
- Commit after every green test cycle.

---

### Task 1: Service scaffold + config

**Files:**
- Create: `service/requirements.txt`, `service/requirements-model.txt`, `service/.env.example`, `service/app/__init__.py`, `service/app/config.py`
- Test: `service/tests/test_config.py`

**Interfaces:**
- Produces: `from app.config import settings` — `Settings` with fields `forecaster:str="chronos"`, `chronos_model:str="amazon/chronos-bolt-small"`, `horizon:int=16`, `context_len:int=200`, `mode:str="grading"`, `confirm_threshold:float=0.6`, `telegram_bot_token:str=""`, `telegram_chat_id:str=""`, `db_path:str="xau_assistant.db"`.

- [ ] **Step 1: Create venv and install core deps**

`service/requirements.txt`:
```
fastapi>=0.111
uvicorn[standard]>=0.30
pydantic>=2.7
pydantic-settings>=2.3
numpy>=1.26
httpx>=0.27
pytest>=8.2
```
`service/requirements-model.txt`:
```
torch>=2.3
chronos-forecasting>=1.4
```
Run: `cd service && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test** — `service/tests/test_config.py`:
```python
from app.config import Settings

def test_defaults():
    s = Settings(_env_file=None)
    assert s.forecaster == "chronos"
    assert s.mode == "grading"
    assert s.horizon == 16
    assert s.confirm_threshold == 0.6

def test_env_override(monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    assert Settings(_env_file=None).forecaster == "fake"
```

- [ ] **Step 3: Run to verify it fails** — `pytest tests/test_config.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 4: Implement** — `service/app/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    forecaster: str = "chronos"          # chronos | timemoe | fake
    chronos_model: str = "amazon/chronos-bolt-small"
    horizon: int = 16
    context_len: int = 200
    mode: str = "grading"                # grading | veto
    confirm_threshold: float = 0.6
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    db_path: str = "xau_assistant.db"


settings = Settings()
```
Create empty `service/app/__init__.py`, `service/tests/__init__.py`. `service/.env.example` = the same fields as `KEY=value` lines with defaults and comments (`FORECASTER=chronos`, `TELEGRAM_BOT_TOKEN=`, `TELEGRAM_CHAT_ID=`, `MODE=grading`, `DB_PATH=xau_assistant.db`).

- [ ] **Step 5: Run to verify pass** — `pytest tests/test_config.py -v` → 2 PASS.

- [ ] **Step 6: Commit** — `git add service && git commit -m "feat(service): scaffold + settings"` (add `service/.venv/`, `__pycache__/`, `*.db`, `*.log` to root `.gitignore` first).

---

### Task 2: Request/response schemas

**Files:**
- Create: `service/app/models.py`
- Test: `service/tests/test_models.py`

**Interfaces:**
- Produces: `Candle(t:int,o,h,l,c:float,v:float=0)`; `AnalyzeRequest(symbol, timeframe, signal, candles:list[Candle])` with `signal ∈ {"NONE","BUY","SELL","EXIT"}` and `len(candles) ≥ 50`; `AnalyzeResponse(direction, confidence, regime, verdict, mode, ai_available)` with literals per Global Constraints.

- [ ] **Step 1: Failing test** — `service/tests/test_models.py`:
```python
import pytest
from pydantic import ValidationError
from app.models import AnalyzeRequest, AnalyzeResponse, Candle

def mk_candles(n=50):
    return [Candle(t=1700000000 + i * 900, o=1.0, h=2.0, l=0.5, c=1.5, v=10) for i in range(n)]

def test_valid_request():
    r = AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="BUY", candles=mk_candles())
    assert r.signal == "BUY"

def test_rejects_bad_signal():
    with pytest.raises(ValidationError):
        AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="HOLD", candles=mk_candles())

def test_rejects_short_history():
    with pytest.raises(ValidationError):
        AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="NONE", candles=mk_candles(10))

def test_response_bounds():
    with pytest.raises(ValidationError):
        AnalyzeResponse(direction="bullish", confidence=1.5, regime="trend",
                        verdict="confirm", mode="grading", ai_available=True)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — `service/app/models.py`:
```python
from typing import Literal
from pydantic import BaseModel, Field


class Candle(BaseModel):
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str
    signal: Literal["NONE", "BUY", "SELL", "EXIT"]
    candles: list[Candle] = Field(min_length=50)


class AnalyzeResponse(BaseModel):
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    regime: Literal["trend", "range", "high_volatility"]
    verdict: Literal["confirm", "neutral", "conflict"]
    mode: Literal["grading", "veto"]
    ai_available: bool
```

- [ ] **Step 4: Run** → 4 PASS. **Step 5: Commit** `feat(service): analyze schemas`.

---

### Task 3: Regime classifier (classical)

**Files:**
- Create: `service/app/regime.py`, `service/tests/fixtures.py`
- Test: `service/tests/test_regime.py`

**Interfaces:**
- Produces: `atr_series(h,l,c,period=14) -> np.ndarray`; `adx_series(h,l,c,period=14) -> np.ndarray`; `classify_regime(candles: list[Candle], adx_threshold=25.0, vol_percentile=0.8) -> str` returning `"trend"|"range"|"high_volatility"`; `last_atr(candles) -> float`. Rule: ATR percentile-rank over last 100 ATR values ≥ 0.8 → high_volatility; else ADX ≥ threshold → trend; else range.
- Produces (fixtures): `trend_candles(n=200)`, `range_candles(n=200)`, `spike_candles(n=200)` — reused by Tasks 5, 7, 9.

- [ ] **Step 1: Fixtures** — `service/tests/fixtures.py`:
```python
import math
from app.models import Candle


def _mk(i, o, c, hl_pad, v=100.0):
    hi, lo = max(o, c) + hl_pad, min(o, c) - hl_pad
    return Candle(t=1700000000 + i * 900, o=o, h=hi, l=lo, c=c, v=v)


def trend_candles(n=200):
    out, price = [], 3000.0
    for i in range(n):
        out.append(_mk(i, price, price + 2.0, 0.5))
        price += 2.0
    return out


def range_candles(n=200):
    out = []
    for i in range(n):
        o = 3000.0 + 3.0 * math.sin(i / 4.0)
        c = 3000.0 + 3.0 * math.sin((i + 1) / 4.0)
        out.append(_mk(i, o, c, 0.5))
    return out


def spike_candles(n=200):
    out = list(range_candles(n - 10))
    price = out[-1].c
    for i in range(n - 10, n):
        out.append(_mk(i, price, price + (15.0 if i % 2 else -15.0), 8.0))
        price = out[-1].c
    return out
```

- [ ] **Step 2: Failing test** — `service/tests/test_regime.py`:
```python
from app.regime import classify_regime, last_atr
from tests.fixtures import trend_candles, range_candles, spike_candles

def test_trend():
    assert classify_regime(trend_candles()) == "trend"

def test_range():
    assert classify_regime(range_candles()) == "range"

def test_high_volatility():
    assert classify_regime(spike_candles()) == "high_volatility"

def test_last_atr_positive():
    assert last_atr(trend_candles()) > 0
```

- [ ] **Step 3: Run** → FAIL. **Step 4: Implement** — `service/app/regime.py`:
```python
import numpy as np


def _hlc(candles):
    return (np.array([x.h for x in candles]),
            np.array([x.l for x in candles]),
            np.array([x.c for x in candles]))


def _true_range(h, l, c):
    prev = np.concatenate(([c[0]], c[:-1]))
    return np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))


def atr_series(h, l, c, period=14):
    tr = _true_range(h, l, c)
    out = np.full_like(tr, np.nan)
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def adx_series(h, l, c, period=14):
    up, down = h[1:] - h[:-1], l[:-1] - l[1:]
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    mdm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _true_range(h, l, c)[1:]

    def wilder_sum(x):
        s = np.full_like(x, np.nan)
        s[period - 1] = x[:period].sum()
        for i in range(period, len(x)):
            s[i] = s[i - 1] - s[i - 1] / period + x[i]
        return s

    trs, pdms, mdms = wilder_sum(tr), wilder_sum(pdm), wilder_sum(mdm)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi, mdi = 100 * pdms / trs, 100 * mdms / trs
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    out = np.full_like(dx, np.nan)
    start = 2 * period - 1
    out[start] = np.nanmean(dx[period - 1:start + 1])
    for i in range(start + 1, len(dx)):
        out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


def last_atr(candles, period=14):
    h, l, c = _hlc(candles)
    return float(atr_series(h, l, c, period)[-1])


def classify_regime(candles, adx_threshold=25.0, vol_percentile=0.8):
    h, l, c = _hlc(candles)
    atr = atr_series(h, l, c)
    recent = atr[~np.isnan(atr)][-100:]
    rank = float((recent <= recent[-1]).mean())
    if rank >= vol_percentile:
        return "high_volatility"
    if float(adx_series(h, l, c)[-1]) >= adx_threshold:
        return "trend"
    return "range"
```

- [ ] **Step 5: Run** → 4 PASS (if the spike fixture lands as "trend", raise spike candle ranges until ATR rank ≥ 0.8 — the fixture is the tunable, not the classifier). **Step 6: Commit** `feat(service): classical regime classifier (ADX + ATR percentile)`.

---

### Task 4: Forecaster interface + Fake + Chronos-Bolt

**Files:**
- Create: `service/app/forecaster.py`
- Test: `service/tests/test_forecaster.py`

**Interfaces:**
- Produces: `QuantileForecast(q10:list[float], q50:list[float], q90:list[float])` (dataclass); `Forecaster` ABC with `forecast(closes:list[float], horizon:int) -> QuantileForecast`; `FakeForecaster` (deterministic linear extrapolation, band = std of diffs); `ChronosBoltForecaster(model_name)` (lazy model load on first call); `get_forecaster(settings) -> Forecaster` factory keyed on `settings.forecaster` (`"fake"|"chronos"`; `"timemoe"` raises `NotImplementedError("planned alternative")` for now — documented in spec §3 as A/B option, wired in a later plan).

- [ ] **Step 1: Failing test** — `service/tests/test_forecaster.py`:
```python
import pytest
from app.config import Settings
from app.forecaster import FakeForecaster, get_forecaster, QuantileForecast

def test_fake_shape_and_order():
    fc = FakeForecaster().forecast([1.0 * i for i in range(100)], horizon=16)
    assert len(fc.q50) == 16
    assert all(a <= b <= c for a, b, c in zip(fc.q10, fc.q50, fc.q90))

def test_fake_follows_trend():
    closes = [3000.0 + 2.0 * i for i in range(100)]
    fc = FakeForecaster().forecast(closes, 16)
    assert fc.q50[-1] > closes[-1]

def test_factory_fake():
    s = Settings(_env_file=None, forecaster="fake")
    assert isinstance(get_forecaster(s), FakeForecaster)

@pytest.mark.slow
def test_chronos_real():
    chronos = pytest.importorskip("chronos")
    s = Settings(_env_file=None, forecaster="chronos")
    fc = get_forecaster(s).forecast([3000.0 + i for i in range(200)], 16)
    assert len(fc.q50) == 16
```
Register the marker in `service/pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = ["slow: needs model download"]
addopts = "-m 'not slow'"
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — `service/app/forecaster.py`:
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class QuantileForecast:
    q10: list[float]
    q50: list[float]
    q90: list[float]


class Forecaster(ABC):
    @abstractmethod
    def forecast(self, closes: list[float], horizon: int) -> QuantileForecast: ...


class FakeForecaster(Forecaster):
    """Deterministic linear extrapolation — for tests and offline dev."""

    def forecast(self, closes, horizon):
        x = np.arange(len(closes))
        slope, intercept = np.polyfit(x, np.array(closes), 1)
        q50 = [float(intercept + slope * (len(closes) + i)) for i in range(1, horizon + 1)]
        band = float(np.std(np.diff(closes))) or 1e-9
        return QuantileForecast(q10=[v - band for v in q50], q50=q50, q90=[v + band for v in q50])


class ChronosBoltForecaster(Forecaster):
    def __init__(self, model_name: str):
        self._model_name = model_name
        self._pipeline = None

    def _load(self):
        if self._pipeline is None:
            import torch
            from chronos import BaseChronosPipeline
            self._pipeline = BaseChronosPipeline.from_pretrained(
                self._model_name, device_map="cpu", torch_dtype=torch.float32)
        return self._pipeline

    def forecast(self, closes, horizon):
        import torch
        q, _ = self._load().predict_quantiles(
            context=torch.tensor(closes, dtype=torch.float32),
            prediction_length=horizon,
            quantile_levels=[0.1, 0.5, 0.9])
        q = q[0]  # (horizon, 3)
        return QuantileForecast(q10=q[:, 0].tolist(), q50=q[:, 1].tolist(), q90=q[:, 2].tolist())


def get_forecaster(settings) -> Forecaster:
    if settings.forecaster == "fake":
        return FakeForecaster()
    if settings.forecaster == "chronos":
        return ChronosBoltForecaster(settings.chronos_model)
    if settings.forecaster == "timemoe":
        raise NotImplementedError("planned alternative — see spec §3")
    raise ValueError(f"unknown forecaster: {settings.forecaster}")
```

- [ ] **Step 4: Run** → 3 PASS, 1 deselected (slow). **Step 5: Commit** `feat(service): Forecaster interface, Fake + Chronos-Bolt implementations`.

---

### Task 5: Forecast analysis (direction + confidence)

**Files:**
- Create: `service/app/analysis.py`
- Test: `service/tests/test_analysis.py`

**Interfaces:**
- Consumes: `QuantileForecast` (Task 4).
- Produces: `analyze_forecast(fc: QuantileForecast, last_close: float, atr_value: float) -> tuple[str, float]` — direction `"bullish"|"bearish"|"neutral"`, confidence `0–1`. Rules: median move `m = q50[-1] - last_close`; deadband `|m| < 0.1 * atr` → `("neutral", 0.0)`; else direction by sign, `confidence = |m| / (|m| + band)` with `band = (q90[-1] - q10[-1]) / 2`, rounded to 2 dp.

- [ ] **Step 1: Failing test** — `service/tests/test_analysis.py`:
```python
from app.analysis import analyze_forecast
from app.forecaster import QuantileForecast

def _fc(move, band):
    q50 = [3000.0 + move]
    return QuantileForecast(q10=[q50[0] - band], q50=q50, q90=[q50[0] + band])

def test_deadband_neutral():
    assert analyze_forecast(_fc(0.1, 1.0), 3000.0, atr_value=3.0) == ("neutral", 0.0)

def test_bullish_tight_band_high_conf():
    d, c = analyze_forecast(_fc(6.0, 0.5), 3000.0, 3.0)
    assert d == "bullish" and c > 0.9

def test_bearish():
    d, _ = analyze_forecast(_fc(-6.0, 0.5), 3000.0, 3.0)
    assert d == "bearish"

def test_wide_band_low_conf():
    _, c = analyze_forecast(_fc(6.0, 20.0), 3000.0, 3.0)
    assert c < 0.4
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — `service/app/analysis.py`:
```python
from app.forecaster import QuantileForecast


def analyze_forecast(fc: QuantileForecast, last_close: float, atr_value: float):
    move = fc.q50[-1] - last_close
    if abs(move) < 0.1 * atr_value:
        return "neutral", 0.0
    band = max((fc.q90[-1] - fc.q10[-1]) / 2.0, 1e-9)
    confidence = round(abs(move) / (abs(move) + band), 2)
    return ("bullish" if move > 0 else "bearish"), confidence
```

- [ ] **Step 4: Run** → 4 PASS. **Step 5: Commit** `feat(service): quantile-based direction/confidence`.

---

### Task 6: Verdict combiner

**Files:**
- Create: `service/app/verdict.py`
- Test: `service/tests/test_verdict.py`

**Interfaces:**
- Produces: `combine(signal:str, direction:str, confidence:float, threshold:float=0.6) -> str` — `"confirm"` if direction agrees with BUY/SELL and confidence ≥ threshold; `"conflict"` if direction opposes and confidence ≥ threshold; otherwise `"neutral"`. `EXIT`/`NONE` always `"neutral"`.

- [ ] **Step 1: Failing test** — `service/tests/test_verdict.py`:
```python
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
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — `service/app/verdict.py`:
```python
def combine(signal: str, direction: str, confidence: float, threshold: float = 0.6) -> str:
    if signal not in ("BUY", "SELL"):
        return "neutral"
    want = "bullish" if signal == "BUY" else "bearish"
    against = "bearish" if signal == "BUY" else "bullish"
    if direction == want and confidence >= threshold:
        return "confirm"
    if direction == against and confidence >= threshold:
        return "conflict"
    return "neutral"
```

- [ ] **Step 4: Run** → 8 PASS. **Step 5: Commit** `feat(service): verdict combiner`.

---

### Task 7: SQLite signal log + lazy outcome resolution

**Files:**
- Create: `service/app/db.py`
- Test: `service/tests/test_db.py`

**Interfaces:**
- Produces: `SignalDb(path)` with `insert_signal(*, bar_time:int, symbol:str, signal:str, price:float, direction:str, confidence:float, regime:str, verdict:str, mode:str, ai_available:bool) -> int`; `resolve_outcomes(candles:list[Candle], horizon_bars:int=16) -> int` (rows resolved); `stats() -> dict` (`total`, `resolved`, `ai_correct_pct`). Outcome: first candle with `t >= bar_time + horizon_bars*900` sets `outcome_price=c`, `outcome_move=c-price`, `ai_correct` = 1/0 for bullish/bearish calls (NULL when direction was neutral).

- [ ] **Step 1: Failing test** — `service/tests/test_db.py`:
```python
from app.db import SignalDb
from tests.fixtures import trend_candles

def test_insert_and_resolve(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    candles = trend_candles(200)
    sig_bar = candles[100].t
    db.insert_signal(bar_time=sig_bar, symbol="XAUUSD", signal="BUY",
                     price=candles[100].c, direction="bullish", confidence=0.8,
                     regime="trend", verdict="confirm", mode="grading", ai_available=True)
    assert db.resolve_outcomes(candles) == 1
    s = db.stats()
    assert s["resolved"] == 1 and s["ai_correct_pct"] == 100.0

def test_unresolved_when_horizon_not_reached(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    candles = trend_candles(200)
    db.insert_signal(bar_time=candles[-1].t, symbol="XAUUSD", signal="SELL",
                     price=candles[-1].c, direction="bearish", confidence=0.7,
                     regime="trend", verdict="confirm", mode="grading", ai_available=True)
    assert db.resolve_outcomes(candles) == 0
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — `service/app/db.py`:
```python
import sqlite3
import time

_SCHEMA = """CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  bar_time INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  signal TEXT NOT NULL,
  price REAL NOT NULL,
  direction TEXT, confidence REAL, regime TEXT, verdict TEXT,
  mode TEXT, ai_available INTEGER,
  outcome_price REAL, outcome_move REAL, ai_correct INTEGER
)"""


class SignalDb:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def insert_signal(self, *, bar_time, symbol, signal, price, direction,
                      confidence, regime, verdict, mode, ai_available) -> int:
        cur = self.conn.execute(
            "INSERT INTO signals (created_ts, bar_time, symbol, signal, price, direction,"
            " confidence, regime, verdict, mode, ai_available)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), bar_time, symbol, signal, price, direction,
             confidence, regime, verdict, mode, int(ai_available)))
        self.conn.commit()
        return cur.lastrowid

    def resolve_outcomes(self, candles, horizon_bars: int = 16) -> int:
        bar_seconds = candles[1].t - candles[0].t
        resolved = 0
        rows = self.conn.execute(
            "SELECT id, bar_time, price, direction FROM signals"
            " WHERE outcome_price IS NULL").fetchall()
        for rid, bar_time, price, direction in rows:
            target = bar_time + horizon_bars * bar_seconds
            hit = next((x for x in candles if x.t >= target), None)
            if hit is None:
                continue
            move = hit.c - price
            correct = None
            if direction == "bullish":
                correct = int(move > 0)
            elif direction == "bearish":
                correct = int(move < 0)
            self.conn.execute(
                "UPDATE signals SET outcome_price=?, outcome_move=?, ai_correct=? WHERE id=?",
                (hit.c, move, correct, rid))
            resolved += 1
        self.conn.commit()
        return resolved

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        done = self.conn.execute(
            "SELECT COUNT(*), COALESCE(AVG(ai_correct) * 100, 0) FROM signals"
            " WHERE outcome_price IS NOT NULL").fetchone()
        return {"total": total, "resolved": done[0], "ai_correct_pct": round(done[1], 1)}
```

- [ ] **Step 4: Run** → 2 PASS. **Step 5: Commit** `feat(service): SQLite signal log with lazy outcome resolution`.

---

### Task 8: Telegram reporter

**Files:**
- Create: `service/app/telegram.py`
- Test: `service/tests/test_telegram.py`

**Interfaces:**
- Consumes: `AnalyzeRequest`, `AnalyzeResponse` (Task 2).
- Produces: `format_report(req, resp) -> str`; `send_alert(text:str, settings) -> bool` — POSTs `https://api.telegram.org/bot{token}/sendMessage` via httpx (5 s timeout), returns False without raising when token/chat empty or request fails.

- [ ] **Step 1: Failing test** — `service/tests/test_telegram.py`:
```python
from app.config import Settings
from app.models import AnalyzeRequest, AnalyzeResponse
from app.telegram import format_report, send_alert
from tests.fixtures import trend_candles

def test_format_contains_essentials():
    req = AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="BUY",
                         candles=trend_candles(50))
    resp = AnalyzeResponse(direction="bullish", confidence=0.82, regime="trend",
                           verdict="confirm", mode="grading", ai_available=True)
    text = format_report(req, resp)
    for token in ("XAUUSD", "BUY", "82%", "trend", "confirm"):
        assert token in text

def test_send_noop_without_token():
    assert send_alert("hi", Settings(_env_file=None)) is False
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — `service/app/telegram.py`:
```python
import httpx

_ICON = {"confirm": "✅", "conflict": "⚠️", "neutral": "➖"}


def format_report(req, resp) -> str:
    ai = (f"{resp.direction} {resp.confidence:.0%} — {resp.verdict} {_ICON[resp.verdict]}"
          if resp.ai_available else "AI unavailable ❌")
    return (f"🥇 {req.symbol} {req.timeframe} — {req.signal}\n"
            f"Strategy: {req.signal}\n"
            f"AI: {ai}\n"
            f"Regime: {resp.regime}\n"
            f"Mode: {resp.mode}")


def send_alert(text: str, settings) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text}, timeout=5.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False
```

- [ ] **Step 4: Run** → 2 PASS. **Step 5: Commit** `feat(service): telegram reporter`.

---

### Task 9: FastAPI app — /health and /analyze

**Files:**
- Create: `service/app/main.py`
- Test: `service/tests/test_api.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: `app` (FastAPI). `GET /health` → `{"status":"ok","forecaster":<class name>,"db":<path>}`. `POST /analyze` per contract; model exception → fail-open response; non-NONE signals logged + Telegram attempted; every call runs `resolve_outcomes`.

- [ ] **Step 1: Failing test** — `service/tests/test_api.py`:
```python
import pytest
from fastapi.testclient import TestClient
from tests.fixtures import trend_candles

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.db"))
    import importlib
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c

def _payload(signal="BUY"):
    return {"symbol": "XAUUSD", "timeframe": "M15", "signal": signal,
            "candles": [c.model_dump() for c in trend_candles(200)]}

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

def test_analyze_buy_in_uptrend_confirms(client):
    r = client.post("/analyze", json=_payload("BUY"))
    body = r.json()
    assert r.status_code == 200
    assert body["direction"] == "bullish"
    assert body["verdict"] in ("confirm", "neutral")
    assert body["ai_available"] is True

def test_analyze_none_still_returns(client):
    r = client.post("/analyze", json=_payload("NONE"))
    assert r.status_code == 200 and r.json()["verdict"] == "neutral"

def test_fail_open_on_model_error(client):
    from app import main

    class Boom:
        def forecast(self, closes, horizon):
            raise RuntimeError("model exploded")

    main.app.state.forecaster = Boom()
    r = client.post("/analyze", json=_payload("BUY"))
    body = r.json()
    assert r.status_code == 200
    assert body["ai_available"] is False
    assert body["direction"] == "neutral" and body["confidence"] == 0.0
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — `service/app/main.py`:
```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.analysis import analyze_forecast
from app.config import settings
from app.db import SignalDb
from app.forecaster import get_forecaster
from app.models import AnalyzeRequest, AnalyzeResponse
from app.regime import classify_regime, last_atr
from app.telegram import format_report, send_alert
from app.verdict import combine


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.forecaster = get_forecaster(settings)
    app.state.db = SignalDb(settings.db_path)
    yield


app = FastAPI(title="XAU Assistant AI Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok",
            "forecaster": type(app.state.forecaster).__name__,
            "db": settings.db_path}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    regime = classify_regime(req.candles)
    atr_value = last_atr(req.candles)
    closes = [x.c for x in req.candles]
    try:
        fc = app.state.forecaster.forecast(closes, settings.horizon)
        direction, confidence = analyze_forecast(fc, closes[-1], atr_value)
        ai_available = True
    except Exception:
        direction, confidence, ai_available = "neutral", 0.0, False

    resp = AnalyzeResponse(
        direction=direction, confidence=confidence, regime=regime,
        verdict=combine(req.signal, direction, confidence, settings.confirm_threshold),
        mode=settings.mode, ai_available=ai_available)

    app.state.db.resolve_outcomes(req.candles, settings.horizon)
    if req.signal != "NONE":
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=req.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, verdict=resp.verdict, mode=settings.mode,
            ai_available=ai_available)
        send_alert(format_report(req, resp), settings)
    return resp
```

- [ ] **Step 4: Run** → 4 PASS, then full suite `pytest -v` → all green. **Step 5: Smoke run** — `FORECASTER=fake uvicorn app.main:app --host 0.0.0.0 --port 8000` and `curl http://127.0.0.1:8000/health` → ok. **Step 6: Commit** `feat(service): FastAPI /analyze + /health with fail-open`.

---

### Task 10: MQL5 — Strategy stub, Alerts, EA skeleton

**Files:**
- Create: `mt5/Include/XauAssistant/Strategy.mqh`, `mt5/Include/XauAssistant/Alerts.mqh`, `mt5/Experts/XauAssistant.mq5`

**Interfaces:**
- Produces: `enum ENUM_SIGNAL {SIGNAL_NONE, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_EXIT}`; `class CStrategy { virtual ENUM_SIGNAL Evaluate(); }` (stub returns SIGNAL_NONE — real rules replace only this file in the Phase-3 plan); `SignalToString(ENUM_SIGNAL)`; `CAlerts::Draw(ENUM_SIGNAL sig, string grade)` + `CAlerts::Notify(string text)`. EA inputs exactly as spec §5/§5a/§5b/§5c (names in Global Constraints). New-bar detection via `iTime(_Symbol, PERIOD_CURRENT, 0)`.

- [ ] **Step 1: Write `Strategy.mqh`**
```mql5
// Strategy.mqh — the ONLY file the real strategy rules will touch.
#ifndef XAU_STRATEGY_MQH
#define XAU_STRATEGY_MQH

enum ENUM_SIGNAL { SIGNAL_NONE, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_EXIT };

string SignalToString(ENUM_SIGNAL s)
  {
   switch(s)
     {
      case SIGNAL_BUY:  return "BUY";
      case SIGNAL_SELL: return "SELL";
      case SIGNAL_EXIT: return "EXIT";
     }
   return "NONE";
  }

class CStrategy
  {
public:
   // Called once per closed bar. Stub until the documented rules are extracted.
   virtual ENUM_SIGNAL Evaluate() { return SIGNAL_NONE; }
   // True while the entry condition remains valid (pyramiding gate, spec 5b).
   virtual bool ConditionStillTrue(ENUM_SIGNAL dir) { return false; }
  };
#endif
```

- [ ] **Step 2: Write `Alerts.mqh`**
```mql5
#ifndef XAU_ALERTS_MQH
#define XAU_ALERTS_MQH
#include <XauAssistant/Strategy.mqh>

class CAlerts
  {
public:
   void Draw(ENUM_SIGNAL sig, string grade)
     {
      if(sig == SIGNAL_NONE) return;
      string name = "xau_sig_" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      int    code  = (sig == SIGNAL_BUY) ? 233 : (sig == SIGNAL_SELL) ? 234 : 251;
      color  clr   = (sig == SIGNAL_BUY) ? clrLime : (sig == SIGNAL_SELL) ? clrRed : clrYellow;
      if(ObjectCreate(0, name, OBJ_ARROW, 0, TimeCurrent(), price))
        {
         ObjectSetInteger(0, name, OBJPROP_ARROWCODE, code);
         ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
         ObjectSetString(0, name, OBJPROP_TOOLTIP, grade);
        }
     }
   void Notify(string text) { Alert(text); Print(text); }
  };
#endif
```

- [ ] **Step 3: Write `XauAssistant.mq5`** (skeleton; API/risk/trade managers wired in Tasks 11–12)
```mql5
#property copyright "xau-assistant"
#property version   "0.1"
#property strict

#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/Alerts.mqh>

enum ENUM_EXEC_MODE { EXEC_MANUAL, EXEC_AUTO };

input ENUM_EXEC_MODE ExecutionMode          = EXEC_MANUAL;
input bool           AllowLiveTrading       = false;
input string         ApiUrl                 = "http://127.0.0.1:8000/analyze";
input int            ApiTimeoutMs           = 3000;
input double         RiskPerTradePct        = 0.5;
input double         MaxDrawdownPct         = 10.0;
input bool           EnablePyramiding       = true;
input int            MaxPositions           = 3;
input double         AddTriggerATR          = 1.0;
input double         ProfitTargetPct        = 2.0;
input double         StopAtrMult            = 2.0;
input double         MaxSpreadPoints        = 500;
input int            TradingWindowStartHour = 15;
input int            TradingWindowEndHour   = 18;
input int            MaxDailyExposureMin    = 60;
input double         AdxTrendThreshold      = 25.0;
input bool           DebugFireTestSignal    = false;
input long           MagicNumber            = 20260729;

CStrategy g_strategy;
CAlerts   g_alerts;
datetime  g_lastBar = 0;
bool      g_debugFired = false;

int OnInit()
  {
   if(ExecutionMode == EXEC_AUTO && !AllowLiveTrading &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
     {
      g_alerts.Notify("XauAssistant: AUTO on LIVE account blocked (AllowLiveTrading=false)");
      return INIT_FAILED;
     }
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   datetime bar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar == g_lastBar) return;   // act once per new bar
   g_lastBar = bar;
   ProcessBar();
  }

void ProcessBar()
  {
   ENUM_SIGNAL sig = g_strategy.Evaluate();
   if(DebugFireTestSignal && !g_debugFired) { sig = SIGNAL_BUY; g_debugFired = true; }
   if(sig == SIGNAL_NONE) return;
   g_alerts.Draw(sig, "pipeline test");
   g_alerts.Notify("XauAssistant " + _Symbol + " " + SignalToString(sig));
  }
```

- [ ] **Step 4: Compile check** — run the MetaEditor command from Global Constraints; read `mt5/Experts/XauAssistant.log`; expect `0 errors`. If MetaEditor is not at the default path, locate it (`ls "/mnt/c/Program Files/"*MetaTrader*/MetaEditor64.exe`) and update the command.
- [ ] **Step 5: Commit** `feat(mt5): EA skeleton with stub strategy, alerts, debug signal`.

---

### Task 11: MQL5 — AiApi + SignalManager (EA ↔ service round trip)

**Files:**
- Create: `mt5/Include/XauAssistant/AiApi.mqh`, `mt5/Include/XauAssistant/SignalManager.mqh`
- Modify: `mt5/Experts/XauAssistant.mq5` (wire both into `ProcessBar`)

**Interfaces:**
- Consumes: `ENUM_SIGNAL`, `SignalToString` (Task 10).
- Produces: `struct AiResponse { string direction; double confidence; string regime; string verdict; string mode; bool ai_available; }`; `class CAiApi { void Init(string url, int timeout_ms); bool Analyze(ENUM_SIGNAL sig, AiResponse &out); }` (false = transport failure → fail-open); `class CSignalManager { string BuildReport(ENUM_SIGNAL sig, AiResponse &r, bool api_ok); }`.

- [ ] **Step 1: Write `AiApi.mqh`**
```mql5
#ifndef XAU_AIAPI_MQH
#define XAU_AIAPI_MQH
#include <XauAssistant/Strategy.mqh>

struct AiResponse
  {
   string direction;
   double confidence;
   string regime;
   string verdict;
   string mode;
   bool   ai_available;
  };

class CAiApi
  {
private:
   string m_url;
   int    m_timeout;

   string JsonEscape(string s) { return s; } // symbols/timeframes contain no specials

   string BuildJson(ENUM_SIGNAL sig, int count)
     {
      MqlRates rates[];
      // shift 1 = last CLOSED bar; the forming bar is never sent
      int copied = CopyRates(_Symbol, PERIOD_CURRENT, 1, count, rates);
      if(copied <= 0) return "";
      string tf = StringSubstr(EnumToString(_Period), 7); // "PERIOD_M15" -> "M15"
      string json = "{\"symbol\":\"" + _Symbol + "\",\"timeframe\":\"" + tf +
                    "\",\"signal\":\"" + SignalToString(sig) + "\",\"candles\":[";
      for(int i = 0; i < copied; i++)
        {
         if(i > 0) json += ",";
         json += "{\"t\":" + (string)(long)rates[i].time +
                 ",\"o\":" + DoubleToString(rates[i].open, _Digits) +
                 ",\"h\":" + DoubleToString(rates[i].high, _Digits) +
                 ",\"l\":" + DoubleToString(rates[i].low, _Digits) +
                 ",\"c\":" + DoubleToString(rates[i].close, _Digits) +
                 ",\"v\":" + (string)rates[i].tick_volume + "}";
        }
      return json + "]}";
     }

   string ExtractString(string body, string key)
     {
      string pat = "\"" + key + "\":\"";
      int a = StringFind(body, pat);
      if(a < 0) return "";
      a += StringLen(pat);
      int b = StringFind(body, "\"", a);
      return (b > a) ? StringSubstr(body, a, b - a) : "";
     }

   double ExtractNumber(string body, string key)
     {
      string pat = "\"" + key + "\":";
      int a = StringFind(body, pat);
      if(a < 0) return 0.0;
      a += StringLen(pat);
      int b = a;
      while(b < StringLen(body))
        {
         ushort ch = StringGetCharacter(body, b);
         if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-') b++;
         else break;
        }
      return StringToDouble(StringSubstr(body, a, b - a));
     }

public:
   void Init(string url, int timeout_ms) { m_url = url; m_timeout = timeout_ms; }

   bool Analyze(ENUM_SIGNAL sig, AiResponse &out)
     {
      out.ai_available = false;
      string json = BuildJson(sig, 200);
      if(json == "") return false;
      char req[], res[];
      StringToCharArray(json, req, 0, StringLen(json), CP_UTF8);
      string resp_headers;
      ResetLastError();
      int code = WebRequest("POST", m_url, "Content-Type: application/json\r\n",
                            m_timeout, req, res, resp_headers);
      if(code != 200)
        {
         Print("AiApi: WebRequest failed code=", code, " err=", GetLastError(),
               " (is the URL whitelisted in Tools>Options>Expert Advisors?)");
         return false;
        }
      string body = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
      out.direction    = ExtractString(body, "direction");
      out.confidence   = ExtractNumber(body, "confidence");
      out.regime       = ExtractString(body, "regime");
      out.verdict      = ExtractString(body, "verdict");
      out.mode         = ExtractString(body, "mode");
      out.ai_available = (StringFind(body, "\"ai_available\":true") >= 0);
      return true;
     }
  };
#endif
```

- [ ] **Step 2: Write `SignalManager.mqh`**
```mql5
#ifndef XAU_SIGNALMANAGER_MQH
#define XAU_SIGNALMANAGER_MQH
#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/AiApi.mqh>

class CSignalManager
  {
public:
   string BuildReport(ENUM_SIGNAL sig, AiResponse &r, bool api_ok)
     {
      string head = _Symbol + " " + SignalToString(sig);
      if(!api_ok || !r.ai_available)
         return head + " | AI unavailable — strategy signal stands (fail-open)";
      return head + " | AI: " + r.direction + " " +
             DoubleToString(r.confidence * 100, 0) + "% (" + r.verdict + ")" +
             " | regime: " + r.regime + " | mode: " + r.mode;
     }
  };
#endif
```

- [ ] **Step 3: Wire into the EA** — in `XauAssistant.mq5` add includes `<XauAssistant/AiApi.mqh>`, `<XauAssistant/SignalManager.mqh>`, globals `CAiApi g_api; CSignalManager g_sm;`, call `g_api.Init(ApiUrl, ApiTimeoutMs);` in `OnInit`, and replace the body of `ProcessBar` after the debug-signal block with:
```mql5
   if(sig == SIGNAL_NONE)
     {
      AiResponse quiet;
      g_api.Analyze(sig, quiet);   // keeps outcome-resolution data flowing (spec 6.3)
      return;
     }
   AiResponse r;
   bool ok = g_api.Analyze(sig, r);
   string report = g_sm.BuildReport(sig, r, ok);
   g_alerts.Draw(sig, report);
   g_alerts.Notify(report);
```

- [ ] **Step 4: Compile check** → `0 errors`.
- [ ] **Step 5: Live round-trip test** — service running with `FORECASTER=fake`; in MT5: whitelist `http://127.0.0.1:8000/analyze`, attach EA to XAUUSD M15 demo chart with `DebugFireTestSignal=true`; expect a chart arrow + alert containing "AI:" within one bar, and a row in `service/xau_assistant.db` (`sqlite3 xau_assistant.db "SELECT * FROM signals;"`). This step needs the user at the terminal — pause here and ask them to confirm what they see.
- [ ] **Step 6: Commit** `feat(mt5): AI API client + signal report round trip`.

---

### Task 12: MQL5 — RiskManager (MoneyWatch) + TradeManager (AUTO mode)

**Files:**
- Create: `mt5/Include/XauAssistant/RiskManager.mqh`, `mt5/Include/XauAssistant/TradeManager.mqh`
- Modify: `mt5/Experts/XauAssistant.mq5`

**Interfaces:**
- Consumes: `ENUM_SIGNAL` (Task 10).
- Produces: `class CRiskManager { void Init(...); bool CanEnter(string &why); double CalcLots(double sl_points, double ratio); void OnBarUpdate(); bool KillSwitchTripped(); string Status(); }`; `class CTradeManager { void Init(...); void OnSignal(ENUM_SIGNAL sig, double atr_value); void Manage(double atr_value); int OpenCount(); }`. Persistence keys: `"XAU_HWM_<login>"`, `"XAU_KILL_<login>"`, `"XAU_EXPO_<login>_<yyyymmdd>"`, `"XAU_CYCLE_BAL_<login>"` via `GlobalVariableSet/Get`.

- [ ] **Step 1: Write `RiskManager.mqh`**
```mql5
#ifndef XAU_RISKMANAGER_MQH
#define XAU_RISKMANAGER_MQH

class CRiskManager
  {
private:
   double m_riskPct, m_maxDdPct, m_maxSpread, m_adxThreshold;
   int    m_winStart, m_winEnd, m_maxExpoMin;
   int    m_adxHandle;
   long   m_login;

   string Key(string tag) { return "XAU_" + tag + "_" + (string)m_login; }
   string ExpoKey()
     {
      MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
      return Key("EXPO") + "_" + StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);
     }

public:
   void Init(double riskPct, double maxDdPct, double maxSpread, double adxThr,
             int winStart, int winEnd, int maxExpoMin)
     {
      m_riskPct = riskPct; m_maxDdPct = maxDdPct; m_maxSpread = maxSpread;
      m_adxThreshold = adxThr; m_winStart = winStart; m_winEnd = winEnd;
      m_maxExpoMin = maxExpoMin;
      m_login = AccountInfoInteger(ACCOUNT_LOGIN);
      m_adxHandle = iADX(_Symbol, PERIOD_CURRENT, 14);
     }

   void OnBarUpdate()
     {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      double hwm = GlobalVariableGet(Key("HWM"));
      if(eq > hwm) { hwm = eq; GlobalVariableSet(Key("HWM"), hwm); }
      if(hwm > 0 && eq <= hwm * (1.0 - m_maxDdPct / 100.0))
         GlobalVariableSet(Key("KILL"), 1);
      // accumulate exposure: bar minutes while a position of ours is open
      if(PositionsTotal() > 0)
        {
         double mins = GlobalVariableGet(ExpoKey());
         GlobalVariableSet(ExpoKey(), mins + PeriodSeconds(PERIOD_CURRENT) / 60.0);
        }
     }

   bool KillSwitchTripped() { return GlobalVariableGet(Key("KILL")) > 0; }

   bool TrendOK()
     {
      double adx[];
      if(CopyBuffer(m_adxHandle, 0, 1, 1, adx) != 1) return false;
      return adx[0] >= m_adxThreshold;
     }

   bool CanEnter(string &why)
     {
      why = "";
      if(KillSwitchTripped())                         { why = "kill switch tripped"; return false; }
      MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
      if(dt.hour < m_winStart || dt.hour >= m_winEnd) { why = "outside trading window"; return false; }
      if(GlobalVariableGet(ExpoKey()) >= m_maxExpoMin){ why = "daily exposure spent"; return false; }
      long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(spread > m_maxSpread)                        { why = "spread too wide"; return false; }
      if(!TrendOK())                                  { why = "ADX below threshold"; return false; }
      return true;
     }

   double CalcLots(double sl_points, double ratio)
     {
      double eq        = AccountInfoDouble(ACCOUNT_EQUITY);
      double tick_val  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double loss_per_lot = sl_points * _Point / tick_size * tick_val;
      if(loss_per_lot <= 0) return 0;
      double lots = (eq * m_riskPct / 100.0 * ratio) / loss_per_lot;
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
      lots = MathFloor(lots / step) * step;
      return MathMin(MathMax(lots, vmin), vmax);
     }

   string Status()
     {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      double hwm = GlobalVariableGet(Key("HWM"));
      double dd = (hwm > 0) ? (1.0 - eq / hwm) * 100.0 : 0.0;
      return StringFormat("MoneyWatch: risk %.2f%%/trade, DD %.1f%% of %.1f%% limit, expo %.0f/%d min",
                          m_riskPct, dd, m_maxDdPct, GlobalVariableGet(ExpoKey()), m_maxExpoMin);
     }
  };
#endif
```

- [ ] **Step 2: Write `TradeManager.mqh`**
```mql5
#ifndef XAU_TRADEMANAGER_MQH
#define XAU_TRADEMANAGER_MQH
#include <Trade/Trade.mqh>
#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/RiskManager.mqh>

class CTradeManager
  {
private:
   CTrade        m_trade;
   CRiskManager *m_risk;
   long          m_magic;
   bool          m_pyramid;
   int           m_maxPos;
   double        m_addTriggerAtr, m_targetPct, m_stopAtrMult;
   double        m_lastEntryPrice;
   double        m_ratios[3];

   string CycleKey() { return "XAU_CYCLE_BAL_" + (string)AccountInfoInteger(ACCOUNT_LOGIN); }

   int CountOwn()
     {
      int n = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 &&
            PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol) n++;
      return n;
     }

   double BasketProfit()
     {
      double p = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 &&
            PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            p += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      return p;
     }

   void MoveStopsToBreakeven()
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != m_magic ||
            PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         double open = PositionGetDouble(POSITION_PRICE_OPEN);
         m_trade.PositionModify(tk, open, PositionGetDouble(POSITION_TP));
        }
     }

public:
   void Init(CRiskManager *risk, long magic, bool pyramid, int maxPos,
             double addAtr, double targetPct, double stopAtrMult)
     {
      m_risk = risk; m_magic = magic; m_pyramid = pyramid; m_maxPos = maxPos;
      m_addTriggerAtr = addAtr; m_targetPct = targetPct; m_stopAtrMult = stopAtrMult;
      m_trade.SetExpertMagicNumber(magic);
      m_ratios[0] = 1.0; m_ratios[1] = 0.7; m_ratios[2] = 0.4;
     }

   int OpenCount() { return CountOwn(); }

   void CloseAll(string reason)
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk > 0 && PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            m_trade.PositionClose(tk);
        }
      Print("TradeManager: closed all (", reason, ")");
     }

   void OnSignal(ENUM_SIGNAL sig, double atr_value)
     {
      if(sig == SIGNAL_EXIT) { CloseAll("strategy EXIT"); return; }
      if(sig != SIGNAL_BUY && sig != SIGNAL_SELL) return;
      if(CountOwn() > 0) return;                    // one cycle at a time
      string why;
      if(!m_risk.CanEnter(why)) { Print("Entry blocked: ", why); return; }
      double sl_points = m_stopAtrMult * atr_value / _Point;
      double lots = m_risk.CalcLots(sl_points, m_ratios[0]);
      if(lots <= 0) return;
      double price = (sig == SIGNAL_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = (sig == SIGNAL_BUY) ? price - m_stopAtrMult * atr_value
                                      : price + m_stopAtrMult * atr_value;
      bool ok = (sig == SIGNAL_BUY) ? m_trade.Buy(lots, _Symbol, 0, sl)
                                    : m_trade.Sell(lots, _Symbol, 0, sl);
      if(ok)
        {
         m_lastEntryPrice = price;
         GlobalVariableSet(CycleKey(), AccountInfoDouble(ACCOUNT_BALANCE));
        }
     }

   void Manage(double atr_value, bool conditionStillTrue)
     {
      int n = CountOwn();
      if(n == 0) return;
      // profit target: close everything at +targetPct of cycle-start balance
      double cycleBal = GlobalVariableGet(CycleKey());
      if(cycleBal > 0 && BasketProfit() >= cycleBal * m_targetPct / 100.0)
        { CloseAll("profit target reached"); return; }
      // pyramid: add only in profit, only while condition true, shrinking size
      if(!m_pyramid || !conditionStillTrue || n >= m_maxPos) return;
      if(BasketProfit() <= 0) return;               // never add in loss
      long ptype = -1;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
           { ptype = PositionGetInteger(POSITION_TYPE); break; }
      double price = (ptype == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                                  : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double advance = (ptype == POSITION_TYPE_BUY) ? price - m_lastEntryPrice
                                                    : m_lastEntryPrice - price;
      if(advance < m_addTriggerAtr * atr_value) return;
      double sl_points = m_stopAtrMult * atr_value / _Point;
      double lots = m_risk.CalcLots(sl_points, m_ratios[MathMin(n, 2)]);
      if(lots <= 0) return;
      bool ok = (ptype == POSITION_TYPE_BUY) ? m_trade.Buy(lots, _Symbol)
                                             : m_trade.Sell(lots, _Symbol);
      if(ok) { m_lastEntryPrice = price; MoveStopsToBreakeven(); }
     }
  };
#endif
```

- [ ] **Step 3: Wire into the EA** — includes for both files; globals `CRiskManager g_risk; CTradeManager g_trades; int g_atrHandle;`. In `OnInit`: `g_risk.Init(RiskPerTradePct, MaxDrawdownPct, MaxSpreadPoints, AdxTrendThreshold, TradingWindowStartHour, TradingWindowEndHour, MaxDailyExposureMin); g_trades.Init(&g_risk, MagicNumber, EnablePyramiding, MaxPositions, AddTriggerATR, ProfitTargetPct, StopAtrMult); g_atrHandle = iATR(_Symbol, PERIOD_CURRENT, 14);`. In `ProcessBar`, before the AI call:
```mql5
   g_risk.OnBarUpdate();
   double atrBuf[];
   double atrVal = (CopyBuffer(g_atrHandle, 0, 1, 1, atrBuf) == 1) ? atrBuf[0] : 0;
   if(ExecutionMode == EXEC_AUTO && atrVal > 0)
     {
      g_trades.OnSignal(sig, atrVal);                       // execute FIRST (spec 2.2)
      g_trades.Manage(atrVal, g_strategy.ConditionStillTrue(sig));
     }
```
Append `g_risk.Status()` to the report string in the alert.

- [ ] **Step 4: Compile check** → `0 errors`.
- [ ] **Step 5: Strategy Tester smoke test** — user action: run the EA in MT5 Strategy Tester (visual mode, XAUUSD M15, any recent month) with `ExecutionMode=EXEC_AUTO, DebugFireTestSignal=true` and confirm: one BUY opens with a stop-loss attached, lot size ≈ 0.5 % risk, log shows MoneyWatch status. Pause and ask the user to run this and report.
- [ ] **Step 6: Commit** `feat(mt5): MoneyWatch risk manager + pyramid trade manager`.

---

### Task 13: README + run instructions

**Files:**
- Create: `README.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Write `README.md`** covering, in this order: what the system is (2 paragraphs, link to spec); quick start — service (`cd service && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt -r requirements-model.txt && cp .env.example .env && uvicorn app.main:app --host 0.0.0.0 --port 8000`); Telegram setup (BotFather → token → chat id via `getUpdates`, both into `.env`); MT5 setup (copy `mt5/Experts` + `mt5/Include` contents into the MT5 data folder's `MQL5/` — File → Open Data Folder — compile in MetaEditor, whitelist `http://127.0.0.1:8000/analyze`, attach to XAUUSD M15); the mode switches table from spec §5; how to run tests (`pytest` in `service/`); troubleshooting (WebRequest error 4014 = URL not whitelisted; ai_available=false = service down or model failed; first Chronos call downloads the model, ~1 min).
- [ ] **Step 2: Verify accuracy** — every command in the README must have been run (or compile-checked) during Tasks 1–12; fix any drift.
- [ ] **Step 3: Commit** `docs: README with setup and run instructions`.

## Self-review notes

- Spec coverage: §2 rules → Tasks 9 (fail-open), 10 (live guard), 11 (fail-open report), 12 (execute-first, MoneyWatch); §3 → Tasks 4–5; §5a/5b/5c → Task 12; §6 → Tasks 9, 11, 12; §7 → Tasks 2, 9, 11; §8 → Task 7; §11 → fixtures in Task 3, marker config in Task 4. Time-MoE forecaster implementation and veto-mode EA behavior are deliberately deferred (spec marks both as later-phase; factory raises NotImplementedError, mode flows through the contract).
- Type consistency: `ENUM_SIGNAL` names, `AiResponse` fields, and JSON keys match the contract everywhere; `CalcLots(sl_points, ratio)` signature consistent between Tasks 12 steps 1–2.
