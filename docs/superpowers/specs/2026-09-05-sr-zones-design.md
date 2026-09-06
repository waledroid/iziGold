# Support / resistance zones — three timeframe tiers, drawn as boxes

**Date:** 2026-09-05 (approved in-session: three timeframe tiers, boxes not
lines, LIGHT BLUE support / LIGHT YELLOW resistance, report-only first)

## Why

The owner wants both HalfTrend lanes (M5 `halftrend_ema_v1`, M15
`halftrend_m15_v1`) to know where price has been rejected before, so an
entry straight into a wall can be seen, logged and — once the evidence earns
it — refused. Zones are boxes, not lines: a level is an area where price
reacted, and a box shows that area honestly on the chart.

This follows the repo's standing law for every filter (M15 agreement,
EMA-200, RSI): **report first, sweep second, enforce last.** Increment 1 of
this spec changes NO trading behaviour.

## Definitions

- **Tier** — one of three timeframes the zones are built from:
  `H4` (major), `H1` (intermediate), `M15` (minor). Both lanes use the same
  three tiers; on the M15 lane the minor tier is its own timeframe, which is
  fine — those are its own swing points.
- **Zone** — a price box `[lo, hi]` with a `role` (`support` / `resistance`),
  a `tier`, a `touches` count, a `born` bar time (first pivot) and a
  `last_touch` bar time.
- **Pivot** — a fractal swing: a bar whose high is strictly above the highs
  of the `PIVOT_WING = 3` bars on each side (swing high → resistance), or
  whose low is strictly below the `3` lows each side (swing low → support).
  Because of the right wing, a pivot is only known 3 bars after it prints;
  that lag is inherent and is identical in the EA and the replay.
- **Tier ATR** — ATR(14) of the tier's own timeframe at the bar being
  processed. Every width/distance below is in tier ATR unless stated.
  ATR here is MT5's built-in `iATR`, an SMA of True Range (not Wilder) —
  exactly reproducible in Python, see `sma_atr`.

## Zone engine — one algorithm, two implementations

Per tier, processed only when THAT tier closes a bar (a closed M5 bar does
not recompute the H4 tier).

1. **Birth.** A new swing high spawns a resistance box from the pivot bar's
   body top (`max(open, close)`) to its wick `high`; a swing low spawns a
   support box from wick `low` to body bottom (`min(open, close)`). The box
   is then clamped: minimum height `0.15 ATR` (expanded symmetrically about
   the pivot price), maximum height `0.60 ATR` (trimmed toward the wick
   extreme, which is the rejection point).
2. **Merge.** If the new pivot price sits within `0.50 ATR` of an existing
   box of the same tier and role (measured from the nearer box edge, zero
   if inside), the box absorbs it: `lo = min`, `hi = max` (re-clamped to
   the max height, anchored on the wick extreme), `touches += 1`,
   `last_touch = pivot time`. Otherwise a new box is born with
   `touches = 1`.
3. **Death.** A box is removed when a bar of its tier CLOSES beyond it by
   more than `0.50 ATR`: close `> hi + 0.5 ATR` kills a resistance box,
   close `< lo − 0.5 ATR` kills a support box. Wicks through a box do not
   kill it (that is a test, and the box stays). Role flipping (a broken
   support becoming resistance) is deliberately NOT in v1; a later pivot
   simply creates the new box.
4. **Bound.** After each update the tier keeps, per role, only the `3`
   boxes nearest the last close by edge distance (0 if the box contains
   it; ties → more touches, then older). Still at most 6 per tier / 18 in
   total. Boxes further out are dropped (they can be reborn from later
   pivots). This caps chart clutter and bounds compute.
5. **Lookback.** The engine is seeded from history on start:
   `H4: 300 bars`, `H1: 400 bars`, `M15: 400 bars`, then updated
   incrementally. A cold start that cannot read that much history seeds
   from what exists and logs a warning; it never blocks the EA from
   trading (fail-open, like everything else).

The engine is **pure**: candles in, zone list out, no chart or trade state.
The two implementations must produce the same boxes from the same bars:

- `mt5/Include/XauAssistant/SrZones.mqh` — `CSrZones`, one instance per tier,
  reads `iOpen/iHigh/iLow/iClose` on its timeframe and an `iATR(14)` handle.
- `service/app/sr_zones.py` — `build_zones(candles, tier_sec, ...)` operating
  on M5 candles resampled with the SAME bucket rule as
  `scripts/backtest.py::resample` (that helper moves to `scripts/lib` or is
  imported; it is not duplicated). The Python engine serves the replay, the
  mini-app and the screenshot renderer from one function.

All constants above (`PIVOT_WING`, min/max height, merge distance, break
distance, keep-count, lookbacks) live in one place per implementation and
are EA inputs under a new group `"S/R zones"`; defaults are the numbers
above and are NOT tuned by hand — the replay sweep decides.

## Chart boxes — three surfaces, one look

