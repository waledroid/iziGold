# Telegram Broadcast Channel + Live Trade Ticker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A linked Telegram channel mirrors all bot traffic through a privacy filter (no account figures), and one self-editing LIVE message tracks equity/positions during every open trade.

**Architecture:** Service-side only — the 5 s `/heartbeat` already carries equity, floating P/L, and positions. A new `app/ticker.py` holds the flat→open→flat ticker state machine (in-memory, fail-open). `telegram.py` gains channel-addressed client methods (structurally without `reply_markup`) and `redacted=` variants of command replies. `main.py` wires the ticker into `/heartbeat` (non-blocking), handles channel linking in the poller, and mirrors outbound traffic owner-first.

**Tech Stack:** Python 3.12, FastAPI, httpx (fail-open transport), SQLite kv store, pytest with fake Telegram transports.

**Spec:** `docs/superpowers/specs/2026-08-11-telegram-channel-ticker-design.md`

## Global Constraints

- **No EA/MQL5 changes.** Everything is service-side.
- **Fail-open:** channel/ticker Telegram failures are swallowed; they never delay the `/heartbeat` response or owner delivery. Owner send always happens **before** the channel mirror.
- **Privacy filter:** channel text never contains balance, equity, drawdown %, or HWM. Kept: prices, lots, direction, per-leg/basket floating P/L, realized per-trade P/L, session/regime/strategy/mode/EA state. Redacted figures become `•••`.
- **No controls in the channel, ever:** channel sends have no `reply_markup` — the channel-addressed client methods do not even accept the parameter.
- `TICKER_MIN_EDIT_S = 5` (in `app/ticker.py`).
- DB: kv key `channel_id` only (empty/absent = unlinked). No schema migration.
- All work in `service/`; run tests with the venv active (`source .venv/bin/activate`). Full suite green before the final commit (known flake: `test_pop_approved_command_concurrent_exactly_once` — re-run once before treating as regression).
- Work on branch `feat/telegram-channel-ticker` (create from `main` in Task 1, Step 0).
- izi.md must be updated in the same branch (Task 7) before merge.

---

### Task 1: Channel-addressed TelegramClient methods

**Files:**
- Modify: `service/app/telegram.py` (class `TelegramClient`, after `edit_message`, ~line 175)
- Test: `service/tests/test_channel.py` (create)

**Interfaces:**
- Consumes: existing `TelegramClient.transport(method, payload, files)` convention.
- Produces (later tasks call these exact signatures):
  - `TelegramClient.send_message_to(chat_id, text) -> dict | None`
  - `TelegramClient.send_photo_to(chat_id, caption, png_bytes) -> dict | None`
  - `TelegramClient.edit_message_to(chat_id, message_id, text) -> dict | None`

- [ ] **Step 0: Create the branch**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau && git checkout -b feat/telegram-channel-ticker main
```

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_channel.py`:

```python
"""Channel-addressed sends: explicit chat_id, structurally no reply_markup."""
import json

from app.telegram import TelegramClient


class FakeTransport:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return self.result


def _client(result=None):
    t = FakeTransport(result)
    return TelegramClient("tok", "555", transport=t), t


def test_send_message_to_overrides_chat_id():
    client, t = _client()
    client.send_message_to("-1001234", "hello channel")
    assert t.calls == [("sendMessage",
                        {"chat_id": "-1001234", "text": "hello channel"}, None)]


def test_send_message_to_never_has_reply_markup():
    client, t = _client()
    client.send_message_to("-1001234", "x")
    assert "reply_markup" not in t.calls[0][1]


def test_send_photo_to_overrides_chat_id():
    client, t = _client()
    client.send_photo_to("-1001234", "cap", b"png")
    method, payload, files = t.calls[0]
    assert method == "sendPhoto"
    assert payload == {"chat_id": "-1001234", "caption": "cap"}
    assert files == {"photo": ("chart.png", b"png", "image/png")}


def test_edit_message_to_overrides_chat_id():
    client, t = _client()
    client.edit_message_to("-1001234", 42, "new text")
    assert t.calls == [("editMessageText",
                        {"chat_id": "-1001234", "message_id": 42,
                         "text": "new text"}, None)]


def test_owner_methods_unchanged():
    client, t = _client()
    client.send_message("owner text")
    assert t.calls[0][1]["chat_id"] == "555"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && python -m pytest tests/test_channel.py -v`
Expected: FAIL with `AttributeError: 'TelegramClient' object has no attribute 'send_message_to'`

- [ ] **Step 3: Implement the methods**

In `service/app/telegram.py`, inside `class TelegramClient`, after `edit_message` (before `answer_callback`):

```python
    # Channel-addressed sends. Deliberately no reply_markup parameter:
    # the channel must never carry interactive controls (spec invariant),
    # so the restriction is structural, not a call-site convention.
    def send_message_to(self, chat_id, text):
        return self.transport("sendMessage",
                              {"chat_id": chat_id, "text": text}, None)

    def send_photo_to(self, chat_id, caption: str, png_bytes: bytes):
        return self.transport(
            "sendPhoto", {"chat_id": chat_id, "caption": caption},
            {"photo": ("chart.png", png_bytes, "image/png")})

    def edit_message_to(self, chat_id, message_id, text: str):
        return self.transport(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text}, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && python -m pytest tests/test_channel.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/telegram.py service/tests/test_channel.py
git commit -m "feat(telegram): channel-addressed client sends without reply_markup"
```

---

### Task 2: Redacted command variants (privacy filter)

