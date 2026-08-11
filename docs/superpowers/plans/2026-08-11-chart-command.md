# /chart Real-Time Snapshot Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/chart` Telegram command that replies with a freshly rendered chart of closed candles **plus the forming bar** carried on the 5 s heartbeat — real-time to ≤5 s.

**Architecture:** The EA's `PostHeartbeat` (UiApi.mqh) self-reads bar 0 via `CopyRates` and appends five fields to the heartbeat JSON. The service merges that forming bar onto its closed-candle accumulator (`app/chart_cmd.py`), renders with a new `render_snapshot_chart` (reusing `render.py`'s private helpers), and the poller special-cases `/chart` to send the photo (owner first, channel mirror after).

**Tech Stack:** Python 3.12 / FastAPI / matplotlib (OO API), MQL5 (MetaEditor CLI compile).

**Spec:** `docs/superpowers/specs/2026-08-11-chart-command-design.md`

## Global Constraints

- Fail-open: every `/chart` failure path replies with TEXT, never raises into the poller; render functions return bool, never raise.
- New heartbeat fields are optional with default 0 — an old EA payload must still validate; `/heartbeat` response shape unchanged.
- Channel mirror: owner photo first, then channel; no reply_markup (structural — `send_photo_to`).
- `PINNED_HELP_VERSION` → `"5"` with a `/chart` line.
- MQL5 compile gated 0 errors / 0 warnings via MetaEditor CLI; **no EA signature changes** (UiApi reads bar 0 itself).
- Work on branch `feat/chart-command` (create from `main` in Task 1 Step 0). izi.md updated before merge (Task 3).
- Venv: `cd /mnt/c/Users/aatanda/Desktop/xau/service && source .venv/bin/activate`. Known flake: `test_pop_approved_command_concurrent_exactly_once` (re-run once if it alone fails).

---

### Task 1: Forming-bar model fields, merge helper, snapshot render

**Files:**
- Modify: `service/app/models.py` (`HeartbeatRequest`, after `algo_trading`)
- Create: `service/app/chart_cmd.py`
- Modify: `service/app/render.py` (new function after `render_trade_chart`)
- Test: `service/tests/test_chart_cmd.py` (create)

**Interfaces:**
- Consumes: `Candle` model (`t,o,h,l,c,v`); `render.py` helpers `_plot_ema`, `_plot_halftrend`, `_hline_with_label`; `Position` model (`direction`, `open_price`, `sl`).
- Produces (Task 2 relies on):
  - `HeartbeatRequest.bar_t: int = 0`, `bar_o/bar_h/bar_l/bar_c: float = 0.0`
  - `merge_forming_bar(candles: list, hb) -> list` in `app/chart_cmd.py`
  - `render_snapshot_chart(candles, out_path: str, positions=None) -> bool` in `app/render.py`

- [ ] **Step 0: Create the branch**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau && git checkout -b feat/chart-command main
```

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_chart_cmd.py`:

```python
"""Forming-bar merge + snapshot render for the /chart command."""
import types

from app.chart_cmd import merge_forming_bar
from app.models import Candle
from app.render import render_snapshot_chart


def _candles(n=120, start_t=1000, step=300, base=4000.0):
    out = []
    for i in range(n):
        o = base + i
        out.append(Candle(t=start_t + i * step, o=o, h=o + 2, l=o - 2,
                          c=o + 1, v=10))
    return out


def _hb(bar_t, o=5000.0, h=5002.0, l=4998.0, c=5001.0):
    return types.SimpleNamespace(bar_t=bar_t, bar_o=o, bar_h=h, bar_l=l,
                                 bar_c=c)


def test_merge_appends_newer_forming_bar():
    candles = _candles(5)
    merged = merge_forming_bar(candles, _hb(candles[-1].t + 300))
    assert len(merged) == 6
    assert merged[-1].c == 5001.0
    assert len(candles) == 5          # input untouched


def test_merge_replaces_same_bar():
    candles = _candles(5)
    merged = merge_forming_bar(candles, _hb(candles[-1].t))
    assert len(merged) == 5
    assert merged[-1].c == 5001.0


def test_merge_noop_when_bar_t_zero_or_missing():
    candles = _candles(5)
    assert merge_forming_bar(candles, _hb(0)) is candles
    assert merge_forming_bar(candles, types.SimpleNamespace()) is candles


def test_merge_noop_when_older_than_last_closed():
    candles = _candles(5)
    assert merge_forming_bar(candles, _hb(candles[-1].t - 300)) is candles


def test_merge_noop_when_prices_zero():
    candles = _candles(5)
    assert merge_forming_bar(
        candles, _hb(candles[-1].t + 300, o=0.0, h=0.0, l=0.0, c=0.0)) is candles


def test_merge_noop_on_empty_candles():
    assert merge_forming_bar([], _hb(1000)) == []


def test_render_snapshot_writes_png(tmp_path):
    out = tmp_path / "snap.png"
    assert render_snapshot_chart(_candles(), str(out)) is True
    assert out.stat().st_size > 1000


def test_render_snapshot_with_positions(tmp_path):
    out = tmp_path / "snap_pos.png"
    positions = [types.SimpleNamespace(direction="BUY", open_price=4100.0,
                                       sl=4090.0, lots=0.02, profit=1.0,
                                       ticket=1)]
    assert render_snapshot_chart(_candles(), str(out), positions) is True
    assert out.stat().st_size > 1000


def test_render_snapshot_empty_candles_false(tmp_path):
    assert render_snapshot_chart([], str(tmp_path / "x.png")) is False


def test_heartbeat_model_accepts_old_and_new_payloads():
    from app.models import HeartbeatRequest
    old = HeartbeatRequest(equity=1.0, balance=1.0, floating_pl=0.0)
    assert old.bar_t == 0 and old.bar_o == 0.0
    new = HeartbeatRequest(equity=1.0, balance=1.0, floating_pl=0.0,
                           bar_t=123, bar_o=1.0, bar_h=2.0, bar_l=0.5,
                           bar_c=1.5)
    assert new.bar_t == 123 and new.bar_c == 1.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && python -m pytest tests/test_chart_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.chart_cmd'`

