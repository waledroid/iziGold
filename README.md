# XAU Assistant

An MT5 trading assistant for XAUUSD M15 where **your strategy is the sole
decision maker** and an AI forecasting service (Chronos-Bolt) acts as a
confirmation/grading layer. Alerts via Telegram, signal + outcome logging in
SQLite from day one, manual and automatic execution modes with strict money
management.

Full design: [docs/superpowers/specs/2026-07-29-xau-assistant-design.md](docs/superpowers/specs/2026-07-29-xau-assistant-design.md)
Implementation plan: [docs/superpowers/plans/2026-07-29-xau-assistant-scaffold.md](docs/superpowers/plans/2026-07-29-xau-assistant-scaffold.md)

Strategies live in `mt5/Include/XauAssistant/Strategies/` behind the
`CStrategy` interface and register in the EA's `OnInit`. All registered
strategies are shadow-evaluated and logged every bar; only `ActiveStrategy`
trades. First real strategy: `halftrend_ema_v1` (Half Trend amplitude 4 +
EMA 55 dual confirmation, stop at the wick extreme since the trend flip).
Per-strategy hit-rates accumulate in `xau_assistant.db` (`stats()`).

## 1. Run the AI service (WSL2 or any Linux/macOS)

```bash
cd service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # core (fast)
pip install -r requirements-model.txt    # torch + chronos (large, CPU-only is fine)
cp .env.example .env                     # then edit .env
uvicorn app.main:app --host 0.0.0.0 --port 9000
```

Check: `curl http://127.0.0.1:9000/health` → `{"status":"ok",...}`.

Notes:
- The **first** Chronos call downloads the model (~1 min); afterwards inference
  is ~20–100 ms on CPU.
- To develop without the model: `FORECASTER=fake` in `.env`.
- **If port 9000 is taken**, pick
  another port and set the EA input `ApiUrl` to match.

## 2. Telegram (optional but recommended)

1. Talk to `@BotFather` → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send your bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id` into
   `TELEGRAM_CHAT_ID`.
3. Restart the service. Every non-NONE signal now sends a report.

## 3. Install the EA in MetaTrader 5 (Windows)

1. Install MT5 + open a **demo** account (any broker with XAUUSD).
2. MT5 → File → **Open Data Folder** → `MQL5/`.
3. Copy `mt5/Experts/XauAssistant.mq5` → `MQL5/Experts/`
   and `mt5/Include/XauAssistant/` → `MQL5/Include/XauAssistant/`.
4. Open MetaEditor (F4), open `XauAssistant.mq5`, **Compile** — expect 0 errors.
5. MT5 → Tools → Options → Expert Advisors → check *Allow WebRequest for
   listed URL* and add `http://127.0.0.1:9000` (or your port).
6. Attach `XauAssistant` to a **XAUUSD M15** chart. Enable *Algo Trading*.
7. First test: set input `DebugFireTestSignal=true` → within one bar you should
   see a chart arrow, an MT5 alert with the AI grade, a Telegram message, and a
   row in `service/xau_assistant.db`
   (`sqlite3 xau_assistant.db "SELECT * FROM signals;"`).

## 4. Mode switches (EA inputs)

| Input | Default | Meaning |
|---|---|---|
| `ExecutionMode` | `EXEC_MANUAL` | `EXEC_AUTO` = EA opens/closes positions itself |
| `AllowLiveTrading` | `false` | AUTO on a live account refuses unless explicitly enabled |
| `RiskPerTradePct` | `0.5` | % of equity risked per cycle (fixed-fractional) |
| `MaxDrawdownPct` | `10.0` | Drawdown from equity peak → AUTO disabled (kill switch) |
| `EnablePyramiding` | `true` | Add to winners (1.0/0.7/0.4 sizing, breakeven on add) |
| `ProfitTargetPct` | `2.0` | Close the whole basket at +2% of cycle-start balance |
| `TradingWindowStartHour/EndHour` | `15/18` | Entries only inside this server-time window |
| `MaxDailyExposureMin` | `60` | Max minutes of open-position time per day |
| `DebugFireTestSignal` | `false` | Fire one fake BUY to test the pipeline |
| `ActiveStrategy` | `halftrend_ema_v1` | Which registered strategy trades; others run as logged shadows |
| `HtAmplitude` / `EmaLength` / `ConfirmCloses` | `4` / `55` / `2` | halftrend_ema_v1 parameters |

AI mode (`grading` vs `veto`) is set service-side in `.env` (`MODE=grading`).
Keep it `grading` until the SQLite accuracy log proves the AI earns veto power.

## 5. Tests

```bash
cd service && source .venv/bin/activate
pytest              # fast suite (fake forecaster)
pytest -m slow      # real Chronos-Bolt inference (downloads model)
```

## Troubleshooting

- **WebRequest error 4014** — the URL is not whitelisted (step 3.5), or you
  changed the port and forgot to update `ApiUrl`.
- **Report says "AI unavailable"** — service down, wrong port, or the model
  threw; the strategy signal still stands (fail-open by design). Check
  `uvicorn` output and `curl .../health`.
- **No trades in AUTO** — check the Experts log: entries are blocked with a
  reason (kill switch, window, exposure, spread, ADX filter).
- **Kill switch tripped** — equity fell 10% below its peak. Deliberate manual
  reset required: delete the `XAU_KILL_<login>` global variable in MT5
  (Tools → Global Variables) after you've reviewed what happened.