**Files:**
- Modify: `service/app/telegram.py` (`_format_status` ~line 212, `_format_balance` ~line 286, `handle_command` ~line 379)
- Test: `service/tests/test_channel.py` (append)

**Interfaces:**
- Consumes: existing `handle_command(text, app) -> str | tuple | None`; `_format_status(app)`; `_format_balance(app)`.
- Produces (Task 6 relies on these):
  - `REDACTED = "•••"` module constant in `app/telegram.py`
  - `handle_command(text, app, redacted=False)` — with `redacted=True` every reply is channel-safe: no balance/equity/drawdown/HWM figures; keyboard tuples still returned as tuples (caller takes `[0]`).
  - `_format_status(app, redacted=False)`, `_format_balance(app, redacted=False)`

**Redaction rules (from spec):** status drops the `💰` line and the drawdown suffix; `/bal` becomes `💰 Balance: ••• | Equity: ••• | Floating: +$X.XX`; `/config` masks its balance/equity values. Everything else (`/stats`, `/history`, `/switch`, `/mode`, `/strategy`) is already account-free and passes through unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `service/tests/test_channel.py`:

```python
import time
import types

from app.telegram import REDACTED, handle_command


class _KvDb:
    """Minimal db stub: exec_mode + kv store, enough for handle_command."""

    def __init__(self):
        self.kv = {}

    def exec_mode(self):
        return "auto"

    def get_kv(self, key):
        return self.kv.get(key)

    def set_kv(self, key, value):
        self.kv[key] = value

    def strategy_ids(self):
        return ["halftrend_ema_v1"]


def _hb_ns(**over):
    base = dict(equity=4785.18, balance=4719.78, floating_pl=65.40,
                positions=[], kill_switch=False, hwm=4800.0, exposure_min=5,
                window_open=True, spread_points=25.0,
                active_strategy="halftrend_ema_v1", algo_trading=True)
    base.update(over)
    return types.SimpleNamespace(**base)


def _cmd_app():
    return types.SimpleNamespace(state=types.SimpleNamespace(
        latest_heartbeat=(time.time(), _hb_ns()), pending_switch=None,
        db=_KvDb(), pending_channel=None))


def test_status_redacted_hides_account_figures():
    app = _cmd_app()
    text = handle_command("/status", app, redacted=True)
    for figure in ("4785.18", "4719.78", "4800", "drawdown"):
        assert figure not in text
    assert "halftrend_ema_v1" in text          # strategy stays
    assert "Protection armed" in text           # state stays, number goes


def test_status_redacted_keeps_position_pl():
    app = _cmd_app()
    app.state.latest_heartbeat[1].positions = [types.SimpleNamespace(
        ticket=7, direction="SELL", lots=0.02, open_price=4391.60,
        sl=4400.0, profit=54.02)]
    text = handle_command("/status", app, redacted=True)
    assert "54.02" in text and "4391.6" in text


def test_bal_redacted_masks_balance_and_equity():
    text = handle_command("/bal", app=_cmd_app(), redacted=True)
    assert REDACTED in text
    assert "4719.78" not in text and "4785.18" not in text
    assert "+$65.40" in text                     # floating is trade-level


def test_config_redacted_masks_account_line():
    text = handle_command("/config", app=_cmd_app(), redacted=True)
    assert "4719.78" not in text and "4785.18" not in text
    assert REDACTED in text


def test_default_is_unredacted():
    text = handle_command("/bal", app=_cmd_app())
    assert "4719.78" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && python -m pytest tests/test_channel.py -v`
Expected: new tests FAIL (`ImportError: cannot import name 'REDACTED'`)

- [ ] **Step 3: Implement the redaction**

In `service/app/telegram.py`:

Add near the top (after `_ICON`):

```python
# Channel privacy filter: account-level figures are replaced with this
# marker in every channel-bound text (spec: members see how trades
# perform, never what the account is worth).
REDACTED = "•••"
```

Change `_format_status(app)` to `_format_status(app, redacted=False)` and adjust its body — the protection block and money line become:

```python
    if hb.kill_switch:
        protection = "⛔ KILL SWITCH TRIPPED — trading halted"
    else:
        protection = "🛡 Protection armed"
        if hb.hwm and not redacted:
            dd = max(0.0, (1 - hb.equity / hb.hwm) * 100)
            protection += f" · drawdown {dd:.1f}%"
```

and where the lines list is built:

```python
    if getattr(hb, "algo_trading", True) is False:
        lines.append("⚠️ ALGO TRADING OFF — MT5 cannot execute trades")
    if not redacted:
        lines.append(f"💰 {hb.equity} equity · {hb.balance} balance "
                     f"· {hb.floating_pl:+g} floating")
    lines += [
        protection,
        f"🎯 {strategy} · {mode}",
    ]
```

(The positions block below is unchanged — per-leg P/L is trade-level and stays.)

Change `_format_balance(app)` to `_format_balance(app, redacted=False)`; before the final return add:

```python
    if redacted:
        return f"💰 Balance: {REDACTED} | Equity: {REDACTED} | Floating: {floating}"
```

Change `handle_command(text, app)` to `handle_command(text, app, redacted=False)`:
- `/status` branch → `return _format_status(app, redacted=redacted)`
- `/bal` branch → `return _format_balance(app, redacted=redacted)`
- `/config` branch: the balance line becomes:

```python
            f"balance: {REDACTED if redacted else (hb.balance if hb else '?')} | "
            f"equity: {REDACTED if redacted else (hb.equity if hb else '?')}\n"
```

All other branches ignore `redacted` (already account-free).

- [ ] **Step 4: Run the new tests plus the existing telegram suites**

