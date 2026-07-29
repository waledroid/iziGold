from app.forecaster import QuantileForecast


def analyze_forecast(fc: QuantileForecast, last_close: float, atr_value: float):
    move = fc.q50[-1] - last_close
    if abs(move) < 0.1 * atr_value:
        return "neutral", 0.0
    band = max((fc.q90[-1] - fc.q10[-1]) / 2.0, 1e-9)
    confidence = round(abs(move) / (abs(move) + band), 2)
    return ("bullish" if move > 0 else "bearish"), confidence
