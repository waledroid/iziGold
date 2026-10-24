"""Canonical trade basket derivation (Phase 3 of Unified Realtime Chart).

Computes the single source of truth for the current trade band across all surfaces:
  {
    "state": "open" | "closed" | "none",
    "direction": "BUY" | "SELL" | "",
    "entry": float,
    "sl": float,
    "tp": float,
    "lots": float,
    "floating_pl": float,
    "strategy_id": str,
    "legs": list[dict],
    "last_event": str,
    "risk_box": {"low": float, "high": float},
    "profit_box": {"low": float, "high": float}
  }

Rule:
- state == 'open' while the EA holds live open positions (heartbeat.positions).
- state == 'closed' when no positions are held, referencing the most recent closed basket.
- state == 'none' when no trading history exists yet.
"""
from typing import Any
import time


def derive_basket_band(db: Any, hb: Any | None, profit_target_pct: float = 2.0,
                       default_strategy: str = "iziGold15", symbol: str | None = None) -> dict:
    """Derive canonical basket band from live heartbeat positions or historical trade rows."""
    positions = getattr(hb, "positions", []) if hb is not None else []
    sym = getattr(hb, "symbol", None) or symbol or "XAUUSD"

    server_offset = 0
    if hb is not None and getattr(hb, "bar_t", 0) > 0:
        diff = hb.bar_t - int(time.time())
        server_offset = int(round(diff / 1800.0) * 1800)
    
    if positions:
        # Live OPEN basket
        first_p = positions[0]
        direction = getattr(first_p, "direction", "BUY")
        is_buy = (direction == "BUY")
        total_lots = sum(getattr(p, "lots", 0.0) for p in positions)
        if total_lots > 0:
            weighted_entry = sum(getattr(p, "lots", 0.0) * getattr(p, "open_price", 0.0)
                                 for p in positions) / total_lots
        else:
            weighted_entry = getattr(first_p, "open_price", 0.0)
        
        sl = getattr(first_p, "sl", 0.0)
        floating_pl = sum(getattr(p, "profit", 0.0) for p in positions)
        strat_id = getattr(hb, "active_strategy", default_strategy) or default_strategy
        
        # Determine TP:
        # Check most recent trade row in db for a recorded tp
        target_price = 0.0
        tp2_price = 0.0
        open_ts = None
        first_ticket = getattr(first_p, "ticket", 0)
        try:
            try:
                recent = db.recent_trades(limit=50, symbol=sym)
            except TypeError:
                recent = db.recent_trades(limit=50)
            if first_ticket > 0 and recent:
                for r in recent:
                    if r.get("ticket") == first_ticket and r.get("event") in ("open", "add"):
                        open_ts = r.get("ts")
                        if r.get("tp", 0) > 0 and target_price <= 0:
                            target_price = float(r["tp"])
                        break
            if not open_ts and recent:
                for r in recent:
                    if r.get("event") in ("open", "add"):
                        open_ts = r.get("ts")
                        if r.get("tp", 0) > 0 and target_price <= 0:
                            target_price = float(r["tp"])
                        break
        except Exception:
            target_price = 0.0
            
        r_dist = abs(weighted_entry - sl) if (sl > 0 and weighted_entry > 0) else 0.0
        if target_price <= 0 and weighted_entry > 0:
            if r_dist > 0:
                # 1.5R partial scale-out target
                target_price = (weighted_entry + 1.5 * r_dist) if is_buy else (weighted_entry - 1.5 * r_dist)
                # 2.0R runner target
                tp2_price = (weighted_entry + 2.0 * r_dist) if is_buy else (weighted_entry - 2.0 * r_dist)
            elif profit_target_pct > 0:
                delta = weighted_entry * (profit_target_pct / 100.0)
                target_price = (weighted_entry + delta) if is_buy else (weighted_entry - delta)
        elif target_price > 0 and r_dist > 0:
            tp2_price = (weighted_entry + 2.0 * r_dist) if is_buy else (weighted_entry - 2.0 * r_dist)
            
        legs = []
        for p in positions:
            legs.append({
                "ticket": getattr(p, "ticket", 0),
                "lots": getattr(p, "lots", 0.0),
                "open_price": getattr(p, "open_price", 0.0),
                "sl": getattr(p, "sl", 0.0),
                "profit": getattr(p, "profit", 0.0),
            })
            
        # Round prices to 2 decimals
        weighted_entry = round(weighted_entry, 2)
        sl = round(sl, 2)
        if target_price > 0:
            target_price = round(target_price, 2)
        if tp2_price > 0:
            tp2_price = round(tp2_price, 2)

        # Risk & profit boxes
        risk_box = {}
        profit_box = {}
        if sl > 0 and weighted_entry > 0:
            risk_box = {"low": min(sl, weighted_entry), "high": max(sl, weighted_entry)}
        if target_price > 0 and weighted_entry > 0:
            profit_box = {"low": min(target_price, weighted_entry), "high": max(target_price, weighted_entry)}
            
        return {
            "state": "open",
            "symbol": sym,
            "direction": direction,
            "open_ts": open_ts,
            "server_offset": server_offset,
            "entry": weighted_entry,
            "sl": sl,
            "tp": target_price if target_price > 0 else 0.0,
            "tp_ratio": 1.5,
            "tp2": tp2_price if tp2_price > 0 else 0.0,
            "tp2_ratio": 2.0,
            "r_distance": round(r_dist, 2),
            "lots": round(total_lots, 2),
            "floating_pl": round(floating_pl, 2),
            "strategy_id": strat_id,
            "legs": legs,
            "last_event": "open" if len(legs) == 1 else "add",
            "risk_box": risk_box,
            "profit_box": profit_box,
        }
        
    # No live positions -> state == 'closed' (or 'none')
    try:
        try:
            recent = db.recent_trades(limit=10, symbol=sym)
        except TypeError:
            recent = db.recent_trades(limit=10)
    except Exception:
        recent = []
        
    if not recent:
        return {
            "state": "none",
            "symbol": sym,
            "direction": "",
            "entry": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "lots": 0.0,
            "floating_pl": 0.0,
            "strategy_id": default_strategy,
            "legs": [],
            "last_event": "none",
            "risk_box": {},
            "profit_box": {},
        }
        
    # Find most recent trade or closed basket
    last_t = recent[0]
    direction = last_t.get("direction", "")
    event = last_t.get("event", "close")
    open_ts = None
    exit_ts = None
    exit_px = 0.0

    if event == "close":
        exit_ts = last_t.get("ts")
        exit_px = float(last_t.get("price", 0.0))
        entry_px = float(last_t.get("price", 0.0))
        sl_px = float(last_t.get("sl", 0.0))
        tp_px = float(last_t.get("tp", 0.0))
        matching_open = None
        for r in recent[1:]:
            if r.get("event") in ("open", "add"):
                if last_t.get("ticket") and r.get("ticket") == last_t.get("ticket"):
                    matching_open = r
                    break
                elif not matching_open:
                    matching_open = r
        if matching_open:
            open_ts = matching_open.get("ts")
            entry_px = float(matching_open.get("price", entry_px))
            sl_px = float(matching_open.get("sl", sl_px))
            if float(matching_open.get("tp", 0.0)) > 0:
                tp_px = float(matching_open.get("tp"))
            if not direction:
                direction = matching_open.get("direction", "")
    else:
        open_ts = last_t.get("ts")
        entry_px = float(last_t.get("price", 0.0))
        sl_px = float(last_t.get("sl", 0.0))
        tp_px = float(last_t.get("tp", 0.0))

    strat = last_t.get("strategy_id", default_strategy)
    profit = float(last_t.get("profit", 0.0))
    
    r_dist = abs(entry_px - sl_px) if (sl_px > 0 and entry_px > 0) else 0.0
    if tp_px <= 0 and entry_px > 0 and r_dist > 0:
        is_buy = (direction == "BUY")
        tp_px = (entry_px + 1.5 * r_dist) if is_buy else (entry_px - 1.5 * r_dist)

    entry_px = round(entry_px, 2)
    sl_px = round(sl_px, 2)
    tp_px = round(tp_px, 2)

    risk_box = {}
    profit_box = {}
    if sl_px > 0 and entry_px > 0:
        risk_box = {"low": min(sl_px, entry_px), "high": max(sl_px, entry_px)}
    if tp_px > 0 and entry_px > 0:
        profit_box = {"low": min(tp_px, entry_px), "high": max(tp_px, entry_px)}
        
    return {
        "state": "closed",
        "symbol": sym,
        "direction": direction,
        "open_ts": open_ts,
        "exit_ts": exit_ts,
        "server_offset": server_offset,
        "exit_price": exit_px,
        "entry": entry_px,
        "sl": sl_px,
        "tp": tp_px,
        "tp_ratio": 1.5,
        "r_distance": round(r_dist, 2),
        "lots": float(last_t.get("lots", 0.0)),
        "floating_pl": round(profit, 2),
        "strategy_id": strat,
        "legs": [],
        "last_event": str(last_t.get("event", "close")),
        "risk_box": risk_box,
        "profit_box": profit_box,
    }