Run: `cd service && python -m pytest tests/test_channel.py tests/test_telegram.py tests/test_telegram_commands.py tests/test_telegram_buttons.py -v`
Expected: all PASS (default `redacted=False` keeps existing behavior byte-identical)

- [ ] **Step 5: Commit**

```bash
git add service/app/telegram.py service/tests/test_channel.py
git commit -m "feat(telegram): redacted command variants for the channel privacy filter"
```

---

### Task 3: Live ticker module (`app/ticker.py`)

**Files:**
- Create: `service/app/ticker.py`
- Test: `service/tests/test_ticker.py` (create)

**Interfaces:**
- Consumes: `HeartbeatRequest` shape (`equity`, `floating_pl`, `positions[].direction/lots/open_price/profit`); `TelegramClient.send_message`, `edit_message`, `send_message_to`, `edit_message_to` (Task 1); `app.state.db.exec_mode()`, `app.state.db.get_kv("channel_id")`.
- Produces (Task 4 relies on these):
  - `TICKER_MIN_EDIT_S = 5`
  - `class TickerState` (dataclass, all-default constructor)
  - `ticker_tick(app, hb, now: float) -> None` — sync, fail-open per Telegram call, reads/writes `app.state.ticker`.
  - `format_ticker(hb, mode: str, ts_str: str, closed=False, redacted=False) -> str`

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_ticker.py`:

```python
"""Live ticker state machine: flat→open posts once, open edits in place
(only on change, throttled), flat again freezes with CLOSED."""
import time
import types

from app.telegram import TelegramClient
from app.ticker import TICKER_MIN_EDIT_S, TickerState, format_ticker, ticker_tick


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.next_message_id = 100

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        if method == "sendMessage":
            self.next_message_id += 1
            return {"ok": True, "result": {"message_id": self.next_message_id}}
        return {"ok": True}

    def of(self, method):
        return [c for c in self.calls if c[0] == method]


class _Db:
    def __init__(self, channel_id=""):
        self._channel = channel_id

    def exec_mode(self):
        return "auto"

    def get_kv(self, key):
        return self._channel if key == "channel_id" else None


def _pos(direction="SELL", lots=0.02, price=4391.60, profit=54.02):
    return types.SimpleNamespace(ticket=1, direction=direction, lots=lots,
                                 open_price=price, sl=0.0, profit=profit)


def _hb(positions):
    return types.SimpleNamespace(equity=4785.18, floating_pl=65.40,
                                 positions=positions)


def _app(channel_id=""):
    transport = FakeTransport()
    tg = TelegramClient("tok", "555", transport=transport)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        telegram=tg, db=_Db(channel_id), ticker=TickerState()))
    return app, transport


def test_flat_heartbeats_send_nothing():
    app, t = _app()
    ticker_tick(app, _hb([]), now=1000.0)
    assert t.calls == []


def test_open_posts_one_live_message_and_remembers_id():
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    sends = t.of("sendMessage")
    assert len(sends) == 1
    assert "LIVE" in sends[0][1]["text"]
    assert "SELL 0.02 @ 4391.6" in sends[0][1]["text"]
    assert "reply_markup" not in sends[0][1]
    assert app.state.ticker.owner_msg_id == 101


def test_open_again_same_text_does_not_edit():
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    ticker_tick(app, _hb([_pos()]), now=1000.0 + TICKER_MIN_EDIT_S + 1)
    assert t.of("editMessageText") == []


def test_open_again_changed_text_edits_in_place():
    app, t = _app()
    ticker_tick(app, _hb([_pos(profit=54.02)]), now=1000.0)
    ticker_tick(app, _hb([_pos(profit=60.00)]),
                now=1000.0 + TICKER_MIN_EDIT_S + 1)
    edits = t.of("editMessageText")
    assert len(edits) == 1
    assert edits[0][1]["message_id"] == 101
    assert "60.00" in edits[0][1]["text"]


def test_edit_throttled_below_min_interval():
    app, t = _app()
    ticker_tick(app, _hb([_pos(profit=54.02)]), now=1000.0)
    ticker_tick(app, _hb([_pos(profit=60.00)]), now=1000.0 + 1)
    assert t.of("editMessageText") == []


def test_close_freezes_message_and_resets_state():
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    ticker_tick(app, _hb([]), now=1000.0 + TICKER_MIN_EDIT_S + 1)
    edits = t.of("editMessageText")
    assert len(edits) == 1
    assert "CLOSED" in edits[0][1]["text"]
    assert app.state.ticker.owner_msg_id is None
    # a fresh cycle later posts a brand-new message
    ticker_tick(app, _hb([_pos()]), now=2000.0)
    assert len(t.of("sendMessage")) == 2


def test_close_edit_is_never_throttled():
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    ticker_tick(app, _hb([]), now=1000.5)     # < TICKER_MIN_EDIT_S later
    assert len(t.of("editMessageText")) == 1


def test_channel_gets_redacted_variant():
    app, t = _app(channel_id="-1001234")
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    sends = t.of("sendMessage")
    assert len(sends) == 2
    assert sends[0][1]["chat_id"] == "555"        # owner first
    assert sends[1][1]["chat_id"] == "-1001234"
    assert "Equity" in sends[0][1]["text"]
    assert "Equity" not in sends[1][1]["text"]    # privacy filter
    assert "4785.18" not in sends[1][1]["text"]
    assert "+$65.40" in sends[1][1]["text"]       # floating stays


def test_telegram_none_is_safe():
    app, _ = _app()
    app.state.telegram = None
    ticker_tick(app, _hb([_pos()]), now=1000.0)   # must not raise


