# Telegram Control Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quiet, interactive Telegram: entry/exit proposals with 🟢/🔴 buttons, AUTO/MANUAL mode toggle, strategy switching, and `/config` — with the EA executing approved proposals via its heartbeat.

**Architecture:** All decision state lives service-side in SQLite (`proposals` table, `kv.exec_mode`). `/analyze` raises/expires proposals and stops per-signal alerting; the poller handles `callback_query` taps; `/heartbeat`'s response carries the runtime mode and at most ONE pending command per beat; the EA obeys the runtime mode, executes commands through the existing `CTradeManager` pipeline, and reports via a new `/proposal-result` endpoint which edits the Telegram message.

**Tech Stack:** FastAPI + SQLite (stdlib sqlite3, matching `db.py`'s existing style), httpx transport already abstracted in `telegram.py`, MQL5.

**Spec:** `docs/superpowers/specs/2026-08-03-telegram-control-design.md`
Deviation from spec (approved here): the heartbeat response carries a single `command` object (or `null`) instead of a `commands` list — the EA heartbeats every 5s, so the queue drains one command per beat; MQL5 JSON handling stays trivial.

## Global Constraints

- Python in `service/`; tests: `cd service && FORECASTER=fake .venv/bin/pytest -q`. Follow `db.py`'s existing conventions (module-level SQL, `Row` access) and `telegram.py`'s fail-open transport pattern (any exception → None, never raises).
- Fail-open: no Telegram call may ever raise into `/analyze`, `/heartbeat`, or the poller loop; the EA must behave correctly with the service down.
- Proposal statuses: exactly `pending|approved|dispatched|executed|skipped|expired|blocked`.
- `kv` key `exec_mode`, values `auto|manual`, default `manual`.
- Callback data format: `prop:<id>:take`, `prop:<id>:skip`, `mode:auto`, `mode:manual`, `strat:<strategy_id>`.
- Heartbeat response JSON adds: `"mode":"auto"|"manual"` and `"command":{"cmd":"execute","proposal_id":N,"direction":"BUY"|"SELL"}` or `"command":{"cmd":"close_all","proposal_id":N}` or `"command":null`.
- Single-chat security: messages AND callback queries are ignored unless the sender chat/user id equals the active client's chat id.
- MQL5 compile gate: 0 errors, 0 warnings (MetaEditor CLI, same mechanism as `scripts/setup.sh` phase 6; data folder `Terminal/D0E8209F77C8CF37AD8BF550E51FF075`).
- Commit prefix `feat(tg):`. Restart the service after service-side tasks to keep the live system current.

---

### Task 1: `db.py` — proposals table + exec-mode kv

**Files:**
- Modify: `service/app/db.py`
- Test: `service/tests/test_db_proposals.py` (create)

**Interfaces:**
- Consumes: `db.py`'s existing `Database` class (check its constructor/`_conn` pattern and mirror it; `kv` table exists with get/set helpers — find their names with grep and reuse).
- Produces (methods on the Database class):
  - `exec_mode() -> str` (default `"manual"`), `set_exec_mode(mode: str) -> None` (validates against `("auto","manual")`, raises ValueError otherwise)
  - `create_proposal(kind: str, direction: str, strategy_id: str, price: float, signal_id: int | None) -> int`
  - `pending_proposal(kind: str | None = None) -> sqlite3.Row | None` (newest pending, optionally filtered by kind)
  - `set_proposal_status(pid: int, status: str) -> None` (stamps `decided_ts` for skipped/approved, `executed_ts` for executed)
  - `set_proposal_message(pid: int, tg_message_id: int) -> None`
  - `pop_approved_command() -> sqlite3.Row | None` (oldest `approved` row → marks it `dispatched` in the same transaction, returns it)
  - `get_proposal(pid: int) -> sqlite3.Row | None`

- [ ] **Step 1: Write the failing tests**

`service/tests/test_db_proposals.py` (mirror the setup style of the existing db tests — grep `tests/` for how a temp `Database` is constructed):

```python
import pytest


def test_exec_mode_default_and_set(db):
    assert db.exec_mode() == "manual"
    db.set_exec_mode("auto")
    assert db.exec_mode() == "auto"
    with pytest.raises(ValueError):
        db.set_exec_mode("yolo")


def test_proposal_lifecycle(db):
    pid = db.create_proposal("entry", "BUY", "halftrend_ema_v1", 4066.5, None)
    row = db.get_proposal(pid)
    assert row["status"] == "pending" and row["direction"] == "BUY"
    assert db.pending_proposal()["id"] == pid
    assert db.pending_proposal(kind="exit") is None
    db.set_proposal_message(pid, 777)
    assert db.get_proposal(pid)["tg_message_id"] == 777
    db.set_proposal_status(pid, "approved")
    cmd = db.pop_approved_command()
    assert cmd["id"] == pid
    assert db.get_proposal(pid)["status"] == "dispatched"
    assert db.pop_approved_command() is None          # delivered exactly once
    db.set_proposal_status(pid, "executed")
    assert db.get_proposal(pid)["executed_ts"] is not None


def test_pending_is_newest_and_single_query(db):
    a = db.create_proposal("entry", "BUY", "s", 1.0, None)
    b = db.create_proposal("entry", "SELL", "s", 2.0, None)
    assert db.pending_proposal()["id"] == b
    db.set_proposal_status(b, "expired")
    assert db.pending_proposal()["id"] == a
```

Provide a `db` fixture in this file (temp-file or `:memory:` Database, matching existing test conventions).

- [ ] **Step 2: Run to verify failure**

Run: `cd service && FORECASTER=fake .venv/bin/pytest -q tests/test_db_proposals.py`
Expected: FAIL (no such methods/table).

- [ ] **Step 3: Implement**

In `db.py`: add to the schema-creation section (same place existing `CREATE TABLE IF NOT EXISTS` statements live):

```sql
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  direction TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  price REAL NOT NULL,
  signal_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  tg_message_id INTEGER,
  decided_ts INTEGER,
  executed_ts INTEGER
)
```

Methods (adapt `self._conn`/lock usage to the class's existing pattern; use `int(time.time())` like the rest of the file):

```python
    def exec_mode(self):
        row = self._conn.execute(
            "SELECT value FROM kv WHERE key='exec_mode'").fetchone()
        return row[0] if row else "manual"

    def set_exec_mode(self, mode):
        if mode not in ("auto", "manual"):
            raise ValueError(f"invalid exec mode: {mode}")
        with self._conn:
            self._conn.execute(
                "INSERT INTO kv(key, value) VALUES('exec_mode', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,))

    def create_proposal(self, kind, direction, strategy_id, price, signal_id):
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO proposals(created_ts, kind, direction, strategy_id,"
                " price, signal_id) VALUES(?,?,?,?,?,?)",
                (int(time.time()), kind, direction, strategy_id, price, signal_id))
            return cur.lastrowid

    def get_proposal(self, pid):
        return self._conn.execute(
            "SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()

    def pending_proposal(self, kind=None):
        q = "SELECT * FROM proposals WHERE status='pending'"
        args = ()
        if kind:
            q += " AND kind=?"
            args = (kind,)
        return self._conn.execute(q + " ORDER BY id DESC LIMIT 1", args).fetchone()

    def set_proposal_status(self, pid, status):
        now = int(time.time())
        with self._conn:
            self._conn.execute("UPDATE proposals SET status=? WHERE id=?", (status, pid))
            if status in ("approved", "skipped", "expired", "blocked"):
                self._conn.execute(
                    "UPDATE proposals SET decided_ts=? WHERE id=? AND decided_ts IS NULL",
                    (now, pid))
            if status == "executed":
                self._conn.execute(
                    "UPDATE proposals SET executed_ts=? WHERE id=?", (now, pid))

    def set_proposal_message(self, pid, tg_message_id):
        with self._conn:
            self._conn.execute(
                "UPDATE proposals SET tg_message_id=? WHERE id=?", (tg_message_id, pid))

    def pop_approved_command(self):
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM proposals WHERE status='approved' "
                "ORDER BY id ASC LIMIT 1").fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE proposals SET status='dispatched' WHERE id=?", (row["id"],))
            return row
```

If the existing `kv` helpers already provide get/set, use them instead of raw SQL for `exec_mode`.

- [ ] **Step 4: Run tests + full suite**

Run: `cd service && FORECASTER=fake .venv/bin/pytest -q tests/test_db_proposals.py && FORECASTER=fake .venv/bin/pytest -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add service/app/db.py service/tests/test_db_proposals.py
git commit -m "feat(tg): proposals table + exec-mode kv"
```

---

### Task 2: models — heartbeat mode/command + ProposalResult

**Files:**
- Modify: `service/app/models.py`
- Test: `service/tests/test_api.py` (append)

**Interfaces:**
- Produces: `HeartbeatResponse(switch_to, mode: Literal["auto","manual"] = "manual", command: dict | None = None)`; `ProposalResultRequest(proposal_id: int, ok: bool, detail: str = "")`. Tasks 4-6 and the EA rely on these exact field names.

- [ ] **Step 1: Failing contract test**

```python
def test_heartbeat_response_carries_mode_and_command(client, heartbeat_payload):
    r = client.post("/heartbeat", json=heartbeat_payload)
    body = r.json()
    assert body["mode"] in ("auto", "manual")
    assert "command" in body
```

(`heartbeat_payload`: reuse the file's existing heartbeat test payload or build the minimal `HeartbeatRequest` dict: equity/balance/floating_pl.)

- [ ] **Step 2: Verify it fails** — `pytest -q tests/test_api.py -k mode_and_command` → KeyError/assert fail.

- [ ] **Step 3: Implement**

`models.py`:

```python
class HeartbeatResponse(BaseModel):
    switch_to: str | None = None
    mode: Literal["auto", "manual"] = "manual"
    command: dict | None = None


class ProposalResultRequest(BaseModel):
    proposal_id: int
    ok: bool
    detail: str = ""
```

In `main.py`'s `heartbeat()` return, fill `mode=app.state.db.exec_mode()` and `command=None` for now (Task 5 wires the queue).

- [ ] **Step 4: Test + suite green.** Same commands as always.

- [ ] **Step 5: Commit** — `git add service/app/models.py service/app/main.py service/tests/test_api.py && git commit -m "feat(tg): heartbeat carries mode + command slot; ProposalResult model"`

---

### Task 3: `telegram.py` — inline keyboards, edits, callback answering

**Files:**
- Modify: `service/app/telegram.py`
- Test: `service/tests/test_telegram_buttons.py` (create)

**Interfaces:**
- Consumes: `TelegramClient`'s existing transport injection (`_default_transport(token)`; tests inject a fake `transport(method, payload, files=None)`), existing `send_message`.
- Produces on `TelegramClient`:
  - `send_message(text, reply_markup: dict | None = None) -> dict | None` (extend the existing method with the optional param; when set, include `reply_markup` in the sendMessage payload)
  - `edit_message(message_id: int, text: str, reply_markup: dict | None = None) -> dict | None` (method `editMessageText`)
  - `answer_callback(callback_id: str, text: str = "") -> dict | None` (method `answerCallbackQuery`)
- Module helpers: `kb(rows: list[list[tuple[str, str]]]) -> dict` building `{"inline_keyboard": [[{"text":t,"callback_data":d},...],...]}`; `PROPOSAL_KB = lambda pid: kb([[("🟢 Take trade", f"prop:{pid}:take"), ("🔴 Skip", f"prop:{pid}:skip")]])`; `EXIT_KB = lambda pid: kb([[("🔴 Exit now", f"prop:{pid}:take"), ("⏸ Hold", f"prop:{pid}:skip")]])`.

- [ ] **Step 1: Failing tests**

```python
from app.telegram import TelegramClient, kb


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 42}}


def make_client(t):
    # match TelegramClient's real constructor signature (check the file);
    # it accepts a transport for tests
    return TelegramClient("tok", "123", transport=t)


def test_kb_builds_inline_keyboard():
    m = kb([[("A", "a"), ("B", "b")]])
    assert m == {"inline_keyboard": [[{"text": "A", "callback_data": "a"},
                                      {"text": "B", "callback_data": "b"}]]}


def test_send_with_markup_and_edit_and_answer():
    t = FakeTransport()
    c = make_client(t)
    c.send_message("hi", reply_markup=kb([[("X", "x")]]))
    method, payload = t.calls[-1]
    assert method == "sendMessage" and "reply_markup" in payload
    c.edit_message(42, "new", reply_markup=None)
    method, payload = t.calls[-1]
    assert method == "editMessageText" and payload["message_id"] == 42
    c.answer_callback("cb1", "done")
    method, payload = t.calls[-1]
    assert method == "answerCallbackQuery" and payload["callback_query_id"] == "cb1"
```

Adjust `make_client` to the real constructor (read the class first — chat_id/transport arg names must match exactly).

- [ ] **Step 2: Verify failure.** `pytest -q tests/test_telegram_buttons.py` → TypeError/AttributeError.

- [ ] **Step 3: Implement** the three methods + `kb` helper following the class's existing `_transport(method, payload)` call pattern (fail-open: transport already returns None on any error; methods return that as-is). `edit_message` payload: `{"chat_id": self.chat_id, "message_id": message_id, "text": text}` plus `reply_markup` when given. `answer_callback` payload: `{"callback_query_id": callback_id, "text": text}`.

- [ ] **Step 4: Tests + suite green.**

- [ ] **Step 5: Commit** — `git add service/app/telegram.py service/tests/test_telegram_buttons.py && git commit -m "feat(tg): inline keyboards, message edits, callback answers"`

---

### Task 4: `/analyze` — alert diet + proposal lifecycle

**Files:**
- Modify: `service/app/main.py` (the `analyze()` route), `service/app/telegram.py` (proposal message formatter)
- Test: `service/tests/test_proposals_flow.py` (create)

**Interfaces:**
- Consumes: Task 1 db methods, Task 3 keyboards, existing `send_alert` removal point (`main.py:193` today: `send_alert(format_report(req, resp), settings)`).
- Produces: `format_proposal(kind, direction, price, resp) -> str` in telegram.py; `maybe_propose(app, req, resp) -> None` in main.py (called from `analyze()`); expiry semantics tested below. Task 5/6 rely on proposals being created ONLY in manual mode.

- [ ] **Step 1: Failing tests**

`tests/test_proposals_flow.py` — use the FastAPI TestClient fixture; monkeypatch the app's telegram client with a recording fake (`app.state.telegram`) exposing `send_message/edit_message` that records calls and returns `{"result": {"message_id": 7}}`:

```python
def _post_signal(client, signal, strategy_id="halftrend_ema_v1"):
    payload = make_analyze_payload()          # existing helper/fixture
    payload["signal"] = signal
    payload["strategy_id"] = strategy_id
    return client.post("/analyze", json=payload)


def test_manual_entry_creates_pending_proposal_and_sends_buttons(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    p = client.app.state.db.pending_proposal()
    assert p["kind"] == "entry" and p["direction"] == "BUY"
    assert p["tg_message_id"] == 7
    assert any("reply_markup" in str(c) or c[0] == "send" for c in fake_tg.calls)


def test_auto_mode_creates_no_proposal(client, fake_tg):
    client.app.state.db.set_exec_mode("auto")
    _post_signal(client, "BUY")
    assert client.app.state.db.pending_proposal() is None


def test_plain_none_signal_sends_nothing(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "NONE")
    assert fake_tg.calls == []          # alert diet: no per-bar noise


def test_opposite_signal_expires_pending_entry(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    pid = client.app.state.db.pending_proposal()["id"]
    _post_signal(client, "SELL")
    assert client.app.state.db.get_proposal(pid)["status"] == "expired"
    # and the SELL raised its own new proposal
    assert client.app.state.db.pending_proposal()["direction"] == "SELL"


def test_exit_signal_expires_pending_entry(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    pid = client.app.state.db.pending_proposal()["id"]
    _post_signal(client, "EXIT")
    assert client.app.state.db.get_proposal(pid)["status"] == "expired"


def test_duplicate_same_direction_signal_keeps_single_pending(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    first = client.app.state.db.pending_proposal()["id"]
    _post_signal(client, "BUY")
    assert client.app.state.db.pending_proposal()["id"] == first


def test_shadow_strategy_signal_never_proposes(client, fake_tg):
    # req.signal is the ACTIVE strategy's signal by contract; shadow-only
    # signals arrive with signal=NONE + shadows list — covered by the NONE test.
    # This guards the contract: a NONE post with shadows creates nothing.
    payload = make_analyze_payload()
    payload["signal"] = "NONE"
    payload["shadows"] = [{"strategy_id": "boll_stochrsi_v1", "signal": "BUY"}]
    client.post("/analyze", json=payload)
    assert client.app.state.db.pending_proposal() is None
```

Write the `fake_tg` fixture in this file: sets `app.state.telegram` to the recorder AND calls `set_active_client(recorder)`; restore/reset in teardown. Recorder's `send_message(text, reply_markup=None)` appends `("send", text, reply_markup)` and returns `{"ok": True, "result": {"message_id": 7}}`; `edit_message(mid, text, reply_markup=None)` appends `("edit", mid, text)`.

- [ ] **Step 2: Verify failures.**

- [ ] **Step 3: Implement**

`telegram.py`:

```python
def format_proposal(kind, direction, price, resp) -> str:
    ai = (f"{resp.direction} {resp.confidence:.0%} — {resp.verdict} {_ICON[resp.verdict]}"
          if resp.ai_available else "AI unavailable ❌")
    head = "📥 Entry proposal" if kind == "entry" else "📤 Exit proposal"
    return (f"{head}: {direction} @ {price}\n"
            f"AI: {ai}\nRegime: {resp.regime}\n"
            f"Valid while the strategy holds this stance.")
```

`main.py` — replace the `send_alert(format_report(req, resp), settings)` line with `maybe_propose(req, resp)` and add (module level, using `app.state`):

```python
def maybe_propose(req: AnalyzeRequest, resp: AnalyzeResponse) -> None:
    """Proposal lifecycle + alert diet. Never raises (telegram fail-open;
    db errors are logged by db layer conventions)."""
    db = app.state.db
    tg = getattr(app.state, "telegram", None)

    def edit(pid_row, suffix):
        if tg is None or pid_row["tg_message_id"] is None:
            return
        tg.edit_message(pid_row["tg_message_id"],
                        f"{'📥' if pid_row['kind']=='entry' else '📤'} "
                        f"{pid_row['direction']} @ {pid_row['price']} — {suffix}")

    # 1. expiry: does the active strategy still hold the pending stance?
    pending = db.pending_proposal()
    if pending is not None:
        stale = (
            (pending["kind"] == "entry" and (
                req.signal == "EXIT" or
                (req.signal in ("BUY", "SELL") and req.signal != pending["direction"])))
            or (pending["kind"] == "exit" and req.signal in ("BUY", "SELL"))
        )
        if stale:
            db.set_proposal_status(pending["id"], "expired")
            edit(pending, "⌛ expired (strategy stance changed)")
            pending = None

    # 2. new proposals: manual mode only, entry/exit signals only
    if req.signal not in ("BUY", "SELL", "EXIT"):
        return
    if db.exec_mode() != "manual":
        return
    kind = "exit" if req.signal == "EXIT" else "entry"
    price = req.candles[-1].c if req.candles else 0.0
    if pending is not None and pending["kind"] == kind and \
       (kind == "exit" or pending["direction"] == req.signal):
        return  # one pending proposal per stance
    if kind == "entry":
        direction = req.signal
    else:
        # An exit proposal's direction is informational only (close_all closes
        # the whole basket regardless). Best source: the newest proposal row
        # with kind='entry' and status='executed'; fall back to "BUY".
        last = db.last_executed_entry()   # tiny db helper added in this task:
        direction = last["direction"] if last else "BUY"
        # SELECT * FROM proposals WHERE kind='entry' AND status='executed'
        # ORDER BY id DESC LIMIT 1
    pid = db.create_proposal(kind, direction, req.strategy_id, price, None)
    if tg is not None:
        markup = (PROPOSAL_KB(pid) if kind == "entry" else EXIT_KB(pid))
        sent = tg.send_message(format_proposal(kind, direction, price, resp),
                               reply_markup=markup)
        if sent and sent.get("result", {}).get("message_id"):
            db.set_proposal_message(pid, sent["result"]["message_id"])
```

Import `PROPOSAL_KB, EXIT_KB, format_proposal` from `app.telegram`. NOTE for implementer: the exit-direction resolution above is intentionally simple — document in code that an exit proposal's direction is informational (the EA's `close_all` closes the whole basket regardless).
Call site in `analyze()`: `maybe_propose(req, resp)` wrapped in `try/except Exception: pass` (fail-open, mirroring the old send_alert's tolerance).

- [ ] **Step 4: All tests green** (new file + whole suite).

- [ ] **Step 5: Commit** — `git add service/app/main.py service/app/telegram.py service/tests/test_proposals_flow.py && git commit -m "feat(tg): proposal lifecycle + alert diet on /analyze"`

---

### Task 5: `/heartbeat` command delivery + `/proposal-result`

**Files:**
- Modify: `service/app/main.py`
- Test: `service/tests/test_proposals_flow.py` (append)

**Interfaces:**
- Consumes: Task 1 `pop_approved_command()`, Task 2 models.
- Produces: heartbeat response `command` population; `POST /proposal-result` (body `ProposalResultRequest`) → `{"ok": true}`; proposal statuses `executed`/`blocked` + message edits. EA (Task 7) consumes both.

- [ ] **Step 1: Failing tests**

```python
def test_approved_proposal_delivered_once_via_heartbeat(client, fake_tg, heartbeat_payload):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    pid = client.app.state.db.pending_proposal()["id"]
    client.app.state.db.set_proposal_status(pid, "approved")
    b1 = client.post("/heartbeat", json=heartbeat_payload).json()
    assert b1["command"] == {"cmd": "execute", "proposal_id": pid, "direction": "BUY"}
    b2 = client.post("/heartbeat", json=heartbeat_payload).json()
    assert b2["command"] is None


def test_exit_proposal_delivers_close_all(client, fake_tg, heartbeat_payload):
    client.app.state.db.set_exec_mode("manual")
    pid = client.app.state.db.create_proposal("exit", "BUY", "s", 1.0, None)
    client.app.state.db.set_proposal_status(pid, "approved")
    b = client.post("/heartbeat", json=heartbeat_payload).json()
    assert b["command"] == {"cmd": "close_all", "proposal_id": pid}


def test_proposal_result_updates_status_and_edits_message(client, fake_tg):
    pid = client.app.state.db.create_proposal("entry", "BUY", "s", 1.0, None)
    client.app.state.db.set_proposal_message(pid, 7)
    client.app.state.db.set_proposal_status(pid, "approved")
    client.app.state.db.pop_approved_command()
    r = client.post("/proposal-result",
                    json={"proposal_id": pid, "ok": True, "detail": "filled @4067.1"})
    assert r.status_code == 200
    assert client.app.state.db.get_proposal(pid)["status"] == "executed"
    assert any(c[0] == "edit" for c in fake_tg.calls)


def test_proposal_result_blocked(client, fake_tg):
    pid = client.app.state.db.create_proposal("entry", "SELL", "s", 1.0, None)
    client.app.state.db.set_proposal_status(pid, "approved")
    client.app.state.db.pop_approved_command()
    client.post("/proposal-result",
                json={"proposal_id": pid, "ok": False, "detail": "spread too wide"})
    assert client.app.state.db.get_proposal(pid)["status"] == "blocked"
```

- [ ] **Step 2: Verify failures.**

- [ ] **Step 3: Implement**

In `heartbeat()`:

```python
    cmd_row = app.state.db.pop_approved_command()
    command = None
    if cmd_row is not None:
        if cmd_row["kind"] == "entry":
            command = {"cmd": "execute", "proposal_id": cmd_row["id"],
                       "direction": cmd_row["direction"]}
        else:
            command = {"cmd": "close_all", "proposal_id": cmd_row["id"]}
    return HeartbeatResponse(switch_to=..., mode=app.state.db.exec_mode(),
                             command=command)
```

New endpoint:

```python
@app.post("/proposal-result")
async def proposal_result(res: ProposalResultRequest):
    db = app.state.db
    row = db.get_proposal(res.proposal_id)
    if row is None:
        return {"ok": False}
    db.set_proposal_status(res.proposal_id, "executed" if res.ok else "blocked")
    tg = getattr(app.state, "telegram", None)
    if tg is not None and row["tg_message_id"] is not None:
        mark = "✅ executed" if res.ok else "🚫 blocked"
        try:
            await asyncio.to_thread(
                tg.edit_message, row["tg_message_id"],
                f"{'📥' if row['kind']=='entry' else '📤'} {row['direction']} "
                f"@ {row['price']} — {mark}: {res.detail}")
        except Exception:
            pass
    return {"ok": True}
```

(If the fake in tests is synchronous, `asyncio.to_thread` still works; keep it.)

- [ ] **Step 4: Green** (file + suite).

- [ ] **Step 5: Commit** — `git add service/app/main.py service/tests/test_proposals_flow.py && git commit -m "feat(tg): heartbeat command delivery + /proposal-result"`

---

### Task 6: poller — callbacks + `/mode` `/strategy` `/config`

**Files:**
- Modify: `service/app/main.py` (`telegram_poller`), `service/app/telegram.py` (`handle_command`, new `handle_callback`)
- Test: `service/tests/test_telegram_commands.py` (create)

**Interfaces:**
- Consumes: everything above; existing `handle_command(text, app) -> str | None` (`/status` today).
- Produces: `handle_command` may now return `str | tuple[str, dict] | None` (tuple = text + reply_markup; the poller unpacks); `handle_callback(data: str, app) -> tuple[str | None, str]` returning `(edit_text_or_None, toast)` — poller edits the tapped message when edit_text is not None and always answers the callback with toast.

- [ ] **Step 1: Failing tests**

```python
from app.telegram import handle_command, handle_callback


def test_mode_command_returns_buttons(client):
    out = handle_command("/mode", client.app)
    assert isinstance(out, tuple)
    text, markup = out
    assert "manual" in text.lower()
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert set(datas) == {"mode:auto", "mode:manual"}


def test_mode_callback_switches(client):
    edit, toast = handle_callback("mode:auto", client.app)
    assert client.app.state.db.exec_mode() == "auto"
    assert "auto" in (edit or "").lower() or "auto" in toast.lower()


def test_strategy_command_lists_known_strategies(client):
    _post_signal(client, "NONE")     # seeds signals table w/ strategy ids
    out = handle_command("/strategy", client.app)
    text, markup = out
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert any(d.startswith("strat:") for d in datas)


def test_strategy_callback_sets_pending_switch(client):
    edit, toast = handle_callback("strat:boll_stochrsi_v1", client.app)
    assert client.app.state.pending_switch == "boll_stochrsi_v1"


def test_config_command_reports_mode_and_settings(client):
    out = handle_command("/config", client.app)
    text = out if isinstance(out, str) else out[0]
    assert "mode" in text.lower() and "strategy" in text.lower()


def test_proposal_callback_take_and_skip(client, fake_tg):
    pid = client.app.state.db.create_proposal("entry", "BUY", "s", 1.0, None)
    edit, toast = handle_callback(f"prop:{pid}:take", client.app)
    assert client.app.state.db.get_proposal(pid)["status"] == "approved"
    pid2 = client.app.state.db.create_proposal("entry", "SELL", "s", 1.0, None)
    edit, toast = handle_callback(f"prop:{pid2}:skip", client.app)
    assert client.app.state.db.get_proposal(pid2)["status"] == "skipped"
    # acting on a decided proposal is a no-op with an informative toast
    edit, toast = handle_callback(f"prop:{pid}:take", client.app)
    assert "already" in toast.lower()
```

Check how `app.state.pending_switch` is actually named in `main.py` (`/ui/switch` handler shows it) and use the real attribute in test + implementation.

- [ ] **Step 2: Verify failures.**

- [ ] **Step 3: Implement**

`telegram.py` — extend `handle_command` (keep `/status` untouched):

```python
def handle_command(text: str, app):
    cmd = text.split()[0].lower()
    ...existing /status branch...
    if cmd == "/mode":
        mode = app.state.db.exec_mode()
        return (f"Execution mode: {mode.upper()}\nAUTO executes signals "
                f"immediately; MANUAL sends proposals with buttons.",
                kb([[("🤖 AUTO", "mode:auto"), ("👤 MANUAL", "mode:manual")]]))
    if cmd == "/strategy":
        rows = app.state.db.strategy_ids()   # add tiny db helper: distinct strategy_id from signals
        active = getattr(app.state, "last_active_strategy", "") or ""
        buttons = [[(("● " if s == active else "") + s, f"strat:{s}")] for s in rows]
        return ("Switch active strategy (applies at next bar):",
                kb(buttons) if buttons else None)
    if cmd == "/config":
        db = app.state.db
        from app.config import settings
        hb = getattr(app.state, "last_heartbeat", None) or {}
        return (
            "⚙️ Config\n"
            f"mode: {db.exec_mode()}\n"
            f"strategy: {hb.get('active_strategy', '?')}\n"
            f"forecaster: {settings.forecaster} | horizon: {settings.horizon}\n"
            f"ai mode: {settings.mode} | confirm ≥ {settings.confirm_threshold}\n"
            f"balance: {hb.get('balance', '?')} | equity: {hb.get('equity', '?')}\n"
            f"kill switch: {hb.get('kill_switch', '?')} | "
            f"window open: {hb.get('window_open', '?')}\n"
            f"spread: {hb.get('spread_points', '?')}pt")
    return None
```

(Verify `settings` attribute names in `app/config.py` — `forecaster`, `horizon`, `mode`, `confirm_threshold` — and use the real ones. `last_heartbeat`: `heartbeat()` in main.py already stores the latest heartbeat — find where (`/ui/state` reads it) and reuse that source; add `app.state.last_active_strategy = hb.active_strategy` in `heartbeat()` if no equivalent exists.)

`handle_callback`:

```python
def handle_callback(data: str, app):
    db = app.state.db
    parts = data.split(":")
    if parts[0] == "mode" and parts[1] in ("auto", "manual"):
        db.set_exec_mode(parts[1])
        return (f"Execution mode → {parts[1].upper()}", f"mode: {parts[1]}")
    if parts[0] == "strat":
        sid = parts[1]
        app.state.pending_switch = sid       # real attribute name from /ui/switch
        return (f"Switching to {sid} at next bar.", f"→ {sid}")
    if parts[0] == "prop":
        pid, action = int(parts[1]), parts[2]
        row = db.get_proposal(pid)
        if row is None or row["status"] != "pending":
            return (None, f"already {row['status'] if row else 'gone'}")
        if action == "take":
            db.set_proposal_status(pid, "approved")
            return (f"{row['direction']} @ {row['price']} — 👍 approved, "
                    f"executing on next heartbeat…", "approved")
        db.set_proposal_status(pid, "skipped")
        return (f"{row['direction']} @ {row['price']} — ❌ skipped", "skipped")
    return (None, "unknown")
```

`main.py` poller: in the update loop, add before the message branch:

```python
                cq = upd.get("callback_query")
                if cq is not None:
                    from_id = str((cq.get("from") or {}).get("id"))
                    if from_id == chat_id:
                        edit_text, toast = handle_callback(cq.get("data", ""), app)
                        await asyncio.to_thread(app.state.telegram.answer_callback,
                                                cq.get("id", ""), toast)
                        msg = cq.get("message") or {}
                        if edit_text and msg.get("message_id"):
                            await asyncio.to_thread(app.state.telegram.edit_message,
                                                    msg["message_id"], edit_text)
                    continue
```

And the message branch unpacks tuples from `handle_command`:

```python
                    reply = handle_command(text, app)
                    if isinstance(reply, tuple):
                        await asyncio.to_thread(app.state.telegram.send_message,
                                                reply[0], reply[1])
                    elif reply is not None:
                        await asyncio.to_thread(app.state.telegram.send_message, reply)
```

Add db helper `strategy_ids()`: `SELECT DISTINCT strategy_id FROM signals ORDER BY strategy_id` → `[r[0] for r in ...]`.

- [ ] **Step 4: Green** (new file + whole suite).

- [ ] **Step 5: Commit** — `git add service/app/telegram.py service/app/main.py service/app/db.py service/tests/test_telegram_commands.py && git commit -m "feat(tg): callback handling + /mode /strategy /config commands"`

---

### Task 7: EA — runtime mode + command execution + result reporting

**Files:**
- Modify: `mt5/Include/XauAssistant/UiApi.mqh`, `mt5/Experts/XauAssistant.mq5`

**Interfaces:**
- Consumes: heartbeat JSON `mode` + `command` (Task 5 exact shapes); existing `CTradeManager::OnSignal(ENUM_SIGNAL, double atr, double stopPrice)` and `CloseAll(string reason)`; `ExtractString` helper in UiApi.mqh.
- Produces: `UiApi`: `PostHeartbeat` additionally outputs (by ref params) `string &mode` and command fields (`string &cmd; long &cmdProposalId; string &cmdDirection`); new `void PostProposalResult(long proposal_id, bool ok, string detail)`. EA: global `ENUM_EXEC_MODE g_execMode` replacing reads of the `ExecutionMode` input inside `ProcessBar`.

- [ ] **Step 1: UiApi.mqh changes**

`PostHeartbeat` currently returns the switch string. Refactor: keep its signature returning `string` (switch_to), and add ref-parameter outputs appended to the parameter list:

```mql5
   // outputs: runtime mode ("auto"/"manual"/"" when absent) and at most one
   // command per beat (cmd "" when none)
   string PostHeartbeat(double equity, double balance, double floating_pl,
                        bool kill, double hwm, int exposure, bool window,
                        double spread, string activeId,
                        string &mode, string &cmd, long &cmdId, string &cmdDir)
```

(match the existing parameter list exactly and append the four refs). After the existing `switch_to` extraction add:

```mql5
      mode = ExtractString(body, "mode");
      cmd = ""; cmdId = 0; cmdDir = "";
      int cpos = StringFind(body, "\"command\":{");
      if(cpos >= 0)
        {
         string tail = StringSubstr(body, cpos);
         cmd    = ExtractString(tail, "cmd");
         cmdDir = ExtractString(tail, "direction");
         int idpos = StringFind(tail, "\"proposal_id\":");
         if(idpos >= 0)
            cmdId = StringToInteger(StringSubstr(tail, idpos + 14));
        }
```

New method (mirror the existing POST helper pattern in the class — same WebRequest wrapping as PostTradeEvent):

```mql5
   void PostProposalResult(long proposalId, bool ok, string detail)
     {
      string body = "{\"proposal_id\":" + (string)proposalId +
                    ",\"ok\":" + (ok ? "true" : "false") +
                    ",\"detail\":\"" + detail + "\"}";
      Post("/proposal-result", body);   // reuse/extract the class's POST plumbing
     }
```

(If the class has no generic `Post(path, body)` helper, add one by extracting the WebRequest boilerplate the other POSTs share.)

- [ ] **Step 2: XauAssistant.mq5 changes**

- Global: `ENUM_EXEC_MODE g_execMode;` — set `g_execMode = ExecutionMode;` in `OnInit`.
- `ProcessBar`: change `if(ExecutionMode == EXEC_AUTO && atrVal > 0)` to `if(g_execMode == EXEC_AUTO && atrVal > 0)`.
- Live-guard: `OnInit`'s existing AUTO+live check stays on the INPUT (initial mode); ADD the same guard at runtime: when a heartbeat switches mode to auto on a real account without `AllowLiveTrading`, refuse and keep manual (log + alert).
- `OnTimer`: update the PostHeartbeat call site with the new ref args, then:

```mql5
   string mode = "", cmd = "", cmdDir = "";
   long cmdId = 0;
   string sw = g_ui.PostHeartbeat(equity, balance, floating_pl,
                                  g_risk.KillSwitchTripped(), g_risk.HighWaterMark(),
                                  g_risk.ExposureMinutesUsed(), g_risk.InTradingWindow(),
                                  spreadPts, activeId,
                                  mode, cmd, cmdId, cmdDir);
   if(sw != "") g_pendingSwitch = sw;

   if(mode == "auto" || mode == "manual")
     {
      ENUM_EXEC_MODE want = (mode == "auto") ? EXEC_AUTO : EXEC_MANUAL;
      if(want == EXEC_AUTO && !AllowLiveTrading &&
         AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
        {
         if(g_execMode != EXEC_MANUAL)
            g_alerts.Notify("AUTO refused on live account (AllowLiveTrading=false)");
         g_execMode = EXEC_MANUAL;
        }
      else if(want != g_execMode)
        {
         g_execMode = want;
         Print("XauAssistant: execution mode -> ", mode);
        }
     }

   if(cmd == "execute" && (cmdDir == "BUY" || cmdDir == "SELL"))
     {
      ENUM_SIGNAL dir = (cmdDir == "BUY") ? SIGNAL_BUY : SIGNAL_SELL;
      double atrBuf[];
      double atrVal = (CopyBuffer(g_atrHandle, 0, 1, 1, atrBuf) == 1) ? atrBuf[0] : 0;
      CStrategy *act = g_registry.Active();
      bool opened = false;
      if(atrVal > 0 && act != NULL)
         opened = g_trades.OnSignal(dir, atrVal, act.StopPrice(dir));
      bool ok = opened || g_trades.BasketDirection() == dir;
      g_ui.PostProposalResult(cmdId, ok,
                              ok ? "opened" : "blocked by risk checks");
     }
   else if(cmd == "close_all")
     {
      g_trades.CloseAll("telegram exit");
      g_ui.PostProposalResult(cmdId, true, "basket closed");
     }
```

- [ ] **Step 3: Compile via CLI**

Copy + compile (or `bash scripts/setup.sh`, timeout 200). Parse the log: expect `0 errors, 0 warnings`.

- [ ] **Step 4: Commit** — `git add mt5/Include/XauAssistant/UiApi.mqh mt5/Experts/XauAssistant.mq5 && git commit -m "feat(tg): EA runtime mode + heartbeat command execution + proposal results"`

---

### Task 8: Live verification + push

- [ ] **Step 1: Suite + restart**

`cd service && FORECASTER=fake .venv/bin/pytest -q` → green. Restart the service, wait `/health`.

- [ ] **Step 2: Live poke**

- `curl -s http://127.0.0.1:9000/ui/state | head -c 200` → heartbeat flowing (EA reconnects automatically).
- Send `/mode` **from the service side**: verify command handling wiring by calling the poller path indirectly — practical check: `curl -s -X POST http://127.0.0.1:9000/heartbeat -H 'Content-Type: application/json' -d '{"equity":1,"balance":1,"floating_pl":0}' | python3 -m json.tool` → response contains `"mode"` and `"command": null`.
- Report that button/command interaction testing needs the user tapping in Telegram (list the exact things to try: `/mode`, `/strategy`, `/config`, and a proposal when the next MANUAL entry signal fires).

- [ ] **Step 3: Push**

```bash
git push
```
