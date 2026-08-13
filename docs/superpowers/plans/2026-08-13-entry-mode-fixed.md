# Entry Modes ADR/FIXED Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runtime-switchable "FIXED" entry mode (fixed lots, no adds/target/lock, ride until confirmed reversal or stop) beside today's "ADR" mode, switched from the existing `/mode` Telegram command (four buttons), backtested before anyone flips it live.

**Architecture:** Follows the exec-mode pattern end-to-end: service kv `entry_mode` → `HeartbeatResponse.entry_mode` → EA runtime `g_entryMode`; the basket's mode is captured at entry and persisted in a per-symbol MT5 global so restarts and mid-trade switches can't change a running basket's management. FIXED behavior lives in `TradeManager` (sizing branch + `Manage()` early-out).

**Tech Stack:** Python/FastAPI/SQLite service; MQL5 EA (MetaEditor CLI compile gate); `scripts/backtest.py` for the evidence phase.

**Spec:** `docs/superpowers/specs/2026-08-13-entry-mode-fixed-design.md`

## Global Constraints

- ADR behavior must remain byte-identical while `entry_mode` is "adr" (the default) — every new branch is FIXED-only.
- Safety rails are NEVER mode-dependent: kill switch, daily loss brake, news blackout, spread/ADX/window/exposure gates, 23:54 flatten, `AllowLiveTrading` all apply in both modes.
- Mode switch applies to the NEXT entry; an open basket finishes under `XAU_BASKET_MODE_<login>_<symbol>` (0=ADR, 1=FIXED).
- `FixedLots` default 0.05, clamped to broker min/max/step at use.
- FIXED exits: confirmed reversal (existing dual-confirmation path) or the shared stop — `Manage()` must skip adds, profit target, and profit lock for FIXED baskets.
- kv `entry_mode` values exactly "adr"/"fixed" (default "adr"); `PINNED_HELP_VERSION` → "6".
- MQL5 compile gate 0 errors / 0 warnings via izi.md's MetaEditor CLI runbook (quote the Result line). Service suite green (known flake `test_pop_approved_command_concurrent_exactly_once`: re-run once if it alone fails).
- Branch `feat/entry-mode-fixed` from `main`; izi.md updated before merge (Task 4). Venv: `cd service && source .venv/bin/activate`.

---

### Task 1: Backtester FIXED mode + comparison evidence

**Files:**
- Modify: `scripts/backtest.py` (argparse ~line 266; sizing/manage/exit logic — read the file fully first)
- Create: `.superpowers/entry-mode-backtest-report.md` (results, git-ignored)

**Interfaces:**
- Consumes: existing `--exit-scheme`, `--adx`, `--risk`, `--days`, `--confirm`, `--stop-buffer` flags and the dumped 17-month bar file (see izi.md backtest runbook; terminal is running if a re-dump is needed).
- Produces: `--entry-mode {adr,fixed}` (default adr — byte-identical when omitted) and `--fixed-lots` (default 0.05). FIXED replay: entry size = fixed lots (no 1% calc), no pyramid adds, no profit target, no profit lock, exits ONLY on confirmed reversal, shared stop, or the session-boundary flatten the replay already models.

- [ ] **Step 1: Branch + baseline capture**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau && git checkout -b feat/entry-mode-fixed main
```

Run the unmodified backtester over the full window and the last 30 days (current live params: risk 1%, ADX 10, expo 360) and save both outputs — they are the ADR reference numbers AND the byte-identical gate for Step 3.

- [ ] **Step 2: Implement the flags**

Add to `main()`'s argparse:

```python
    ap.add_argument("--entry-mode", choices=["adr", "fixed"], default="adr",
                    help="adr = live behavior; fixed = fixed lots, no adds/"
                         "target/lock, exit on confirmed reversal or stop")
    ap.add_argument("--fixed-lots", type=float, default=0.05)
