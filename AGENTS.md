# XAU Assistant

## Repository shape

- `service/` is the FastAPI/SQLite/Telegram/dashboard service; run Python commands from this directory.
- `mt5/` is the MetaTrader 5 Expert Advisor and maintenance script; it runs and compiles on Windows, not in WSL/Linux.
- `bridge/mt5_feed.py` is a read-only Windows bridge that pushes MT5 data to the mini-app; it never places trades.
- `config/strategy.json` is shared strategy configuration used by the EA and backtest scripts.
- `.claude/agents/izi.md` contains the detailed, evolving domain/runbook knowledge; consult it for trading-system behavior and operations.

## Setup and verification

- Use the shared launcher with `python3 launch.py` on Linux or `py -3 launch.py` on Windows; `launch.py` is the canonical staged startup path. `--dry-run`, `--no-install`, `--skip-mt5`, `--restart-mt5`, and explicit `--install-model` are available for safe checks.
- A new machine still needs Git, Docker, Python, and the external MT5 terminal installed before cloning and running the launcher; the launcher cannot bootstrap Python itself on Linux.
- One-shot WSL setup is `bash scripts/setup.sh`; it is idempotent but expects WSL, an MT5 data directory, and MetaEditor unless those phases are handled separately.
- Create the service environment with `cd service && python3 -m venv .venv && source .venv/bin/activate`.
- Install core dependencies with `pip install -r requirements.txt`; install `requirements-model.txt` only when using real Chronos inference.
- Use `FORECASTER=fake .venv/bin/pytest -q` for the fast suite. Pytest excludes `slow` by default via `service/pyproject.toml`.
- Run one test with `service/.venv/bin/pytest tests/test_api.py::test_name` (replace with the target node); run real model tests with `pytest -m slow`.
- Create a consistent SQLite backup with `bash scripts/backup-data.sh`; backups are written to the ignored `backups/` directory.
- Start the main service with `service/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9000`; copy `service/.env.example` to `.env` first. Do not expose it on `0.0.0.0` casually because it serves credentials and the dashboard.
- `FORECASTER=fake` avoids torch/model downloads during development; real Chronos loads lazily on first inference.
- Docker Compose runs only the Linux services: `cp service/.env.example service/.env && docker compose up -d --build`; it publishes host-local ports `9000` and `9101` for MT5 and the bridge. Use `docker compose down` to stop it.
- Inside Compose, the main service probes the mini-app at `http://miniapp:9101/healthz` via `MINIAPP_HEALTH_URL`; `127.0.0.1:9101` is the host/MT5 endpoint, not the main container's mini-app address.
- The ngrok tunnel is optional: `docker compose --profile tunnel up -d` starts it only after `NGROK_AUTHTOKEN` and `NGROK_DOMAIN` are populated from onboarding; missing values must produce a warning, not block trading services.
- SQLite is the runtime configuration source: `/api/config` exposes the effective execution, entry, strategy, confirmation, and onboarding risk settings; the heartbeat carries these values to the EA.
- The trading window is 04–22 server (pre-rollover hour cut, backtest-justified 2026-09-23); it is set in `config/strategy.json`, the EA `TradingWindowEndHour` input, and `runtime_config()`/`HeartbeatResponse` defaults, which must all stay in agreement (enforced by `test_strategy_config.py`).
- Build real Chronos dependencies explicitly with `INSTALL_MODEL=true docker compose build`; the default image does not install the model requirements.
- MQL5 changes must be compiled in MetaEditor on Windows and copied into the MT5 data folder; they cannot be compiled or tested here.

## Architecture constraints