def test_send_failure_keeps_state_clean_for_retry():
    app, t = _app()
    t.__call__ = None  # not used; replace transport wholesale below
    app.state.telegram = TelegramClient(
        "tok", "555", transport=lambda m, p, f=None: None)
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    assert app.state.ticker.owner_msg_id is None  # next tick retries open


def test_format_ticker_closed_footer():
    text = format_ticker(_hb([_pos()]), "auto", "14:32:05", closed=True)
    assert text.startswith("📊 CLOSED")
    assert "final P/L in the close report" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && python -m pytest tests/test_ticker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ticker'`

- [ ] **Step 3: Implement `service/app/ticker.py`**

```python
"""Live trade ticker: one self-editing Telegram message per trade cycle.

Driven from /heartbeat (positions/equity/floating arrive every ~5 s).
State is in-memory only (app.state.ticker) — after a service restart the
first open-position heartbeat simply starts a fresh LIVE message and the
old one stops updating. Every Telegram call is fail-open: a failed send
or edit is dropped and the next heartbeat retries naturally.
"""
import time
from dataclasses import dataclass, field

TICKER_MIN_EDIT_S = 5


@dataclass
class TickerState:
    owner_msg_id: int | None = None
    owner_text: str = ""
    channel_msg_id: int | None = None
    channel_text: str = ""
    last_edit_ts: float = 0.0


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def format_ticker(hb, mode: str, ts_str: str, closed=False,
                  redacted=False) -> str:
    direction = hb.positions[0].direction if hb.positions else "?"
    head = "📊 CLOSED" if closed else "📊 LIVE"
    lines = [f"{head} — {direction} basket ({mode})"]
    if not redacted:
        lines.append(f"Equity     ${hb.equity:,.2f}")
    lines.append(f"Floating   {_money(hb.floating_pl)}")
    lines.append("")
    for p in hb.positions:
        lines.append(f"{p.direction} {p.lots:g} @ {p.open_price:g}   "
                     f"{_money(p.profit)}")
    lines.append("")
    if closed:
        lines.append(f"closed {ts_str} — final P/L in the close report")
    else:
        lines.append(f"updated {ts_str}")
    return "\n".join(lines)


def _channel_id(app) -> str | None:
    try:
        return app.state.db.get_kv("channel_id") or None
    except Exception:
        return None


def ticker_tick(app, hb, now: float) -> None:
    """One heartbeat's worth of ticker work. Sync (call via to_thread or
    directly in tests); never raises."""
    tg = getattr(app.state, "telegram", None)
    if tg is None:
        return
    st = app.state.ticker
    ts_str = time.strftime("%H:%M:%S", time.localtime(now))
    try:
        mode = app.state.db.exec_mode()
    except Exception:
        mode = "?"
    cid = _channel_id(app)

    if hb.positions and st.owner_msg_id is None:
        # flat -> open: post the LIVE message(s)
        text = format_ticker(hb, mode, ts_str)
        try:
            sent = tg.send_message(text)
        except Exception:
            sent = None
        msg_id = (sent or {}).get("result", {}).get("message_id")
        if msg_id is None:
            return  # retry the open on the next heartbeat
        st.owner_msg_id, st.owner_text = msg_id, text
        st.last_edit_ts = now
        if cid:
            ch_text = format_ticker(hb, mode, ts_str, redacted=True)
            try:
                ch_sent = tg.send_message_to(cid, ch_text)
            except Exception:
                ch_sent = None
            ch_id = (ch_sent or {}).get("result", {}).get("message_id")
            if ch_id is not None:
                st.channel_msg_id, st.channel_text = ch_id, ch_text
        return

    if hb.positions and st.owner_msg_id is not None:
        # open -> open: silent in-place edit, throttled, only on change.
        # The timestamp line alone always differs, so compare without it —
        # otherwise every heartbeat would count as "changed".
        if now - st.last_edit_ts < TICKER_MIN_EDIT_S:
            return
        text = format_ticker(hb, mode, ts_str)
        if _body(text) == _body(st.owner_text):
            return
        try:
            tg.edit_message(st.owner_msg_id, text)
        except Exception:
            pass
        st.owner_text = text
        st.last_edit_ts = now
        if cid and st.channel_msg_id is not None:
            ch_text = format_ticker(hb, mode, ts_str, redacted=True)
            try:
                tg.edit_message_to(cid, st.channel_msg_id, ch_text)
            except Exception:
                pass
            st.channel_text = ch_text
        return

    if not hb.positions and st.owner_msg_id is not None:
        # open -> flat: freeze with CLOSED (never throttled), reset state.
        # The frozen numbers are the LAST OPEN snapshot (this heartbeat is
        # already flat); the close report remains the authoritative P/L.
        prev = app.state.latest_heartbeat
        snapshot = prev[1] if prev is not None and prev[1].positions else hb
        text = format_ticker(snapshot, mode, ts_str, closed=True)
        try:
            tg.edit_message(st.owner_msg_id, text)
        except Exception:
            pass
        if cid and st.channel_msg_id is not None:
            try:
                tg.edit_message_to(
                    cid, st.channel_msg_id,
                    format_ticker(snapshot, mode, ts_str, closed=True,
                                  redacted=True))
            except Exception:
                pass
        app.state.ticker = TickerState()


def _body(text: str) -> str:
    """Ticker text minus its trailing 'updated HH:MM:SS' line."""
    return text.rsplit("\n", 1)[0]
```

