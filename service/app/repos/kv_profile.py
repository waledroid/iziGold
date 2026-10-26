"""Key-Value configuration, Execution Modes, Rule Toggles, and Profile repository mixin for SignalDb."""

from __future__ import annotations

import time

PROFILE_FIELDS = ["name", "email", "phone", "telegram_bot_token",
                  "telegram_chat_id", "risk_per_trade_pct", "max_drawdown_pct",
                  "profit_target_pct", "broker_name", "account_login",
                  "account_type", "risk_ack",
                  "ngrok_authtoken", "ngrok_domain", "miniapp_direct_link",
                  "btc_symbol", "btc_risk_pct", "btc_lane", "btc_enabled"]

CORE_PROFILE_FIELDS = ["name", "email", "phone", "telegram_bot_token",
                       "telegram_chat_id", "risk_per_trade_pct", "max_drawdown_pct",
                       "profit_target_pct", "broker_name", "account_login",
                       "account_type", "risk_ack",
                       "ngrok_authtoken", "ngrok_domain", "miniapp_direct_link"]


def profile_completion(profile) -> int:
    if not profile:
        return 0
    filled = sum(1 for f in CORE_PROFILE_FIELDS
                 if profile.get(f) not in (None, ""))
    return round(100 * filled / len(CORE_PROFILE_FIELDS))