- The strategy is the sole trading decision maker. AI grades signals; keep `MODE=grading` until the accuracy log justifies veto mode.
- In AUTO mode the EA executes before calling `/analyze`; service failure or timeout must remain fail-open so strategy alerts/trades still work.
- The EA posts every closed bar, including `NONE`; those calls lazily resolve prior signal outcomes and must not be removed.
- The EA heartbeat default is 1 second for live status/control; service heartbeat history is downsampled before SQLite persistence.
- The heartbeat interval is service-authoritative (`HEARTBEAT_SEC`, sent as `heartbeat_sec` on every heartbeat response; the EA re-arms its timer). MT5 keeps a chart EA's saved inputs across recompiles, so never rely on an MQL `input` default to change a running EA.
- Deploying MQL changes: restart MT5 so the terminal recompiles the copied sources and reloads the EA. MetaEditor CLI under Wine can exit 0 without producing a new `.ex5`; `launch.py` now verifies `.ex5` freshness and warns instead of reporting a false success.
- Keep forecaster implementations behind the `Forecaster` interface in `service/app/forecaster.py`; model swaps belong there.
- The DeepSeek supervisory agent is optional, asynchronous and fail-open (`service/app/agent.py`, `service/app/agent_actions.py`). With `AGENT_ENABLED=false`, no credentials, or any model failure, the system must behave exactly as before: strategy decides, grading is pending, trading continues. It may only act through bounded intents translated onto the existing heartbeat/proposal rails, never by placing orders.
- The agent governor flag (`entries_paused`, SQLite kv) pauses NEW auto entries only; open-basket management, exits and every risk gate keep working. It is deliberately separate from `exec_mode`.
- The two live strategy lanes are `izi5` (M5) and `izi15` (M15); `ActiveStrategy` in the EA, `config/strategy.json` keys, `telegram.STRATEGY_LANES`, `agent_actions.LANE_ALLOWLIST` and the backtest must use those ids. `SignalDb._migrate_strategy_ids` rewrites historical rows and `kv active_strategy` from the pre-rename ids at startup; the old ids exist only as overlay aliases in `main._OVERLAY_BUILDERS`.
- The agent supervisor loop (`AGENT_SUPERVISOR_INTERVAL_S`) backfills any actionable signal whose bar has no grade (`db.ungraded_signal`), so grades lost to a restart are recovered and the agent keeps a continuous view. `tests/conftest.py` forces `AGENT_ENABLED=false` so the runtime `.env` can never make the suite non-deterministic or reach the network.
- Grades are grounded in `service/app/agent_context.py::strategy_facts` (HalfTrend window state from `config/strategy.json`, EMA/ATR/ADX/RSI/MACD, spread, S/R walls, agent accuracy) and carry typed decisions (`entry_quality`, `expected_r`, `invalidation`). `db.resolve_outcomes` stamps `ai_grades.ai_correct`, so agent quality is measurable — tune prompts/models only against that log, never on impressions.
- The learning loop is `db.resolve_grade_outcomes` (scores directional grades independently of signals) + `agent_actions.reflect` (one lesson per supervisor tick, stored in `agent_lessons`, fed back as `lessons_from_mistakes`). `db.agent_scorecard()` / `GET /api/agent/scorecard` is the setup breakdown, and `scripts/agent_replay.py` grades historical bars offline — run it before changing the prompt, model or thresholds.
- `agent_actions.calibration_gate()` blocks actions on a REAL account until `AGENT_MIN_RESOLVED` grades at `AGENT_MIN_ACCURACY_PCT` accuracy exist (or `AGENT_GATE_BYPASS=true`); demo actions are rate-bounded only. Never widen this gate without reviewing `/api/agent/scorecard`.
- Do not introduce martingale sizing. Pyramiding may add only to winners, and risk/kill-switch state persists in MT5 global variables.
- Live-account AUTO requires `AllowLiveTrading=true`; otherwise initialization/runtime execution must refuse trading.
- When changing `service/app/` or `mt5/`, update `.claude/agents/izi.md` in the same commit or an immediate follow-up. Tests-only, docs-only, and comment-only changes may omit it.

## Secrets and runtime data

- Never commit `service/.env` or SQLite databases; `.env` contains Telegram/feed/ngrok secrets and databases may contain the Telegram token.
- Keep service binds on `127.0.0.1` by default. MT5 must explicitly allow `http://127.0.0.1:9000` in its WebRequest list.
- The live-chart mini-app uses `MINIAPP_PORT` from `service/.env` (default `9101`); do not hard-code another source of truth.
- MT5 is never containerized. The bridge runs on the host: native Windows Python on Windows, or Windows Python inside `~/.mt5` under Wine on Linux. It is read-only and feeds the mini-app; EA-to-service communication must still work if the bridge is unavailable.
- Linux MT5 uses the dedicated `WINEPREFIX` (default `~/.mt5`); use `WINEPREFIX=~/.mt5 winecfg` to fix display scaling or clipped windows rather than changing Docker services.
- The WebRequest allowlist does NOT persist under Wine (tested 2026-09-23): it is GUI-only, stored encrypted in `Config/settings.ini`, and is lost on every MT5 restart even after a clean exit. Startup-ini keys (`WebRequestUrl=`) and `terminal.ini`/`common.ini` `[Experts]` sections have no effect — never re-add them. `err=4014` in the MQL5 log is the symptom and `launch.py` diagnoses it.
- The durable fix is the FILE RELAY (implemented): `mt5/Include/XauAssistant/Transport.mqh` is the EA's single network path (AUTO probes `/health`; uses HTTP when allowed, else writes `MQL5/Files/xau_relay_req.json`), and `scripts/mt5_relay.py`, started by `launch.py` as a HOST process, answers from `xau_relay_resp.json` over an ordinary socket — no allowlist, no GUI, nothing to re-set. On native Windows WebRequest wins and behaviour is unchanged. Recompile the EA after touching `Transport.mqh`/`AiApi.mqh`/`UiApi.mqh`.
