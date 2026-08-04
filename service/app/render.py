import matplotlib
matplotlib.use("Agg")  # noqa: E402 -- harmless with the OO API below, kept
# in case anything else in the process imports pyplot and picks a backend.
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from app.indicators import ema, halftrend


def _favorable(direction: str, entry: float, exit_price: float) -> bool:
    """True when a close's exit price is on the winning side of entry for
    the basket's direction. Pure helper (no plotting) so it's unit-testable
    without rendering a chart."""
    if direction == "BUY":
        return exit_price > entry
    if direction == "SELL":
        return exit_price < entry
    return False


def _hline_with_label(ax, window_len, value, color, linestyle, prefix,
                      linewidth=1.0, alpha=1.0):
    """A full-width horizontal line plus a small right-edge price label,
    e.g. 'E 4041.65' -- used for entry/add/TP/exit lines (SL keeps its own
    inline call for parity with the pre-existing code)."""
    ax.axhline(value, color=color, linestyle=linestyle, linewidth=linewidth,
              alpha=alpha, zorder=1.5)
    ax.text(window_len - 1, value, f"{prefix} {value:g}", color=color,
            fontsize=7, ha="right", va="bottom", alpha=alpha, zorder=6)


def _plot_ema(ax, values, offset, window_len, color, linewidth, alpha=1.0, zorder=1):
    """Plot an EMA series (full-length, indexed like the input candles)
    over the render window [offset, offset + window_len), skipping the
    None warm-up prefix."""
    xs, ys = [], []
    for i in range(window_len):
        v = values[offset + i]
        if v is None:
            continue
        xs.append(i)
        ys.append(v)
    if xs:
        ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha, zorder=zorder)


def _plot_halftrend(ax, ht_values, offset, window_len, linewidth=1.6, zorder=1):
    """Plot the HalfTrend line, segmented per-pair so each piece is colored
    by the trend of its later endpoint (dodgerblue = up, orangered = down),
    matching the EA's live chart painting."""
    prev_x, prev_y = None, None
    for i in range(window_len):
        entry = ht_values[offset + i]
        if entry is None:
            prev_x, prev_y = None, None
            continue
        y, trend = entry
        color = "dodgerblue" if trend == 0 else "orangered"
        if prev_x is not None:
            ax.plot([prev_x, i], [prev_y, y], color=color, linewidth=linewidth,
                     zorder=zorder)
        prev_x, prev_y = i, y