```

Thread them into the replay: in the entry-sizing block, `fixed` uses `args.fixed_lots` verbatim; in the manage/exit logic, `fixed` skips the add trigger, the profit-target check, and the profit-lock check (the reversal exit and stop checks are shared and untouched). Follow the file's existing structure — the profit-floor `--exit-scheme` work is the template for how modes branch.

- [ ] **Step 3: Byte-identical gate**

Run with no new flags and with `--entry-mode adr`: both must reproduce Step 1's outputs exactly (same trade list, same net). Fix before proceeding if not.

- [ ] **Step 4: Run the comparison matrix**

Four runs, same window/params otherwise: ADR full-window, ADR last-30d, FIXED(0.05) full-window, FIXED(0.05) last-30d. Also FIXED(0.10) full-window as the "owner's original size" data point. Write per-run: net P/L, trade count, win rate, max open-equity valley, avg/max winner, avg hold time — to `.superpowers/entry-mode-backtest-report.md` with a short plain-language summary at the top.

- [ ] **Step 5: Commit**

```bash
git add scripts/backtest.py
git commit -m "feat(backtest): --entry-mode fixed replay (fixed lots, pure trend ride)"
```

---

### Task 2: Service — kv, contract, /mode four buttons, trades migration

**Files:**
- Modify: `service/app/db.py` (kv helpers after `set_exec_mode` ~line 331; guarded migration block ~line 114; `insert_trade` ~line 246)
- Modify: `service/app/models.py` (`HeartbeatRequest` after `bar_c`; `HeartbeatResponse` ~line 74; `TradeEventRequest` ~line 80)
- Modify: `service/app/main.py` (heartbeat response ~line 551)
- Modify: `service/app/telegram.py` (`/mode` handler ~line 430; `mode:` callback ~line 500; `/config` ~line 445; `format_pinned_help` + `PINNED_HELP_VERSION`)
- Test: `service/tests/test_entry_mode.py` (create); update the `PINNED_HELP_VERSION == "5"` assertion (grep for it) and the `/mode` reply test if one asserts two buttons.

**Interfaces:**
- Consumes: `kb()` keyboard helper; existing kv store; exec-mode pattern.
- Produces (Task 3 relies on the wire contract): `db.entry_mode() -> str` ("adr" default) / `db.set_entry_mode(mode)` (ValueError on anything but "adr"/"fixed"); `HeartbeatRequest.entry_mode: str = "adr"`; `HeartbeatResponse.entry_mode: Literal["adr","fixed"] = "adr"`; `TradeEventRequest.entry_mode: str = ""`; trades column `entry_mode TEXT DEFAULT ''`; callbacks `tmode:adr` / `tmode:fixed`.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_entry_mode.py`:

