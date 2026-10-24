"""Telegram inline and reply keyboards and strategy lane constants for XAU Assistant."""

from __future__ import annotations

# The strategy lanes the EA registers (Experts/iziBot.mq5 OnInit).
STRATEGY_LANES = [("M5", "iziGold5"), ("M15", "iziGold15")]
BTC_STRATEGY_LANES = [("₿ M5", "iziBit5"), ("₿ M15", "iziBit15")]


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    """Build an inline keyboard from rows of (text, callback_data) tuples.

    Args:
        rows: list of rows, each row is list of (text, callback_data) tuples

    Returns:
        dict with "inline_keyboard" key containing the structure expected by Telegram API
    """
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def MAIN_MENU_REPLY_KB(xau_paused: bool = False, btc_paused: bool = False) -> dict:
    """Persistent ultra-clean reply keyboard docking at the bottom of the user's screen.
    Includes the two primary security toggle buttons for XAU and BTC."""
    gold_btn = "🥇 Gold: ▶️ PLAY" if xau_paused else "🥇 Gold: ⏹️ STOP"
    btc_btn = "₿ BTC: ▶️ PLAY" if btc_paused else "₿ BTC: ⏹️ STOP"
    return {
        "keyboard": [
            [{"text": gold_btn}, {"text": btc_btn}],
            [{"text": "📊 Status"}, {"text": "⚡ Trade"}, {"text": "📈 Report"}],
            [{"text": "⚙️ Settings"}, {"text": "🤖 AI"}, {"text": "🌐 Web Chart"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def STATUS_INLINE_KB() -> dict:
    """Action deck attached to Live Status replies."""
    return kb([
        [("🔄 Refresh", "nav:status"), ("⚡ Quick Trade", "nav:trade")],
        [("📈 P/L Report", "nav:report"), ("⚙️ Settings", "nav:mode")],
        [("🤖 AI Hub", "nav:ai")]
    ])


def PERF_INLINE_KB() -> dict:
    """Action deck attached to Performance/Stats replies."""
    return kb([
        [("🔄 Refresh", "nav:report"), ("📜 History", "nav:history")],
        [("📊 Live Status", "nav:status"), ("⚡ Quick Trade", "nav:trade")]
    ])


def REPORT_INLINE_KB() -> dict:
    """Action deck attached to /report replies."""
    return kb([
        [("🔄 Refresh", "nav:report"), ("📜 Last 10 Trades", "nav:history")],
        [("📊 Live Status", "nav:status"), ("⚡ Quick Trade", "nav:trade")]
    ])


def NEWS_INLINE_KB() -> dict:
    """Action deck attached to News & Calendar replies."""
    return kb([
        [("🔄 Refresh Calendar", "nav:news"), ("📊 Live Status", "nav:status")]
    ])


def HISTORY_INLINE_KB() -> dict:
    """Action deck attached to Trade History replies."""
    return kb([
        [("🔄 Refresh History", "nav:history"), ("📈 Performance", "nav:stats")],
        [("📊 Live Status", "nav:status")]
    ])


def AI_INLINE_KB(ai_on: bool) -> dict:
    """Action deck for the AI Supervisor cockpit."""
    btn_text = "🔴 Turn AI OFF" if ai_on else "🟢 Turn AI ON"
    btn_val = "ai:off" if ai_on else "ai:on"
    return kb([
        [(btn_text, btn_val)],
        [("🔄 Refresh", "nav:ai"), ("📊 Live Status", "nav:status")]
    ])


def PROPOSAL_KB(pid):
    """Keyboard for taking or skipping a trade proposal."""
    return kb([[("🟢 Take trade", f"prop:{pid}:take"), ("🔴 Skip", f"prop:{pid}:skip")]])


def EXIT_KB(pid):
    """Keyboard for exiting or holding a position."""
    return kb([[("🔴 Exit now", f"prop:{pid}:take"), ("⏸ Hold", f"prop:{pid}:skip")]])


def EXIT_NOW_KB(trade_id):
    """Keyboard on trade-open notifications: close the basket or manage risk."""
    return kb([
        [("🔴 Close Position", f"exitnow:{trade_id}")],
        [("🔒 Lock BE", f"movesl:be:{trade_id}"), ("✂️ Close 50%", f"closehalf:{trade_id}")],
    ])


def RICH_TRADE_KB(trade_id):
    return EXIT_NOW_KB(trade_id)


def BRAKE_RESET_KB():
    """Keyboard on the daily-loss-brake 70% / TRIPPED notices: reset the
    brake for today (owner-only callback -> pre-approved reset_brake
    proposal -> EA command on the next heartbeat)."""
    return kb([[("🔓 Reset brake for today", "brakereset:1")]])


def TARGET_KB():
    """Keyboard on the FIXED-ride target alert: exit the basket, or ratchet
    the stop to the current price to lock the gain and keep riding."""
    return kb([[("🔴 EXIT — close trade", "exitnow:0")],
               [("🔒 Move SL to here", "movesl:1")]])


def TRADE_KB(multi_asset: bool = True):
    """Keyboard on the /trade prompt."""
    if multi_asset:
        return kb([
            [("🥇 BUY Gold", "mtrade:XAUUSD:BUY"), ("🥇 SELL Gold", "mtrade:XAUUSD:SELL")],
            [("₿ BUY Bitcoin", "mtrade:BTCUSD:BUY"), ("₿ SELL Bitcoin", "mtrade:BTCUSD:SELL")],
            [("📊 Cockpit Status", "nav:status")]
        ])
    return kb([[("🔵 BUY", "mtrade:BUY")], [("🔴 SELL", "mtrade:SELL")]])
