import matplotlib
matplotlib.use("Agg")  # noqa: E402 -- must precede pyplot import, no display in service
import matplotlib.pyplot as plt  # noqa: E402


def render_trade_chart(candles, trade: dict, out_path: str) -> bool:
    """Render the last 100 candles as manual OHLC bars with an entry/exit
    marker and optional SL line, saved as a PNG to `out_path`.

    Returns True on success, False on any failure (empty candles, bad
    out_path, etc.) -- never raises.
    """
    if not candles:
        return False

    fig = None
    try:
        window = candles[-100:]
        fig, ax = plt.subplots(figsize=(10, 5))

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
    finally:
        if fig is not None:
            plt.close(fig)
