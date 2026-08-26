#!/usr/bin/env python3
"""Regenerate docs/izi_manual.pdf — the operator quick-reference shipped by
the Telegram /manual command.

Run from anywhere with the service venv:
    service/.venv/bin/python3 scripts/build_manual.py

Content lives in PAGES below as (kind, text) lines; kinds are "h1" (page
title), "h2" (section), "body" (wrapped prose), "code" (monospace command,
never wrapped), "gap". Keep it emoji-free: PDF core fonts carry no emoji,
so buttons are written as [EXIT], [Move SL]."""
import datetime
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "izi_manual.pdf"

PAGES = [
    [
        ("h1", "izi — XAU Assistant: operator manual"),
        ("body", f"Generated {datetime.date.today().isoformat()} by scripts/build_manual.py. "
                 "Get this in Telegram any time with /manual."),
        ("gap", ""),
        ("h2", "What this is"),
        ("body", "An MT5 trading assistant for XAUUSD in two halves. The MQL5 Expert "
                 "Advisor (runs inside MetaTrader 5 on Windows) holds the strategy and is "
                 "the sole decision maker: it evaluates every closed bar, executes in AUTO "
                 "mode, and manages the basket. The Python service (runs in WSL2, port "
                 "9000) grades each signal with an AI forecaster, sends every alert to "
                 "Telegram, and logs everything to SQLite."),
        ("body", "Two rules explain most behavior. (1) The strategy decides; the AI only "
                 "grades. In AUTO the EA executes FIRST, then asks the AI. (2) Fail-open "
                 "everywhere: if the AI service is down, strategy signals still alert and "
                 "execute, marked 'AI unavailable'. A dead service never blocks a trade."),
        ("gap", ""),
        ("h2", "The moving parts"),
        ("code", "MT5 (Windows): XauAssistant EA on a XAUUSD chart"),
        ("code", "Service (WSL2): FastAPI on http://127.0.0.1:9000"),
        ("code", "Database:      service/xau_assistant.db (SQLite)"),
        ("code", "Telegram bot:  alerts + remote control (below)"),
        ("body", "The EA heartbeats the service every 5 seconds; remote commands (manual "
                 "trades, exits, stop moves) ride back on the next heartbeat, so a tap "
                 "takes at most ~5 s to reach MT5."),
    ],
    [
        ("h1", "New client setup"),
        ("body", "One script does almost everything and is safe to re-run at any time — "
                 "finished phases skip:"),
        ("code", "cd <repo> && scripts/setup.sh"),
        ("body", "It creates the venv, installs dependencies, runs the tests, starts the "
                 "service, links Telegram (message your bot once when prompted), copies "
                 "the EA into the MT5 data folder and compiles it."),
        ("gap", ""),
        ("h2", "Configuration"),
        ("code", "cd service && cp .env.example .env"),
        ("body", "FORECASTER=fake runs without the AI model (deterministic stand-in) — "
                 "useful on machines without torch. Leave the default for real grading."),
        ("gap", ""),
        ("h2", "Two manual MT5 steps (MT5 stores these encrypted)"),
        ("body", "1. Tools > Options > Expert Advisors: tick 'Allow WebRequest for listed "
                 "URL' and add exactly:"),
        ("code", "http://127.0.0.1:9000"),
        ("body", "2. Drag XauAssistant (Navigator > Expert Advisors) onto a XAUUSD chart "
                 "and tick 'Allow Algo Trading' in the dialog. If the EA was already on "
                 "the chart, remove and re-attach it."),
        ("gap", ""),
        ("h2", "Live accounts"),
        ("body", "On a real-money account AUTO refuses to trade unless the EA input "
                 "AllowLiveTrading is set to true. This is deliberate: flip it only when "
                 "the logged AI accuracy says the system has earned it."),
    ],
    [
        ("h1", "Daily operations"),
        ("h2", "Come up"),
        ("body", "Double-click xau-launch.bat on the Desktop — it boots MT5, WSL and the "
                 "service. Or by hand:"),
        ("code", "cd <repo>/service"),
        ("code", "nohup .venv/bin/uvicorn app.main:app \\"),
        ("code", "  --host 127.0.0.1 --port 9000 >> service.log 2>&1 &"),
        ("body", "The first AI forecast after a start can take minutes (model load); "
                 "everything after is fast. Trading is not blocked meanwhile (fail-open)."),
        ("gap", ""),
        ("h2", "Is it running?"),
        ("code", "curl http://127.0.0.1:9000/health"),
        ("body", "...and in Telegram, /status: the first line shows the EA connection "
                 "('EA: connected (Ns ago)'). If MT5 restarted, verify the EA is still on "
                 "the chart — it can detach silently; re-attaching is manual."),
        ("gap", ""),
        ("h2", "Shutdown"),
        ("code", "pkill -f \"uvicorn app.main\"   # exit code 144 is normal"),
        ("body", "...then close MetaTrader 5. Risk state (kill switch, drawdown "
                 "watermark, daily brake) survives restarts — it lives in MT5 global "
                 "variables, not in memory."),
        ("gap", ""),
        ("h2", "Restart after a code change"),
        ("body", "Kill first (foreground), start second (background) — never both in one "
                 "backgrounded command. The service does not auto-reload. For EA changes, "
                 "re-run scripts/setup.sh (copies + compiles; expect '0 errors'); the "
                 "terminal reloads the EA automatically after a successful compile."),
    ],
    [
        ("h1", "Telegram commands"),
        ("body", "Everything is also pinned in the chat. Commands reply only to the "
                 "owner chat; a linked broadcast channel sees trade activity but never "
                 "account figures or buttons."),
        ("code", "/status   snapshot: session, EA + mini-app connection, protection"),
        ("code", "/bal      balance, equity, floating P/L, today + week P/L"),
        ("code", "/mode     execution AUTO/MANUAL, entry ADR/FIXED, lane M5/M15"),
        ("code", "/agree    confirmation modules: higher-TF + EMA-200 agreement"),
        ("code", "/trade    manual entry: [BUY] / [SELL] buttons, tap = confirm"),
        ("code", "/news     upcoming high-impact USD events + blackout windows"),
        ("code", "/config   current settings incl. entry mode"),
        ("code", "/chart    live chart (mini app) or a rendered snapshot"),
        ("code", "/stats    per-strategy signal hit-rates"),
        ("code", "/history  last 10 trade events"),
        ("code", "/channel  link/unlink the broadcast channel"),
        ("code", "/help     the pinned command reference again"),
        ("code", "/manual   this PDF"),
        ("gap", ""),
        ("h2", "How a manual trade works"),
        ("body", "/trade shows the live price and two buttons. Tapping one queues the "
                 "entry; the EA opens it on the next heartbeat, subject to ALL its own "
                 "risk gates (kill switch, daily brake, spread, sizing). Once open it is "
                 "a normal EA basket: managed, alerted, and auto-exited like any other. "
                 "Guards refuse the tap when a trade is already open, an entry is "
                 "already queued, or the EA is disconnected."),
    ],
    [
        ("h1", "Buttons and troubleshooting"),
        ("h2", "Buttons you will meet"),
        ("code", "[Take trade]/[Skip]  MANUAL mode entry proposals"),
        ("code", "[EXIT - close trade] on trade-open notices: close the basket"),
        ("code", "[EXIT] + [Move SL]   on the FIXED-ride target alert"),
        ("code", "[Reset brake]        on daily-loss-brake notices (>=70% spent)"),
        ("body", "[Move SL to here] ratchets every leg's stop to the current price, "
                 "locking the gain while the ride continues. It only ever tightens, and "
                 "the buttons return after each move, so later taps lock more. Old "
                 "buttons stay tappable forever — every tap is re-checked, so a stale "
                 "tap degrades to a polite refusal, never a wrong trade."),
        ("gap", ""),
        ("h2", "Quick diagnosis"),
        ("body", "Entries are also frozen for 30 minutes either side of high-impact USD "
                 "news (the guard alerts you ~35 minutes ahead; /news lists the day's "
                 "windows). Exits are never blocked. "
                 "EA shows disconnected: is MT5 open, the EA on the chart (smiley icon), "
                 "Algo Trading enabled, and the WebRequest allowlist set? "
                 "Indicator lines missing after switching timeframes: they repaint on "
                 "the first tick; if the market is closed they return with the next tick. "
                 "A trade did not fire: /status — check protection (brake or kill "
                 "switch), the trading window, and spread; refusals are also alerted "
                 "with the real reason."),
        ("gap", ""),
        ("h2", "Where things live"),
        ("code", "service/service.log      service log (rotated at 20 MB)"),
        ("code", "service/xau_assistant.db signals, trades, heartbeats (SQLite)"),
        ("code", "MT5 'Experts' tab        EA-side log"),
        ("code", "scripts/setup.sh         re-run any time; it self-skips"),
    ],
]

