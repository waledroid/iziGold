# Client Onboarding Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nonblocking onboarding page (`/ui/onboarding`) storing a client-shaped profile (identity, Telegram, risk prefs, account+consent), with live Telegram apply, redirect-once, and a dashboard header tie-in. Spec: `docs/superpowers/specs/2026-07-31-onboarding-design.md`.

**Architecture:** One `profile` row (id=1 upsert) in the existing SQLite db; three endpoints on the existing FastAPI app; one static HTML form matching the dashboard's style; a `_apply_telegram` helper that becomes the single owner of Telegram client/task lifecycle (lifespan startup reuses it, so profile credentials override `.env`).

**Tech Stack:** Python/FastAPI/sqlite3/pytest; vanilla HTML/JS.

## Global Constraints

- Tests from `service/`: `.venv/bin/python -m pytest`; suite green after every task.
- Nonblocking: every profile field nullable; Save and Skip both create the row; `/ui` redirects (307) to `/ui/onboarding` ONLY when no profile row exists.
- Fail-open: profile/Telegram-apply failures never affect `/analyze`, `/heartbeat`, `/trade-event`, or trading. Page never touches trading controls.
- Profile Telegram credentials override `.env`; empty strings clear back to the `.env` fallback. No writing to `.env`.
- Profile field list (exact column names): name, email, phone, telegram_bot_token, telegram_chat_id, risk_per_trade_pct, max_drawdown_pct, profit_target_pct, window_start_hour, window_end_hour, broker_name, account_login, account_type, experience_level, risk_ack (+ created_ts, updated_ts, risk_ack_ts). Completion % = set fields / 15, computed not stored.
- Commits: repo style + trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Profile storage

**Files:**
- Modify: `service/app/db.py`
- Test: `service/tests/test_profile.py` (new)

**Interfaces:**
- Produces: `SignalDb.get_profile() -> dict | None` (None when the row has never been created); `SignalDb.save_profile(partial: dict) -> dict` (creates row id=1 if missing, updates only provided keys, ignores unknown keys, refreshes updated_ts, sets risk_ack_ts once when risk_ack first becomes truthy, returns the full row as a dict); `profile_completion(profile: dict | None) -> int` (module-level function in db.py: percent of the 15 §2 fields that are non-None and non-empty-string; None → 0).

- [ ] **Step 1: Write the failing tests** — create `service/tests/test_profile.py`:

```python
from app.db import SignalDb, profile_completion


def test_profile_absent_then_created(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert db.get_profile() is None
    row = db.save_profile({})               # Skip: creates empty row
    assert row["id"] == 1 and db.get_profile() is not None


def test_partial_update_only_touches_sent_fields(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    db.save_profile({"name": "Wale", "email": "w@x.com"})
    row = db.save_profile({"phone": "+33 6 00"})
    assert row["name"] == "Wale" and row["email"] == "w@x.com"
    assert row["phone"] == "+33 6 00"
    assert db.save_profile({"bogus_key": 1})["name"] == "Wale"  # unknown ignored


def test_risk_ack_ts_set_once(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert db.save_profile({})["risk_ack_ts"] is None
    first = db.save_profile({"risk_ack": 1})["risk_ack_ts"]
    assert first is not None
    assert db.save_profile({"risk_ack": 1})["risk_ack_ts"] == first


def test_completion_percent(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert profile_completion(None) == 0
    assert profile_completion(db.save_profile({})) == 0
    row = db.save_profile({"name": "W", "email": "e", "phone": "p"})
    assert profile_completion(row) == 20            # 3 of 15
    assert profile_completion(db.save_profile({"name": ""})) == 13  # empty string unsets → 2/15
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_profile.py -v` → FAIL (no table/methods).

- [ ] **Step 3: Implement** in `service/app/db.py`. Schema (executed with the others in `__init__`):