**Note for the implementer:** `ticker_tick` reads `app.state.latest_heartbeat` only in the close branch, and only to freeze the last open snapshot; the test app namespace doesn't define it, so use `prev = getattr(app.state, "latest_heartbeat", None)`. Use exactly that `getattr` form.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && python -m pytest tests/test_ticker.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/ticker.py service/tests/test_ticker.py
git commit -m "feat(service): live trade ticker state machine (self-editing message)"
```

---

### Task 4: Wire the ticker into `/heartbeat` (non-blocking)

**Files:**
- Modify: `service/app/main.py` (lifespan init ~line 190; `heartbeat` endpoint ~line 399)
- Test: `service/tests/test_ticker.py` (append)

**Interfaces:**
- Consumes: `ticker_tick`, `TickerState` from Task 3.
- Produces: `app.state.ticker` (TickerState) and `app.state.ticker_busy` (bool) initialized in lifespan; ticker dispatched fire-and-forget from `/heartbeat`.

- [ ] **Step 1: Write the failing test**

Append to `service/tests/test_ticker.py`:

```python
def test_heartbeat_endpoint_triggers_ticker(tmp_path, monkeypatch):
    """Integration: /heartbeat with positions posts a LIVE message without
    delaying the response; flat heartbeats post nothing."""
    import importlib

    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tick.db"))
    from fastapi.testclient import TestClient

    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as client:
        transport = FakeTransport()
        main.app.state.telegram = TelegramClient("tok", "555",
                                                 transport=transport)
        hb = {"equity": 4785.18, "balance": 4719.78, "floating_pl": 65.40,
              "positions": [{"ticket": 1, "direction": "SELL", "lots": 0.02,
                             "open_price": 4391.60, "sl": 4400.0,
                             "profit": 54.02}],
              "kill_switch": False, "hwm": 4800.0, "exposure_min": 5,
              "window_open": True, "spread_points": 25.0,
              "active_strategy": "halftrend_ema_v1"}
        r = client.post("/heartbeat", json=hb)
        assert r.status_code == 200
        assert r.json()["command"] is None      # response shape unchanged
        for _ in range(40):                      # ticker runs in background
            if transport.of("sendMessage"):
                break
            time.sleep(0.05)
        sends = transport.of("sendMessage")
        assert len(sends) == 1 and "LIVE" in sends[0][1]["text"]
```

(Also add `import time` at the top of the file if not already present.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && python -m pytest tests/test_ticker.py::test_heartbeat_endpoint_triggers_ticker -v`
Expected: FAIL — no sendMessage recorded (nothing dispatches the ticker yet)

- [ ] **Step 3: Wire it in `service/app/main.py`**

Add the import near the other `app.*` imports:

```python
from app.ticker import TickerState, ticker_tick
```

In the lifespan startup block (next to `app.state.latest_heartbeat = None`, ~line 190):

```python
    app.state.ticker = TickerState()
    app.state.ticker_busy = False
```

In the `heartbeat` endpoint, immediately after
`app.state.db.insert_heartbeat(...)` (~line 405) — **before** the
command-delivery logic so an exception can't be introduced below it:

```python
    # Live ticker: fire-and-forget so three potential Telegram calls (10 s
    # timeout each) can never delay this response — the EA's commands ride
    # on it. ticker_busy collapses overlapping runs to at most one.
    if not app.state.ticker_busy and getattr(app.state, "telegram", None) is not None:
        app.state.ticker_busy = True
        hb_now = time.time()

        async def _ticker_bg(hb=hb, hb_now=hb_now):
            try:
                await asyncio.to_thread(ticker_tick, app, hb, hb_now)
            except Exception:
                pass
            finally:
                app.state.ticker_busy = False

        asyncio.create_task(_ticker_bg())
```

**Ordering note:** the close branch of `ticker_tick` freezes the *previous*
heartbeat's snapshot via `app.state.latest_heartbeat` — but line 403 has
already overwritten it with the flat heartbeat by the time the task runs.
That is exactly why `ticker_tick` falls back to the current `hb` when the
stored one has no positions (`snapshot = ... if prev[1].positions else hb`):
the frozen text then shows the flat basket's direction placeholder rather
than stale legs. To keep the last real legs in the frozen message, capture
`previous` (line 401 already holds it) and pass it: change the dispatch to
`ticker_tick(app, hb, hb_now, previous)` and add the parameter in
`ticker.py`:

```python
def ticker_tick(app, hb, now: float, previous=None) -> None:
```

and in the close branch replace the `prev = getattr(...)` line with:

```python
        snapshot = (previous[1] if previous is not None
                    and previous[1].positions else hb)
```

(Task 3's direct-call tests pass `previous=None` implicitly and still pass:
the close-branch falls back to `hb`.) Update the Task 3 close tests? No —
they assert on "CLOSED" and reset only, which hold either way.

- [ ] **Step 4: Run the ticker + heartbeat suites**

Run: `cd service && python -m pytest tests/test_ticker.py tests/test_heartbeat.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/main.py service/app/ticker.py service/tests/test_ticker.py
git commit -m "feat(service): dispatch live ticker from /heartbeat, non-blocking"
```

---

### Task 5: Channel linking (owner-approved) + `/channel` command

**Files:**
- Modify: `service/app/telegram.py` (`handle_command`, `handle_callback`, `format_pinned_help`, `PINNED_HELP_VERSION`)
- Modify: `service/app/main.py` (`telegram_poller` ~line 77–104; lifespan init `app.state.pending_channel = None`)
- Test: `service/tests/test_channel.py` (append)

