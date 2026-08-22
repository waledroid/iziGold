# Launcher revamp — report

Branch: `refactor/halftrend-lane`. Scope: ops-only (no EA input defaults or
strategy parameters touched).

## What changed

1. **De-duplicated the launcher.** `XAU-Launch.bat` (repo root) and
   `scripts/xau-launch.bat` were byte-identical (verified with `diff`). Kept
   `scripts/xau-launch.bat` as the real implementation (it's what README.md
   and `.claude/agents/izi.md` already tell people to copy to their Desktop,
   and it does its own repo-location detection so a Desktop copy still finds
   the repo). `XAU-Launch.bat` is now a 7-line forwarder:
   `call "%~dp0scripts\xau-launch.bat" %*`.

2. **Durable watchdog lock** (`scripts/xau-watchdog.sh`). The singleton lock
   moved from `/tmp/xau-watchdog.lock` to `.run/xau-watchdog.lock` inside the
   repo (new `.gitignore` entry: `.run/`). A `/tmp` sweep recreates
   `/tmp/*` with fresh inodes, which is exactly how two supervisors ended up
   running before; a repo-local, gitignored path is never touched by a `/tmp`
   cleanup. Behaviour otherwise identical: `flock -n`, second instance logs
   `"another watchdog holds ... -- exiting"` and exits 0.

3. **Mini-app port (9001 → 9101).** Checked: already fully done in a prior
   commit — `scripts/setup.sh` (`MINIAPP_PORT_DEFAULT=9101`), `.env` /
   `.env.example`, `scripts/xau-watchdog.sh`, and `bridge/mt5_feed.py` all
   read `MINIAPP_PORT` from `.env` with a 9101 fallback. Nothing in
   `XAU-Launch.bat` / `scripts/xau-launch.bat` ever hard-coded a port. No
   change needed; verified by grep across all four files.

