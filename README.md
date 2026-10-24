# XAU Assistant

MT5 trading assistant for XAUUSD (chart timeframe, M5 default). **Your strategy
is the sole decision maker**; an AI forecaster (Chronos-Bolt) grades every
signal, everything is logged to SQLite, and Telegram is the remote control.
Manual mode proposes trades with approve/skip buttons; auto mode executes and
reports. Strict money management throughout (fixed-fractional risk, pyramiding
into winners only, breakeven ratchets, proportional profit lock, drawdown kill
switch).

Design docs: [docs/superpowers/specs/](docs/superpowers/specs/)

## Quick start

**Single launcher (Windows or Linux):** run `py -3 launch.py` on Windows or
`python3 launch.py` on Linux. It runs staged prerequisite checks with a progress
bar, prepares `service/.env`, starts Docker Compose, detects native Windows MT5
or MT5 in the Linux Wine prefix (`~/.mt5` by default), copies and compiles the
EA when the MT5 data folder is available, starts the Windows feed bridge when
its Python package is installed, and waits for service/EA health. Use
`--dry-run` to inspect actions, `--no-install` to forbid package installation,
`--skip-mt5` to start only Docker services, `--restart-mt5` to restart an
already-running terminal and apply the EA startup profile, or `--install-model`
to explicitly install the large Torch/Chronos dependencies.

**Windows shortcut:** `XAU-Launch.bat` and `scripts/xau-launch.bat` are
compatibility wrappers around `launch.py`; they no longer contain a separate
WSL startup path.

**Installer (WSL/Linux):**

```bash
bash scripts/setup.sh
```

This legacy path remains useful for the full WSL setup, but the cross-platform
launcher and Compose stack are the preferred path.

**Docker services (recommended cross-platform layout):** MT5 stays on the host,
either native on Windows or under Wine on Linux. Docker runs only the main
service and read-only mini-app feed service:

```bash
cp service/.env.example service/.env
# Set FORECASTER=fake for a lightweight local image, or configure Telegram.
docker compose up -d --build
docker compose ps
```

The stack publishes only `127.0.0.1:9000` and `127.0.0.1:9101`, so the EA can
use its existing localhost WebRequest URL on either host OS. Stop it with
`docker compose down`; persistent SQLite data and rendered charts stay in the
`xau-data` Docker volume. To build real Chronos support, use
`INSTALL_MODEL=true docker compose build` before starting the stack.

The `ngrok` service is an opt-in `tunnel` profile. The shared launcher starts
it only when both `ngrok_authtoken` and `ngrok_domain` exist in the onboarding
profile; otherwise it prints a warning and leaves the local stack running.
After those values are saved, use `docker compose --profile tunnel up -d` for
manual tunnel startup.

The MT5 feed bridge remains outside Docker because it communicates with the
running terminal through the MetaTrader5 Python package. On Windows the
launcher installs the package into host Python when needed; on Linux/Wine it
installs Windows Python and the package inside the dedicated `~/.mt5` prefix
when needed, then starts `bridge/mt5_feed.py`. The bridge is read-only and
feeds the mini-app; trading and EA-to-service communication do not depend on
it.

The EA heartbeat runs every second for live status and control responsiveness;
the service keeps the newest heartbeat in memory and down-samples database
persistence so SQLite is not written every second. Set `HEARTBEAT_SEC` in
`service/.env` to change it: the service is authoritative and sends the interval
on every heartbeat, so the EA re-arms its timer without a recompile (MT5 keeps a
chart EA's saved inputs across recompiles, so an MQL `input` default cannot
change a running EA). `/api/state` shows the EA's reported interval next to the
desired one.

**DeepSeek supervisory agent (optional).** With `AGENT_ENABLED=false` (the
default) the system behaves exactly as it did before the agent existed. To
enable it, set `FORECASTER=rules` plus `AGENT_ENABLED=true`, `AGENT_MODEL`,
`AGENT_BASE_URL`, and `AGENT_API_KEY` in `service/.env` (opencode-go:
`https://opencode.ai/zen/go/v1` + `deepseek-v4.1-flash` — the gateway also
needs an `x-opencode-session` header, which the client sends automatically).
Grading runs asynchronously; `/analyze` never waits on the model, and any
failure leaves the strategy trading untouched. The agent may pause new entries
(`POST /api/rules {"key":"entries_paused","value":"on"}`), close, or tighten
stops, always within rate bounds and only on a demo account unless
`AGENT_ALLOW_LIVE_ACTIONS=true`.