```python
_PROFILE_SCHEMA = """CREATE TABLE IF NOT EXISTS profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT, email TEXT, phone TEXT,
  telegram_bot_token TEXT, telegram_chat_id TEXT,
  risk_per_trade_pct REAL, max_drawdown_pct REAL, profit_target_pct REAL,
  window_start_hour INTEGER, window_end_hour INTEGER,
  broker_name TEXT, account_login TEXT, account_type TEXT,
  experience_level TEXT, risk_ack INTEGER,
  created_ts INTEGER, updated_ts INTEGER, risk_ack_ts INTEGER
)"""

PROFILE_FIELDS = ["name", "email", "phone", "telegram_bot_token",
                  "telegram_chat_id", "risk_per_trade_pct", "max_drawdown_pct",
                  "profit_target_pct", "window_start_hour", "window_end_hour",
                  "broker_name", "account_login", "account_type",
                  "experience_level", "risk_ack"]


def profile_completion(profile) -> int:
    if not profile:
        return 0
    filled = sum(1 for f in PROFILE_FIELDS
                 if profile.get(f) not in (None, ""))
    return round(100 * filled / len(PROFILE_FIELDS))
```

Methods on `SignalDb`:

```python
    def get_profile(self):
        row = self.conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM profile WHERE id = 1").description]
        return dict(zip(cols, row))

    def save_profile(self, partial: dict) -> dict:
        now = int(time.time())
        if self.get_profile() is None:
            self.conn.execute(
                "INSERT INTO profile (id, created_ts, updated_ts) VALUES (1, ?, ?)",
                (now, now))
        updates = {k: v for k, v in partial.items() if k in PROFILE_FIELDS}
        if updates.get("risk_ack") and not (self.get_profile() or {}).get("risk_ack_ts"):
            updates["risk_ack_ts"] = now
        updates["updated_ts"] = now
        sets = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(f"UPDATE profile SET {sets} WHERE id = 1",
                          tuple(updates.values()))
        self.conn.commit()
        return self.get_profile()
```

(`risk_ack_ts` is intentionally allowed into the UPDATE via `updates` even though it is not in PROFILE_FIELDS — it is added internally only.)

- [ ] **Step 4: Verify** — profile tests PASS, then full suite PASS.
- [ ] **Step 5: Commit** — `feat(service): client profile storage with partial updates`.

---

### Task 2: Profile endpoints, redirect-once, live Telegram apply

**Files:**
- Modify: `service/app/main.py`
- Test: `service/tests/test_profile.py` (append)

**Interfaces:**
- Consumes: Task 1 (`get_profile`, `save_profile`, `profile_completion`); existing `TelegramClient`, `telegram_poller`, `pinned_editor`, lifespan task guards.
- Produces:
  - `_effective_telegram(app) -> tuple[str, str]` — profile credentials when non-empty, else `settings` values, both stripped.
  - `async def _apply_telegram(app)` — cancels `telegram_task`/`pinned_task` (wait_for 3.0s, swallow Timeout/Cancelled), then from `_effective_telegram`: sets `app.state.telegram` (client or None) and recreates both tasks (or None). Never raises.
  - Lifespan startup replaced to call `await _apply_telegram(app)` instead of its inline telegram setup (profile now overrides `.env` at startup); shutdown unchanged.
  - `GET /ui/profile` → `{"profile": dict | None, "completion_pct": int}`.
  - `POST /ui/profile` (json dict) → same shape after save; when the body contains `telegram_bot_token` or `telegram_chat_id`, `await _apply_telegram(app)` after saving (wrapped, fail-open).
  - `GET /ui` → 307 `RedirectResponse("/ui/onboarding")` when `get_profile()` is None, else the dashboard file as today.

- [ ] **Step 1: Failing tests** — append to `service/tests/test_profile.py` (reuse the TestClient fixture pattern from test_heartbeat.py verbatim, db path `ob.db`):