```python
"""Entry mode (ADR/FIXED): kv round-trip, /mode four buttons, tmode
callbacks, heartbeat contract, trades column."""
import time
import types

import pytest

from app.db import SignalDb
from app.telegram import handle_callback, handle_command


def _db(tmp_path):
    return SignalDb(str(tmp_path / "em.db"))


def _app(db):
    hb = types.SimpleNamespace(
        equity=1000.0, balance=1000.0, floating_pl=0.0, positions=[],
        kill_switch=False, hwm=0.0, exposure_min=0, window_open=True,
        spread_points=0.0, active_strategy="halftrend_ema_v1",
        algo_trading=True)
    return types.SimpleNamespace(state=types.SimpleNamespace(
        db=db, latest_heartbeat=(time.time(), hb), pending_switch=None,
        pending_channel=None))


def test_entry_mode_kv_roundtrip_defaults_adr(tmp_path):
    db = _db(tmp_path)
    assert db.entry_mode() == "adr"
    db.set_entry_mode("fixed")
    assert db.entry_mode() == "fixed"
    with pytest.raises(ValueError):
        db.set_entry_mode("yolo")


def test_mode_command_shows_both_states_and_four_buttons(tmp_path):
    app = _app(_db(tmp_path))
    text, keyboard = handle_command("/mode", app)
    assert "Execution mode" in text and "Entry mode" in text
    flat = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
    assert flat == ["mode:auto", "mode:manual", "tmode:adr", "tmode:fixed"]


def test_tmode_callback_sets_kv_and_names_next_trade(tmp_path):
    db = _db(tmp_path)
    app = _app(db)
    edit_text, toast = handle_callback("tmode:fixed", app)
    assert db.entry_mode() == "fixed"
    assert "FIXED" in edit_text and "next" in edit_text.lower()
    edit_text, _ = handle_callback("tmode:adr", app)
    assert db.entry_mode() == "adr"


def test_tmode_callback_rejects_unknown_value(tmp_path):
    db = _db(tmp_path)
    _, toast = handle_callback("tmode:yolo", _app(db))
    assert db.entry_mode() == "adr"


def test_config_shows_entry_mode(tmp_path):
    app = _app(_db(tmp_path))
    assert "entry mode: adr" in handle_command("/config", app)


def test_heartbeat_response_carries_entry_mode(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "hb_em.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    hb = {"equity": 1.0, "balance": 1.0, "floating_pl": 0.0}
    with TestClient(main.app) as client:
        assert client.post("/heartbeat", json=hb).json()["entry_mode"] == "adr"
        main.app.state.db.set_entry_mode("fixed")
        assert client.post("/heartbeat", json=hb).json()["entry_mode"] == "fixed"
        # old EA payload (no entry_mode field) still validates
        assert client.post("/heartbeat", json=hb).status_code == 200


def test_trades_table_stores_entry_mode(tmp_path):
    db = _db(tmp_path)
    tid = db.insert_trade({"event": "open", "direction": "BUY", "lots": 0.05,
                           "price": 4000.0, "entry_mode": "fixed"})
    row = db.conn.execute(
        "SELECT entry_mode FROM trades WHERE id=?", (tid,)).fetchone()
    assert row[0] == "fixed"
    tid2 = db.insert_trade({"event": "open", "direction": "BUY", "lots": 0.05,
                            "price": 4000.0})
    assert db.conn.execute("SELECT entry_mode FROM trades WHERE id=?",
                           (tid2,)).fetchone()[0] == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `cd service && python -m pytest tests/test_entry_mode.py -v`
Expected: FAIL (`AttributeError: 'SignalDb' object has no attribute 'entry_mode'`)

- [ ] **Step 3: Implement**

`db.py` — after `set_exec_mode`:

```python
    def entry_mode(self) -> str:
        val = self.get_kv("entry_mode")
        return val if val else "adr"

    def set_entry_mode(self, mode: str) -> None:
        if mode not in ("adr", "fixed"):
            raise ValueError(f"invalid entry mode: {mode}")
        self.set_kv("entry_mode", mode)
```

Migration block (same guarded pattern as `final`/`tp`, ~line 119):

```python
            if "entry_mode" not in cols:
                self.conn.execute(
                    "ALTER TABLE trades ADD COLUMN entry_mode TEXT DEFAULT ''")
```

(match the surrounding code's way of obtaining `cols`). `insert_trade`: add `entry_mode` to the column list + `ev.get("entry_mode", "")` to the values, keeping order consistent.

`models.py`: `HeartbeatRequest` gains `entry_mode: str = "adr"` (after `bar_c`); `HeartbeatResponse` gains `entry_mode: Literal["adr", "fixed"] = "adr"`; `TradeEventRequest` gains `entry_mode: str = ""`.

`main.py` heartbeat return:

```python
    return HeartbeatResponse(
        switch_to=app.state.pending_switch,
        mode=app.state.db.exec_mode(),
        entry_mode=app.state.db.entry_mode(),
        command=command
    )
```

`telegram.py` `/mode` handler:

```python
    if cmd == "/mode":
        mode = app.state.db.exec_mode()
        emode = app.state.db.entry_mode()
        return (f"Execution mode: {mode.upper()}\nAUTO executes signals "
                f"immediately; MANUAL sends proposals with buttons.\n"
                f"Entry mode: {emode.upper()}\nADR sizes by 1% risk with "
                f"pyramid adds and targets; FIXED rides a fixed lot until "
                f"the trend confirms a change.",
                kb([[("🤖 AUTO", "mode:auto"), ("👤 MANUAL", "mode:manual")],
                    [("📊 ADR", "tmode:adr"), ("🎯 FIXED", "tmode:fixed")]]))