- [ ] **Step 3: Implement**

`service/app/models.py` — append to `HeartbeatRequest` after `algo_trading: bool = True`:

```python
    # Forming (bar 0) OHLC carried by the EA every heartbeat so /chart can
    # render in real time without waiting for the bar to close. 0 = absent
    # (old EA, or CopyRates failure — fail-open).
    bar_t: int = 0
    bar_o: float = 0.0
    bar_h: float = 0.0
    bar_l: float = 0.0
    bar_c: float = 0.0
```

Create `service/app/chart_cmd.py`:

```python
"""Forming-bar merge for the /chart command.

The closed-candle accumulator only advances when a bar closes (the
/analyze cadence); the heartbeat carries the still-forming bar 0 so a
/chart render is real-time to the last heartbeat (~5 s)."""
from app.models import Candle


def merge_forming_bar(candles: list, hb) -> list:
    """Return `candles` with the heartbeat's forming bar appended, or
    replacing the last candle when it is the same bar re-observed. The
    input list is never mutated. No-op (same list back) when there is no
    usable forming bar: bar_t absent/0, prices 0 (CopyRates failure), an
    empty accumulator, or a forming bar older than the last closed candle
    (stale heartbeat from before the last close)."""
    bar_t = int(getattr(hb, "bar_t", 0) or 0)
    if not candles or bar_t <= 0:
        return candles
    o = getattr(hb, "bar_o", 0.0)
    h = getattr(hb, "bar_h", 0.0)
    l = getattr(hb, "bar_l", 0.0)
    c = getattr(hb, "bar_c", 0.0)
    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return candles
    forming = Candle(t=bar_t, o=o, h=h, l=l, c=c, v=0.0)
    last_t = candles[-1].t
    if bar_t == last_t:
        return candles[:-1] + [forming]
    if bar_t < last_t:
        return candles
    return candles + [forming]
```

`service/app/render.py` — add after `render_trade_chart` (reuse the module's
private helpers; copy the OO-API/tail conventions from `render_trade_chart`
verbatim — read that function's ending for the exact `set_xticks`/grid/
`tight_layout`/`savefig(dpi=...)`/close sequence and mirror it):