The agent learns from its own outcomes: directional grades are scored against
the resolved move, mistakes are turned into one-line lessons that are fed back
into future grades, and `GET /api/agent/scorecard` breaks accuracy down by
window state, regime, session and entry-quality band. Before changing the
prompt, model or thresholds, grade history offline with:

```bash
docker compose exec main python /workspace/scripts/agent_replay.py \
  --limit 200 --step 60 --out /data/replay.json
```

Live actions additionally require `AGENT_MIN_RESOLVED` resolved grades at
`AGENT_MIN_ACCURACY_PCT` accuracy (or `AGENT_GATE_BYPASS=true`), so an unproven
agent can never manage a real account.

On Linux/Wine, a clipped or incorrectly scaled MT5 window is a Wine display
configuration issue, not a service issue. Adjust the dedicated prefix with
`WINEPREFIX=~/.mt5 winecfg` (Graphics tab, DPI or virtual desktop), then restart
MT5. The launcher does not force a virtual desktop because the correct setting
depends on the host display.

**WebRequest allowlist (set once, then stop MT5 cleanly).** MT5 keeps the
approved URL list in `Config/settings.ini`, stored obfuscated and written only
on a clean exit; `scripts/mt5-start.ini` cannot set it. So: enable
`Tools > Options > Expert Advisors -> Allow WebRequest for listed URL ->
http://127.0.0.1:9000` once in the GUI, then always stop the terminal with
`python3 launch.py --stop` (or `scripts/stop-mt5.sh`) so the setting is flushed.
Snapshot it so a force-kill cannot cost you the setup:

```bash
scripts/mt5-settings.sh backup    # after a clean stop
scripts/mt5-settings.sh restore   # if err=4014 comes back
scripts/mt5-settings.sh status    # live vs saved, and whether MT5 runs
```

**The allowlist is no longer required under Wine.** MT5 does not persist it
across restarts there (tested), so the EA also speaks a file-relay transport:
`Transport.mqh` writes each request into `MQL5/Files/` and `scripts/mt5_relay.py`
— started automatically by `launch.py` as a host process — forwards it to the
service and writes the reply back. No allowlist, no GUI, nothing to re-set. On
native Windows the normal HTTP path is used and nothing changes.

## New PC Checklist

1. Install Git and clone the repository:

   ```bash
   git clone https://github.com/waledroid/iziGold.git
   cd iziGold
   ```

2. Install the host terminal:

   - **Windows:** install Docker Desktop, Python 3, and MetaTrader 5. Log in to
     the broker demo account and enable `XAUUSD` in Market Watch.
   - **Linux:** install Docker, Python 3, Wine, and MetaTrader 5 in the
     dedicated `~/.mt5` Wine prefix. Log in to the broker demo account and
     enable `XAUUSD` in Market Watch.

3. Run the shared launcher:

   ```bash
   # Windows
   py -3 launch.py

   # Linux
   python3 launch.py
   ```

   It creates `service/.env`, generates the feed key, starts Docker, detects
   MT5, copies and compiles the EA, and starts the Windows feed bridge when its
   Python package is available. Use `--no-install` only after prerequisites are
   already installed.

4. Configure the service after it starts:

   - Open `http://127.0.0.1:9000/onboarding` to configure the Telegram bot and
     mini-app settings, or edit `service/.env` directly.
   - AUTO trading is the default. Set `FORECASTER=fake` for a lightweight smoke test, or run
     `python3 launch.py --install-model` / `py -3 launch.py --install-model` for
     real Chronos dependencies.

5. In MT5, perform the one-time security approval:

   - `Tools > Options > Expert Advisors`
   - Enable `Allow WebRequest for listed URL`
   - Add `http://127.0.0.1:9000`
   - Enable the green `Algo Trading` button