```

`handle_callback`, after the `mode:` branch:

```python
    if parts[0] == "tmode" and len(parts) > 1 and parts[1] in ("adr", "fixed"):
        db.set_entry_mode(parts[1])
        return (f"Entry mode → {parts[1].upper()} — applies from the next trade.",
                f"entry mode: {parts[1]}")
```

`/config`: add `f"entry mode: {db.entry_mode()}\n"` beside the `mode:` line. `format_pinned_help`: `/mode` line becomes `"/mode — execution (AUTO/MANUAL) + entry mode (ADR/FIXED)"`; `PINNED_HELP_VERSION = "6"`; update the `== "5"` test assertion (grep `PINNED_HELP_VERSION ==`).

- [ ] **Step 4: Run the suites**

Run: `cd service && python -m pytest tests/test_entry_mode.py tests/test_telegram_commands.py tests/test_channel.py tests/test_heartbeat.py tests/test_trades.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/db.py service/app/models.py service/app/main.py service/app/telegram.py service/tests/test_entry_mode.py service/tests/test_channel.py
git commit -m "feat(service): entry-mode kv + /mode four buttons + heartbeat/trade contract"
```

---

### Task 3: EA — inputs, runtime switch, sticky basket mode, FIXED behavior

**Files:**
- Modify: `mt5/Experts/XauAssistant.mq5` (inputs; OnInit; OnTimer heartbeat handling)
- Modify: `mt5/Include/XauAssistant/UiApi.mqh` (`PostHeartbeat` JSON + response parse ~line 192)
- Modify: `mt5/Include/XauAssistant/TradeManager.mqh` (`OnSignal` sizing ~line 302; `Manage()` ~line 322; trade events)

**Interfaces:**
- Consumes: heartbeat wire contract from Task 2 (`entry_mode` both directions, values "adr"/"fixed"); `TradeEventRequest.entry_mode`.
- Produces: `input ENUM_ENTRY_MODE EntryMode = ENTRY_ADR;` + `input double FixedLots = 0.05;`; runtime `g_entryMode` seeded from the input, updated from each heartbeat response; MT5 global `XAU_BASKET_MODE_<login>_<symbol>` (0=ADR, 1=FIXED) written at basket open.

- [ ] **Step 1: EA inputs + runtime state (`XauAssistant.mq5`)**

```mql5
enum ENUM_ENTRY_MODE { ENTRY_ADR = 0, ENTRY_FIXED = 1 };
input ENUM_ENTRY_MODE EntryMode  = ENTRY_ADR;  // ADR = 1% risk + adds/targets; FIXED = fixed lots, pure ride
input double          FixedLots  = 0.05;       // FIXED-mode entry size (broker-clamped)
```

`g_entryMode` (int/enum global) seeded from `EntryMode` in OnInit. In the OnTimer heartbeat handling, parse the response's `entry_mode` (UiApi outputs it — Step 2) and update `g_entryMode` when it differs, with one Print per change ("entry mode → FIXED (from Telegram) — applies to the next trade").

- [ ] **Step 2: Wire contract (`UiApi.mqh`)**

`PostHeartbeat`: append `",\"entry_mode\":\"" + (g_entryMode-style param) + "\""` to the JSON — pass the current mode string in as a new trailing parameter (`string entryMode`) from the EA (field order vs models.py is not significant to pydantic, but match Task 2's placement after `bar_c` for consistency). Parse the response: `entryMode_out = ExtractString(body, "entry_mode");` returned via a new reference output parameter next to `mode`.

- [ ] **Step 3: Sticky basket mode + FIXED behavior (`TradeManager.mqh`)**

- Key helper (same per-symbol pattern as `CycleKey`): `BasketModeKey()` → `"XAU_BASKET_MODE_" + login + "_" + _Symbol`.
- `Init` gains the fixed-lots value and a way to read the current runtime mode (pass both per-call from the EA: `OnSignal(sig, atr, stopPrice, entryModeFixed, fixedLots)` — adding trailing defaulted params keeps existing tests/calls valid, or thread via setters; match the file's existing style and document the choice in the report).
- `OnSignal` entry path: when opening a NEW basket in FIXED mode, `lots = clamp(FixedLots to SYMBOL_VOLUME_MIN/MAX/STEP)` instead of `m_risk.CalcLots(...)`, and `GlobalVariableSet(BasketModeKey(), 1)`; ADR entries write `0`. The reversal path (close old + open new) uses the CURRENT runtime mode for the new basket.
- `Manage()`: first line — read `bool basketFixed = GlobalVariableGet(BasketModeKey()) > 0.5;` and if a basket exists and `basketFixed`, RETURN after the shared-stop/reversal-independent bookkeeping — i.e. skip the add trigger, profit-target, and profit-lock blocks entirely. Read `Manage()` fully first and place the early-out so ONLY those three behaviors are skipped (anything else it does — e.g. peak tracking writes — may be skipped too for FIXED; peak has no consumer without the lock).
- Trade events: `OnTradeEvent`/`PostTradeEvent` calls gain the basket-mode string ("adr"/"fixed") → `entry_mode` in the JSON (`UiApi.PostTradeEvent` gains a trailing defaulted param so the reconciler's existing call keeps compiling; the reconciler may send `""`).

- [ ] **Step 4: Compile gate**

Copy ALL changed files to the MT5 data folder and compile via izi.md's MetaEditor CLI runbook: **0 errors / 0 warnings**, quote the Result line. Note: new inputs take defaults on hot-reload (`ENTRY_ADR`, 0.05) — live behavior unchanged, which is the intended safe rollout.

- [ ] **Step 5: Service suite regression**

Run: `cd service && python -m pytest`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add mt5/Experts/XauAssistant.mq5 mt5/Include/XauAssistant/UiApi.mqh mt5/Include/XauAssistant/TradeManager.mqh
git commit -m "feat(mt5): FIXED entry mode — fixed lots, sticky basket mode, pure trend ride"
```