**Interfaces:**
- Consumes: `db.get_kv/set_kv`; existing poller structure; `kb()` helper.
- Produces (used by Task 6):
  - kv `channel_id` as the single source of link state.
  - `handle_channel_post(post: dict, app) -> tuple[str, dict] | None` in `app/telegram.py` — returns the owner-chat prompt `(text, keyboard)` or None; sets `app.state.pending_channel`.
  - `handle_callback` understands `chan:link:<id>` / `chan:ignore:<id>`.
  - `handle_command` understands `/channel` and `/channel unlink`.

- [ ] **Step 1: Write the failing tests**

Append to `service/tests/test_channel.py`:

```python
from app.telegram import (PINNED_HELP_VERSION, format_pinned_help,
                          handle_callback, handle_channel_post)


def _post(chat_id="-1001234", title="XAU Signals"):
    return {"chat": {"id": chat_id, "title": title, "type": "channel"},
            "text": "hello"}


def test_channel_post_offers_link_to_owner():
    app = _cmd_app()
    result = handle_channel_post(_post(), app)
    assert result is not None
    text, keyboard = result
    assert "XAU Signals" in text
    flat = [b for row in keyboard["inline_keyboard"] for b in row]
    assert [b["callback_data"] for b in flat] == \
        ["chan:link:-1001234", "chan:ignore:-1001234"]
    assert app.state.pending_channel == "-1001234"


def test_channel_post_ignored_when_already_linked_or_pending():
    app = _cmd_app()
    app.state.db.set_kv("channel_id", "-1009999")
    assert handle_channel_post(_post(), app) is None
    app2 = _cmd_app()
    app2.state.pending_channel = "-1008888"
    assert handle_channel_post(_post(), app2) is None


def test_chan_link_callback_stores_kv_and_clears_pending():
    app = _cmd_app()
    app.state.pending_channel = "-1001234"
    edit_text, toast = handle_callback("chan:link:-1001234", app)
    assert app.state.db.get_kv("channel_id") == "-1001234"
    assert app.state.pending_channel is None
    assert "linked" in edit_text.lower()


def test_chan_ignore_callback_stores_nothing():
    app = _cmd_app()
    app.state.pending_channel = "-1001234"
    edit_text, toast = handle_callback("chan:ignore:-1001234", app)
    assert not app.state.db.get_kv("channel_id")
    assert app.state.pending_channel is None


def test_channel_command_states_and_unlink():
    app = _cmd_app()
    assert "no channel linked" in handle_command("/channel", app)
    app.state.db.set_kv("channel_id", "-1001234")
    assert "-1001234" in handle_command("/channel", app)
    reply = handle_command("/channel unlink", app)
    assert "unlinked" in reply
    assert not app.state.db.get_kv("channel_id")


def test_pinned_help_mentions_channel_and_version_bumped():
    assert "/channel" in format_pinned_help()
    assert PINNED_HELP_VERSION == "3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && python -m pytest tests/test_channel.py -v`
Expected: new tests FAIL (`ImportError: cannot import name 'handle_channel_post'`)

- [ ] **Step 3: Implement in `service/app/telegram.py`**

Add after `handle_command`:

```python
def handle_channel_post(post: dict, app):
    """A message posted in a channel the bot was added to. If no channel is
    linked and no offer is pending, stage this channel and return the
    owner-chat confirmation (text, keyboard); otherwise None. Only the
    owner's ✅ callback (chan:link) actually stores the id — a stranger's
    channel can never self-link."""
    chat = post.get("chat") or {}
    cid = str(chat.get("id") or "")
    if not cid:
        return None
    if app.state.db.get_kv("channel_id"):
        return None
    if getattr(app.state, "pending_channel", None) is not None:
        return None
    title = chat.get("title") or "channel"
    app.state.pending_channel = cid
    text = (f"🔗 Link channel «{title}» ({cid})?\n"
            f"Members will see trade activity — never account figures.")
    keyboard = kb([[("✅ Link", f"chan:link:{cid}"),
                    ("❌ Ignore", f"chan:ignore:{cid}")]])
    return (text, keyboard)
```

In `handle_command`, before the final `return None`:

```python
    if cmd == "/channel":
        if parts[1:] and parts[1].lower() == "unlink":
            app.state.db.set_kv("channel_id", "")
            return "🔗 channel unlinked — mirroring off"
        cid = app.state.db.get_kv("channel_id")
        if cid:
            return f"🔗 linked to channel {cid} — /channel unlink to stop"
        return ("no channel linked — add the bot as admin to your channel, "
                "post any message there, then approve the prompt that "
                "appears here")
```

In `handle_callback`, before the final `return (None, "unknown")`:

```python
    if parts[0] == "chan" and len(parts) == 3:
        # parts[2] is the channel id; ids are negative ("-100..."), but the
        # split on ":" is safe — callback data is built as chan:<action>:<id>
        # and the id contains no colon.
        app.state.pending_channel = None
        if parts[1] == "link":
            db.set_kv("channel_id", parts[2])
            return (f"🔗 Channel linked ({parts[2]}) — mirroring on.", "linked")
        return ("Channel ignored.", "ignored")
```

In `format_pinned_help`, add after the `/config` line:

```python
        "/channel — link/unlink the broadcast channel",
```

and bump:

```python
PINNED_HELP_VERSION = "3"
```

- [ ] **Step 4: Wire the poller in `service/app/main.py`**

In lifespan startup (next to `app.state.ticker = ...`):

```python
    app.state.pending_channel = None
```

In `telegram_poller`, inside the update loop, after the `callback_query`
block's `continue` (~line 93) and before `message = upd.get("message")`:

```python
                ch_post = upd.get("channel_post")
                if ch_post is not None:
                    offer = handle_channel_post(ch_post, app)
                    if offer is not None:
                        await asyncio.to_thread(
                            app.state.telegram.send_message,
                            offer[0], offer[1])
                    continue
```