6. If MT5 was already running before the launcher copied the EA, restart it
   through the launcher so `scripts/mt5-start.ini` opens XAUUSD M5 and attaches
   the EA:

   ```bash
   python3 launch.py --restart-mt5
   ```

   Use `py -3 launch.py --restart-mt5` on Windows. Do not restart MT5 while
   positions are open.

7. Verify the installation:

   ```bash
   docker compose ps
   curl http://127.0.0.1:9000/health
   curl http://127.0.0.1:9101/healthz
   ```

   Telegram `/status` should eventually show the EA connected. Weekend market
   closure can delay symbol synchronization and live feed data; validate the
   first heartbeat when the broker sends XAUUSD quotes.

   On Saturday/Sunday, use the launcher to verify services and the bridge, but
   perform the final EA/trading verification during the next broker session.

Back up the live SQLite volume without stopping the services:

```bash
bash scripts/backup-data.sh
```

Verify: `/status` in Telegram → `EA: 🟢 connected`.

## What you get

- **Strategies** — modular behind `CStrategy`
  (`mt5/Include/XauAssistant/Strategies/`). Active: `izi5`
  (HalfTrend + EMA-55 dual confirmation, ATR-padded wick stop). Shadow:
  `boll_stochrsi_v1`. All strategies are evaluated and logged every bar;
  only `ActiveStrategy` trades. Per-strategy hit-rates in SQLite.
- **Telegram control** — quiet by default. Entry/exit proposals with
  🟢 Take / 🔴 Skip buttons (valid while the strategy holds its stance),
  execution photos, failure notices with the exact risk-gate reason.
  Commands: `/status` (incl. EA connection + market session), `/mode`
  (AUTO/MANUAL), `/strategy`, `/config`, `/stats`, `/history`. A pinned
  message lists all commands.
- **Dashboard** (`http://127.0.0.1:9000/ui`) — live XAUUSD candlestick chart
  with risk/reward trade boxes (red entry↔SL, green entry↔exit), strategy
  comparison, signal log with AI grades and resolved outcomes, trade history
  with chart thumbnails. First visit redirects to onboarding.
- **MT5 chart** — dark theme, the strategy's own HalfTrend/EMA lines painted
  live, trade boxes drawn per basket.
- **Rendered trade charts** (Telegram + dashboard) — candles with HalfTrend,
  EMA 9/21/55/200 overlays, entry price and SL labels.
- **Exits** — dual-confirmation trend reversal, 50%-of-peak profit lock,
  +2% basket target, ATR-padded stop. No martingale, ever.

## Key EA inputs

| Input | Default | Meaning |
|---|---|---|
| `ExecutionMode` | `EXEC_AUTO` | runtime-switchable via Telegram `/mode` |
| `AllowLiveTrading` | `true` | AUTO is enabled by default; review the live-account warning during onboarding |
| `RiskPerTradePct` / `MaxDrawdownPct` | `1.0` / `10` | risk per trade / kill switch |
| `ProfitTargetPct` | `2.0` | bank the basket at +2% of cycle balance (0 = off) |
| `TrailLockPct` / `TrailActivateR` | `50` / `1.0` | keep 50% of peak profit once ≥1R |
| `StopBufferATR` | `0.75` | pad the wick stop by k×ATR (0 = exact wick) |
| `TradingWindowStartHour/EndHour` | `4` / `22` | server-time entry window (pre-rollover hour cut) |
| `MaxDailyExposureMin` | `360` | max open-position minutes per day |

AI mode (`grading` vs `veto`) is service-side in `.env`. Keep `grading` until
the accuracy log proves the AI earns veto power.

## Tests

```bash
cd service && FORECASTER=fake .venv/bin/pytest -q   # fast suite
pytest -m slow                                      # real model inference
```

## Troubleshooting

- **WebRequest error 4014** — URL not in MT5's allowlist, or port mismatch.
- **"AI unavailable"** — service down; strategy still trades (fail-open).
- **No trades in AUTO** — the Telegram 🚫 notice and Experts log name the gate
  (window, spread, exposure, ADX, kill switch, margin).
- **Kill switch tripped** — deliberate manual reset: delete `XAU_KILL_<login>`
  in MT5 → Tools → Global Variables after reviewing what happened.
