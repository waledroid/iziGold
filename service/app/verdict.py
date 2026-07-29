def combine(signal: str, direction: str, confidence: float, threshold: float = 0.6) -> str:
    if signal not in ("BUY", "SELL"):
        return "neutral"
    want = "bullish" if signal == "BUY" else "bearish"
    against = "bearish" if signal == "BUY" else "bullish"
    if direction == want and confidence >= threshold:
        return "confirm"
    if direction == against and confidence >= threshold:
        return "conflict"
    return "neutral"
