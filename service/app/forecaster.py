from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class QuantileForecast:
    q10: list[float]
    q50: list[float]
    q90: list[float]


class Forecaster(ABC):
    @abstractmethod
    def forecast(self, closes: list[float], horizon: int) -> QuantileForecast: ...


class FakeForecaster(Forecaster):
    """Deterministic linear extrapolation — for tests and offline dev."""

    def forecast(self, closes, horizon):
        x = np.arange(len(closes))
        slope, intercept = np.polyfit(x, np.array(closes), 1)
        q50 = [float(intercept + slope * (len(closes) + i)) for i in range(1, horizon + 1)]
        band = float(np.std(np.diff(closes))) or 1e-9
        return QuantileForecast(q10=[v - band for v in q50], q50=q50, q90=[v + band for v in q50])


class ChronosBoltForecaster(Forecaster):
    def __init__(self, model_name: str):
        self._model_name = model_name
        self._pipeline = None

    def _load(self):
        if self._pipeline is None:
            import torch
            from chronos import BaseChronosPipeline
            self._pipeline = BaseChronosPipeline.from_pretrained(
                self._model_name, device_map="cpu", torch_dtype=torch.float32)
        return self._pipeline

    def forecast(self, closes, horizon):
        import torch
        q, _ = self._load().predict_quantiles(
            context=torch.tensor(closes, dtype=torch.float32),
            prediction_length=horizon,
            quantile_levels=[0.1, 0.5, 0.9])
        q = q[0]  # (horizon, 3)
        return QuantileForecast(q10=q[:, 0].tolist(), q50=q[:, 1].tolist(), q90=q[:, 2].tolist())


def get_forecaster(settings) -> Forecaster:
    if settings.forecaster == "fake":
        return FakeForecaster()
    if settings.forecaster == "chronos":
        return ChronosBoltForecaster(settings.chronos_model)
    if settings.forecaster == "timemoe":
        raise NotImplementedError("planned alternative — see spec section 3")
    raise ValueError(f"unknown forecaster: {settings.forecaster}")
