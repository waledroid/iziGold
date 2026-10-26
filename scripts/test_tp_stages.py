import json
import math
import numpy as np
import datetime

# --- TECHNICAL INDICATOR UTILITIES ---

def calculate_atr(candles, period=14):
    highs = np.array([c["h"] for c in candles])
    lows = np.array([c["l"] for c in candles])
    closes = np.array([c["c"] for c in candles])
    n = len(candles)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = np.zeros(n)
    atr[period-1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr

def calculate_ema(values, period):
    n = len(values)
    ema = np.zeros(n)
    alpha = 2.0 / (period + 1)
    ema[0] = values[0]
    for i in range(1, n):
        ema[i] = alpha * values[i] + (1.0 - alpha) * ema[i-1]
    return ema

def calculate_adx(candles, period=14):
    n = len(candles)
    highs = np.array([c["h"] for c in candles])
    lows = np.array([c["l"] for c in candles])
    closes = np.array([c["c"] for c in candles])
    
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    
    for i in range(1, n):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        
    atr_smooth = np.zeros(n)
    plus_di_smooth = np.zeros(n)
    minus_di_smooth = np.zeros(n)
    
    atr_smooth[period] = np.sum(tr[1:period+1])
    plus_di_smooth[period] = np.sum(plus_dm[1:period+1])
    minus_di_smooth[period] = np.sum(minus_dm[1:period+1])
    
    dx = np.zeros(n)
    for i in range(period+1, n):
        atr_smooth[i] = atr_smooth[i-1] - (atr_smooth[i-1] / period) + tr[i]
        plus_di_smooth[i] = plus_di_smooth[i-1] - (plus_di_smooth[i-1] / period) + plus_dm[i]
        minus_di_smooth[i] = minus_di_smooth[i-1] - (minus_di_smooth[i-1] / period) + minus_dm[i]
        
        plus_di = 100 * (plus_di_smooth[i] / atr_smooth[i]) if atr_smooth[i] > 0 else 0
        minus_di = 100 * (minus_di_smooth[i] / atr_smooth[i]) if atr_smooth[i] > 0 else 0
        
        di_sum = plus_di + minus_di
        dx[i] = 100 * (abs(plus_di - minus_di) / di_sum) if di_sum > 0 else 0
        
    adx = np.zeros(n)
    start_adx = 2 * period
    if start_adx < n:
        adx[start_adx] = np.mean(dx[period+1:start_adx+1])
        for i in range(start_adx+1, n):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period
            
    return adx

def calculate_halftrend(candles, amplitude=4):
    n = len(candles)
    highs = np.array([c["h"] for c in candles])
    lows = np.array([c["l"] for c in candles])
    closes = np.array([c["c"] for c in candles])
    
    trend = np.zeros(n, dtype=int)
    next_trend = np.zeros(n, dtype=int)
    max_low_price = np.zeros(n)
    min_high_price = np.zeros(n)
    ht_line = np.zeros(n)
    
    for i in range(amplitude, n):
        low_window = np.min(lows[i-amplitude:i+1])
        high_window = np.max(highs[i-amplitude:i+1])
        
        prev_next_trend = next_trend[i-1]
        prev_max_low = max_low_price[i-1]
        prev_min_high = min_high_price[i-1]
        
        if prev_next_trend == 1:
            max_low = max(low_window, prev_max_low)
            if high_window < max_low and closes[i] < lows[i-1]:
                trend[i] = 1 # Down
                next_trend[i] = 0
                min_high = high_window
            else:
                trend[i] = 0 # Up
                next_trend[i] = 1
                min_high = prev_min_high
        else:
            min_high = min(high_window, prev_min_high)
            if low_window > min_high and closes[i] > highs[i-1]:
                trend[i] = 0 # Up
                next_trend[i] = 1
                max_low = low_window
            else:
                trend[i] = 1 # Down
                next_trend[i] = 0
                max_low = prev_max_low
                
        max_low_price[i] = max_low
        min_high_price[i] = min_high
        ht_line[i] = max_low if trend[i] == 0 else min_high

    return trend, ht_line

def resample_candles(candles, target_tf_sec=900):
    resampled = []
    bucket = []
    for c in candles:
        bucket.append(c)
        if (c["t"] + 300) % target_tf_sec == 0 or len(bucket) == (target_tf_sec // 300):
            resampled.append({
                "t": bucket[-1]["t"],
                "o": bucket[0]["o"],
                "h": max(b["h"] for b in bucket),
                "l": min(b["l"] for b in bucket),
                "c": bucket[-1]["c"],
                "v": sum(b["v"] for b in bucket),
            })
            bucket = []
    return resampled

# --- MULTI-STAGE TP ENGINE ---

def backtest_multi_stage_tp(candles, symbol, stop_atr_mult=1.0, 
                            tp1_r=1.0, tp1_pct=0.50,
                            tp2_r=2.0, tp2_pct=0.25,
                            tp3_r=3.0, tp3_pct=0.25,
                            use_ema200=True, min_adx=20.0,
                            trail_runner=True,
                            starting_balance=10000.0, fixed_lot=0.03):
    is_btc = "BTC" in symbol.upper()
    is_gold = not is_btc
    
    price_point_value = 0.03 if is_btc else 3.0
    spread_cost_per_trade = 0.15 if is_btc else 0.60
    
    n = len(candles)
    highs = np.array([c["h"] for c in candles])
    lows = np.array([c["l"] for c in candles])
    closes = np.array([c["c"] for c in candles])
    
    atr = calculate_atr(candles, 14)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)
    trend, ht_line = calculate_halftrend(candles, 4)
    adx = calculate_adx(candles, 14) if min_adx > 0 else np.zeros(n)
    
    balance = starting_balance
    peak_balance = starting_balance
    max_drawdown = 0.0
    
    trades = []
    open_pos = None
    pending_signal = None
    
    for i in range(200, n):
        c = candles[i]
        cur_px = c["c"]
        dt = datetime.datetime.fromtimestamp(c["t"], datetime.UTC)
        h, m = dt.hour, dt.minute
        
        if is_gold:
            in_window = (4 <= h < 22)
            is_flatten = (h == 23 and m >= 50)
        else:
            in_window = True
            is_flatten = False
            
        # 1. Manage position
        if open_pos is not None:
            is_buy = (open_pos["dir"] == "BUY")
            
            if is_flatten:
                diff = (cur_px - open_pos["entry_px"]) if is_buy else (open_pos["entry_px"] - cur_px)
                pnl = (diff * price_point_value * (open_pos["remaining_lots"] / fixed_lot)) - spread_cost_per_trade
                balance += pnl
                trades.append({"type": "FLATTEN", "pnl": pnl})
                open_pos = None
                continue
                
            # Stop loss check
            sl_hit = False
            exit_px = 0.0
            if is_buy and c["l"] <= open_pos["sl"]:
                sl_hit = True
                exit_px = min(c["o"], open_pos["sl"])
            elif not is_buy and c["h"] >= open_pos["sl"]:
                sl_hit = True
                exit_px = max(c["o"], open_pos["sl"])
                
            if sl_hit:
                diff = (exit_px - open_pos["entry_px"]) if is_buy else (open_pos["entry_px"] - exit_px)
                pnl = (diff * price_point_value * (open_pos["remaining_lots"] / fixed_lot)) - spread_cost_per_trade
                balance += pnl
                trades.append({"type": "SL", "pnl": pnl})
                open_pos = None
            else:
                fav = (c["h"] - open_pos["entry_px"]) if is_buy else (open_pos["entry_px"] - c["l"])
                r_mult = fav / open_pos["r"] if open_pos["r"] > 0 else 0
                
                # --- STAGE 1: TP1 (1:1 Ratio / 1.0R) ---
                if tp1_r > 0 and not open_pos["tp1_hit"] and r_mult >= tp1_r:
                    lots_to_close = round(open_pos["initial_lots"] * tp1_pct, 2)
                    open_pos["remaining_lots"] = max(0.01, round(open_pos["remaining_lots"] - lots_to_close, 2))
                    diff = open_pos["r"] * tp1_r
                    pnl = (diff * price_point_value * (lots_to_close / fixed_lot)) - (spread_cost_per_trade * tp1_pct)
                    balance += pnl
                    open_pos["tp1_hit"] = True
                    # Immediately move SL to Breakeven
                    open_pos["sl"] = open_pos["entry_px"]
                    trades.append({"type": "TP1", "pnl": pnl})
                    
                # --- STAGE 2: TP2 (e.g. 2.0R) ---
                if tp2_r > 0 and open_pos["tp1_hit"] and not open_pos["tp2_hit"] and r_mult >= tp2_r:
                    lots_to_close = round(open_pos["initial_lots"] * tp2_pct, 2)
                    open_pos["remaining_lots"] = max(0.01, round(open_pos["remaining_lots"] - lots_to_close, 2))
                    diff = open_pos["r"] * tp2_r
                    pnl = (diff * price_point_value * (lots_to_close / fixed_lot)) - (spread_cost_per_trade * tp2_pct)
                    balance += pnl
                    open_pos["tp2_hit"] = True
                    # Ratchet SL into positive profit at TP1 level (+1.0R)!
                    open_pos["sl"] = (open_pos["entry_px"] + open_pos["r"] * tp1_r) if is_buy else (open_pos["entry_px"] - open_pos["r"] * tp1_r)
                    trades.append({"type": "TP2", "pnl": pnl})
                    
                # --- STAGE 3: TP3 (e.g. 3.0R or Runner) ---
                if tp3_r > 0 and open_pos["tp2_hit"] and not open_pos["tp3_hit"] and r_mult >= tp3_r:
                    # Final bank or partial
                    lots_to_close = open_pos["remaining_lots"] if not trail_runner else round(open_pos["initial_lots"] * tp3_pct, 2)
                    diff = open_pos["r"] * tp3_r
                    pnl = (diff * price_point_value * (lots_to_close / fixed_lot)) - (spread_cost_per_trade * (lots_to_close / fixed_lot))
                    balance += pnl
                    open_pos["tp3_hit"] = True
                    trades.append({"type": "TP3", "pnl": pnl})
                    
                    if not trail_runner or open_pos["remaining_lots"] <= lots_to_close:
                        open_pos = None
                        continue
                    else:
                        open_pos["remaining_lots"] = max(0.01, round(open_pos["remaining_lots"] - lots_to_close, 2))
                        # Ratchet SL to TP2
                        open_pos["sl"] = (open_pos["entry_px"] + open_pos["r"] * tp2_r) if is_buy else (open_pos["entry_px"] - open_pos["r"] * tp2_r)
                        
                # Reversal flip
                flip = (is_buy and trend[i] == 1) or (not is_buy and trend[i] == 0)
                if flip and open_pos is not None:
                    diff = (cur_px - open_pos["entry_px"]) if is_buy else (open_pos["entry_px"] - cur_px)
                    pnl = (diff * price_point_value * (open_pos["remaining_lots"] / fixed_lot)) - spread_cost_per_trade
                    balance += pnl
                    trades.append({"type": "REVERSAL", "pnl": pnl})
                    open_pos = None
                    
        # Track drawdown
        if balance > peak_balance:
            peak_balance = balance
        dd = peak_balance - balance
        if dd > max_drawdown:
            max_drawdown = dd
            
        # 2. Entries
        if open_pos is None and in_window:
            cur_atr = atr[i]
            if trend[i] != trend[i-1]:
                pending_signal = "BUY" if trend[i] == 0 else "SELL"
                
            if pending_signal is not None:
                is_buy_sig = (pending_signal == "BUY")
                clear_buf = 0.3 * cur_atr
                valid_ema = (c["c"] > ema50[i] + clear_buf) if is_buy_sig else (c["c"] < ema50[i] - clear_buf)
                
                if valid_ema:
                    pass_filters = True
                    if use_ema200:
                        if is_buy_sig and c["c"] < ema200[i]:
                            pass_filters = False
                        elif not is_buy_sig and c["c"] > ema200[i]:
                            pass_filters = False
                            
                    if min_adx > 0 and adx[i] < min_adx:
                        pass_filters = False
                        
                    if pass_filters:
                        stop_dist = stop_atr_mult * cur_atr
                        open_pos = {
                            "dir": pending_signal,
                            "entry_px": cur_px,
                            "initial_lots": fixed_lot,
                            "remaining_lots": fixed_lot,
                            "sl": (cur_px - stop_dist) if is_buy_sig else (cur_px + stop_dist),
                            "tp1_hit": False,
                            "tp2_hit": False,
                            "tp3_hit": False,
                            "entry_i": i,
                            "r": stop_dist
                        }
                    pending_signal = None
                else:
                    pending_signal = None

    total_pnl = balance - starting_balance
    win_trades = [t for t in trades if t["pnl"] > 0]
    loss_trades = [t for t in trades if t["pnl"] <= 0]
    win_rate = (len(win_trades) / len(trades) * 100) if trades else 0.0
    gross_win = sum(t["pnl"] for t in win_trades)
    gross_loss = abs(sum(t["pnl"] for t in loss_trades))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    
    return {
        "trades": len(trades),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_drawdown / starting_balance * 100, 1),
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
    }