Add `handle_channel_post` to the imports from `app.telegram` (line ~21).

- [ ] **Step 5: Run the channel + telegram + pinned suites**

Run: `cd service && python -m pytest tests/test_channel.py tests/test_telegram_commands.py tests/test_telegram.py -v`
Expected: all PASS (pinned tests read PINNED_HELP_VERSION dynamically; if one hardcodes "2", update that assertion to "3")

- [ ] **Step 6: Commit**

```bash
git add service/app/telegram.py service/app/main.py service/tests/test_channel.py
git commit -m "feat(telegram): owner-approved channel linking + /channel command"
```

---

### Task 6: Outbound mirroring (alerts, charts, commands, callbacks)

**Files:**
- Modify: `service/app/main.py` — new `_mirror` helper; call sites: `maybe_propose` (~line 298), algo transition in `heartbeat` (~line 406), `proposal_result` (~line 448), `notify` (~line 469), trade-event photo + close message (~line 751/759), poller command + callback handling (~line 85–103)
- Test: `service/tests/test_channel.py` (append)

**Interfaces:**
- Consumes: `send_message_to`/`send_photo_to` (Task 1), `handle_command(..., redacted=True)` (Task 2), kv `channel_id` (Task 5).
- Produces: `async _mirror(app, text=None, photo_bytes=None, caption="")` in `main.py` — owner-independent, fail-open, no-op when unlinked.

- [ ] **Step 1: Write the failing tests**

Append to `service/tests/test_channel.py`:

```python
import importlib

import pytest
from fastapi.testclient import TestClient


class _RecordingTransport:
    def __init__(self, fail_chat_ids=()):
        self.calls = []
        self.fail_chat_ids = set(fail_chat_ids)
        self._mid = 200

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        if str(payload.get("chat_id")) in self.fail_chat_ids:
            return None
        if method in ("sendMessage", "sendPhoto"):
            self._mid += 1
            return {"ok": True, "result": {"message_id": self._mid}}
        return {"ok": True}

    def sends(self):
        return [(p.get("chat_id"), p.get("text") or p.get("caption"))
                for m, p, f in self.calls if m in ("sendMessage", "sendPhoto")]


@pytest.fixture()
def linked_app(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mirror.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as client:
        transport = _RecordingTransport()
        main.app.state.telegram = TelegramClient("tok", "555",
                                                 transport=transport)
        main.app.state.db.set_kv("channel_id", "-1001234")
        yield main, client, transport


def test_notify_mirrors_owner_first(linked_app):
    main, client, transport = linked_app
    r = client.post("/notify", json={"text": "🚫 entry not executed: spread"})
    assert r.status_code == 200
    sends = transport.sends()
    assert sends[0][0] == "555"
    assert sends[1] == ("-1001234", "🚫 entry not executed: spread")


def test_channel_failure_leaves_owner_delivery_intact(linked_app):
    main, client, transport = linked_app
    transport.fail_chat_ids = {"-1001234"}
    r = client.post("/notify", json={"text": "hello"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert transport.sends()[0][0] == "555"


def test_unlinked_channel_sends_nothing_extra(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "nolink.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as client:
        transport = _RecordingTransport()
        main.app.state.telegram = TelegramClient("tok", "555",
                                                 transport=transport)
        client.post("/notify", json={"text": "hello"})
        assert [c for c, _ in transport.sends()] == ["555"]


def test_channel_payloads_never_carry_reply_markup(linked_app):
    main, client, transport = linked_app
    client.post("/notify", json={"text": "hi"})
    for method, payload, files in transport.calls:
        if str(payload.get("chat_id")) == "-1001234":
            assert "reply_markup" not in payload


def test_mirror_helper_redacts_command_replies(linked_app):
    """Poller-level mirroring is driven by _mirror_command; verify the
    composed channel text: '👤 /bal' header + redacted reply."""
    main, client, transport = linked_app
    hb = {"equity": 4785.18, "balance": 4719.78, "floating_pl": 65.40,
          "positions": [], "kill_switch": False, "hwm": 4800.0,
          "exposure_min": 5, "window_open": True, "spread_points": 25.0,
          "active_strategy": "halftrend_ema_v1"}
    client.post("/heartbeat", json=hb)
    text = main._mirror_command_text("/bal", main.app)
    assert text.startswith("👤 /bal")
    assert "4719.78" not in text and "•••" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && python -m pytest tests/test_channel.py -v`
Expected: new tests FAIL (no mirroring yet; `_mirror_command_text` missing)

- [ ] **Step 3: Implement in `service/app/main.py`**

Add helpers after `telegram_poller`'s definition-neighbors (e.g. right
before `async def _apply_telegram`, ~line 144):

```python
def _linked_channel(app) -> str | None:
    try:
        return app.state.db.get_kv("channel_id") or None
    except Exception:
        return None


async def _mirror(app, text: str | None = None,
                  photo_bytes: bytes | None = None, caption: str = "") -> None:
    """Mirror one already-sent owner message to the linked channel.
    Owner-first ordering is the caller's job (call this after the owner
    send). Fail-open: never raises, no-op when unlinked/no client."""
    cid = _linked_channel(app)
    tg = getattr(app.state, "telegram", None)
    if cid is None or tg is None:
        return
    try:
        if photo_bytes is not None:
            await asyncio.to_thread(tg.send_photo_to, cid, caption, photo_bytes)
        elif text:
            await asyncio.to_thread(tg.send_message_to, cid, text)
    except Exception:
        pass


def _mirror_command_text(text: str, app) -> str | None:
    """Channel rendition of an owner command: '👤 /cmd' + the redacted
    reply. None when the command is unknown or owner-only."""
    if text.split()[0].lower() == "/channel":
        return None  # link management is owner-only housekeeping
    reply = handle_command(text, app, redacted=True)
    if reply is None:
        return None
    body = reply[0] if isinstance(reply, tuple) else reply
    return f"👤 {text}\n\n{body}"
```