def render_trade_chart(candles, trade: dict, out_path: str) -> bool:
    """Render the last 100 candles as manual OHLC bars with HalfTrend/EMA
    overlays, an entry/exit marker with a price label, and an optional SL
    line, saved as a PNG to `out_path`.

    Indicators are computed over the FULL `candles` list passed in (the EA
    sends 200) so EMA55/EMA200 get maximal warmup, then only the last-100
    window is actually rendered. EMA200 over 200 input bars only has ~1 bar
    of settled history (SMA-seeded), so on a fresh series it is effectively
    just the seed SMA -- acceptable for a visual overlay, not a precision
    indicator.

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
        window_len = len(window)
        offset = len(candles) - window_len

        closes = [c.c for c in candles]
        ema9_full = ema(closes, 9)
        ema21_full = ema(closes, 21)
        ema55_full = ema(closes, 55)
        ema200_full = ema(closes, 200)
        ht_full = halftrend(candles, amplitude=4)

        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot(111)

        # Overlays first, drawn under the price bars (lower zorder).
        _plot_halftrend(ax, ht_full, offset, window_len, linewidth=1.6, zorder=1)
        _plot_ema(ax, ema9_full, offset, window_len, "#888888", 0.8, alpha=0.35, zorder=1)
        _plot_ema(ax, ema21_full, offset, window_len, "#888888", 0.8, alpha=0.35, zorder=1)
        _plot_ema(ax, ema55_full, offset, window_len, "gold", 1.2, zorder=1)
        _plot_ema(ax, ema200_full, offset, window_len, "mediumpurple", 1.2, zorder=1)

        for i, c in enumerate(window):
            color = "#2ecc71" if c.c >= c.o else "#e74c3c"
            ax.vlines(i, c.l, c.h, color=color, linewidth=1, zorder=2)
            ax.vlines(i, min(c.o, c.c), max(c.o, c.c), color=color, linewidth=3, zorder=2)

        event = trade.get("event", "")
        direction = trade.get("direction", "")
        reason = trade.get("reason", "")
        price = trade.get("price", 0.0)
        sl = trade.get("sl", 0.0)
        tp = trade.get("tp", 0.0)
        legs = trade.get("legs") or []
        # The basket's original entry price: first leg when the caller
        # supplied one (open/add/close all carry the basket's legs), else
        # fall back to this event's own price -- keeps old callers/tests
        # that never populate "legs" rendering exactly as before.
        first_entry = legs[0]["price"] if legs else price

        marker = "^" if event == "open" else "v"
        marker_color = "#2ecc71" if event == "open" else "#e74c3c"
        entry_x = window_len - 1
        ax.scatter([entry_x], [price], marker=marker, color=marker_color,
                  s=160, zorder=5, edgecolors="black")
        ax.annotate(f"{price:g}", xy=(entry_x, price),
                    xytext=(6, 8 if event == "open" else -12),
                    textcoords="offset points", color=marker_color,
                    fontsize=7, zorder=6)

        # Risk/profit boxes. Legs carry no timestamps, so there's no real
        # per-leg x-position to anchor a box that spans the trade's whole
        # duration -- keep it simple: a one-bar-wide translucent band at the
        # current event's bar (entry_x), sized in y by entry<->SL /
        # entry<->exit. Same semantics as the MT5/web risk-reward boxes,
        # just collapsed in x since this is a single-snapshot render.
        if sl and sl > 0:
            y1, y2 = sorted((first_entry, sl))
            ax.add_patch(Rectangle((entry_x, y1), 1, y2 - y1,
                                   facecolor="red", alpha=0.12,
                                   edgecolor="none", zorder=0.5))
        if event == "close" and _favorable(direction, first_entry, price):
            y1, y2 = sorted((first_entry, price))
            ax.add_patch(Rectangle((entry_x, y1), 1, y2 - y1,
                                   facecolor="#2ecc71", alpha=0.12,
                                   edgecolor="none", zorder=0.5))

        # Entry/add/SL/TP/exit horizontal lines, each with a small
        # right-edge price label.
        entry_color = "#2ecc71" if direction == "BUY" else "#e74c3c"
        _hline_with_label(ax, window_len, first_entry, entry_color, "-", "E",
                          linewidth=1.2)
        for leg in legs[1:]:
            if leg.get("event") != "add":
                continue
            _hline_with_label(ax, window_len, leg["price"], "gray", ":", "A",
                              linewidth=0.8, alpha=0.7)
        if sl and sl > 0:
            ax.axhline(sl, color="red", linestyle="--", linewidth=1, zorder=1.5)
            ax.text(window_len - 1, sl, f"SL {sl:g}", color="red", fontsize=7,
                    ha="right", va="bottom", zorder=6)
        if tp and tp > 0:
            _hline_with_label(ax, window_len, tp, "green", "--", "TP",
                              linewidth=1.0)
        if event == "close":
            _hline_with_label(ax, window_len, price, "gray", "-", "X",
                              linewidth=1.2)

        legend_handles = [
            Line2D([0], [0], color="dodgerblue", linewidth=1.6, label="HalfTrend"),
            Line2D([0], [0], color="#888888", linewidth=0.8, alpha=0.35, label="EMA9"),
            Line2D([0], [0], color="#888888", linewidth=0.8, alpha=0.35, label="EMA21"),
            Line2D([0], [0], color="gold", linewidth=1.2, label="EMA55"),
            Line2D([0], [0], color="mediumpurple", linewidth=1.2, label="EMA200"),
        ]
        ax.legend(handles=legend_handles, loc="upper left", fontsize=7, framealpha=0.3)

        title = f"{direction} {event} @ {price}" if price else f"{direction} {event}"
        if reason and reason not in (f"signal {direction}",):
            title += f" — {reason}"
        ax.set_title(title, fontsize=10)
        fig.tight_layout()
        fig.savefig(out_path)
        return True
    except Exception:
        return False