Colours (owner): **support = LIGHT blue, resistance = LIGHT yellow.** Tier
is the shade: H4 strongest, M15 faintest. Boxes sit behind price. MT5
objects have no alpha, so the MT5 tints are pre-blended for the dark theme;
the web uses true alpha on the light-blue `173,216,230` and light-yellow
`255,245,157` bases.

| Tier | Support (light blue)                              | Resistance (light yellow)                          |
|------|---------------------------------------------------|----------------------------------------------------|
| H4   | MT5 `C'120,170,220'`, web `rgba(173,216,230,0.35)` | MT5 `C'235,225,140'`, web `rgba(255,245,157,0.35)` |
| H1   | MT5 `C'90,130,175'`,  web `rgba(173,216,230,0.22)` | MT5 `C'185,175,105'`, web `rgba(255,245,157,0.22)` |
| M15  | MT5 `C'60,95,130'`,   web `rgba(173,216,230,0.12)` | MT5 `C'130,122,75'`,  web `rgba(255,245,157,0.12)` |

Where tiers overlap, the stronger tier is drawn last (on top) so its shade
wins; the MT5 `OBJPROP_ZORDER` and web series creation order both follow
H4 > H1 > M15.

- **MT5** — new `mt5/Include/XauAssistant/SrBoxes.mqh` (`CSrBoxes`), pure
  charting exactly like `TradeBoxes.mqh`: `OBJ_RECTANGLE`, `OBJPROP_FILL`,
  `OBJPROP_BACK`, not selectable, hidden from the object list. Name prefix
  `xau_sr_<tier>_<role>_<bornTime>`; one `ObjectsDeleteAll(0, "xau_sr_")`
  clears everything. Left edge = `born`, right edge = the current bar,
  rebuilt (delete-all + recreate, at most 36 objects) whenever the engine's
  version changes or the chart opens a new bar — zones die and change
  shape, so create-once-then-move does not fit them.
  A right-edge `OBJ_TEXT` label reads e.g. `R H4 x3` / `S M15 x1`. Drawn
  whenever `ShowSrBoxes` is true — the zones are lane-independent, so
  they are not tied to the active lane's paint state — and redrawn from
  the engine's list after any tier update; dead boxes are
  deleted, not left as history (a dead S/R box is misinformation, unlike a
  finished trade box). `ShowSrBoxes` EA input, default true.
- **Mini-app + dashboard** — `_indicator_series` gains
  `"sr_zones": [{tier, role, lo, hi, born, touches}]`. Lightweight-charts
  v4.2.3 has no rectangle primitive; each box is an `addBaselineSeries`
  with `baseValue = {type: "price", price: lo}`, data = `hi` at every bar
  from `born` to the last bar, `lineWidth 0`-equivalent (transparent
  line colours), top fill = the tier colour, bottom fill transparent,
  `lastValueVisible`/`priceLineVisible` off. Series are recreated on
  each history load and on push when the zone list changes (compare a
  cheap fingerprint, like the existing no-change guard).
- **Screenshot (`render.py`)** — one `axhspan`-style rectangle per zone
  spanning `born` → right edge, same colours, drawn before candles, with
  the label at the right edge. Both `render_*` functions get it.

## Strategy hook — report-only in this increment

`CStrategy` gains, in the style of `LastRsiAgree()`:

```
virtual int    LastSrAgree() const { return -1; }   // 1 clear / 0 wall / -1 n/a
virtual string LastSrTier()  const { return ""; }   // "H4"/"H1"/"M15" that judged 0, else ""
virtual void   SetSrOverride(string v) {}           // "" follow input, "off", "on" (increment 3)
```

Both HalfTrend lanes evaluate at the **confirm bar** (the same instant as
`m_confirmEma200` / `m_confirmRsi`), against the zones as they stood at that
bar:

- **BUY**: find the nearest resistance box with `lo >= entry` across the
  enabled tiers, and any box containing `entry`. Verdict `0` (wall) if the
  entry is inside a resistance box, or the nearest box's `lo` is within
  `SrRoomAtr = 1.0` × ATR(14, **trade** timeframe) above entry. Else `1`.
- **SELL**: mirrored against support boxes below.
- `LastSrTier()` names the highest tier that produced the `0` (H4 beats H1
  beats M15) so the report says how big the wall was.
- Tiers consulted: EA input `SrTiers = "H4,H1,M15"` per lane (M5 and M15
  groups, like every other lane input).

A `0` verdict logs one line, `"<id>: S/R wall (<tier> R x3 at lo..hi), entering
anyway (report only)"`, and NEVER blocks in increments 1–2.

**Plumbing** mirrors `rsi_agree` end-to-end: `UiSink` → `PostTradeEvent`
`sr_agree` (int) + `sr_tier` (string) → `TradeEventRequest` in `models.py`
→ `trades` migration (`ALTER TABLE ... ADD COLUMN sr_agree INTEGER DEFAULT
-1`, `sr_tier TEXT DEFAULT ''`) → `recent_trades` → basket flag `"sr"` →
mini-app history column **"S/R"** (`Yes` / `No H4` / `–`) → screenshot
caption `S/R: clear` / `S/R: WALL H4`.