```python
def render_snapshot_chart(candles, out_path: str, positions=None) -> bool:
    """Current-market render for /chart: the last-100 window with
    HalfTrend/EMA overlays and a right-edge last-price label; when an open
    basket is supplied, each leg's entry line plus the shared SL line are
    overlaid. No event marker or risk boxes — there is no single trade
    event in a snapshot. Returns True on success; never raises."""
    if not candles:
        return False
    try:
        window = candles[-100:]
        window_len = len(window)
        offset = len(candles) - window_len

        closes = [c.c for c in candles]
        ema9_full = ema(closes, 9)
        ema21_full = ema(closes, 21)
        ema55_full = ema(closes, 55)
        ema200_full = ema(closes, 200)
        ht_full = halftrend(candles, amplitude=4)

        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot(111)

        _plot_halftrend(ax, ht_full, offset, window_len, linewidth=1.6, zorder=1)
        _plot_ema(ax, ema9_full, offset, window_len, "#888888", 0.8, alpha=0.35, zorder=1)
        _plot_ema(ax, ema21_full, offset, window_len, "#888888", 0.8, alpha=0.35, zorder=1)
        _plot_ema(ax, ema55_full, offset, window_len, "gold", 1.2, zorder=1)
        _plot_ema(ax, ema200_full, offset, window_len, "mediumpurple", 1.2, zorder=1)

        for i, c in enumerate(window):
            color = "#2ecc71" if c.c >= c.o else "#e74c3c"
            ax.vlines(i, c.l, c.h, color=color, linewidth=1, zorder=2)
            ax.vlines(i, min(c.o, c.c), max(c.o, c.c), color=color, linewidth=3, zorder=2)

        last = window[-1]
        ax.annotate(f"{last.c:g}", xy=(window_len - 1, last.c),
                    xytext=(6, 0), textcoords="offset points",
                    color="#2ecc71" if last.c >= last.o else "#e74c3c",
                    fontsize=8, fontweight="bold", zorder=6)

        for p in (positions or []):
            entry_color = "#2ecc71" if p.direction == "BUY" else "#e74c3c"
            _hline_with_label(ax, window_len, p.open_price, entry_color,
                              "--", "E", alpha=0.8)
        if positions:
            sl = positions[0].sl
            if sl and sl > 0:
                ax.axhline(sl, color="red", linestyle="-", linewidth=1.2, zorder=1.5)
                ax.text(window_len - 1, sl, f"SL {sl:g}", color="red",
                        fontsize=7, ha="right", va="bottom", zorder=6)
        # <tail: mirror render_trade_chart's axis/layout/save/close sequence>
        ...
        return True
    except Exception:
        return False
```

(The `...` tail is the ONLY part left to transcribe from `render_trade_chart`'s ending — same xticks handling, grid, `tight_layout`, `savefig`, and figure cleanup. Everything else above is complete.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && python -m pytest tests/test_chart_cmd.py tests/test_heartbeat.py tests/test_render.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/models.py service/app/chart_cmd.py service/app/render.py service/tests/test_chart_cmd.py
git commit -m "feat(service): forming-bar heartbeat fields + snapshot render pipeline"
```

---

### Task 2: /chart command in the poller + channel mirror + pinned v5

**Files:**
- Modify: `service/app/main.py` (poller command branch; new `_send_chart_snapshot` helper near `_mirror`)
- Modify: `service/app/telegram.py` (`format_pinned_help`, `PINNED_HELP_VERSION` → "5")
- Test: `service/tests/test_chart_cmd.py` (append)

**Interfaces:**
- Consumes: `merge_forming_bar`, `render_snapshot_chart` (Task 1); `_mirror(app, photo_bytes=, caption=)`; `app.state.recent_candles` dict (`{"symbol", "timeframe", "candles"}` or None); `app.state.latest_heartbeat` (`(ts, HeartbeatRequest) | None`); `app.state.screenshot_dir` (Path).
- Produces: `async _send_chart_snapshot(app) -> None` in `main.py`; `/chart` intercepted in the poller BEFORE `handle_command` (so `_mirror_command_text` never sees it — the photo mirror replaces the text mirror).

- [ ] **Step 1: Write the failing tests**

Append to `service/tests/test_chart_cmd.py`:

```python
import asyncio
import pathlib

from app.telegram import PINNED_HELP_VERSION, TelegramClient, format_pinned_help


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return {"ok": True, "result": {"message_id": 1}}

    def of(self, method):
        return [c for c in self.calls if c[0] == method]


class _Db:
    def __init__(self, channel_id=""):
        self._c = channel_id

    def get_kv(self, key):
        return self._c if key == "channel_id" else None


def _snap_app(tmp_path, candles, hb=None, hb_age=0.0, channel_id=""):
    import time as _time
    from app import main as app_main
    transport = FakeTransport()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        telegram=TelegramClient("tok", "555", transport=transport),
        recent_candles=({"symbol": "XAUUSD", "timeframe": "M5",
                         "candles": candles} if candles else None),
        latest_heartbeat=((_time.time() - hb_age, hb) if hb is not None else None),
        screenshot_dir=pathlib.Path(tmp_path),
        db=_Db(channel_id)))
    return app, transport, app_main