_STYLES = {           # (font size, family, weight, line height, indent)
    "h1":   (17, "DejaVu Sans", "bold",  0.045, 0.06),
    "h2":   (12, "DejaVu Sans", "bold",  0.030, 0.06),
    "body": (9.5, "DejaVu Sans", "normal", 0.0205, 0.06),
    "code": (9, "DejaVu Sans Mono", "normal", 0.0215, 0.10),
}
_WRAP = {"body": 92}


def _render_page(pdf, lines, footer):
    fig = Figure(figsize=(8.27, 11.69))       # A4 portrait
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    y = 0.945
    for kind, text in lines:
        if kind == "gap":
            y -= 0.012
            continue
        size, family, weight, lh, x = _STYLES[kind]
        chunks = textwrap.wrap(text, _WRAP[kind]) if kind in _WRAP else [text]
        if kind == "h2":
            y -= 0.008
        for chunk in chunks:
            ax.text(x, y, chunk, fontsize=size, family=family,
                    fontweight=weight, va="top", ha="left",
                    color="#1a1a1a" if kind != "code" else "#0b3d66")
            y -= lh
        y -= 0.004
    ax.text(0.5, 0.025, footer, fontsize=7.5, family="DejaVu Sans",
            color="#888888", ha="center", va="bottom")
    pdf.savefig(fig)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(OUT) as pdf:
        n = len(PAGES)
        for i, page in enumerate(PAGES, 1):
            _render_page(pdf, page, f"izi - XAU Assistant operator manual - page {i}/{n}")
        meta = pdf.infodict()
        meta["Title"] = "izi - XAU Assistant operator manual"
        meta["Author"] = "izi"
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(PAGES)} pages)")


if __name__ == "__main__":
    main()
