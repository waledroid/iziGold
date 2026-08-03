# Trade boxes — risk/reward zones on MT5 chart and web price chart

**Date:** 2026-08-03 (approved in-session: both surfaces, risk/reward zones)

Semantics (identical on both surfaces), per trade/basket:
- **Red risk box**: price span entry ↔ stop-loss, time span open ↔ close (open
  trades: extends to the current bar / right edge).
- **Green profit box**: price span entry ↔ exit for closed trades — drawn only
  when the exit is on the favorable side of entry (BUY exit > entry, SELL
  exit < entry). For open trades: entry ↔ current price, only while price is
  favorable; recomputed per bar (web: per refresh).
- A losing trade therefore shows only the red box, with the exit implicitly
  where the band ends (web keeps its existing ▼ exit marker; MT5 relies on
  the box right edge + existing arrows).
- Boxes sit BEHIND price (MT5: OBJPROP_BACK; web: drawn before candles).

## MT5 (EA)

- Drawing hook: `CUiSink.OnTradeEvent` sees "open" (entry price, sl) and the
  basket-final "close" (exit price). New helper (in the EA file or a small
  include) draws/updates rectangles:
  - names `xau_tr_<ticket>_r` / `xau_tr_<ticket>_g`; OBJ_RECTANGLE,
    OBJPROP_FILL=true, OBJPROP_BACK=true, OBJPROP_SELECTABLE=false.
  - colors tuned for the dark theme (true alpha unsupported for objects):
    risk C'66,32,36', profit C'26,56,44'.
  - on "open": risk box (entry↔sl, open-time↔open-time+1 bar); green none.
  - per closed bar while the basket is open (`ProcessBar`): extend both
    boxes' right edge to the current bar; recompute green top/bottom
    (entry↔last close, favorable side only, else delete green).
  - on basket-final "close": freeze right edge at close time; green box
    entry↔exit if favorable else deleted. Pyramid "add" events draw no new
    boxes (arrows already mark them); the basket box tracks the FIRST
    entry price.
  - retention: delete box objects older than the 30 most recent tickets
    (simple prune by object list scan on each open).
  - `OnDeinit`/strategy switch: boxes persist (they're history, not
    indicator state) — only `ObjectsDeleteAll(0, "xau_tr_")` on account
    change is NOT required; no cleanup beyond retention.
- Compile gate 0/0.

## Web (`dashboard.html` priceChart)

- Replace the single profit-tinted band per trade with the two boxes:
  - red rect: x from entry bar to exit bar (or right edge if open), y
    between Y(entry) and Y(sl); skipped when sl missing/0.
  - green rect: closed+favorable → y entry↔exit over same x span; open →
    y entry↔current last close when favorable.
  - fills: rgba(231,76,60,.14) and rgba(46,204,113,.14); keep existing
    entry ▲ / exit ▼ markers and click-to-lightbox; the dashed SL line
    stays for open trades (it doubles as the red box's boundary).
- No service/API changes (sl already in /ui/trades rows).

## Tests / verification

- Web: suite stays green (canvas logic untested by pytest; report the
  integration visually — controller checks the served page renders and the
  drawing code parses).
- MT5: compile 0/0; live visual check is the user's on next trade.