4. **Two registered strategies.** Checked: neither launcher file nor
   `setup.sh` printed any singular "the strategy" wording to begin with
   (grepped both before and after editing). No fix was needed; kept it that
   way in the new preflight/service-phase text I added (talks about "the
   registered strategies" / doesn't imply one).

5. **`config/strategy.json` preflight check.** Added to Phase 1 (Preflight)
   in `scripts/setup.sh`: fails immediately, plainly, with a fix command
   (`git checkout -- config/strategy.json`) if the file is missing — instead
   of letting it surface three phases later as an opaque pytest collection
   error (backtest.py raises at import time when it's absent, and
   `test_strategy_config.py` imports backtest.py).

6. **Stale-service restart, one implementation.** Factored the "process
   predates its code on disk" check out of `scripts/xau-watchdog.sh` into a
   new shared file, `scripts/lib/stale-code.sh` (`proc_started`,
   `newest_mtime`, `stale_code`), sourced by both `scripts/xau-watchdog.sh`
   and `scripts/setup.sh`. `setup.sh`'s Service phase (Phase 4) now checks
   `stale_code` before treating an already-running service as SKIP-and-done:
   if the running process predates its code, it's killed and restarted
   before the smoke test runs, using the exact watchdog logic (no second
   implementation to drift). The watchdog keeps its own `acted_mtime`
   restart-once-per-code-change memoization (renamed the wrapper
   `stale_code_once`, since that memoization is watchdog-specific — a
   one-shot `setup.sh` run doesn't need it).

## Verification

### Test suite
Baseline 530. Ran clean (isolated, no other setup.sh run in flight):
```
530 passed, 1 deselected, 3 warnings in 85.70s (0:01:25)
```
One run during the launcher testing hit the documented
`test_pop_approved_command_concurrent_exactly_once` timing flake (`1 failed,
529 passed` inside a live `setup.sh` Test-gate phase, heavier machine load
from concurrent runs); re-run in isolation passed in 1.95s, confirming it was
the known flake, not a regression.

### Full launcher run 1 (`bash scripts/setup.sh`, first pass after edits)
Exit code 0. Every phase:
```
[1/11] Preflight               OK   MT5 data folder / MetaEditor / config/strategy.json present
[2/11] Python environment       SKIP .venv exists | OK core requirements installed | SKIP .env exists
[3/11] Test gate                OK   530 passed, 1 deselected, 3 warnings in 86.86s
[4/11] Service                  SKIP already running at http://127.0.0.1:9000 (code up to date)
                                 OK   service healthy + /analyze smoke passed
[5/11] Mini-app feed service    SKIP MINIAPP_PORT already set | SKIP FEED_KEY already set | SKIP already running at :9101
[6/11] Live chart config        SKIP NGROK_AUTHTOKEN / MINIAPP_PUBLIC_URL / MINIAPP_DIRECT_LINK already set
[7/11] ngrok tunnel             SKIP binary installed | SKIP authtoken configured | SKIP tunnel already running
[8/11] Watchdog                 SKIP watchdog already running
[9/11] Telegram                 SKIP credentials already in service profile
[10/11] MT5 install + compile   OK   compiled: Result: 0 errors, 0 warnings, 3616 ms elapsed
[11/11] Handoff + verify        OK   EA heartbeat received — end-to-end wiring confirmed
Summary: OK OK OK OK / SKIP SKIP SKIP SKIP SKIP / OK OK — all green, no FAILED.
```

### Full launcher run 2 (idempotency — immediately after run 1, nothing changed)
Exit code 0. Identical SKIP pattern to run 1 for every phase whose state
didn't change (Service, Mini-app, Live chart config, ngrok, Watchdog,
Telegram all SKIP). Preflight/Test-gate/MT5-compile/Handoff run their checks
every time by design (pre-existing behaviour, not something this revamp
changed) and reported OK again — 530 passed, compiled 0 errors, heartbeat
received.

### Stale-service restart proof
```
main pid BEFORE = 851095, started 2026-08-22 19:55:02
touch service/app/main.py  → mtime 2026-08-22 20:00:48   (predates nothing; process is now older than the file)
```
Ran `scripts/setup.sh` (watchdog stopped first, so the restart credit is
unambiguously the Service phase's own — the watchdog has the identical guard
and would otherwise also catch it within its 30s cycle, which is fine in
production but muddies an isolated test). Phase 4 output:
```
[4/11] Service
  running service predates its code on disk — restarting
  OK restarted (was running stale code; logs: service/service.log)
  smoke POST /analyze ...
  OK service healthy + /analyze smoke passed
```
New pid 853827, started 2026-08-22 20:02:43 — after the 20:00:48 touch.
Re-ran `scripts/setup.sh` immediately after (no further code changes):
```
[4/11] Service
  SKIP already running at http://127.0.0.1:9000 (code up to date)
```
No restart line, no `pkill` — confirms the guard is exactly a one-shot
correction, not a restart-every-run regression. (`grep -c "restarted (was
running stale code" run4.log` → `0`.)

### Durable watchdog lock proof
Started three watchdog instances back-to-back (`nohup bash
scripts/xau-watchdog.sh &` × 3): exactly one (pid 849903 in that run)
stayed alive; the other two exited immediately. Log:
```
19:52:00 another watchdog holds .../.run/xau-watchdog.lock -- exiting
19:52:00 another watchdog holds .../.run/xau-watchdog.lock -- exiting
19:52:00 watchdog start (interval 30s, miniapp port=9101, tunnel=...)
```
`.run/xau-watchdog.lock` confirmed created inside the repo (not `/tmp`).
Then simulated the exact failure mode the fix targets: deleted
`/tmp/xau-watchdog.lock` (the now-unused old path) while the watchdog was
running, to model a `/tmp` sweep. The durable lock was untouched (`fuser
.run/xau-watchdog.lock` still showed the running watchdog's pid), and a
fourth start attempt still correctly deferred and exited — proving a `/tmp`
sweep can no longer produce a second live supervisor.

**Caveat found during testing, not a regression:** while transitioning from
the pre-existing long-running watchdog (started before this session, holding
the *old* `/tmp` lock in memory) to the new code, a window exists where an
old-code instance and a new-code instance can both be "running" briefly,
because they lock different files — this is an inherent one-time transition
hazard of relocating a lock path under a live process, not a flaw in the
flock logic itself (flock never let two processes hold the *same* lock
simultaneously in any test). I resolved it operationally by killing the
stale pre-edit watchdog process once and letting exactly one new-code
instance take over — the normal "restart to pick up a code change" step,
identical in spirit to the stale-code guard this task added for the main
service. Confirmed clean afterward (single real watchdog process, single
lock holder).

### Final system state
```
main service   : {"status":"ok","forecaster":"ChronosBoltForecaster","db":"xau_assistant.db"}
mini-app       : {"ok":true,"feed_age_s":0.5,"uptime_s":24949.9}
EA heartbeat   : age_s=2.8, active_strategy=halftrend_ema_v1, balance=4762.18
watchdog       : exactly 1 process (pid 854065), sole holder of .run/xau-watchdog.lock
```
`feed_age_s` near-zero confirms the Windows bridge is alive and pushing
(there is no other way that field stays fresh).

## Left alone deliberately

- **Phase numbering/skip semantics, soft-fail design, MT5-compile-every-run,
  pytest-every-run** — pre-existing, working-as-designed behaviour per the
  spec; not touched.
- **README.md** still says the shadow strategy is `boll_stochrsi_v1`; the
  live system actually shadows `halftrend_m15_v1` per this task's brief and
  `config/strategy.json`. Out of scope for a launcher revamp (README is
  documentation, not something the launcher reads or prints), left alone —
  flagging so it doesn't get missed in a future doc pass.
- **`docs/superpowers/specs/2026-08-01-setup-script-design.md`** doesn't
  mention the watchdog lock path or the mini-app port at all — nothing to
  reconcile there.
- Did not touch `bars_max.json`, did not regenerate any golden file, per
  instructions.
