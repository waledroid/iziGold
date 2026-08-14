# MT5 feed bridge

Read-only bridge that pushes ticks, multi-TF candles, and open positions
from the running MT5 terminal to the mini-app service's `/feed/push`.
It never places or modifies trades — the strategy EA remains the sole
decision maker.

Run with Windows Python (same environment `scripts/dump_bars.py` uses,
since the `MetaTrader5` package only works there), with the terminal
already running and `FEED_KEY` set in `service/.env`:

```
python.exe bridge/mt5_feed.py            # run forever (launcher does this)
python.exe bridge/mt5_feed.py --once     # one snapshot printed + pushed, exit 0/1
```