Wire the call sites (each mirror strictly **after** the owner send):

1. **`notify` endpoint** (~line 480): after the owner `send_message`
   `except` block, still inside `if tg is not None:`:

```python
        await _mirror(app, text=text)
```

2. **Algo-trading transition in `heartbeat`** (~line 414): after the
   owner send's `except` block:

```python
            await _mirror(app, text=text)
```

3. **`maybe_propose`** (~line 300): `maybe_propose` is sync (called from
   the sync `/analyze` path), so mirror synchronously after the owner
   send block:

```python
    if tg is not None:
        cid = _linked_channel(app)
        if cid is not None:
            try:
                tg.send_message_to(
                    cid, format_proposal(kind, direction, price, resp))
            except Exception:
                pass
```

4. **`proposal_result`** (~line 455/463): after the owner edit (executed/
   blocked), mirror the outcome as a fresh channel message (the channel
   has no proposal message to edit):

```python
        await _mirror(app, text=(
            f"{'📥' if row['kind']=='entry' else '📤'} {row['direction']} "
            f"@ {row['price']} — {mark}: {res.detail}"))
```

   and after the messageless close-fail send:

```python
        await _mirror(app, text=f"🚫 close failed: {res.detail}")
```

5. **Trade-event chart + close message** (~line 756 and ~line 763): after
   `_send_render_photo` succeeds:

```python
                    await _mirror(app, photo_bytes=render_path.read_bytes(),
                                  caption=caption)
```

   and after the close-message send:

```python
            await _mirror(app, text=_pl_message(ev.profit, ev.direction,
                                                legs, ev.price))
```

6. **Poller — command mirroring** (~line 103): after the owner reply
   send in `telegram_poller`:

```python
                    chan_text = _mirror_command_text(text, app)
                    if chan_text is not None:
                        await _mirror(app, text=chan_text)
```

7. **Poller — callback mirroring** (~line 92): after the tapped-message
   edit (`edit_text` truthy), mirror mode/strategy/proposal outcomes:

```python
                        if not cq.get("data", "").startswith("chan:"):
                            await _mirror(app, text=edit_text)
```

   (`chan:` link confirmations are owner-only housekeeping.)

- [ ] **Step 4: Run the full fast suite**

Run: `cd service && python -m pytest`
Expected: all PASS (271+ passed, 1 deselected). If only
`test_pop_approved_command_concurrent_exactly_once` fails, re-run once.

- [ ] **Step 5: Commit**

```bash
git add service/app/main.py service/tests/test_channel.py
git commit -m "feat(service): mirror bot traffic to the linked channel through the privacy filter"
```

---

### Task 7: izi.md update, full-suite gate, merge prep

**Files:**
- Modify: `.claude/agents/izi.md` (Telegram section + ops runbook)
- No code changes.

**Interfaces:** none — documentation and verification only.

- [ ] **Step 1: Update izi.md**

Add to the Telegram/commands section (match existing style):

- `/channel` — shows link state; `/channel unlink` stops mirroring. Linking procedure: create channel → add bot as **admin with post rights** → post anything in the channel → approve the "Link channel?" prompt in the owner chat. kv `channel_id` is the single source of truth.
- **Privacy filter invariant:** channel text never contains balance, equity, drawdown %, or HWM (masked as `•••`); trade-level figures (prices, lots, per-leg/basket floating, realized per-trade P/L) pass through. Owner commands are mirrored as `👤 /cmd` + redacted reply.
- **No controls in the channel, ever** — channel-addressed client methods (`send_message_to` etc.) don't accept `reply_markup`.
- **Live ticker:** one self-editing `📊 LIVE` message per trade cycle (owner + redacted channel copy), edits throttled to ≥5 s and skipped when unchanged; freezes as `📊 CLOSED` on flat; in-memory state → a service restart mid-trade simply starts a fresh message. Authoritative P/L remains the close report.
- Ops note: mirroring/ticker are fail-open — channel send failures never touch owner delivery or the heartbeat path.

- [ ] **Step 2: Full suite + one slow-free verification run**

Run: `cd service && python -m pytest`
Expected: green (known flake rule applies)

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/izi.md
git commit -m "docs(izi): channel mirroring, privacy filter, live ticker"
```

- [ ] **Step 4: Merge to main (after final branch review)**

```bash
git checkout main && git merge --no-ff feat/telegram-channel-ticker && git push
```

---

## Self-Review Notes (already applied)

- Spec §ticker restart-safety → covered by in-memory `TickerState` + Task 3 `test_close_freezes_message_and_resets_state`.
- Spec "owner send happens first, channel second" → asserted in `test_notify_mirrors_owner_first` and the ticker channel test.
- Spec "no reply_markup ever in channel payloads" → structural (Task 1) + asserted (Task 6).
- Spec "`/heartbeat` response shape unchanged" → asserted in Task 4's integration test.
- Type consistency: `ticker_tick(app, hb, now, previous=None)` is the final signature (Task 4 amends Task 3 — implementers of Task 4 must apply that amendment; Task 3 tests remain valid).
- `/channel` excluded from command mirroring (owner-only housekeeping) — decided here, consistent with spec's "owner-only" list.