```python
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ob.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_ui_redirects_once(client):
    r = client.get("/ui", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/onboarding"
    client.post("/ui/profile", json={})          # Skip creates the row
    assert client.get("/ui", follow_redirects=False).status_code == 200


def test_profile_roundtrip_and_completion(client):
    assert client.get("/ui/profile").json() == {"profile": None, "completion_pct": 0}
    body = client.post("/ui/profile", json={"name": "Wale", "risk_ack": 1}).json()
    assert body["profile"]["name"] == "Wale"
    assert body["completion_pct"] == 13          # 2 of 15


def test_telegram_live_apply(client):
    from app import main
    assert main.app.state.telegram is None       # test env has no credentials
    client.post("/ui/profile", json={"telegram_bot_token": "T", "telegram_chat_id": "C"})
    assert main.app.state.telegram is not None
    assert main.app.state.telegram_task is not None
    client.post("/ui/profile", json={"telegram_bot_token": "", "telegram_chat_id": ""})
    assert main.app.state.telegram is None       # cleared back to .env fallback (empty)
```

- [ ] **Step 2: Verify failure. Step 3: Implement** per the Interfaces block. The `POST /ui/profile` handler:

```python
@app.post("/ui/profile")
async def ui_profile_save(body: dict):
    row = app.state.db.save_profile(body if isinstance(body, dict) else {})
    if "telegram_bot_token" in body or "telegram_chat_id" in body:
        try:
            await _apply_telegram(app)
        except Exception:
            pass
    return {"profile": row, "completion_pct": profile_completion(row)}
```

- [ ] **Step 4: Verify** new + full suite PASS (existing telegram lifecycle tests must still pass — `_apply_telegram` at startup must preserve the "no tasks when unconfigured" behavior they assert).
- [ ] **Step 5: Commit** — `feat(service): profile endpoints, redirect-once, live telegram apply`.

---

### Task 3: Onboarding page + dashboard tie-in + docs

**Files:**
- Create: `service/app/static/onboarding.html`
- Modify: `service/app/main.py` (serve it), `service/app/static/dashboard.html`, `README.md`
- Test: `service/tests/test_profile.py` (append)

**Interfaces:**
- Consumes: `/ui/profile` GET/POST (Task 2).
- Produces: `GET /ui/onboarding` (FileResponse, like `/ui`); dashboard header showing name + completion badge linking to `/ui/onboarding`, and a window-mismatch hint (declared window vs heartbeat `window_open` is not directly comparable — show declared window hours next to the live window state; that is the §5 "values present in the heartbeat" scope).

- [ ] **Step 1: Failing test** — append:

```python
def test_onboarding_page_served(client):
    r = client.get("/ui/onboarding")
    assert r.status_code == 200
    for needle in ("Identity", "Telegram", "Risk profile", "Account", "/ui/profile"):
        assert needle in r.text
```

- [ ] **Step 2: Verify failure. Step 3: Implement.** Route mirrors `/ui`. Page content — create `service/app/static/onboarding.html` exactly:

```html
<!doctype html>
<meta charset="utf-8">
<title>iziGold — setup</title>
<style>
 body{font:14px system-ui;margin:0;background:#111;color:#ddd}
 .wrap{max-width:640px;margin:0 auto;padding:24px}
 fieldset{border:1px solid #333;border-radius:8px;margin:0 0 16px;padding:12px 16px}
 legend{padding:0 6px;color:#f5c542}
 label{display:block;margin:8px 0 2px;font-size:13px;color:#aaa}
 input,select{width:100%;box-sizing:border-box;background:#1a1a1a;color:#ddd;border:1px solid #333;border-radius:4px;padding:7px}
 .row{display:flex;gap:12px}.row>div{flex:1}
 .ack{display:flex;gap:8px;align-items:flex-start;margin-top:10px}
 .ack input{width:auto;margin-top:3px}
 .btns{display:flex;gap:12px;margin-top:8px}
 button{border:0;border-radius:6px;padding:10px 22px;cursor:pointer;font-size:14px}
 .save{background:#2a4d69;color:#fff}.skip{background:#222;color:#999}
 .note{font-size:12px;color:#777;margin:4px 0 16px}
</style>
<div class="wrap">
 <h1 style="font-size:20px">🥇 Welcome to iziGold</h1>
 <p class="note">Everything below is optional — save what you have, skip the rest,
 and finish anytime from the dashboard's profile badge. Nothing here blocks or
 alters trading.</p>
 <form id="f">
  <fieldset><legend>Identity &amp; contact</legend>
   <label>Name</label><input name="name">
   <div class="row"><div><label>Email</label><input name="email" type="email"></div>
   <div><label>Phone</label><input name="phone"></div></div>
  </fieldset>
  <fieldset><legend>Telegram (applied live)</legend>
   <label>Bot token</label><input name="telegram_bot_token">
   <label>Chat ID</label><input name="telegram_chat_id">
   <p class="note">From @BotFather; alerts, /status, /switch and the pinned live
   message activate as soon as you save.</p>
  </fieldset>
  <fieldset><legend>Risk profile (your declared preferences — the EA enforces its own inputs)</legend>
   <div class="row">
    <div><label>Risk % per trade</label><input name="risk_per_trade_pct" type="number" step="0.1"></div>
    <div><label>Max drawdown %</label><input name="max_drawdown_pct" type="number" step="0.5"></div>
    <div><label>Profit target %</label><input name="profit_target_pct" type="number" step="0.5"></div>
   </div>
   <div class="row">
    <div><label>Window start (h)</label><input name="window_start_hour" type="number" min="0" max="23"></div>
    <div><label>Window end (h)</label><input name="window_end_hour" type="number" min="0" max="23"></div>
   </div>
  </fieldset>
  <fieldset><legend>Account &amp; consent</legend>
   <div class="row">
    <div><label>Broker</label><input name="broker_name"></div>
    <div><label>Account login</label><input name="account_login"></div>
   </div>
   <div class="row">
    <div><label>Account type</label><select name="account_type">
     <option value="">—</option><option>demo</option><option>live</option></select></div>
    <div><label>Experience</label><select name="experience_level">
     <option value="">—</option><option>beginner</option><option>intermediate</option><option>advanced</option></select></div>
   </div>
   <div class="ack"><input type="checkbox" name="risk_ack" id="ack">
    <label for="ack" style="margin:0">I understand automated trading can lose money,
    past results don't guarantee future ones, and I remain responsible for this
    account.</label></div>
  </fieldset>
  <div class="btns">
   <button type="submit" class="save">Save</button>
   <button type="button" class="skip" id="skip">Skip for now</button>
  </div>
 </form>
</div>
<script>
const f=document.getElementById('f');
fetch('/ui/profile').then(r=>r.json()).then(({profile})=>{
 if(!profile)return;
 for(const el of f.elements){ if(!el.name) continue;
  if(el.type==='checkbox') el.checked=!!profile[el.name];
  else if(profile[el.name]!=null) el.value=profile[el.name]; }
});
f.onsubmit=async e=>{e.preventDefault();
 const body={};
 for(const el of f.elements){ if(!el.name) continue;
  if(el.type==='checkbox') body[el.name]=el.checked?1:0;
  else if(el.value!=='') body[el.name]=el.type==='number'?Number(el.value):el.value; }
 await fetch('/ui/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 location.href='/ui';};
document.getElementById('skip').onclick=async()=>{
 await fetch('/ui/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
 location.href='/ui';};
</script>
```

- [ ] **Step 4: Dashboard tie-in** — in `dashboard.html`, next to the `<h1>` title add `<span id="prof" style="font-size:13px;color:#888"></span>`; in `state()` after rendering the strip append:

```js
 fetch('/ui/profile').then(r=>r.json()).then(({profile,completion_pct})=>{
  const who=profile&&profile.name?profile.name+' · ':'';
  const win=profile&&profile.window_start_hour!=null?` · declared window ${profile.window_start_hour}–${profile.window_end_hour}h`:'';
  $('prof').innerHTML=`${who}<a href="/ui/onboarding" style="color:#f5c542">profile ${completion_pct}%</a>${win}`;
 });
```

(Call it from `state()` so it refreshes with the 5s poll; the declared window renders alongside the live strip's actual window state — that is the mismatch visibility in scope.)

- [ ] **Step 5: README** — in the Dashboard section add: first visit redirects to `/ui/onboarding` (nonblocking; Skip works); Telegram credentials entered there apply live and override `.env`.
- [ ] **Step 6: Full suite green. Step 7: Commit** — `feat(service): onboarding page with dashboard profile badge`.
