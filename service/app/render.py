import matplotlib
matplotlib.use("Agg")  # noqa: E402 -- harmless with the OO API below, kept
# in case anything else in the process imports pyplot and picks a backend.
from matplotlib.figure import Figure  # noqa: E402


def render_trade_chart(candles, trade: dict, out_path: str) -> bool:
    """Render the last 100 candles as manual OHLC bars with an entry/exit
    marker and optional SL line, saved as a PNG to `out_path`.

    Uses the matplotlib object-oriented API (`Figure` directly) rather than
    `pyplot`, whose global figure-manager state is not thread-safe --
    renders here run concurrently via `asyncio.to_thread` for overlapping
    /trade-event calls, so sharing pyplot's global current-figure state
    across threads could race and corrupt/cross-contaminate figures.

    Returns True on success, False on any failure (empty candles, bad
    out_path, etc.) -- never raises.
    """
    if not candles:
        return False

    try:
        window = candles[-100:]
        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot(111)

        for i, c in enumerate(window):
            color = "#2ecc71" if c.c >= c.o else "#e74c3c"
            ax.vlines(i, c.l, c.h, color=color, linewidth=1)
            ax.vlines(i, min(c.o, c.c), max(c.o, c.c), color=color, linewidth=3)

        event = trade.get("event", "")
        direction = trade.get("direction", "")
        reason = trade.get("reason", "")
        price = trade.get("price", 0.0)
        sl = trade.get("sl", 0.0)

        marker = "^" if event == "open" else "v"
        marker_color = "#2ecc71" if event == "open" else "#e74c3c"
        ax.scatter([len(window) - 1], [price], marker=marker, color=marker_color,
                  s=160, zorder=5, edgecolors="black")

        if sl and sl > 0:
            ax.axhline(sl, color="#f39c12", linestyle="--", linewidth=1)

        ax.set_title(f"{event} {direction} {reason}")
        fig.tight_layout()
        fig.savefig(out_path)
        return True
    except Exception:
        return False
