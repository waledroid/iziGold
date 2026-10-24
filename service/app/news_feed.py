"""External economic news calendar feed & fallback.

Provides fail-safe calendar data for upcoming high-impact USD economic events
(e.g., CPI, NFP, FOMC, GDP, Interest Rate decisions) when MT5's internal
`CalendarValueHistory` is unsupported, empty, or failing (-1).

Events are returned in the exact shape the EA and service expect:
  {"in_s": int, "name": str}
where `in_s` is the relative seconds remaining until the event.
"""
import logging
import time
import urllib.request
import json
from typing import Any

log = logging.getLogger("uvicorn.error")

# Cached events: (fetch_ts, list_of_absolute_events)
# Each absolute event is {"ts": int_epoch_utc, "name": str, "impact": "high", "currency": "USD"}
_CACHE: tuple[float, list[dict]] = (0.0, [])
_CACHE_TTL_S = 3600  # refresh hourly


# High-impact USD keywords to identify market-moving releases
_HIGH_IMPACT_KEYWORDS = (
    "cpi", "consumer price index", "fomc", "fed rate", "interest rate decision",
    "non-farm", "nonfarm", "nfp", "employment situation", "unemployment rate",
    "gdp", "gross domestic product", "ppi", "producer price index",
    "retail sales", "pmi", "ism manufacturing", "ism services", "powell"
)


def _is_high_impact_usd(title: str, impact: str = "", currency: str = "") -> bool:
    if currency and currency.upper() != "USD":
        return False
    if impact and impact.lower() in ("high", "red"):
        return True
    t = title.lower()
    return any(k in t for k in _HIGH_IMPACT_KEYWORDS)


def fetch_upcoming_news(horizon_s: int = 86400) -> list[dict]:
    """Return high-impact USD news events occurring in the next `horizon_s` seconds.
    Returns list of dicts with `in_s` (relative seconds from now) and `name`.
    Fail-open: returns empty list on any network/format error.
    """
    global _CACHE
    now = time.time()
    last_fetch, events = _CACHE

    if now - last_fetch > _CACHE_TTL_S:
        fetched = _try_fetch_events()
        if fetched is not None:
            events = fetched
            _CACHE = (now, events)

    results = []
    for ev in events:
        diff = int(ev["ts"] - now)
        if 0 < diff <= horizon_s:
            results.append({"in_s": diff, "name": ev["name"]})

    results.sort(key=lambda x: x["in_s"])
    return results


def _try_fetch_events() -> list[dict] | None:
    """Best-effort fetch from public economic calendars. Fail-open."""
    # Attempt 1: Fetch from ForexFactory / nfs calendar JSON mirror
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    parsed = []
                    for item in data:
                        country = str(item.get("country", "")).upper()
                        impact = str(item.get("impact", "")).lower()
                        title = str(item.get("title", ""))
                        date_str = str(item.get("date", ""))
                        if country == "USD" and _is_high_impact_usd(title, impact, country):
                            try:
                                # ISO 8601 or similar timestamp parse
                                import datetime as _dt
                                # Format example: 2026-10-02T08:30:00-04:00
                                t_val = _dt.datetime.fromisoformat(date_str).timestamp()
                                parsed.append({"ts": int(t_val), "name": title, "impact": "high"})
                            except Exception:
                                continue
                    if parsed:
                        return parsed
        except Exception:
            pass

    return None