## Replay flag — the evidence

`scripts/backtest.py` gains, in the `exp` group, default off and
**byte-identical when off** (golden pins in izi §6 must not move):

```
--sr-zone-tiers TIERS    comma list of H4,H1,M15 to honour; empty = off
--sr-zone-room-atr K     room needed ahead of the entry, x ATR(14) of --tf (default 1.0)
--sr-zone-report         tag every entry with its wall tier, never refuse
--sr-zone-params K=V,... engine constant overrides (wing, merge_atr, ...)
```

(Shipped names carry the `sr-zone-` prefix because `--sr-lookback` /
`--sr-min-headroom` / `--sr-report` already belong to an older pivot-headroom
study in the same file.) When on, an entry whose verdict is `0` under those
tiers is refused and counted (`run.sr_zone_refused`, printed like
`rsi-filter: refused N entries`, with a per-tier breakdown). The zones are
rebuilt at each signal decision from the trailing lookback of tier bars
closed by that bar, by `service/app/sr_zones.py` on the same resampled bars,
so the replay and the live EA judge identical boxes. The backtest page's
flag map gains an `sr_filter` option for both lanes.

The sweep runs the 17-month dataset, both halves, tiers × room; the
decision to enforce is written into izi with the numbers, as every
previous filter was.

## Enforcement (increment 3, only if the sweep earns it)

EA inputs `SrConfirm` (M5) / `M15SrConfirm` (M15), default `false`, plus a
service-side override pushed on the heartbeat (`SetSrOverride`, values
`""`/`off`/`on`) and a Telegram/`/agree`-style toggle — the EMA-200 module's
shape exactly. When enforced, a `0` verdict makes the confirm fail like a
wrong-side close (signal dead until the next flip). Stop placement at zones
and profit targets at zones are **out of scope**; each is its own future
increment with its own replay flag and sweep.

## Error handling

- Any tier that cannot read enough history, or whose ATR handle is
  invalid, produces an empty zone list for that tier and a log line; the
  verdict for a missing tier is "no wall". The EA never refuses to init or
  trade because zones are unavailable.
- Chart drawing is best-effort: a failed `ObjectCreate` skips that box.
- The service side degrades the same way `ema()`/`halftrend()` do on short
  input: empty list, no exception out of `_indicator_series` or `render`.

## Testing

- **Python unit tests** (`tests/test_sr_zones.py`) on synthetic candles:
  pivot detection with the 3-bar wing and lag; clamp to min/max height;
  merge within 0.5 ATR increments touches; close-through kills, wick-through
  does not; nearest-3 bound; the BUY/SELL room verdict at the boundary; and
  the resample bucket rule shared with the replay.
- **Replay**: existing golden pins unchanged with the flag off; one pinned
  run with `--sr-filter H4 --sr-room-atr 1.0` added to izi §6 once the
  sweep is done.
- **Contract tests**: `TradeEventRequest` accepts/defaults `sr_agree` /
  `sr_tier`; `recent_trades` returns them; migration idempotent.
- **MQL5**: compiles in MetaEditor with 0 errors; verified by reading, then
  by eye on the live chart (boxes present, colours per tier, label text).

## Files

New: `mt5/Include/XauAssistant/SrZones.mqh`, `mt5/Include/XauAssistant/SrBoxes.mqh`,
`service/app/sr_zones.py`, `service/tests/test_sr_zones.py`.
Changed: `mt5/Experts/XauAssistant.mq5` (inputs, wiring, paint), `Strategy.mqh`,
`Strategies/HalfTrendEma.mqh` (both lanes are instances), `UiSink.mqh`,
`service/app/models.py`, `db.py`, `miniapp.py`, `render.py`,
`static/miniapp.html`, `static/dashboard.html`, `backtest_runner.py`,
`scripts/backtest.py`, `.claude/agents/izi.md`.

## Increments

1. Engine + boxes on all three surfaces + report-only column. No trading
   change. (This is the first implementation plan.) — **DONE 2026-09-05.**
2. Replay flag + sweep + izi write-up of the numbers. — **DONE 2026-09-06.**
   Shipped as `--sr-zone-tiers` / `--sr-zone-room-atr` / `--sr-zone-report`
   / `--sr-zone-params` (the `sr-zone-` prefix keeps them apart from the
   older `--sr-lookback` headroom study). Result
   (`docs/studies/2026-09-06-sr-zone-wall-sweep.md`): the M5 lane PASSES
   with `M15 @ 0.5` (fixed-lot +58% net, dd −28%, both halves, on a
   0.25–0.75 plateau); the M15 lane FAILS everywhere — its wall entries are
   its best trades.
3. Enforcement inputs/override — only if increment 2 earns it. **Earned for
   the M5 lane only**, with tiers `M15` and room `0.5`, never for the M15
   lane.