def _full_hb(bar_t, positions=()):
    from app.models import HeartbeatRequest
    return HeartbeatRequest(equity=1000.0, balance=1000.0, floating_pl=0.0,
                            positions=list(positions), bar_t=bar_t,
                            bar_o=5000.0, bar_h=5002.0, bar_l=4998.0,
                            bar_c=5001.0)


def test_chart_sends_photo_with_caption(tmp_path):
    candles = _candles()
    app, t, m = _snap_app(tmp_path, candles,
                          hb=_full_hb(candles[-1].t + 300))
    asyncio.run(m._send_chart_snapshot(app))
    photos = t.of("sendPhoto")
    assert len(photos) == 1
    assert photos[0][1]["chat_id"] == "555"
    assert "XAUUSD" in photos[0][1]["caption"]
    assert "closed bars only" not in photos[0][1]["caption"]


def test_chart_no_candles_replies_text(tmp_path):
    app, t, m = _snap_app(tmp_path, candles=None)
    asyncio.run(m._send_chart_snapshot(app))
    assert t.of("sendPhoto") == []
    assert "no candles yet" in t.of("sendMessage")[0][1]["text"]


def test_chart_stale_heartbeat_notes_closed_bars_only(tmp_path):
    candles = _candles()
    app, t, m = _snap_app(tmp_path, candles,
                          hb=_full_hb(candles[-1].t + 300), hb_age=120.0)
    asyncio.run(m._send_chart_snapshot(app))
    assert "closed bars only" in t.of("sendPhoto")[0][1]["caption"]


def test_chart_mirrors_photo_to_channel_owner_first(tmp_path):
    candles = _candles()
    app, t, m = _snap_app(tmp_path, candles,
                          hb=_full_hb(candles[-1].t + 300),
                          channel_id="-1001234")
    asyncio.run(m._send_chart_snapshot(app))
    photos = t.of("sendPhoto")
    assert [p[1]["chat_id"] for p in photos] == ["555", "-1001234"]
    assert photos[1][1]["caption"].startswith("👤 /chart")
    assert "reply_markup" not in photos[1][1]


def test_pinned_help_lists_chart_and_version_bumped():
    assert "/chart" in format_pinned_help()
    assert PINNED_HELP_VERSION == "5"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && python -m pytest tests/test_chart_cmd.py -v`
Expected: new tests FAIL (`AttributeError: ... '_send_chart_snapshot'`)

- [ ] **Step 3: Implement**

`service/app/main.py` — add near `_mirror` (imports: `from app.chart_cmd import merge_forming_bar`; `from app.render import render_snapshot_chart` joins the existing render import; `Path` is already imported):

```python
_CHART_HB_FRESH_S = 60


async def _send_chart_snapshot(app) -> None:
    """/chart: render closed candles + the heartbeat's forming bar and send
    as a photo (owner first, channel mirror after). Every failure path
    replies with text instead; never raises into the poller."""
    tg = app.state.telegram
    rc = app.state.recent_candles
    if not rc or not rc.get("candles"):
        await asyncio.to_thread(
            tg.send_message, "no candles yet — waiting for the first bar post")
        return
    latest = app.state.latest_heartbeat
    hb = latest[1] if latest is not None else None
    stale = (latest is None or (time.time() - latest[0]) > _CHART_HB_FRESH_S
             or not getattr(hb, "bar_t", 0))
    candles = rc["candles"]
    if not stale:
        candles = merge_forming_bar(candles, hb)
    out = str(app.state.screenshot_dir / "chart_cmd.png")
    positions = hb.positions if hb is not None else []
    ok = await asyncio.to_thread(render_snapshot_chart, candles, out, positions)
    if not ok:
        await asyncio.to_thread(tg.send_message, "chart render failed")
        return
    caption = (f"📈 {rc['symbol']} {rc['timeframe']} — {candles[-1].c:g} "
               f"(as of {time.strftime('%H:%M:%S')})")
    if stale:
        caption += " · closed bars only"
    png = await asyncio.to_thread(Path(out).read_bytes)
    await asyncio.to_thread(tg.send_photo, caption, png)
    await _mirror(app, photo_bytes=png, caption=f"👤 /chart\n{caption}")
```

In `telegram_poller`, inside the owner-command branch, BEFORE the
`handle_command` call:

```python
                if text.strip().split()[0].lower() == "/chart":
                    try:
                        await _send_chart_snapshot(app)
                    except Exception:
                        pass  # fail-open: /chart must never kill the poller
                    continue
```

`service/app/telegram.py` — in `format_pinned_help`, after the `/config`
line add:

```python
        "/chart — current chart snapshot",