---

### Task 4: izi.md + full gates

**Files:**
- Modify: `.claude/agents/izi.md`

- [ ] **Step 1: izi.md** — entry-modes table (ADR vs FIXED per the spec's comparison), the `/mode` four-button layout + `tmode:` callbacks, kv `entry_mode`, wire contract, `XAU_BASKET_MODE` key (add to the global-keys list), switch-applies-next-trade + sticky-basket rule, FixedLots default + the size math note (0.05 ≈ 1.5–2% risk/trade), backtest-first evidence pointer (`.superpowers/entry-mode-backtest-report.md`).
- [ ] **Step 2: Full suite** — `cd service && python -m pytest` green (flake rule).
- [ ] **Step 3: Commit** — `git add .claude/agents/izi.md && git commit -m "docs(izi): entry modes ADR/FIXED"`.

---

## Self-Review Notes (applied)

- Spec coverage: comparison table → T1 (backtest) + T3 (EA behavior); mode selection/stickiness → T3; /mode four buttons + pinned v6 + /config → T2; bookkeeping (TradeEventRequest + migration) → T2/T3; safety-rails invariant → Global Constraints; backtest-first → T1 runs before anything ships.
- Type consistency: `entry_mode` strings "adr"/"fixed" everywhere; `tmode:` callback prefix; `XAU_BASKET_MODE_<login>_<symbol>`; `FixedLots`/`ENTRY_FIXED` names consistent across T2/T3.
- Deliberate freedom in T3 (threading style for mode/lots into TradeManager) is bounded: trailing defaulted params or setters, existing file style governs, choice documented in the implementer report — not a placeholder, a scoped decision.