if __name__ == "__main__":
    with open("bars_max.json") as f:
        gold_m5 = json.load(f)["candles"]
    gold_m15 = resample_candles(gold_m5, 900)
    
    with open("/home/waledroid/.gemini/antigravity/brain/887516b6-38a8-49a8-8330-a7d79b83ed8c/scratch/btcusd_bars_60k.json") as f:
        btc_m5 = json.load(f)["candles"]
    btc_m15 = resample_candles(btc_m5, 900)
    
    experiments = [
        # Name, Stop ATR, TP1_R, TP1_Pct, TP2_R, TP2_Pct, TP3_R, TP3_Pct, Trail Runner, EMA200, ADX
        ("A1. Current Baseline (Stop 1.75 ATR, 1.5R Partial 50%, Runner)", 1.75, 1.5, 0.50, 0, 0, 0, 0, True, False, 0),
        ("A2. Recommended Filtered (Stop 1.75 ATR, 1.5R Partial 50%, ADX20)", 1.75, 1.5, 0.50, 0, 0, 0, 0, True, False, 20.0),
        
        # User Preferred: 1:1 Ratio (1.0R) to Bank 50% + SL to BE
        ("B1. User 1:1 Ratio (Stop 1.75 ATR, TP1=1.0R 50% Bank, Runner)", 1.75, 1.0, 0.50, 0, 0, 0, 0, True, False, 20.0),
        ("B2. User 1:1 Ratio + Multi-TP (Stop 1.75 ATR, TP1=1.0R 50%, TP2=2.0R 25%, Runner 25%)", 1.75, 1.0, 0.50, 2.0, 0.25, 0, 0, True, False, 20.0),
        ("B3. User 1:1 Ratio + 3-Stage TP (Stop 1.75 ATR, TP1=1.0R 50%, TP2=2.0R 25%, TP3=3.0R 25%)", 1.75, 1.0, 0.50, 2.0, 0.25, 3.0, 0.25, False, False, 20.0),
        
        # Testing Tighter Stops with 1:1 Ratio
        ("C1. Tighter Stop 1.25 ATR (TP1=1.0R 50%, TP2=2.0R 25%, Runner 25%)", 1.25, 1.0, 0.50, 2.0, 0.25, 0, 0, True, False, 20.0),
        ("C2. Tighter Stop 1.0 ATR (TP1=1.0R 50%, TP2=2.0R 25%, Runner 25%)", 1.0, 1.0, 0.50, 2.0, 0.25, 0, 0, True, False, 20.0),
        
        # Testing EMA200 Macro Alignment + Multi-TP
        ("D1. Full Blueprint: Stop 1.5 ATR, TP1=1.0R 50%, TP2=2.0R 25%, Runner 25% + EMA200 + ADX20", 1.5, 1.0, 0.50, 2.0, 0.25, 0, 0, True, True, 20.0),
        ("D2. Full Blueprint: Stop 1.25 ATR, TP1=1.0R 50%, TP2=2.0R 25%, Runner 25% + EMA200 + ADX20", 1.25, 1.0, 0.50, 2.0, 0.25, 0, 0, True, True, 20.0),
        ("D3. Full Blueprint: Stop 1.0 ATR, TP1=1.0R 50%, TP2=2.0R 25%, Runner 25% + EMA200 + ADX20", 1.0, 1.0, 0.50, 2.0, 0.25, 0, 0, True, True, 20.0),
    ]
    
    print("=" * 110)
    print("MULTI-STAGE TP (1:1 RATIO, TP1, TP2, TP3) - GOLD (17 MONTHS, 33,694 M15 BARS)")
    print("=" * 110)
    for name, s_atr, t1_r, t1_p, t2_r, t2_p, t3_r, t3_p, tr, e200, adx_val in experiments:
        res = backtest_multi_stage_tp(gold_m15, "XAUUSD", stop_atr_mult=s_atr,
                                     tp1_r=t1_r, tp1_pct=t1_p,
                                     tp2_r=t2_r, tp2_pct=t2_p,
                                     tp3_r=t3_r, tp3_pct=t3_p,
                                     trail_runner=tr, use_ema200=e200, min_adx=adx_val)
        print(f"{name:<82} | Net: ${res['total_pnl']:>8.2f} | PF: {res['profit_factor']:>4.2f} | Win: {res['win_rate']:>4.1f}% | DD: {res['max_drawdown_pct']:>4.1f}% | Trades: {res['trades']}")
        
    print("\n" + "=" * 110)
    print("MULTI-STAGE TP (1:1 RATIO, TP1, TP2, TP3) - BITCOIN (7 MONTHS, 20,025 M15 BARS)")
    print("=" * 110)
    for name, s_atr, t1_r, t1_p, t2_r, t2_p, t3_r, t3_p, tr, e200, adx_val in experiments:
        res = backtest_multi_stage_tp(btc_m15, "BTCUSD", stop_atr_mult=s_atr,
                                     tp1_r=t1_r, tp1_pct=t1_p,
                                     tp2_r=t2_r, tp2_pct=t2_p,
                                     tp3_r=t3_r, tp3_pct=t3_p,
                                     trail_runner=tr, use_ema200=e200, min_adx=adx_val)
        print(f"{name:<82} | Net: ${res['total_pnl']:>8.2f} | PF: {res['profit_factor']:>4.2f} | Win: {res['win_rate']:>4.1f}% | DD: {res['max_drawdown_pct']:>4.1f}% | Trades: {res['trades']}")