```

and bump `PINNED_HELP_VERSION = "5"`. Update the two existing assertions:
`test_channel.py` (`== "4"` → `== "5"`) and keep
`test_telegram_commands.py`'s token loop passing (add `"/chart"` to its
token tuple).

- [ ] **Step 4: Run the affected suites**

Run: `cd service && python -m pytest tests/test_chart_cmd.py tests/test_channel.py tests/test_telegram_commands.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/main.py service/app/telegram.py service/tests/test_chart_cmd.py service/tests/test_channel.py service/tests/test_telegram_commands.py
git commit -m "feat(telegram): /chart command — real-time snapshot with channel mirror (pinned v5)"
```

---

### Task 3: EA forming-bar heartbeat fields + compile gate + izi.md

**Files:**
- Modify: `mt5/Include/XauAssistant/UiApi.mqh` (`PostHeartbeat`, ~line 132)
- Modify: `.claude/agents/izi.md`
- No service code changes.

**Interfaces:**
- Consumes: heartbeat JSON contract — field names must match `HeartbeatRequest` exactly (`bar_t/bar_o/bar_h/bar_l/bar_c`).
- Produces: every heartbeat carries the forming bar; zeros on `CopyRates` failure.

- [ ] **Step 1: Implement in `PostHeartbeat`**

At the top of the method body (before the `string json = ...` build):

```mql5
      // Forming (bar 0) OHLC for the service's /chart real-time render.
      // Zeros on CopyRates failure -- the service treats 0 as "no forming
      // bar" (fail-open, old-service compatible: unknown JSON fields are
      // ignored by pydantic).
      MqlRates bar0[];
      long   bar_t = 0;
      double bar_o = 0, bar_h = 0, bar_l = 0, bar_c = 0;
      if(CopyRates(_Symbol, PERIOD_CURRENT, 0, 1, bar0) == 1)
        {
         bar_t = (long)bar0[0].time;
         bar_o = bar0[0].open;
         bar_h = bar0[0].high;
         bar_l = bar0[0].low;
         bar_c = bar0[0].close;
        }
```

In the JSON build, after the `algo_trading` field (replace the closing `"}"`):

```mql5
                    ",\"algo_trading\":" + (algo_trading ? "true" : "false") +
                    ",\"bar_t\":" + (string)bar_t +
                    ",\"bar_o\":" + DoubleToString(bar_o, 2) +
                    ",\"bar_h\":" + DoubleToString(bar_h, 2) +
                    ",\"bar_l\":" + DoubleToString(bar_l, 2) +
                    ",\"bar_c\":" + DoubleToString(bar_c, 2) + "}";
```

- [ ] **Step 2: Copy to the MT5 data folder and compile via MetaEditor CLI**

Use the exact procedure from izi.md's ops runbook (copy `mt5/` includes into the data folder's `MQL5/Include/XauAssistant/`, run `MetaEditor64.exe /compile` on `XauAssistant.mq5`, parse the UTF-16 log with iconv). Gate: **0 errors, 0 warnings**. The EA hot-reloads on the chart with existing input values; no new inputs here.

- [ ] **Step 3: Run the full service suite (unchanged code, regression gate)**

Run: `cd service && python -m pytest`
Expected: green (flake rule applies)

- [ ] **Step 4: Update izi.md**

Telegram commands section: `/chart — current chart snapshot` (renders closed candles + the heartbeat's forming bar → real-time to ≤5 s; open-basket entry/SL lines overlaid; fallbacks: "no candles yet", "closed bars only" when the heartbeat is stale >60 s or has no forming bar; photo mirrored to the channel as `👤 /chart`). Heartbeat section: `bar_t/bar_o/bar_h/bar_l/bar_c` forming-bar fields, zeros = absent, fail-open both directions. Pinned help now v5.

- [ ] **Step 5: Commit**

```bash
git add mt5/Include/XauAssistant/UiApi.mqh .claude/agents/izi.md
git commit -m "feat(mt5): heartbeat carries the forming bar for /chart real-time renders"
```

---

## Self-Review Notes (applied)

- Spec coverage: EA fields (T3), models+merge+render (T1), command+mirror+pinned v5 (T2), izi (T3). Freshness threshold 60 s named `_CHART_HB_FRESH_S`.
- `/chart` intercepted before `handle_command` → `_mirror_command_text` never double-mirrors it (it would return None for unknown `/chart` anyway — but interception makes the photo mirror the single mirror path).
- Type consistency: `merge_forming_bar(candles, hb)` and `render_snapshot_chart(candles, out_path, positions=None)` used identically in T1 tests and T2 code.
- The one deliberate transcription point (render tail conventions) is explicitly scoped in T1 Step 3.
