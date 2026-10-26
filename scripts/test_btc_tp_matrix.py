import json
import numpy as np
from test_tp_stages import resample_candles, backtest_multi_stage_tp

with open("/home/waledroid/.gemini/antigravity/brain/887516b6-38a8-49a8-8330-a7d79b83ed8c/scratch/btcusd_bars_60k.json") as f:
    btc_m5 = json.load(f)["candles"]
btc_m15 = resample_candles(btc_m5, 900)

print("Sweeping Bitcoin with TP1=1.0R (1:1 Ratio) and various Stop/TP2/TP3/ADX/EMA200 combinations:")
print("-" * 105)

results = []
for stop_atr in [1.25, 1.5, 1.75, 2.0]:
    for tp2_r in [1.75, 2.0, 2.5, 3.0]:
        for tp3_r in [3.0, 4.0, 5.0, 0]: # 0 = trail runner
            for adx_th in [0, 15, 20]:
                for use_ema200 in [False, True]:
                    tr = (tp3_r == 0)
                    res = backtest_multi_stage_tp(
                        btc_m15, "BTCUSD",
                        stop_atr_mult=stop_atr,
                        tp1_r=1.0, tp1_pct=0.50,
                        tp2_r=tp2_r, tp2_pct=0.25,
                        tp3_r=tp3_r, tp3_pct=0.25 if tp3_r > 0 else 0,
                        trail_runner=tr,
                        use_ema200=use_ema200,
                        min_adx=adx_th
                    )
                    cfg_desc = f"Stop={stop_atr}R TP2={tp2_r}R TP3={tp3_r}R ADX={adx_th} E200={use_ema200}"
                    results.append((res['total_pnl'], res['profit_factor'], res['win_rate'], res['max_drawdown_pct'], res['trades'], cfg_desc))

# Sort by profit factor and net pnl
results.sort(key=lambda x: (x[1], x[0]), reverse=True)

print(f"{'Config':<55} | {'Net PnL':<10} | {'PF':<5} | {'Win%':<6} | {'DD%':<5} | {'Trades'}")
print("=" * 105)
for pnl, pf, win, dd, trades, cfg in results[:15]:
    print(f"{cfg:<55} | ${pnl:>8.2f} | {pf:>4.2f} | {win:>5.1f}% | {dd:>4.1f}% | {trades}")