class KvProfileRepoMixin:
    HTF_CHOICES = ("off", "M15", "M30", "H1")
    EMA200_CHOICES = ("off", "on")
    RSI_CHOICES = ("off", "on")
    SR_CHOICES = ("off", "on")
    ADDS_CHOICES = ("off", "on")

    DYNAMIC_STRATEGY_CHOICES = ("off", "auto")

    def get_kv(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_kv(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO kv (key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self.conn.commit()

    def get_profile(self):
        row = self.conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM profile WHERE id = 1").description]
        return dict(zip(cols, row))

    def save_profile(self, partial: dict) -> dict:
        with self._lock:
            now = int(time.time())
            if self.get_profile() is None:
                self.conn.execute(
                    "INSERT INTO profile (id, created_ts, updated_ts) VALUES (1, ?, ?)",
                    (now, now))
            updates = {k: v for k, v in partial.items() if k in PROFILE_FIELDS}
            if isinstance(updates.get("ngrok_domain"), str):
                domain = updates["ngrok_domain"].strip()
                domain = domain.lower().removeprefix("https://").removeprefix("http://")
                domain = domain.split("/", 1)[0].split("?", 1)[0]
                updates["ngrok_domain"] = domain
            if updates.get("risk_ack") and not (self.get_profile() or {}).get("risk_ack_ts"):
                updates["risk_ack_ts"] = now
            updates["updated_ts"] = now
            sets = ", ".join(f"{k} = ?" for k in updates)
            self.conn.execute(f"UPDATE profile SET {sets} WHERE id = 1",
                              tuple(updates.values()))
            self.conn.commit()
            return self.get_profile()

    def get_choice(self, name: str, choices: tuple, default: str,
                   on_invalid: str | None = None) -> str:
        val = self.get_kv(name)
        if val is None:
            return default
        if val in choices:
            return val
        return on_invalid if on_invalid is not None else default

    def set_choice(self, name: str, value: str, choices: tuple) -> None:
        if value not in choices:
            raise ValueError(f"invalid {name}: {value}")
        self.set_kv(name, value)

    def exec_mode(self) -> str:
        return self.get_choice("exec_mode", ("auto", "manual"), "auto",
                               on_invalid="manual")

    def set_exec_mode(self, mode: str) -> None:
        self.set_choice("exec_mode", mode, ("auto", "manual"))

    def runtime_config(self) -> dict:
        profile = self.get_profile() or {}

        def number(name: str, default, cast):
            value = profile.get(name)
            return cast(value) if value not in (None, "") else default

        return {
            "execution_mode": self.exec_mode(),
            "entry_mode": self.entry_mode(),
            "active_strategy": self.get_kv("active_strategy") or "",
            "dynamic_strategy": self.dynamic_strategy(),
            "entries_paused": self.entries_paused(),
            "ai_enabled": self.ai_enabled(),
            "htf_enforce": self.htf_enforce(),
            "ema200_enforce": self.ema200_enforce(),
            "rsi_enforce": self.rsi_enforce(),
            "sr_enforce": self.sr_enforce(),
            "m15_adds": self.m15_adds(),
            "risk_per_trade_pct": number("risk_per_trade_pct", 1.0, float),
            "max_drawdown_pct": number("max_drawdown_pct", 10.0, float),
            "profit_target_pct": number("profit_target_pct", 2.0, float),
            "window_start_hour": number("window_start_hour", 4, int),
            "window_end_hour": number("window_end_hour", 22, int),
            "max_daily_exposure_min": number("max_daily_exposure_min", 0, int),
            "allow_live_trading": True,
            "btc_symbol": str(profile.get("btc_symbol") or "BTCUSD").strip(),
            "btc_risk_pct": number("btc_risk_pct", 0.5, float),
            "btc_lane": str(profile.get("btc_lane") or "iziBit15").strip(),
            "btc_enabled": bool(profile.get("btc_enabled", 1)),
        }

    def dynamic_strategy(self) -> str:
        return self.get_choice("dynamic_strategy", self.DYNAMIC_STRATEGY_CHOICES, "off")

    def set_dynamic_strategy(self, value: str) -> None:
        self.set_choice("dynamic_strategy", value, self.DYNAMIC_STRATEGY_CHOICES)

    def entry_mode(self) -> str:
        return self.get_choice("entry_mode", ("adr", "fixed", "hybrid"), "hybrid")

    def set_entry_mode(self, mode: str) -> None:
        self.set_choice("entry_mode", mode, ("adr", "fixed", "hybrid"))

    def entries_paused(self, symbol: str | None = None) -> bool:
        if symbol:
            sym = symbol.upper()
            if "BTC" in sym:
                return self.get_kv("entries_paused_btc") == "on" or self.get_kv("entries_paused") == "on"
            if "XAU" in sym:
                return self.get_kv("entries_paused_xau") == "on" or self.get_kv("entries_paused") == "on"
        return self.get_kv("entries_paused") == "on"

    def set_entries_paused(self, paused: bool, symbol: str | None = None) -> None:
        val = "on" if paused else "off"
        if symbol:
            sym = symbol.upper()
            if "BTC" in sym:
                self.set_kv("entries_paused_btc", val)
                return
            if "XAU" in sym:
                self.set_kv("entries_paused_xau", val)
                return
        self.set_kv("entries_paused", val)

    def ai_enabled(self) -> bool:
        return self.get_kv("ai_enabled") == "on"

    def set_ai_enabled(self, enabled: bool) -> None:
        self.set_kv("ai_enabled", "on" if enabled else "off")

    def htf_enforce(self) -> str:
        return self.get_choice("htf_enforce", self.HTF_CHOICES, "off")

    def set_htf_enforce(self, value: str) -> None:
        self.set_choice("htf_enforce", value, self.HTF_CHOICES)

    def ema200_enforce(self) -> str:
        return self.get_choice("ema200_enforce", self.EMA200_CHOICES, "off")

    def set_ema200_enforce(self, value: str) -> None:
        self.set_choice("ema200_enforce", value, self.EMA200_CHOICES)

    def rsi_enforce(self) -> str:
        return self.get_choice("rsi_enforce", self.RSI_CHOICES, "on")

    def set_rsi_enforce(self, value: str) -> None:
        self.set_choice("rsi_enforce", value, self.RSI_CHOICES)

    def sr_enforce(self) -> str:
        return self.get_choice("sr_enforce", self.SR_CHOICES, "on")

    def set_sr_enforce(self, value: str) -> None:
        self.set_choice("sr_enforce", value, self.SR_CHOICES)

    def m15_adds(self) -> str:
        return self.get_choice("m15_adds", self.ADDS_CHOICES, "off")

    def set_m15_adds(self, value: str) -> None:
        self.set_choice("m15_adds", value, self.ADDS_CHOICES)
